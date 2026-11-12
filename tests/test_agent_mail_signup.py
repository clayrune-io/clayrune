"""Signup crash recovery and independent v2 crypto/real Worker interoperability.

Only synthetic keys and temp stores. The full frozen section-9 schema is applied
by the fixture, standing in for unit 15's authorized initializer, NOT by Signup.
Node executes actual SignupHTTP/VerificationMail SQL against local node:sqlite;
there is no socket, provider, live vault, Cloudflare or browser-engine exercise.
"""
from contextlib import contextmanager
from dataclasses import replace
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_factory import GuardedMailStore
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signup import Signup, START, STATUS, TTL

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'
OWNER = 'Private.Owner+tag@EXAMPLE.com'
CANONICAL_OWNER = 'Private.Owner+tag@example.com'
MASTER = b'M' * 32
ADDRESS = 'm' + 'a' * 29 + '@clayrune.example'


def encoded(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def decoded(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def wire(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii')


class Crash(BaseException):
    """A lost process, not a recoverable adapter error."""


class Harness:
    def __init__(self, folder):
        self.folder, self.now, self.master = folder, NOW, MASTER
        self.service_key = Ed25519PrivateKey.from_private_bytes(b'V' * 32)
        public = self.service_key.public_key().public_bytes_raw()
        self.pin = ServiceKeyCandidate(ORIGIN, 1, 'service-key', encoded(public), digest(public), (), NOW + 10 * TTL)
        self.manifest_digest = 'd' * 64
        self.authorized = True
        self.calls, self.operations = [], {}
        self.remote_state = 'pending'
        self.mutate = lambda value, headers, status: (value, headers, status)
        self.after = lambda: None
        self.store = GuardedMailStore(folder / 'agent_mail.sqlite3', creating=True)
        contract = (ROOT / 'docs/AGENT_MAIL_PULL_CONTRACT.md').read_text(encoding='utf-8')
        section = contract.split('## 9. NEW local schema:', 1)[1]
        sql = section.split('```sql\n', 1)[1].split('```', 1)[0]
        with self.store._tx() as db:
            # executescript would implicitly commit; execute each frozen statement
            # individually to retain the explicit fixture transaction.
            for statement in sql.split(';'):
                if statement.strip():
                    db.execute(statement)
        self.seal = IdentitySeal(folder / 'identity', key=lambda: self.master)

    def client(self, transport=None):
        return Signup(self.store, self.seal, transport or self.transport,
                      reviewed_service=lambda: (self.pin, self.manifest_digest),
                      authorize=lambda: self.authorized, clock_ms=lambda: self.now)

    def restart(self, transport=None):
        self.store = GuardedMailStore(self.folder / 'agent_mail.sqlite3')
        self.seal = IdentitySeal(self.folder / 'identity', key=lambda: self.master)
        return self.client(transport)

    def row(self, table='mail_pull_flows'):
        with self.store._read() as db:
            return dict(db.execute('SELECT * FROM ' + table).fetchone())

    def count(self, table):
        with self.store._read() as db:
            return db.execute('SELECT count(*) FROM ' + table).fetchone()[0]

    def response(self, value, request_headers, path, status=200, *, raw=None, overrides=None):
        raw = wire(value) if raw is None else raw
        fields = {'version': '2', 'direction': 'pull-to-install', 'key-id': self.pin.key_id,
                  'install-id': request_headers['x-mail-install-id'], 'generation': '1', 'owner-generation': '1',
                  'timestamp': str(self.now), 'nonce': request_headers['x-mail-nonce'], 'digest': digest(raw)}
        fields.update(overrides or {})
        transcript = wire(['clayrune-agent-mail-pull-http', '1', fields['direction'], ORIGIN, 'POST', path,
                           fields['key-id'], fields['install-id'], fields['generation'], fields['owner-generation'],
                           fields['timestamp'], fields['nonce'], str(status), fields['digest']])
        headers = {'x-mail-' + k: v for k, v in fields.items()}
        headers['x-mail-signature'] = encoded(self.service_key.sign(transcript))
        headers['content-type'] = 'application/json'
        return status, headers, raw

    def transport(self, method, path, headers, raw):
        assert method == 'POST' and path in (START, STATUS)
        assert headers['x-mail-version'] == '2'
        assert headers['x-mail-direction'] == 'install-to-pull'
        assert headers['x-mail-generation'] == headers['x-mail-owner-generation'] == '1'
        assert headers['x-mail-digest'] == digest(raw)
        value = json.loads(raw)
        # Literal independent transcript: never imports the implementation's
        # serializer/signer/verifier to judge its own outputs.
        transcript = wire(['clayrune-agent-mail-pull-http', '1', 'install-to-pull', ORIGIN, 'POST', path,
                           headers['x-mail-key-id'], headers['x-mail-install-id'], '1', '1',
                           headers['x-mail-timestamp'], headers['x-mail-nonce'], digest(raw)])
        if path == START:
            public = value['public_key']
            op = value['op_id']
            if op not in self.operations:
                self.operations[op] = (raw, self.now + TTL, headers['x-mail-install-id'])
            assert self.operations[op][0] == raw
            self.start = value
        else:
            public = self.start['public_key']
            assert set(value) == {'flow_id', 'flow_secret'}
            assert value['flow_id'] == self.start['flow_id']
            assert digest(value['flow_secret'].encode('ascii')) == self.start['flow_secret_hash']
            op = self.start['op_id']
        Ed25519PublicKey.from_public_bytes(decoded(public)).verify(decoded(headers['x-mail-signature']), transcript)
        self.calls.append((path, dict(headers), raw, transcript))
        result = {'v': 1, 'code': 'ok', 'request_id': op if path == START else headers['x-mail-nonce'],
                  'flow_id': self.start['flow_id'], 'install_id': headers['x-mail-install-id'],
                  'generation': 1, 'owner_generation': 1, 'address': ADDRESS,
                  'state': 'pending' if path == START else self.remote_state,
                  'expires_ms': self.operations[op][1], 'verification_state': 'queued'}
        result, overrides, status = self.mutate(result, {}, 200)
        response = self.response(result, headers, path, status, overrides=overrides)
        self.after()
        return response


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_start_exact_bytes_scope_fence_and_no_enable(h):
    assert h.client().status()['state'] == 'not_enrolled'
    assert h.calls == [] and not h.seal.folder.exists()
    result = h.client().start(OWNER, 'v1')
    assert result['state'] == 'pending', result
    assert result['enabled'] is False and result['address'] == ADDRESS
    body = json.loads(h.calls[0][2])
    assert set(body) == {'op_id', 'flow_id', 'public_key', 'key_id', 'owner', 'flow_secret_hash',
                         'fence_hash', 'consent_version', 'manifest_sequence', 'manifest_digest'}
    assert body['owner'] == CANONICAL_OWNER
    assert all(len(body[k]) == 32 for k in ('op_id', 'flow_id', 'key_id'))
    assert len({body['op_id'], body['flow_id'], body['key_id'], result['install_id']}) == 4
    assert body['manifest_sequence'] == 1 and body['manifest_digest'] == h.manifest_digest
    meta = json.loads(h.row()['result_json'])
    assert body['fence_hash'] == digest(meta['fence_token'].encode('ascii'))
    assert h.count('mail_pull_fences') == 0  # No cancellation intent at signup.
    account = h.row('mail_pull_accounts')
    assert account['state'] == 'pending' and account['local_enabled'] == 0
    assert OWNER not in str(result) and CANONICAL_OWNER not in str(result)
    assert body['public_key'] not in str(result) and meta['fence_token'] not in str(result)
    assert h.row('mail_pull_status')['response_digest']


def test_crash_after_remote_commit_replays_same_start_operation_and_flow(h):
    h.after = lambda: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        h.client().start(OWNER, 'v1')
    first = h.calls[0]
    assert h.count('mail_pull_flows') == h.count('mail_pull_accounts') == 1
    assert json.loads(h.row()['result_json'])['started'] == 0
    h.after = lambda: None
    h.now += 1000
    result = h.restart().resume()
    assert result['state'] == 'pending', result
    assert len(h.operations) == 1
    assert h.calls[1][0] == START and h.calls[1][2] == first[2]
    assert h.calls[1][1]['x-mail-nonce'] != first[1]['x-mail-nonce']
    assert h.calls[1][1]['x-mail-timestamp'] != first[1]['x-mail-timestamp']
    assert len(list(h.seal.folder.glob('*.sealed'))) == 1
    assert h.restart().resume()['state'] == 'pending'
    assert h.calls[-1][0] == STATUS


def test_status_after_restart_verifies_and_does_not_enable(h):
    result = h.client().start(OWNER, 'v1', op_id='b' * 32)
    h.remote_state = 'verified'
    h.authorized = False  # Durable authorized status needs no new human action.
    assert h.restart().resume()['state'] == 'verified'
    assert h.calls[-1][0] == STATUS
    assert h.client().status()['enabled'] is False
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    assert h.row('mail_pull_accounts')['state'] == 'verified'
    assert result['flow_id'] == h.row()['flow_id']


@pytest.mark.parametrize('phase', ['before_start_reply', 'pending', 'verified'])
def test_expired_flow_stays_expired_and_cannot_create_another(h, phase):
    if phase == 'before_start_reply':
        h.after = lambda: (_ for _ in ()).throw(Crash())
        with pytest.raises(Crash):
            h.client().start(OWNER, 'v1')
        h.after = lambda: None
    else:
        assert h.client().start(OWNER, 'v1')['state'] == 'pending'
        if phase == 'verified':
            h.remote_state = 'verified'
            assert h.client().resume()['state'] == 'verified'
    calls = len(h.calls)
    h.now += TTL
    assert h.restart().status()['state'] == 'expired'
    assert h.client().resume()['state'] == 'expired'
    assert h.restart().resume()['state'] == 'expired'
    assert h.client().start(OWNER, 'v1')['reason'] == 'flow_expired'
    assert len(h.calls) == calls and h.count('mail_pull_flows') == 1


def test_locked_vault_never_generates_and_unlock_resumes_authorized_intent(h):
    h.master = None
    result = h.client().start(OWNER, 'v1')
    assert result['reason'] == 'vault_locked'
    assert not h.seal.folder.exists() and not h.calls
    assert h.count('mail_pull_flows') == 1
    assert h.client().status()['reason'] == 'vault_locked'
    h.master = MASTER
    assert h.restart().resume()['state'] == 'pending'
    h.master = None
    assert h.restart().resume()['reason'] == 'vault_locked'
    assert h.client().status()['reason'] == 'vault_locked'
    assert len(h.calls) == 1


@pytest.mark.parametrize('damage,reason', [('missing', 'seal_missing'), ('corrupt', 'identity_unavailable_reenrollment_required'),
                                        ('version', 'version_unsupported')])
def test_key_damage_is_observable_never_regenerated(h, damage, reason):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    seal_path = next(h.seal.folder.glob('*.sealed'))
    if damage == 'missing':
        seal_path.unlink()
    elif damage == 'version':
        value = json.loads(seal_path.read_text())
        value['version'] = 2
        seal_path.write_text(json.dumps(value))
    else:
        seal_path.write_text('{damaged')
    assert h.restart().resume()['reason'] == reason
    assert h.client().start(OWNER, 'v1')['reason'] == reason
    assert len(h.calls) == 1
    if damage == 'missing':
        assert not seal_path.exists()


@pytest.mark.parametrize('change', ['owner', 'local_case', 'consent', 'operation'])
def test_owner_and_operation_binding_immutable_after_start(h, change):
    assert h.client().start(OWNER, 'v1', op_id='b' * 32)['state'] == 'pending'
    args = {'owner': OWNER, 'consent_version': 'v1', 'op_id': 'b' * 32}
    args[{'owner': 'owner', 'local_case': 'owner', 'consent': 'consent_version', 'operation': 'op_id'}[change]] = {
        'owner': 'different@example.com', 'local_case': 'private.Owner+tag@example.com',
        'consent': 'v2', 'operation': 'c' * 32}[change]
    assert h.restart().start(**args)['reason'] == 'operation_conflict'
    assert h.row('mail_pull_accounts')['owner_address'] == CANONICAL_OWNER
    assert len(h.calls) == 1
    assert h.client().start(CANONICAL_OWNER, 'v1', op_id='b' * 32)['state'] == 'pending'
    assert h.calls[-1][0] == STATUS


@pytest.mark.parametrize('authorized', [False, None, 1, 'true'])
def test_literal_human_gate_before_persistence_or_key_generation(h, authorized):
    h.authorized = authorized
    assert h.client().start(OWNER, 'v1')['reason'] == 'human_required'
    assert not h.calls and not h.seal.folder.exists() and h.count('mail_pull_flows') == 0
    assert h.client().resume()['state'] == 'not_enrolled'


@pytest.mark.parametrize('owner', ['A..B@example.com', 'a@localhost', 'a@bad-.example', 'a@exämple.com',
                                 '"a"@example.com', 'a@example.com\r\nBCC:x@y.com', 'a' * 65 + '@example.com'])
def test_invalid_owner_has_no_side_effects(h, owner):
    assert h.client().start(owner, 'v1')['reason'] == 'invalid_request'
    assert h.count('mail_pull_flows') == 0 and not h.calls


@pytest.mark.parametrize('kind', ['signature', 'nonce', 'install', 'direction', 'version', 'generation',
                                 'owner_generation', 'digest', 'timestamp', 'http_status', 'service_key', 'path'])
def test_bad_response_signature_and_scope_refused(h, kind):
    original = h.transport

    def attack(method, path, headers, body):
        status, response_headers, raw = original(method, path, headers, body)
        if kind == 'http_status':
            return 409, response_headers, raw
        if kind == 'path':
            return h.response(json.loads(raw), headers, STATUS)
        field, value = {'signature': ('signature', 'a' * 86), 'nonce': ('nonce', '0' * 32),
                        'install': ('install-id', '0' * 32), 'direction': ('direction', 'install-to-pull'),
                        'version': ('version', '1'), 'generation': ('generation', '2'),
                        'owner_generation': ('owner-generation', '2'), 'digest': ('digest', '0' * 64),
                        'timestamp': ('timestamp', str(NOW - 300001)), 'service_key': ('key-id', 'foreign')}[kind]
        response_headers['x-mail-' + field] = value
        return status, response_headers, raw

    result = h.client(attack).start(OWNER, 'v1')
    assert result['reason'] == 'response_unverified', result
    assert json.loads(h.row()['result_json'])['started'] == 0
    assert h.row('mail_pull_accounts')['address'] == ''
    assert h.count('mail_pull_status') == 0


@pytest.mark.parametrize('field,value', [('flow_id', '0' * 32), ('install_id', '0' * 32), ('generation', True),
                                       ('owner_generation', 2), ('request_id', '0' * 32), ('code', 'accepted'),
                                       ('expires_ms', NOW + TTL + 1), ('address', 'alias@clayrune.example'),
                                       ('state', 'active'), ('verification_state', 'delivered')])
def test_even_signed_invalid_success_is_not_evidence(h, field, value):
    h.mutate = lambda body, headers, status: ({**body, field: value}, headers, status)
    assert h.client().start(OWNER, 'v1')['reason'] == 'response_unverified'
    assert h.count('mail_pull_status') == 0


@pytest.mark.parametrize('raw', [b'{"v":1,"v":1}', b'{"v":-0}', b'{"v":1.0}', b'{"v":NaN}',
                               b'{"v":1,"owner":"\\ud800"}', b'\xef\xbb\xbf{}', b'{"nested":{}}'])
def test_signed_malformed_json_refused(h, raw):
    def transport(method, path, headers, body):
        return h.response({}, headers, path, raw=raw)
    assert h.client(transport).start(OWNER, 'v1')['reason'] == 'response_unverified'


@pytest.mark.parametrize('status,code', [(409, 'owner_limit'), (409, 'nonce_replayed'),
                                       (429, 'verification_limit'), (503, 'storage_unavailable'),
                                       (410, 'flow_expired')])
def test_verified_errors_never_set_started_or_enable(h, status, code):
    def transport(method, path, headers, body):
        op = json.loads(body)['op_id']
        value = {'v': 1, 'code': code, 'request_id': op}
        if status in (429, 503):
            value['retry_after_ms'] = NOW + 60_000
        return h.response(value, headers, path, status)
    result = h.client(transport).start(OWNER, 'v1')
    assert result['reason'] == code
    assert result['enabled'] is False
    assert json.loads(h.row()['result_json'])['started'] == 0
    assert h.count('mail_pull_status') == 0


def test_transport_failure_is_private_observable_and_retryable(h, capsys):
    def offline(*args):
        raise RuntimeError(OWNER + ' seed=private-provider-diagnostic')
    assert h.client(offline).start(OWNER, 'v1')['reason'] == 'connection_lost'
    output = capsys.readouterr().out
    assert 'connection_lost' in output and OWNER not in output and 'private-provider' not in output
    assert h.restart().resume()['state'] == 'pending'


@pytest.mark.parametrize('mutation', ['address', 'expiry', 'regress'])
def test_status_cannot_change_assignment_extend_expiry_or_regress_verified(h, mutation):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    if mutation == 'regress':
        h.remote_state = 'verified'
        assert h.client().resume()['state'] == 'verified'
        h.remote_state = 'pending'
    field, value = {'address': ('address', 'm' + 'b' * 29 + '@clayrune.example'),
                    'expiry': ('expires_ms', NOW + TTL - 1), 'regress': ('state', 'pending')}[mutation]
    h.mutate = lambda body, headers, status: ({**body, field: value}, headers, status)
    assert h.restart().resume()['reason'] == 'response_unverified'
    assert h.row('mail_pull_accounts')['address'] == ADDRESS


def test_pin_change_never_repins_existing_operation(h):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    h.pin = replace(h.pin, key_id='new-key')
    assert h.restart().resume()['reason'] == 'service_key_changed'
    assert len(h.calls) == 1


def test_reviewed_manifest_floor_can_transact_on_same_store_without_deadlock(h):
    client = h.client()
    checked = []

    def review():
        with h.store._tx() as db:
            checked.append(db.execute('SELECT version FROM mail_pull_version').fetchone()[0])
        return h.pin, h.manifest_digest

    client.reviewed_service = review
    assert client.start(OWNER, 'v1')['state'] == 'pending'
    assert client.resume()['state'] == 'pending'
    assert len(checked) >= 4


def test_missing_extension_refused_without_creating_schema(tmp_path):
    store = GuardedMailStore(tmp_path / 'agent_mail.sqlite3', creating=True)
    client = Signup(store, IdentitySeal(tmp_path / 'identity'), lambda *args: pytest.fail(),
                    reviewed_service=lambda: pytest.fail(), authorize=lambda: True)
    assert client.status()['reason'] == 'schema_corrupt'
    with store._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'mail_pull_%'").fetchone()


def fail_commit(h, *, after_yield=None):
    original = h.store._tx

    @contextmanager
    def transaction():
        h.store._tx = original
        with original() as db:
            yield db
            if after_yield:
                after_yield()
            raise OSError('synthetic commit failure')

    h.store._tx = transaction


def test_key_preparation_rollback_reopens_same_orphan(h):
    # A locked attempt durably writes the human intent but generates no key.
    h.master = None
    assert h.client().start(OWNER, 'v1')['reason'] == 'vault_locked'
    h.master = MASTER
    fail_commit(h)
    assert h.client().resume()['reason'] == 'storage_unavailable'
    seal_path = next(h.seal.folder.glob('*.sealed'))
    orphan = seal_path.read_bytes()
    assert not h.calls
    assert h.restart().resume()['state'] == 'pending'
    assert seal_path.read_bytes() == orphan
    assert len(list(h.seal.folder.glob('*.sealed'))) == 1


def test_local_receipt_commit_loss_replays_original_remote_operation(h):
    h.after = lambda: fail_commit(h)
    assert h.client().start(OWNER, 'v1')['reason'] == 'storage_unavailable'
    assert h.count('mail_pull_status') == 0
    assert json.loads(h.row()['result_json'])['started'] == 0
    h.after = lambda: None
    assert h.restart().resume()['state'] == 'pending'
    assert len(h.operations) == 1 and h.calls[0][2] == h.calls[1][2]


def test_opaque_seal_no_plaintext_flow_secret_in_database(h):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    h.client().resume()
    secret = json.loads(h.calls[-1][2])['flow_secret']
    with h.store._read() as db:
        dump = '\n'.join(db.iterdump())
    assert secret not in dump
    assert secret not in str(h.client().status())


def test_crash_before_transport_keeps_prepared_body_and_identity(h):
    captured = []

    def before_send(method, path, headers, body):
        captured.append(body)
        raise Crash()

    with pytest.raises(Crash):
        h.client(before_send).start(OWNER, 'v1')
    assert not h.operations
    assert h.count('mail_pull_flows') == 1
    assert h.restart().resume()['state'] == 'pending'
    assert h.calls[0][2] == captured[0]


def test_intent_commit_failure_creates_no_seal_or_remote_flow(h):
    fail_commit(h)
    assert h.client().start(OWNER, 'v1')['reason'] == 'storage_unavailable'
    assert h.count('mail_pull_flows') == h.count('mail_pull_accounts') == 0
    assert not h.seal.folder.exists() and not h.calls
    assert h.restart().start(OWNER, 'v1')['state'] == 'pending'


@pytest.mark.parametrize('table', ['mail_pull_accounts', 'mail_pull_flows', 'mail_pull_generations'])
def test_missing_authority_rows_never_recreated(h, table):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    with h.store._tx() as db:
        db.execute('DELETE FROM ' + table)
    assert h.restart().resume()['reason'] == 'schema_corrupt'
    assert h.client().start(OWNER, 'v1')['reason'] == 'schema_corrupt'
    assert len(h.calls) == 1 and h.count(table) == 0


def test_concurrent_start_resumes_cannot_overwrite_committed_receipt(h):
    nested = []

    def interleave():
        h.after = lambda: None
        nested.append(h.client().resume())

    h.after = interleave
    assert h.client().start(OWNER, 'v1')['reason'] == 'stale_generation'
    assert nested[0]['state'] == 'pending'
    assert h.client().status()['state'] == 'pending'
    assert len(h.operations) == 1 and h.calls[0][2] == h.calls[1][2]


@pytest.mark.parametrize('mutation', ['disabled', 'consent_epoch', 'fenced', 'service_pin'])
def test_changed_authority_during_transport_refuses_delayed_success(h, mutation):
    def interleave():
        h.after = lambda: None
        if mutation == 'service_pin':
            h.pin = replace(h.pin, sequence=2)
            return
        with h.store._tx() as db:
            if mutation == 'disabled':
                db.execute("UPDATE mail_pull_accounts SET state='disabled',consent_epoch=consent_epoch+1")
            elif mutation == 'consent_epoch':
                db.execute('UPDATE mail_pull_accounts SET consent_epoch=consent_epoch+1')
                db.execute('UPDATE mail_pull_flows SET consent_epoch=consent_epoch+1')
            else:
                row = db.execute('SELECT * FROM mail_pull_flows').fetchone()
                token = json.loads(row['result_json'])['fence_token']
                db.execute("INSERT INTO mail_pull_fences VALUES(?,?,1,?,?,'cancel','pending',NULL,?,?)",
                           ('e' * 32, row['install_id'], row['flow_id'], token, NOW, NOW))
    h.after = interleave
    reason = {'fenced': 'fenced', 'service_pin': 'service_key_changed'}.get(mutation, 'stale_generation')
    assert h.client().start(OWNER, 'v1')['reason'] == reason
    assert h.count('mail_pull_status') == 0
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    assert h.row('mail_pull_accounts')['address'] == ''


def test_delayed_pending_status_cannot_overwrite_concurrent_verification(h):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'

    def interleave():
        h.after = lambda: None
        h.remote_state = 'verified'
        assert h.client().resume()['state'] == 'verified'

    h.after = interleave
    assert h.client().resume()['reason'] == 'stale_generation'
    assert h.client().status()['state'] == 'verified'


@pytest.mark.parametrize('scope,value', [('nonce', '0' * 32), ('install-id', '0' * 32),
                                       ('generation', '2'), ('owner-generation', '2'),
                                       ('timestamp', str(NOW - 300_001))])
def test_valid_service_signature_for_wrong_scope_still_refused(h, scope, value):
    h.mutate = lambda body, headers, status: (body, {scope: value}, status)
    assert h.client().start(OWNER, 'v1')['reason'] == 'response_unverified'
    assert h.count('mail_pull_status') == 0


@pytest.mark.parametrize('kind', ['oversize', 'headers', 'compression', 'duplicate_header', 'type', 'unsigned', 'nested'])
def test_response_wire_bounds_and_unsigned_refusal_never_establish_state(h, kind):
    original = h.transport

    def attack(method, path, headers, body):
        status, result_headers, raw = original(method, path, headers, body)
        if kind == 'oversize':
            return h.response({}, headers, path, raw=b' ' * 32769)
        if kind == 'nested':
            return h.response({}, headers, path, raw=b'[' * 2000 + b']' * 2000)
        if kind == 'unsigned':
            return 401, {'content-type': 'application/json'}, b'{"v":1,"code":"unauthorized"}'
        if kind == 'headers':
            result_headers['oversize'] = 'a' * 8192
        elif kind == 'compression':
            result_headers['content-encoding'] = 'gzip'
        elif kind == 'duplicate_header':
            result_headers['X-Mail-Version'] = '2'
        else:
            result_headers['content-type'] = 'text/html'
        return status, result_headers, raw

    assert h.client(attack).start(OWNER, 'v1')['reason'] == 'response_unverified'
    assert h.count('mail_pull_status') == 0


def test_reviewed_service_failure_cannot_leak_private_free_text(h, capsys):
    client = h.client()

    def failure():
        raise EnrollmentRefused(OWNER)

    client.reviewed_service = failure
    result = client.start(OWNER, 'v1')
    assert result['reason'] == 'service_unavailable'
    assert OWNER not in str(result) + capsys.readouterr().out
    assert h.count('mail_pull_flows') == 0


@pytest.mark.parametrize('state', ['held', 'enable_pending'])
def test_other_lifecycle_owned_flow_states_are_not_implicitly_resumed(h, state):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_flows SET state=?', (state,))
    assert h.restart().resume()['reason'] == 'local_disabled'
    assert len(h.calls) == 1


@pytest.mark.parametrize('state', ['expired', 'cancelled', 'active'])
def test_verified_remote_terminal_states_preserve_local_disabled(h, state):
    assert h.client().start(OWNER, 'v1')['state'] == 'pending'
    h.remote_state = state
    result = h.client().resume()
    assert result['state'] == {'active': 'completed'}.get(state, state)
    assert result['enabled'] is False
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    count = len(h.calls)
    assert h.restart().resume()['enabled'] is False
    assert len(h.calls) == count


# Real HTTP/SQL interoperability, stdin/stdout only, no subprocess server.
NODE_HANDLER = r'''
import {readFileSync,readdirSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {SignupHTTP} from './signup-http.js';
import {VerificationMail} from './verification-mail.js';
const p=JSON.parse(readFileSync(0,'utf8'));
const sql=new DatabaseSync(p.db); sql.exec('PRAGMA foreign_keys=ON');
if(!sql.prepare("SELECT 1 FROM sqlite_master WHERE name='pull_installs'").get()) {
  sql.exec(readFileSync('schema.sql','utf8'));
  for(const name of readdirSync('migrations').filter(n=>/^\d+.*\.sql$/.test(n)).sort())
    sql.exec(readFileSync('migrations/'+name,'utf8'));
}
const db={prepare(text){return {args:[],bind(...a){this.args=a;return this},
  async first(){return sql.prepare(text).get(...this.args)??null},
  async run(){return this.runSync()},
  runSync(){const r=sql.prepare(text).run(...this.args);return {success:true,meta:{changes:Number(r.changes)}}}}},
  async batch(items){sql.exec('BEGIN IMMEDIATE');try{const out=items.map(s=>s.runSync());sql.exec('COMMIT');return out}
    catch(e){sql.exec('ROLLBACK');throw e}}};
const mail=new VerificationMail({db,serviceOrigin:p.origin,sealKeyId:'synthetic',sealKey:new Uint8Array(32).fill(4)});
const http=new SignupHTTP({db,serviceOrigin:p.origin,domain:'clayrune.example',verificationMail:mail,
 serviceKeyId:'service-key',servicePrivateKey:p.service_key,clock:()=>p.now});
if(p.confirm) {
 const outbox=sql.prepare('SELECT outbox_id FROM pull_control_outbox').get();
 const message=await mail.message(outbox.outbox_id,p.now);
 const token=/token=([a-f0-9]{64})/.exec(message.body)[1];
 const page=await http.handle(new Request(p.origin+'/mail/link?token='+token),{trustedSource:'synthetic-edge'});
 const text=await page.text(); const csrf=/name="csrf" value="([a-f0-9]{64})"/.exec(text)[1];
 const cookie=page.headers.get('set-cookie').split(';')[0];
 const confirmed=await http.handle(new Request(p.origin+'/mail/link/confirm',{method:'POST',
  headers:{'content-type':'application/x-www-form-urlencoded',origin:p.origin,cookie},body:'token='+token+'&csrf='+csrf}),
  {trustedSource:'synthetic-edge'});
 if(confirmed.status!==200)throw Error('synthetic confirmation failed');
}
const response=await http.handle(new Request(p.origin+p.path,{method:p.method,headers:p.headers,
 body:Buffer.from(p.body,'base64')}),{trustedSource:'synthetic-edge'});
const body=Buffer.from(await response.arrayBuffer()).toString('base64');
const counts=Object.fromEntries(['pull_installs','pull_flows','pull_operations','pull_control_outbox','pull_tokens']
 .map(name=>[name,sql.prepare('SELECT count(*) AS n FROM '+name).get().n]));
console.log(JSON.stringify({status:response.status,headers:Object.fromEntries(response.headers),body,counts}));sql.close();
'''


def test_actual_node_handler_accepts_python_start_exact_retry_and_restart_status(h):
    service_der = h.service_key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
    state = {'crash': True, 'confirm': False, 'results': [], 'requests': []}

    def node_transport(method, path, headers, body):
        payload = {'db': str(h.folder / 'worker.sqlite'), 'origin': ORIGIN, 'now': h.now,
                   'service_key': encoded(service_der), 'method': method, 'path': path,
                   'headers': headers, 'body': base64.b64encode(body).decode('ascii'), 'confirm': state['confirm']}
        completed = subprocess.run(['node', '--input-type=module', '-e', NODE_HANDLER], input=json.dumps(payload),
                                   text=True, capture_output=True, cwd=ROOT / 'workers/agent-mail', timeout=20)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result['status'] == 200, result
        state['results'].append(result)
        state['requests'].append((path, body))
        if state['crash']:
            raise Crash()
        return result['status'], result['headers'], base64.b64decode(result['body'])

    with pytest.raises(Crash):
        h.client(node_transport).start(OWNER, 'v1')
    state['crash'] = False
    h.now += 1000
    result = h.restart(node_transport).resume()
    assert result['state'] == 'pending', result
    assert state['requests'][0] == state['requests'][1]
    assert all(n == 1 for n in state['results'][-1]['counts'].values())
    state['confirm'] = True
    result = h.restart(node_transport).resume()
    assert state['requests'][-1][0] == STATUS
    assert result['state'] == 'verified', result
    assert result['enabled'] is False and h.row('mail_pull_accounts')['local_enabled'] == 0
    assert all(n == 1 for n in state['results'][-1]['counts'].values())
