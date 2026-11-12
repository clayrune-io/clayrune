"""Synthetic local custody and independent wire proofs; no service or real mail."""
from dataclasses import replace
import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mc.agent_mail_enrollment_keys import IdentitySeal, SealReference
from mc.agent_mail_factory import GuardedMailStore
from mc.agent_mail_owner_change import OwnerChange, START, STATUS, ACCOUNT_STATUS
from mc.agent_mail_pull_schema import initialize
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signup import Signup

NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'
INSTALL = 'a' * 32
OP = 'b' * 32
CURRENT = SealReference('c' * 32, INSTALL, 3, 'current-key')
CHANGE = 'd' * 32
MASTER = b'M' * 32
ADDRESS = 'm' + 'e' * 29 + '@clayrune.example'


def wire(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def unb64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class Crash(BaseException):
    pass


class Harness:
    def __init__(self, folder):
        self.folder, self.now, self.master, self.allowed = folder, NOW, MASTER, True
        self.service = Ed25519PrivateKey.from_private_bytes(b'V' * 32)
        public = self.service.public_key().public_bytes_raw()
        self.pin = ServiceKeyCandidate(ORIGIN, 1, 'service-key', b64(public), digest(public), (), NOW + 90 * 86400000)
        self.store = GuardedMailStore(folder / 'mail.sqlite3', creating=True)
        initialize(self.store)
        self.seal = IdentitySeal(folder / 'identity', key=lambda: self.master)
        self.public = self.seal.create(CURRENT.flow, INSTALL, 3, CURRENT.key_id, lambda: (b'S' * 32, b'F' * 32))
        with self.store._tx() as db:
            db.execute("INSERT INTO mail_pull_accounts VALUES(?,3,2,?,?,?, ?,?,'active',1,'v1',1,'',?)",
                       (INSTALL, ORIGIN, ADDRESS, 'private-old@example.com', CURRENT.key_id, CURRENT.flow, self.now))
            db.execute("INSERT INTO mail_pull_generations VALUES(?,3,2,?,?,1,?,?,?,'current',?,NULL)",
                       (INSTALL, CURRENT.key_id, CURRENT.flow, 'f' * 64, self.pin.key_id, self.pin.public_key, self.now))
        self.calls, self.operations = [], {}
        self.remote = 'pending'
        self.result = None
        self.change = None
        self.expires = self.now + 1800000
        self.notice = 'queued'
        self.mutate = lambda path, value: value
        self.after = lambda path: None
        self.raw_mutate = lambda path, raw: raw
        self.header_mutate = lambda path, headers: headers
        self.http_code = 200

    def client(self, transport=None):
        return OwnerChange(self.store, self.seal, transport or self.transport, install_id=INSTALL,
                           reviewed_service=lambda: (self.pin, 'f' * 64), authorize=lambda: self.allowed,
                           clock_ms=lambda: self.now)

    def restart(self):
        self.store = GuardedMailStore(self.folder / 'mail.sqlite3')
        self.seal = IdentitySeal(self.folder / 'identity', key=lambda: self.master)
        return self.client()

    def row(self, table='mail_pull_flows'):
        with self.store._read() as db:
            return dict(db.execute('SELECT * FROM ' + table).fetchone())

    def count(self, table):
        with self.store._read() as db:
            return db.execute('SELECT count(*) FROM ' + table).fetchone()[0]

    def completed(self):
        self.remote = 'completed'
        self.result = dict(install_id=INSTALL, generation=4, owner_generation=3, state='verified', change_id=CHANGE)
        # Deliberately unrelated to local observation time; adapter must not infer.
        self.change = dict(change_id=CHANGE, state='completed', created_ms=NOW + 123,
                           revert_until_ms=NOW + 604800123)

    def rebind(self, generation, owner_generation, *, state='verified'):
        """Synthetic stand-in for later explicit HUMAN lifecycle composition.

        Only the test fixture knows this synthetic seed. Production owner
        adapter has no seed accessor or automatic generation-rebind path.
        """
        flow = format(generation, '032x')
        self.seal.create(flow, INSTALL, generation, CURRENT.key_id, lambda: (b'S' * 32, b'R' * 32))
        with self.store._tx() as db:
            db.execute("UPDATE mail_pull_generations SET state='held' WHERE install_id=?", (INSTALL,))
            db.execute("INSERT INTO mail_pull_generations VALUES(?,?,?,?,?,1,?,?,?,'current',?,NULL)",
                       (INSTALL, generation, owner_generation, CURRENT.key_id, flow, 'f' * 64,
                        self.pin.key_id, self.pin.public_key, self.now))
            db.execute('UPDATE mail_pull_accounts SET generation=?,owner_generation=?,seal_flow_id=?,state=?,local_enabled=0',
                       (generation, owner_generation, flow, state))

    def reverted(self):
        self.remote = 'reverted'
        self.result = dict(install_id=INSTALL, generation=5, owner_generation=4, state='frozen', change_id=CHANGE)
        self.change['state'] = 'reverted'

    def transport(self, method, path, headers, raw):
        assert method == 'POST' and path in {START, STATUS, ACCOUNT_STATUS}
        assert headers['x-mail-digest'] == digest(raw)
        assert headers['x-mail-version'] == '2' and headers['x-mail-direction'] == 'install-to-pull'
        assert headers['x-mail-install-id'] == INSTALL and headers['x-mail-key-id'] == CURRENT.key_id
        g, og, nonce = headers['x-mail-generation'], headers['x-mail-owner-generation'], headers['x-mail-nonce']
        proof = wire(['clayrune-agent-mail-pull-http', '1', 'install-to-pull', ORIGIN, 'POST', path,
                      CURRENT.key_id, INSTALL, g, og, headers['x-mail-timestamp'], nonce, digest(raw)])
        Ed25519PublicKey.from_public_bytes(unb64(self.public)).verify(unb64(headers['x-mail-signature']), proof)
        body = json.loads(raw)
        if path == START:
            assert g == '3' and og == '2'
            assert set(body) == {'op_id', 'flow_id', 'purpose', 'candidate_key_id', 'candidate_public_key',
                                 'candidate_signature', 'flow_secret_hash', 'fence_hash'}
            assert body['purpose'] == 'owner_change' and body['candidate_public_key'] == self.public
            assert body['candidate_key_id'] == CURRENT.key_id
            candidate = wire(['clayrune-mail-pull-candidate', '1', ORIGIN, 'owner_change', body['flow_id'],
                              INSTALL, '3', '2', CURRENT.key_id, self.public, body['fence_hash'], body['flow_secret_hash']])
            Ed25519PublicKey.from_public_bytes(unb64(self.public)).verify(unb64(body['candidate_signature']), candidate)
            if body['op_id'] in self.operations:
                assert self.operations[body['op_id']] == raw
            self.operations[body['op_id']] = raw
            self.start_body = body
            v = dict(flow_id=body['flow_id'], state='pending', expires_ms=self.expires, verification_state='queued')
        elif path == STATUS:
            assert g == '3' and og == '2'  # Original bound scope, including after revert.
            assert set(body) == {'flow_id', 'flow_secret'} and body['flow_id'] == self.start_body['flow_id']
            assert digest(body['flow_secret'].encode('ascii')) == self.start_body['flow_secret_hash']
            v = dict(flow_id=body['flow_id'], state=self.remote, expires_ms=self.expires, result=self.result)
        else:
            assert raw == b'{}'
            # General status may use only an actually locally rebound seal.
            assert self.row('mail_pull_accounts')['generation'] == int(g)
            v = dict(install_id=INSTALL, generation=int(g), owner_generation=int(og),
                     state=self.result['state'] if self.result else 'active', address=ADDRESS,
                     owner='private-new@example.com' if self.result else 'private-old@example.com',
                     key_id=CURRENT.key_id, queue_count=0, queue_bytes=0, oldest_received_ms=None,
                     oldest_expires_ms=None, invalid_message_id_holds=0, current_epoch=1,
                     owner_change=self.change, last_notice_state=self.notice, reason='')
        v = dict(v=1, code='ok', request_id=body['op_id'] if path == START else nonce, **v)
        if self.http_code != 200:
            v = dict(v=1, code='service_unavailable', request_id=v['request_id'])
        v = self.mutate(path, v)
        reply = self.raw_mutate(path, wire(v))
        output = dict(version='2', direction='pull-to-install', **{'key-id': self.pin.key_id, 'install-id': INSTALL,
                      'generation': g, 'owner-generation': og, 'timestamp': str(self.now), 'nonce': nonce, 'digest': digest(reply)})
        output = {'x-mail-' + k: v for k, v in output.items()}
        proof = wire(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', ORIGIN, 'POST', path,
                      self.pin.key_id, INSTALL, g, og, str(self.now), nonce, str(self.http_code), digest(reply)])
        output['x-mail-signature'] = b64(self.service.sign(proof))
        output['content-type'] = 'application/json'
        output = self.header_mutate(path, output)
        self.calls.append((path, headers, raw))
        self.after(path)
        return self.http_code, output, reply


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_human_start_exact_scope_independent_secret_and_pending_keeps_mail(h):
    result = h.client().start(OP)
    assert result['state'] == 'pending' and result['enabled'] is True and not result['reason'], result
    assert [c[0] for c in h.calls] == [START, ACCOUNT_STATUS]
    flow = h.row()
    assert flow['human_authorized_ms'] == h.now and flow['seal_flow_id'] == flow['flow_id']
    assert flow['candidate_key_id'] == CURRENT.key_id
    assert h.row('mail_pull_accounts')['generation'] == 3
    assert h.count('mail_pull_generations') == 1 and h.count('mail_pull_fences') == 0
    old = h.seal.flow_secret(CURRENT.flow, INSTALL, 3, CURRENT.key_id)
    new = h.seal.flow_secret(flow['flow_id'], INSTALL, 3, CURRENT.key_id)
    assert new != old and len(new) == 32
    assert new.hex() not in flow['result_json'] and old.hex() not in str(result)
    for forbidden in ('private-old', 'private-new', 'fence_token', 'flow_secret', 'candidate_signature'):
        assert forbidden not in str(result)


