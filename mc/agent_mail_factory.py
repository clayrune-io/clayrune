"""One shared, checked agent-mail store outside the repository (MC-1166 unit 7d-1).

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
from mc.agent_mail_enrollment import Enrollment, Prerequisites
from mc.agent_mail_enrollment_keys import IdentitySeal
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_remote import UnavailableRemote, UnavailableTransport, unavailable_material
from mc.agent_mail_store import DB_FILENAME, MailStore, MailStoreConfig, MailStoreCorrupt, MailStoreError
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
                 prerequisites: Callable[[], Prerequisites] | None = None):
        self.paths = MailPaths(Path(root) if root is not None else default_root())
        self.clock, self.key, self._config, self.store_config = clock, key, config, store_config
        self.remote = remote if remote is not None else UnavailableRemote()
        self.transport = transport if transport is not None else UnavailableTransport()
        self.prerequisites = prerequisites or Prerequisites
        self._lock = threading.RLock()
        self._store: MailStore | None = None
        self._outbound: tuple[tuple[bool, bool], Outbound] | None = None

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
        if isinstance(exc, MailStoreCorrupt):
            return StoreHeld('store_corrupt')
        if isinstance(exc, MailStoreError) and 'newer than this code' in str(exc):
            return StoreHeld('store_version')
        return StoreHeld('store_unavailable')

    def _check_seal(self, store: MailStore) -> None:
        """An enrolled flow whose sealed identity is gone stays held: no new key."""
        with store._read() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_enrollment_flow'").fetchone():
                return
            row = db.execute('SELECT flow,public_key,stage FROM mail_enrollment_flow WHERE slot=1').fetchone()
        if row and row['public_key'] and row['stage'] in ('pending', 'delivery_pending', 'active'):
            if not (self.paths.identity / (row['flow'] + '.sealed')).is_file():
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
        except Exception:
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
        return IdentitySeal(self.paths.identity, key=self.key)

    def enrollment(self) -> Enrollment:
        store = self.require_store()
        install, root, origin = self._deployment()
        return Enrollment(store, self.seal(), adapter=self.remote, material=unavailable_material,
                          prerequisites=self.prerequisites, install_id=install, root_public_key=root,
                          service_origin=origin,
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
