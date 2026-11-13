#!/usr/bin/env python3
"""Human-run provisioning of ONE agent-mail install without the signup ceremony (MC-1166 U2).

Creates the local mail store, the sealed Ed25519 install identity, the pin and the
local account rows already in the ``active`` state, and writes the matching Worker
D1 SQL to a private file under ``~/.clayrune/``. It never talks to Cloudflare, sends
no mail, sets no config and enables nothing: the operator applies the SQL and
edits the config keys this prints. The owner address is an argument; it is
written only to the local store and to that SQL file, never to the repo.

Refuses when any pull account, flow or generation already exists, or when any
sealed identity file is present. The vault must be unlocked: the script asks for
the vault passphrase itself (a separate process cannot borrow the server's key).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
for entry in (str(REPO), str(TOOLS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agent_mail_key_files import key_path_in_checkout, write_new_private_file  # noqa: E402
from mc.agent_mail_enrollment_keys import EnrollmentRefused, SealReference  # noqa: E402
from mc.agent_mail_factory import INSTALL_KEY, ORIGIN_KEY, ROOT_KEY, MailFactory, StoreHeld  # noqa: E402
from mc.agent_mail_service_manifest import ServiceKeyCandidate  # noqa: E402
from mc.agent_mail_signup import ID, ORIGIN, TTL, Signup, _digest, _json, _mailbox  # noqa: E402
from mc.secrets_store import clayrune_home  # noqa: E402

DEFAULT_DOMAIN = 'clayrune.io'
CONFIRM_WORD = 'PROVISION'
DOMAIN = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
ROOT_PUBLIC = re.compile(r'[A-Za-z0-9_-]{43}')
HEX64 = re.compile(r'[0-9a-f]{64}')
FLAGS = ('agent_mail_enrollment_enabled', 'agent_mail_outbound_enabled',
         'agent_mail_ingress_enabled', 'agent_mail_supervisor_enabled')


def _need(ok: object, reason: str) -> None:
    if not ok:
        raise EnrollmentRefused(reason)


def _once(authorize: Callable[[], bool]) -> Callable[[], bool]:
    """One human decision per run: bootstrap and key generation both ask for it."""
    box: list[bool] = []

    def decided() -> bool:
        if not box:
            box.append(authorize() is True)
        return box[0]
    return decided


def _quote(value: str) -> str:
    _need(isinstance(value, str) and value.isprintable(), 'invalid_request')
    return "'" + value.replace("'", "''") + "'"


def _rows_exist(store: Any) -> bool:
    """Any trace of a pull account, generation or flow; absent tables count as none."""
    with store._read() as db:
        for table in ('mail_pull_accounts', 'mail_pull_generations', 'mail_pull_flows'):
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone() \
                    and db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                return True
    return False


def _offline(*_args: Any, **_kwargs: Any) -> Any:
    raise EnrollmentRefused('connection_lost')


def d1_sql(*, install_id: str, flow_id: str, key_id: str, public_key: str, fence_token: str,
           owner: str, address_local: str, consent_version: str, now_ms: int) -> str:
    """The four Worker rows, exactly the column sets signup-http.js / pull-activate-http.js write."""
    owner_hash = hashlib.sha256(owner.encode('ascii')).hexdigest()
    fence_hash = hashlib.sha256(fence_token.encode('ascii')).hexdigest()
    q = _quote
    return '\n'.join([
        '-- Agent-mail Worker D1 provisioning (MC-1166 U2). Review, then apply once:',
        '--   wrangler d1 execute <DATABASE> --remote --file <this file>',
        '-- Rollback (reverse order):',
        f'--   DELETE FROM pull_owner_slots WHERE install_id={q(install_id)};',
        f'--   DELETE FROM pull_addresses WHERE install_id={q(install_id)};',
        f'--   DELETE FROM pull_generations WHERE install_id={q(install_id)};',
        f'--   DELETE FROM pull_installs WHERE install_id={q(install_id)};',
        'INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,'
        'address_local,key_id,public_key,consent_version,active_since_ms,created_ms,updated_ms) VALUES('
        f"{q(install_id)},1,1,'active',{q(owner_hash)},{q(owner)},{q(address_local)},{q(key_id)},"
        f'{q(public_key)},{q(consent_version)},{now_ms},{now_ms},{now_ms});',
        'INSERT INTO pull_generations(install_id,generation,owner_generation,owner_hash,key_id,public_key,'
        'fence_hash,flow_id,state,created_ms,fenced_ms) VALUES('
        f"{q(install_id)},1,1,{q(owner_hash)},{q(key_id)},{q(public_key)},{q(fence_hash)},{q(flow_id)},"
        f"'current',{now_ms},NULL);",
        'INSERT INTO pull_addresses(address_local,install_id,state,until_ms,created_ms) VALUES('
        f"{q(address_local)},{q(install_id)},'assigned',NULL,{now_ms});",
        'INSERT INTO pull_owner_slots(owner_hash,install_id,role,flow_id,until_ms) VALUES('
        f"{q(owner_hash)},{q(install_id)},'active',{q(flow_id)},NULL);",
        ''])


def provision(factory: MailFactory, *, owner: str, authorize: Callable[[], bool],
              mail_domain: str = DEFAULT_DOMAIN, consent_version: str = 'v1',
              sql_path: Path | None = None, token: Callable[[int], str] = secrets.token_hex) -> dict[str, Any]:
    """Provision one active install. Raises EnrollmentRefused/StoreHeld with closed reason codes."""
    canonical = _mailbox(owner)
    config = factory.config
    origin, root_public = config.get(ORIGIN_KEY), config.get(ROOT_KEY)
    _need(isinstance(origin, str) and ORIGIN.fullmatch(origin) and len(origin) <= 261, 'service_origin_invalid')
    _need(isinstance(root_public, str) and ROOT_PUBLIC.fullmatch(root_public), 'invalid_request')
    _need(DOMAIN.fullmatch(mail_domain) and ID.fullmatch(consent_version), 'invalid_request')
    _need(factory.key() is not None, 'vault_locked')
    install_id, flow_id, key_id = token(16), token(16), token(16)
    address_local = 'm' + token(16)[:29]
    fence_token, op_id, activation_op = token(32), token(16), token(16)
    path = sql_path if sql_path is not None else \
        clayrune_home() / 'agent-mail-provision' / (install_id + '.d1.sql')
    path = Path(path).expanduser()
    _need(not key_path_in_checkout(path.parent), 'output_in_checkout')
    _need(not path.exists(), 'output_exists')
    # Refuse before asking the human anything.
    existing = factory.open_store()
    _need(existing is None or not _rows_exist(existing), 'account_exists')
    _need(not any(factory.paths.identity.glob('*.sealed')), 'seal_exists')

    confirm = _once(authorize)
    store = factory.bootstrap(authorize=confirm)
    _need(not _rows_exist(store), 'account_exists')
    pin, digest = factory.reviewed_service()  # Network and manifest-floor CAS happen here, before any key exists.
    _need(isinstance(pin, ServiceKeyCandidate) and isinstance(digest, str) and HEX64.fullmatch(digest)
          and int(factory.clock() * 1000) < pin.expires_at_ms, 'service_key_changed')
    now = int(factory.clock() * 1000)

    ref = SealReference(flow_id, install_id, 1, key_id)
    public_key = factory.seal().generate_pull(ref, authorize=confirm, expected_public_key=None)
    meta: dict[str, Any] = {'v': 1, 'op_id': op_id, 'public_key': public_key, 'start_body': None, 'started': 1,
                            'notice_state': None, 'fence_token': fence_token}
    signup = Signup(store, factory.seal(), _offline, reviewed_service=factory.reviewed_service, authorize=confirm)
    body = signup._body({'meta': meta, 'flow_id': flow_id, 'install_id': install_id, 'seal_flow_id': flow_id,
                         'candidate_key_id': key_id, 'owner_address': canonical,
                         'consent_version': consent_version, 'manifest_sequence': pin.sequence,
                         'manifest_digest': digest}, public_key)
    meta['start_body'] = _json(body).decode('ascii')
    meta.update(activation_op=activation_op,
                activation_digest=_digest(_json({'provisioned_out_of_band': install_id})),
                activation_attempts=1, activation_authorized_ms=now, activation_active_since_ms=now)

    sql = d1_sql(install_id=install_id, flow_id=flow_id, key_id=key_id, public_key=public_key,
                 fence_token=fence_token, owner=canonical, address_local=address_local,
                 consent_version=consent_version, now_ms=now)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _need(not key_path_in_checkout(path.parent), 'output_in_checkout')
    write_new_private_file(path, sql.encode('ascii'))
    address = f'{address_local}@{mail_domain}'
    try:
        # Column order is Signup.begin's and Activation's; the flow is written as the completed signup
        # an activation would have left, so every Signup/Activation reader accepts it.
        with store._tx() as db:
            _need(not db.execute('SELECT 1 FROM mail_pull_accounts LIMIT 1').fetchone(), 'account_exists')
            db.execute('''INSERT INTO mail_pull_accounts VALUES(?,1,1,?,?,?,?,?,'active',1,?,1,'',?)''',
                       (install_id, origin, address, canonical, key_id, flow_id, consent_version, now))
            db.execute("INSERT INTO mail_pull_generations VALUES(?,1,1,?,?,?,?,?,?,'current',?,NULL)",
                       (install_id, key_id, flow_id, pin.sequence, digest, pin.key_id, pin.public_key, now))
            db.execute("INSERT INTO mail_pull_flows VALUES(?,?,'signup',1,1,?,?,?,?,1,'completed',?,'',?)",
                       (flow_id, install_id, key_id, flow_id, _digest(meta['start_body'].encode('ascii')),
                        now, now + TTL, _json(meta).decode('ascii')))
            db.execute('INSERT INTO registrations VALUES(?,1,1,?)', (install_id, now / 1000))
            signup._row(db)  # Same reader status/recovery use; a refusal rolls the whole write back.
    except BaseException:
        path.unlink(missing_ok=True)  # Our own, unapplied file for an install that was not recorded.
        raise
    # A committed account can only fail here if the composition is wrong; say so rather than print success.
    client = factory.pull_client()
    with store._read() as db:
        client._account(db)
    return {'install_id': install_id, 'address': address, 'sql_path': str(path),
            'config': {INSTALL_KEY: install_id, ROOT_KEY: root_public, ORIGIN_KEY: origin}}


def _vault_key(passphrase: Callable[[str], str]) -> Callable[[], bytes | None]:
    from mc import secrets_store as vault
    if vault.peek_passphrase_key() is None:
        _need(vault.wrapped_key_path().is_file(), 'vault_unconfigured')
        try:
            vault.unlock_with_passphrase(passphrase('Vault passphrase: '))
        except vault.SecretsError:
            raise EnrollmentRefused('vault_locked') from None
    return vault.peek_passphrase_key


def build_factory(root: Path | None, config: dict[str, Any], key: Callable[[], bytes | None],
                  build: Callable[[MailFactory], Callable[[], tuple[ServiceKeyCandidate, str]]] | None = None,
                  clock: Callable[[], float] = time.time) -> MailFactory:
    """U1's build(factory) needs the factory, the factory needs its callable: tie them lazily."""
    if build is None:
        from mc.agent_mail_reviewed_service import build as default_build
        builder = default_build
    else:
        builder = build
    box: list[Callable[[], tuple[ServiceKeyCandidate, str]]] = []
    factory = MailFactory(root, clock=clock, key=key, config=config, reviewed_service=lambda: box[0]())
    box.append(builder(factory))
    return factory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    parser.add_argument('--owner', required=True, metavar='ADDRESS', help="the verified owner's mailbox")
    parser.add_argument('--service-origin', required=True, metavar='URL', help='canonical https origin of the mail service')
    parser.add_argument('--root-public-key', required=True, metavar='KEY', help='offline root PUBLIC key (base64url)')
    parser.add_argument('--mail-domain', default=DEFAULT_DOMAIN, metavar='DOMAIN')
    parser.add_argument('--consent-version', default='v1', metavar='ID')
    parser.add_argument('--sql-out', default=None, metavar='PATH', help='D1 SQL file (default: under ~/.clayrune/)')
    return parser