@pytest.mark.parametrize('allowed', [False, None, 1, 'true'])
def test_untrusted_agent_cannot_start_even_existing_flow(h, allowed):
    h.client().start(OP)
    calls, before = len(h.calls), h.row()
    h.allowed = allowed
    assert h.client().start(OP)['reason'] == 'human_required'
    assert h.client().start('f' * 32)['reason'] == 'human_required'
    assert len(h.calls) == calls and h.row() == before


def test_status_and_refresh_before_intent_are_inert(h):
    h.allowed = False
    assert h.client().status()['state'] == h.client().refresh()['state'] == 'idle'
    assert not h.calls and h.count('mail_pull_flows') == 0


def test_idempotent_start_op_after_restart_never_resends_stepup(h):
    first = h.client().start(OP)
    second = h.restart().start(OP)
    assert second['flow_id'] == first['flow_id']
    assert [c[0] for c in h.calls].count(START) == 1
    assert h.count('mail_pull_flows') == 1
    assert h.client().start('f' * 32)['reason'] == 'operation_conflict'


def test_crash_after_remote_commit_human_retry_reuses_exact_bytes(h):
    h.after = lambda path: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        h.client().start(OP)
    first = h.calls[0]
    assert json.loads(h.row()['result_json'])['started'] == 0
    h.after = lambda path: None
    h.now += 999
    assert h.restart().start(OP)['state'] == 'pending'
    assert h.calls[1][0] == START and h.calls[1][2] == first[2]
    assert h.calls[1][1]['x-mail-nonce'] != first[1]['x-mail-nonce']
    assert len(h.operations) == 1


