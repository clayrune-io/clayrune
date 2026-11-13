"""One shared, checked agent-mail store outside the repository (MC-1166 unit 22).

Composes ingress, enrollment, outbound and recovery over the SAME inbox database
under ``<clayrune home>/agent-mail`` (7b's location). Nothing here creates an
identity, key, root or pin, calls a provider, or enables anything: every flag
stays in ``state.CONFIG`` and defaults off.

Store custody is anchored by an external marker next to the database, so a
deleted or replaced inbox can never silently become empty authority:

* no marker, no database        -> never enrolled (a read is an authoritative miss)
* marker ``creating``, no db    -> creation never finished; no authority existed
* marker ``ready``, no database -> HELD ``store_missing`` (never recreated)
* database but no marker        -> HELD ``marker_missing`` (never auto-adopted)
* corrupt/foreign/newer store or marker, or a missing identity seal -> HELD

The earlier receiver's ``data/agent_mail.sqlite3`` is never read or migrated.
This module never calls ``question_channel`` (see ``question_channel_retention``).

Pull setup explicitly calls bootstrap(authorize=...). Runtime pull composition
validates but never creates/upgrades a store, and never starts a socket. The
trusted reviewed_service callback must revalidate root-signed evidence, durable
manifest floor and human pin approval on EVERY call; absent wiring refuses.
Human authorization is supplied per signup/owner-change adapter, never cached
as a factory-wide grant. Legacy enrollment/outbound remain compatibility seams
until their separately scoped retirement; pull never selects a ceremony.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mc import state
from mc.agent_mail_ceremony_device import keystore_identity
from mc.agent_mail_ceremony_remote import ceremony_enabled, select_ceremony
from mc.agent_mail_enrollment import Enrollment, Prerequisites
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, SealReference
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_remote import UnavailableRemote, UnavailableTransport, unavailable_material
from mc.agent_mail_owner_change import OwnerChange
from mc.agent_mail_pull_client import PullClient
from mc.agent_mail_pull_receiver import PullReceiver
from mc.agent_mail_pull_schema import PullSchemaCorrupt, initialize as initialize_pull, validate as validate_pull
from mc.agent_mail_service_client import HttpsTransport, ServiceClient
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signup import Signup, SignupTransport
from mc.agent_mail_store import DB_FILENAME, SCHEMA_VERSION, MailStore, MailStoreConfig, MailStoreCorrupt, MailStoreError
from mc.atomic_json import write_json_atomic
from mc.core import _log
from mc.secrets_store import clayrune_home, peek_passphrase_key

MARKER_NAME = 'agent_mail.marker'
MARKER_VERSION = 1
IDENTITY_DIR = 'identity'
INSTALL_KEY, ROOT_KEY, ORIGIN_KEY = ('agent_mail_install_id', 'agent_mail_root_public_key',
                                     'agent_mail_service_origin')


class StoreHeld(MailStoreError):
    """The store is visibly held. ``reason`` is a closed code, never free text."""

    def __init__(self, reason: str):
        super().__init__('agent mail store held: ' + reason)
        self.reason = reason


@dataclass(frozen=True)
class MailPaths:
    root: Path

    @property
    def store(self) -> Path:
        return self.root / DB_FILENAME

    @property
    def marker(self) -> Path:
        return self.root / MARKER_NAME

    @property
    def identity(self) -> Path:
        return self.root / IDENTITY_DIR


def default_root() -> Path:
    # Same location as mc.agent_mail_enrollment_state.default_enrollment_folder(),
    # but honouring CLAYRUNE_HOME so a test home never reads operator state.
    return clayrune_home() / 'agent-mail'


class GuardedMailStore(MailStore):
    """A MailStore that never lets sqlite recreate a vanished file.

    ``sqlite3.connect`` silently creates a missing database; a deleted inbox
    would then reopen as empty authority. Only the explicit creation path may
    pass ``creating=True``.
    """

    def __init__(self, path: Path, *, creating: bool = False, **kwargs: Any):
        self._creating = creating
        super().__init__(path, **kwargs)
        self._creating = False

    def _connect(self) -> sqlite3.Connection:
        if not self._creating and not self.path.exists():
            raise StoreHeld('store_missing')
        return super()._connect()


class MailFactory:
    def __init__(self, root: Path | None = None, *, clock: Callable[[], float] = time.time,
                 key: Callable[[], bytes | None] = peek_passphrase_key,
                 config: dict[str, Any] | None = None,
                 store_config: MailStoreConfig | None = None,
                 remote: Any = None, transport: Any = None,
                 prerequisites: Callable[[], Prerequisites] | None = None,
                 ceremony_transport: Any = None, ceremony_device: Callable[[], Any] | None = None,
                 pull_transport: SignupTransport | None = None,
                 reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]] | None = None):
        self.paths = MailPaths(Path(root) if root is not None else default_root())
        self.clock, self.key, self._config, self.store_config = clock, key, config, store_config
        self.remote = remote if remote is not None else UnavailableRemote()
        self.transport = transport if transport is not None else UnavailableTransport()
        # Ceremony seams (7d-2e-3). None = the real HTTPS transport / keystore identity, built only
        # when both ceremony flags are on; tests inject fakes.
        self._ceremony_transport, self._ceremony_device = ceremony_transport, ceremony_device
        self._pull_transport, self._reviewed_service = pull_transport, reviewed_service
        self.prerequisites = prerequisites or Prerequisites
        self._lock = threading.RLock()
        self._store: MailStore | None = None
        self._outbound: tuple[tuple[bool, bool], Outbound] | None = None
        self._pull_client: PullClient | None = None
        self._pull_client_origin: str | None = None
        self._seal = IdentitySeal(self.paths.identity, key=lambda: self.key())

    @property
    def config(self) -> dict[str, Any]:
        return state.CONFIG if self._config is None else self._config

    # --- marker ----------------------------------------------------------
    def _marker(self) -> str | None:
        """'creating' | 'ready' | None (absent). Corrupt/foreign refuses."""
        path = self.paths.marker
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreHeld('marker_corrupt') from None
        if type(value) is not dict or value.get('state') not in ('creating', 'ready') \
                or type(value.get('version')) is not int:
            raise StoreHeld('marker_corrupt')
        if value['version'] != MARKER_VERSION:
            raise StoreHeld('marker_version')
        if value.get('store') != DB_FILENAME:
            raise StoreHeld('marker_corrupt')
        return value['state']

    def _write_marker(self, marker_state: str) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.paths.marker, {'version': MARKER_VERSION, 'state': marker_state,
                                              'store': DB_FILENAME})

    # --- store -----------------------------------------------------------
    def _classify(self, exc: Exception) -> StoreHeld:
        if isinstance(exc, StoreHeld):
            return exc
        if isinstance(exc, PullSchemaCorrupt):
            return StoreHeld('schema_corrupt')
        if isinstance(exc, MailStoreCorrupt):
            return StoreHeld('store_corrupt')
        if isinstance(exc, MailStoreError) and 'newer than this code' in str(exc):
            return StoreHeld('store_version')
        return StoreHeld('store_unavailable')

    def _check_seal(self, store: MailStore) -> None:
        """An enrolled flow whose sealed identity is gone stays held: no new key."""
        with store._read() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
                raise StoreHeld('store_version')
            pull = db.execute("SELECT 1 FROM sqlite_master WHERE lower(name) GLOB 'mail_pull_*'").fetchone()
        if pull:
            validate_pull(store)
            self._check_pull_seals(store)
        with store._read() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_enrollment_flow'").fetchone():
                return
            row = db.execute('SELECT flow,public_key,stage FROM mail_enrollment_flow WHERE slot=1').fetchone()
        if row and row['public_key'] and row['stage'] in ('pending', 'delivery_pending', 'active'):
            if not (self.paths.identity / (row['flow'] + '.sealed')).is_file():
                raise StoreHeld('seal_missing')

    def _check_pull_seals(self, store: MailStore) -> None:
        # The sole legitimate unsealed account is Signup's durable preparation
        # intent, committed BEFORE key generation. A committed public key may
        # never be regenerated. Check file presence without unlocking the vault:
        # cancellation-only fences must remain available while it is locked.
        with store._read() as db:
            accounts = db.execute('SELECT * FROM mail_pull_accounts').fetchall()
            for account in accounts:
                generation = db.execute('SELECT * FROM mail_pull_generations WHERE install_id=? AND generation=?',
                                        (account['install_id'], account['generation'])).fetchone()
                if generation is None or any(account[k] != generation[k] for k in
                        ('owner_generation', 'key_id', 'seal_flow_id')):
                    raise StoreHeld('schema_corrupt')
                flow = account['seal_flow_id']
                try:
                    IdentitySeal._reference(SealReference(flow, account['install_id'], account['generation'], account['key_id']))
                except EnrollmentRefused:
                    raise StoreHeld('schema_corrupt') from None
                if (self.paths.identity / (flow + '.sealed')).is_file():
                    continue
                preparing = db.execute("SELECT * FROM mail_pull_flows WHERE flow_id=? AND purpose='signup'",
                                       (flow,)).fetchone()
                try:
                    meta = json.loads(preparing['result_json']) if preparing else None
                    unfinished = (preparing is not None and type(meta) is dict and meta.get('public_key') == ''
                        and meta.get('started') == 0 and meta.get('start_body') is None
                        and preparing['state'] == account['state'] == 'pending' and account['local_enabled'] == 0
                        and preparing['install_id'] == account['install_id'] and preparing['seal_flow_id'] == flow
                        and preparing['candidate_key_id'] == account['key_id']
                        and preparing['expected_generation'] == account['generation'] == 1
                        and preparing['expected_owner_generation'] == account['owner_generation'] == 1)
                except (ValueError, TypeError):
                    raise StoreHeld('schema_corrupt') from None
                if not unfinished:
                    raise StoreHeld('seal_missing')

    def open_store(self, *, create: bool = False) -> MailStore | None:
        """The shared store, or None only when it was never created (and
        ``create`` is False). Every other unhealthy state raises StoreHeld."""
        with self._lock:
            try:
                marker = self._marker()
                present = self.paths.store.exists()
                if marker == 'ready' and not present:
                    self._store = None
                    raise StoreHeld('store_missing')
                if marker is None and present:
                    raise StoreHeld('marker_missing')
                if not present:  # no marker or creating: no authority has existed
                    if not create:
                        return None
                    self._write_marker('creating')
                    self._store = GuardedMailStore(self.paths.store, creating=True,
                                                   config=self.store_config, clock=self.clock)
                    self._write_marker('ready')
                    return self._store
                if self._store is not None and marker == 'ready':
                    self._check_seal(self._store)
                    return self._store
                store = GuardedMailStore(self.paths.store, config=self.store_config, clock=self.clock)
                if marker == 'creating':
                    self._require_pristine(store)
                    self._check_seal(store)
                    self._write_marker('ready')
                self._check_seal(store)
                self._store = store
                return store
            except StoreHeld:
                raise
            except Exception as exc:
                held = self._classify(exc)
                _log(f'[agent-mail] store held: {held.reason}', flush=True)
                raise held from None

    @staticmethod
    def _require_pristine(store: MailStore) -> None:
        with store._read() as db:
            if db.execute('SELECT 1 FROM registrations LIMIT 1').fetchone():
                raise StoreHeld('marker_inconsistent')
            # Never finalize a crashed creation marker over pull authority or
            # custody. Even an inert fence/receipt is durable state to preserve.
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) GLOB 'mail_pull_*'").fetchall()
            for row in tables:
                if row[0] != 'mail_pull_version' and db.execute('SELECT 1 FROM "' + row[0].replace('"', '""') + '" LIMIT 1').fetchone():
                    raise StoreHeld('marker_inconsistent')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_enrollment_flow'").fetchone() \
                    and db.execute('SELECT 1 FROM mail_enrollment_flow LIMIT 1').fetchone():
                raise StoreHeld('marker_inconsistent')

    def require_store(self) -> MailStore:
        """Ingress/outbound entry: a missing store is refused, never created."""
        store = self.open_store()
        if store is None:
            raise StoreHeld('not_enrolled')
        return store

    def health(self) -> dict[str, str]:
        """Never raises, never creates. state: not_configured | ok | held."""
        try:
            store = self.open_store()
        except StoreHeld as exc:
            return {'state': 'held', 'reason': exc.reason}
        except Exception as exc:
            _log(f'[agent-mail] store health failed: {type(exc).__name__}', flush=True)
            return {'state': 'held', 'reason': 'store_unavailable'}
        return {'state': 'not_configured', 'reason': 'not_enrolled'} if store is None \
            else {'state': 'ok', 'reason': ''}

    # --- composition ------------------------------------------------------
    def _deployment(self) -> tuple[str, str, str]:
        values = tuple(self.config.get(k) for k in (INSTALL_KEY, ROOT_KEY, ORIGIN_KEY))
        if not all(isinstance(v, str) and v for v in values):
            raise StoreHeld('deployment_unconfigured')
        return values  # type: ignore[return-value]

    def seal(self) -> IdentitySeal:
        return self._seal

    def bootstrap(self, *, authorize: Callable[[], bool]) -> MailStore:
        """Human-only first store/additive upgrade; no identity, pin or network.

        A ready/missing DB, unmarked DB, partial extension or lost committed seal
        stays held. Repeating this action only validates existing pull objects.
        """
        IdentitySeal._authorize(authorize)
        with self._lock:
            try:
                store = self.open_store(create=True)
                assert store is not None
                initialize_pull(store)
                self._check_seal(store)
                return store
            except Exception as exc:
                held = self._classify(exc)
                _log(f'[agent-mail] bootstrap held: {held.reason}', flush=True)
                raise held from None

    def require_pull_store(self) -> MailStore:
        """Runtime path: exact checked schema; never initialize or repair."""
        with self._lock:
            try:
                store = self.require_store()
                validate_pull(store)
                return store
            except Exception as exc:
                held = self._classify(exc)
                _log(f'[agent-mail] pull store held: {held.reason}', flush=True)
                raise held from None

    def reviewed_service(self) -> tuple[ServiceKeyCandidate, str]:
        """Trusted internal review seam, never a candidate supplied by HTTP.

        UI/manifest wiring supplies the existing root/floor/human review policy;
        no fallback to cached account keys or automatic pin acceptance exists.
        """
        self.require_pull_store()
        if self._reviewed_service is None:
            raise EnrollmentRefused('service_key_changed')
        result = self._reviewed_service()
        if result[0].origin != self.config.get(ORIGIN_KEY):
            raise EnrollmentRefused('service_key_changed')
        return result

    def _pull_origin(self) -> str:
        origin = self.config.get(ORIGIN_KEY)
        if not isinstance(origin, str) or not origin:
            raise StoreHeld('deployment_unconfigured')
        return origin

    def _pull_install(self, store: MailStore) -> str:
        with store._read() as db:
            rows = db.execute('SELECT install_id,service_origin FROM mail_pull_accounts').fetchall()
        if not rows:
            raise StoreHeld('not_enrolled')
        if len(rows) != 1 or rows[0]['service_origin'] != self._pull_origin():
            raise StoreHeld('schema_corrupt')
        return rows[0]['install_id']

    def _http(self) -> SignupTransport:
        return self._pull_transport if self._pull_transport is not None else HttpsTransport(origin=self._pull_origin())

    def signup(self, *, authorize: Callable[[], bool]) -> Signup:
        return Signup(self.require_pull_store(), self.seal(), self._http(),
                      reviewed_service=self.reviewed_service, authorize=authorize,
                      clock_ms=lambda: int(self.clock() * 1000))

    def owner_change(self, *, authorize: Callable[[], bool]) -> OwnerChange:
        store = self.require_pull_store()
        return OwnerChange(store, self.seal(), self._http(), install_id=self._pull_install(store),
                           reviewed_service=self.reviewed_service, authorize=authorize,
                           clock_ms=lambda: int(self.clock() * 1000))

    def service_client(self) -> ServiceClient:
        store = self.require_pull_store()
        return ServiceClient(store, self.seal(), transport=self._http(), service_origin=self._pull_origin(),
                             install_id=self._pull_install(store), reviewed_service=self.reviewed_service,
                             clock=self.clock)

    def pull_receiver(self, *, generation: int, owner_generation: int, epoch: int) -> PullReceiver:
        """Scope comes from unit 18's authenticated, durable hello, not config."""
        store = self.require_pull_store()
        return PullReceiver(store, install_id=self._pull_install(store), generation=generation,
                            owner_generation=owner_generation, epoch=epoch, reviewed_service=self.reviewed_service)

    def pull_client(self) -> PullClient:
        """One cached inert client; unit 24 alone owns start/stop and threads."""
        with self._lock:
            store = self.require_pull_store()
            install = self._pull_install(store)
            if self._pull_client is None:
                self._pull_client = PullClient(store, self.seal(), self._http(), install_id=install,
                                               reviewed_service=self.reviewed_service)
                self._pull_client_origin = self._pull_origin()
            elif (self._pull_client.store is not store or self._pull_client.install_id != install
                  or self._pull_client_origin != self._pull_origin()):
                # Never replace a potentially running client behind its owner.
                raise StoreHeld('client_recomposition_required')
            return self._pull_client

    def enrollment(self) -> Enrollment:
        store = self.require_store()
        install, root, origin = self._deployment()
        adapter, fences = self.remote, None
        if ceremony_enabled(self.config):
            # Default off. Flow tokens exist only on this path, so every ceremony flow can be cancelled.
            fences = LifecycleFences(store, enabled=True)
            adapter = select_ceremony(self.config, store=store, seal=self.seal(), fences=fences,
                                      transport=self._ceremony_transport or HttpsTransport(),
                                      root_public_key=root, service_origin=origin,
                                      device=self._ceremony_device or keystore_identity,
                                      clock=self.clock) or self.remote
        return Enrollment(store, self.seal(), adapter=adapter, material=unavailable_material,
                          prerequisites=self.prerequisites, install_id=install, root_public_key=root,
                          service_origin=origin, fences=fences,
                          enabled=self.config.get('agent_mail_enrollment_enabled') is True)

    def outbound(self) -> Outbound:
        """The question-channel adapter's factory. Read/compose only: it never
        notifies, and sending stays gated by every existing flag and probe."""
        with self._lock:
            flags = (self.config.get('agent_mail_enrollment_enabled') is True,
                     self.config.get('agent_mail_outbound_enabled') is True)
            store = self.require_store()
            if self._outbound is not None and self._outbound[0] == flags \
                    and self._outbound[1].store is store:
                return self._outbound[1]
            built = Outbound(self.enrollment(), transport=self.transport, enabled=flags[1], key=self.key)
            self._outbound = (flags, built)
            return built


_factory: MailFactory | None = None
_factory_lock = threading.Lock()


def get() -> MailFactory:
    """Process singleton for the server. Construction touches nothing on disk."""
    global _factory
    with _factory_lock:
        if _factory is None:
            _factory = MailFactory()
        return _factory


def reset_for_tests(factory: MailFactory | None = None) -> None:
    global _factory
    with _factory_lock:
        _factory = factory