def run(argv: Sequence[str] | None = None, *, stdout: Any = None, stderr: Any = None,
        prompt: Callable[[str], str] = input, passphrase: Callable[[str], str] = getpass.getpass,
        key: Callable[[], bytes | None] | None = None, root: Path | None = None,
        clock: Callable[[], float] = time.time,
        build: Callable[[MailFactory], Callable[[], tuple[ServiceKeyCandidate, str]]] | None = None) -> int:
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        key = key if key is not None else _vault_key(passphrase)
        factory = build_factory(root, {ORIGIN_KEY: args.service_origin, ROOT_KEY: args.root_public_key}, key, build, clock)
        stdout.write(f'Provision ONE active agent-mail install\n  owner:   {args.owner}\n'
                     f'  service: {args.service_origin}\n  domain:  {args.mail_domain}\n'
                     'This creates the local store, a sealed key and an active account. Nothing is sent.\n')
        result = provision(factory, owner=args.owner, mail_domain=args.mail_domain,
                           consent_version=args.consent_version,
                           sql_path=Path(args.sql_out) if args.sql_out else None,
                           authorize=lambda: prompt(f'Type {CONFIRM_WORD} to continue: ').strip() == CONFIRM_WORD)
    except (EnrollmentRefused, StoreHeld, ImportError, OSError, ValueError) as exc:
        # Closed reason codes only; never an owner, key or path from an exception.
        reason = exc.reason if isinstance(exc, StoreHeld) else \
            'reviewed_service_missing' if isinstance(exc, ImportError) else \
            str(exc) if isinstance(exc, EnrollmentRefused) else type(exc).__name__
        stderr.write(f'error: {reason}\n')
        return 2
    stdout.write(f"\nLocal install ready: {result['address']}\n"
                 f"Worker D1 SQL (review, then apply once): {result['sql_path']}\n"
                 '  wrangler d1 execute <DATABASE> --remote --file <that file>\n'
                 'Set these config keys, then restart the server:\n')
    for name, value in result['config'].items():
        stdout.write(f'  {name} = {json.dumps(value)}\n')
    stdout.write('Flags to enable when the SQL is applied (this tool sets none): ' + ', '.join(FLAGS) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(run())