def test_crash_after_remote_commit_background_recovers_without_start(h):
    h.after = lambda path: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        h.client().start(OP)
    h.after = lambda path: None
    h.allowed = False
    assert h.restart().refresh()['state'] == 'pending'
    assert [c[0] for c in h.calls] == [START, STATUS, ACCOUNT_STATUS]


def test_crash_after_seal_before_commit_reopens_orphan(h, monkeypatch):
    original = h.seal.prepare_management
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise Crash()
    monkeypatch.setattr(h.seal, 'prepare_management', lost)
    with pytest.raises(Crash):
        h.client().start(OP)
    row = h.row()
    orphan = h.folder / 'identity' / (row['flow_id'] + '.sealed')
    saved = orphan.read_bytes()
    assert json.loads(row['result_json'])['public_key'] == ''
    assert h.restart().start(OP)['state'] == 'pending'
    assert h.row()['flow_id'] == row['flow_id'] and orphan.read_bytes() == saved
    assert len(list(h.seal.folder.glob('*.sealed'))) == 2


def test_approved_uses_frozen_local_schema_without_confirming(h):
    h.client().start(OP)
    h.remote = 'approved'
    h.expires += 150000
    h.allowed = False
    result = h.client().refresh()
    assert result['flow_state'] == 'approved' and result['enabled'] is True
    assert h.row()['state'] == 'verified'
    assert set(c[0] for c in h.calls) <= {START, STATUS, ACCOUNT_STATUS}


def test_completion_stops_old_authority_without_rebinding_or_old_key_new_scope(h):
    h.client().start(OP)
    h.completed()
    result = h.restart().refresh()
    assert result['flow_state'] == 'completed' and result['state'] == 'verified', result
    assert result['reason'] == 'owner_change_rebind_required'
    assert result['generation'] == 4 and result['owner_generation'] == 3 and not result['enabled']
    assert result['owner_change'] is None  # MR has no deadline; do not invent one.
    assert h.calls[-1][0] == STATUS
    account = h.row('mail_pull_accounts')
    assert account['local_enabled'] == 0 and account['generation'] == 3 and account['state'] == 'held'
    assert account['consent_epoch'] == 2 and account['owner_address'] == 'private-old@example.com'
    assert h.row('mail_pull_generations')['state'] == 'held'
    h.client().refresh()
    assert h.row('mail_pull_accounts')['consent_epoch'] == 2


def test_reverted_freeze_survives_seven_days_and_keeps_original_status_scope(h):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.reverted()
    h.now += 8 * 86400000
    h.allowed = False
    result = h.restart().refresh()
    assert result['state'] == 'frozen' and result['flow_state'] == 'reverted' and not result['enabled'], result
    assert h.row()['state'] == 'completed'  # Exact remote state is in metadata.
    assert h.client().status()['state'] == 'frozen'
    assert h.client().start('e' * 32)['reason'] == 'human_required'
    assert h.calls[-1][1]['x-mail-generation'] == '3'


@pytest.mark.parametrize('field', ['x-mail-signature', 'x-mail-nonce', 'x-mail-key-id', 'x-mail-generation',
                                  'x-mail-owner-generation', 'x-mail-install-id', 'x-mail-direction', 'x-mail-digest'])
def test_invalid_signed_scope_never_commits(h, field):
    h.header_mutate = lambda path, headers: {**headers, field: 'wrong'}
    result = h.client().start(OP)
    assert result['reason'] == 'response_unverified', result
    assert json.loads(h.row()['result_json'])['started'] == 0
    assert h.row('mail_pull_accounts')['local_enabled'] == 1


@pytest.mark.parametrize('raw', [b'{', b'[]', b'\xff', b'{"v":1,"v":1}', b'{"v":-0}',
                                 b'{"v":1.0}', b'{"v":true}', b'{"v":NaN}', b'{"v":"\\ud800"}'])
def test_malformed_wire_is_closed_hold(h, raw):
    h.raw_mutate = lambda path, value: raw
    assert h.client().start(OP)['reason'] == 'response_unverified'
    assert json.loads(h.row()['result_json'])['started'] == 0


def test_signed_service_failure_is_visible_and_retry_preserves_intent(h):
    h.http_code = 503
    result = h.client().start(OP)
    assert result['reason'] == 'service_unavailable'
    first = h.calls[0][2]
    h.http_code = 200
    assert h.client().start(OP)['state'] == 'pending'
    assert h.calls[1][2] == first


def test_transport_failure_is_redacted_and_local_status_remains_available(h, capsys):
    def fail(*args):
        raise OSError('private-new@example.com secret material')
    result = h.client(fail).start(OP)
    assert result['reason'] == 'connection_lost'
    assert result['flow_id'] == h.row()['flow_id']
    assert 'private-new' not in str(result) + capsys.readouterr().out


def test_changed_manifest_during_transport_cannot_commit(h):
    h.after = lambda path: setattr(h, 'pin', replace(h.pin, sequence=2))
    assert h.client().start(OP)['reason'] == 'service_key_changed'
    assert json.loads(h.row()['result_json'])['started'] == 0


def test_disable_during_response_is_not_overwritten(h):
    def disable(path):
        with h.store._tx() as db:
            db.execute("UPDATE mail_pull_accounts SET state='disabled',local_enabled=0,consent_epoch=2")
    h.after = disable
    assert h.client().start(OP)['reason'] == 'stale_generation'
    assert h.row('mail_pull_accounts')['state'] == 'disabled'
    assert json.loads(h.row()['result_json'])['started'] == 0


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'lock'])
def test_committed_management_seal_never_regenerated(h, damage):
    h.client().start(OP)
    path = h.folder / 'identity' / (h.row()['flow_id'] + '.sealed')
    if damage == 'missing':
        path.unlink()
    elif damage == 'corrupt':
        path.write_bytes(b'corrupt')
    else:
        h.master = None
    calls = len(h.calls)
    result = h.restart().start(OP)
    assert result['reason'] in {'seal_missing', 'vault_locked', 'identity_unavailable_reenrollment_required'}
    assert len(h.calls) == calls and (damage != 'missing' or not path.exists())


def test_no_machine_confirmation_revert_or_recovery_api(h):
    client = h.client()
    assert not any(hasattr(client, method) for method in ('confirm', 'revert', 'recover', 'enable', 'resume'))
    with pytest.raises(TypeError):
        client.start(OP, new_owner='private-new@example.com')
    assert not h.calls


@pytest.mark.parametrize('state', ['frozen', 'disabled', 'deleted', 'pending', 'held'])
def test_inactive_registration_cannot_start_change(h, state):
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET state=?,local_enabled=0', (state,))
    result = h.client().start(OP)
    assert result['reason'] in {'registration_inactive', 'registration_frozen'}
    assert not h.calls and h.count('mail_pull_flows') == 0


def test_frozen_schema_corruption_is_not_repaired(h):
    with h.store._tx() as db:
        db.execute('DROP TABLE mail_pull_status')
    assert h.client().start(OP)['reason'] == 'schema_corrupt'
    assert not h.calls


@pytest.mark.parametrize('state', ['completed', 'reverted'])
def test_bad_completion_scope_does_not_stop_or_rebind_mail(h, state):
    h.client().start(OP)
    h.completed()
    if state == 'reverted':
        h.reverted()
    h.result['owner_generation'] = 50
    result = h.client().refresh()
    assert result['reason'] == 'response_unverified', result
    assert h.row('mail_pull_accounts')['local_enabled'] == 1
    assert json.loads(h.row()['result_json'])['remote_state'] == 'pending'


def test_signed_deadline_available_only_after_human_local_key_rebind(h):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.rebind(4, 3)
    h.now += 500000
    result = h.restart().refresh()
    assert not result['reason'], result
    assert result['owner_change'] == h.change
    assert result['owner_change']['revert_until_ms'] == NOW + 604800123
    assert h.calls[-1][0] == ACCOUNT_STATUS and h.calls[-1][1]['x-mail-generation'] == '4'
    assert h.calls[-2][0] == STATUS and h.calls[-2][1]['x-mail-generation'] == '3'
    stored = h.row('mail_pull_status')
    assert stored['generation'] == 4 and stored['response_digest'] == digest(stored['status_json'].encode('utf-8'))
    h.now += 8 * 86400000
    calls = len(h.calls)
    assert h.restart().status()['owner_change'] == h.change
    assert len(h.calls) == calls and not h.client().status()['enabled']


def test_revert_stops_already_rebound_generation_then_surfaces_hosted_freeze(h):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.rebind(4, 3, state='active')
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET local_enabled=1')
    h.reverted()
    result = h.client().refresh()
    assert result['state'] == 'frozen' and not result['enabled']
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    assert h.row('mail_pull_accounts')['state'] == 'held'
    h.rebind(5, 4, state='frozen')
    h.now += 8 * 86400000
    result = h.client().refresh()
    assert result['state'] == 'frozen' and result['owner_change'] == h.change, result
    assert h.calls[-1][1]['x-mail-generation'] == '5' and not result['enabled']


def test_restart_local_projection_does_not_touch_vault_or_transport(h, monkeypatch):
    h.client().start(OP)
    before = h.row()
    monkeypatch.setattr(h.seal, 'sign_pull', lambda *a: pytest.fail('local status signed'))
    monkeypatch.setattr(h.seal, 'generate_pull', lambda *a, **k: pytest.fail('local status generated'))
    calls = len(h.calls)
    h.master = None
    assert h.client().status()['flow_state'] == 'pending'
    assert h.row() == before and len(h.calls) == calls


@pytest.mark.parametrize('bad', ['new_owner', 'url', 'token', 'csrf', 'cookie'])
def test_unknown_response_authority_fields_are_rejected(h, bad):
    h.mutate = lambda path, value: {**value, bad: 'untrusted'}
    assert h.client().start(OP)['reason'] == 'response_unverified'
    assert json.loads(h.row()['result_json'])['started'] == 0


@pytest.mark.parametrize('field,value', [('generation', 99), ('owner_generation', True), ('state', 'active'),
                                        ('change_id', None), ('install_id', 'f' * 32)])
def test_completed_result_must_match_exact_owner_switch(h, field, value):
    h.client().start(OP)
    h.completed()
    h.result[field] = value
    assert h.client().refresh()['reason'] == 'response_unverified'
    assert h.row('mail_pull_accounts')['local_enabled'] == 1


@pytest.mark.parametrize('field,value', [('revert_until_ms', '604800000'), ('revert_until_ms', True),
                                        ('state', 'frozen'), ('change_id', 'invalid'), ('created_ms', -1)])
def test_deadline_shape_refused_without_losing_completed_flow(h, field, value):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.rebind(4, 3)
    h.change[field] = value
    result = h.client().refresh()
    assert result['reason'] == 'response_unverified', result
    assert result['flow_state'] == 'completed' and not result['enabled']
    assert result['owner_change'] is None


@pytest.mark.parametrize('remote', ['pending', 'approved', 'expired', 'cancelled'])
def test_completed_remote_state_cannot_regress(h, remote):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.remote, h.result = remote, None
    result = h.client().refresh()
    assert result['reason'] == 'response_unverified'
    assert result['flow_state'] == 'completed' and not result['enabled']


def test_reverted_result_cannot_be_replaced_with_earlier_switch(h):
    h.client().start(OP)
    h.completed()
    h.reverted()
    h.client().refresh()
    h.completed()
    result = h.client().refresh()
    assert result['reason'] == 'response_unverified' and result['state'] == 'frozen'


@pytest.mark.parametrize('field', ['result_json', 'input_digest'])
def test_local_corrupt_intent_cannot_be_sent_or_repaired(h, field):
    h.client().start(OP)
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_flows SET ' + field + '=?', ('!' * 64,))
    calls = len(h.calls)
    assert h.client().refresh()['reason'] == 'schema_corrupt'
    assert len(h.calls) == calls


def test_draft_lock_does_not_lose_intent_or_create_identity(h):
    h.master = None
    result = h.client().start(OP)
    assert result['reason'] == 'vault_locked' and not h.calls
    assert json.loads(h.row()['result_json'])['start_body'] is None
    assert h.count('mail_pull_flows') == 1
    assert len(list(h.seal.folder.glob('*.sealed'))) == 1
    h.master = MASTER
    assert h.restart().start(OP)['state'] == 'pending'


def test_draft_status_poll_never_generates_or_sends_start(h):
    h.master = None
    h.client().start(OP)
    h.master = MASTER
    result = h.restart().refresh()
    assert result['state'] == 'preparing' and not h.calls
    assert len(list(h.seal.folder.glob('*.sealed'))) == 1


def test_generation_fence_blocks_start_and_retains_human_intent(h):
    with h.store._tx() as db:
        db.execute("INSERT INTO mail_pull_fences VALUES(?, ?,3,?,?,'revoke','pending',NULL,?,?)",
                   ('f' * 32, INSTALL, CURRENT.flow, 'f' * 64, h.now, h.now))
    assert h.client().start(OP)['reason'] == 'fenced'
    assert not h.calls and h.count('mail_pull_flows') == 0


def test_old_mailbox_required_is_not_a_recovery_bypass(h):
    h.allowed = False
    assert h.client().start(OP)['reason'] == 'human_required'
    assert not h.calls


def test_status_failure_after_completion_keeps_denial_durable(h):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.rebind(4, 3)
    h.header_mutate = lambda path, headers: {**headers, 'x-mail-signature': 'bad'} if path == ACCOUNT_STATUS else headers
    result = h.client().refresh()
    assert result['reason'] == 'response_unverified' and result['flow_state'] == 'completed'
    assert not h.restart().status()['enabled']


def test_signup_composes_same_trusted_human_adapter_without_action(h):
    signup = Signup(h.store, h.seal, h.transport, reviewed_service=lambda: (h.pin, 'f' * 64),
                    authorize=lambda: h.allowed, clock_ms=lambda: h.now)
    owner = signup.owner_change(install_id=INSTALL)
    assert owner.store is signup.store and owner.seal is signup.seal and owner.transport is signup.transport
    assert owner.authorize is signup.authorize and owner.reviewed_service is signup.reviewed_service
    assert not h.calls and h.count('mail_pull_flows') == 0
    h.allowed = False
    assert owner.start(OP)['reason'] == 'human_required'


def test_status_reports_explicit_new_generation_enable_without_enabling_itself(h):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.rebind(4, 3, state='active')
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET local_enabled=1')
    h.mutate = lambda path, value: {**value, 'state': 'active'} if path == ACCOUNT_STATUS else value
    result = h.client().refresh()
    assert result['enabled'] and not result['requires_enable'] and result['state'] == 'active', result
    assert result['flow_state'] == 'completed' and result['owner_change'] == h.change


def test_service_expired_revert_window_is_preserved_without_local_calculation(h):
    h.client().start(OP)
    h.completed()
    h.client().refresh()
    h.rebind(4, 3)
    h.now += 8 * 86400000
    h.change['state'] = 'expired'
    result = h.client().refresh()
    assert result['owner_change'] == h.change and not result['reason'], result
    assert result['flow_state'] == 'completed' and not result['enabled']
