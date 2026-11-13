"""Synthetic keys/temp SQLite only; no live service, mailbox, provider or vault."""
from dataclasses import replace
import base64
import hashlib
import json
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_activation import MAX_ATTEMPTS, PATH
from mc.agent_mail_signup import START, STATUS, TTL
from test_agent_mail_signup import (ADDRESS, MASTER, NOW, ORIGIN, OWNER, ROOT, Crash,
                                    Harness, NODE_HANDLER, decoded, encoded, fail_commit, wire)


class ActivationHarness(Harness):
    def __init__(self, folder):
        super().__init__(folder)
        self.activation_calls = []
        self.activation_operations = {}
        self.activation_after = lambda: None
        self.activation_mutate = lambda value: value

    def verified(self):
        assert self.client().start(OWNER, 'v1')['state'] == 'pending'
        self.remote_state = 'verified'
        assert self.client().resume()['state'] == 'verified'

    def transport(self, method, path, headers, raw):
        if path != PATH:
            return super().transport(method, path, headers, raw)
        assert method == 'POST'
        value = json.loads(raw)
        assert set(value) == {'op_id', 'flow_id', 'flow_secret', 'consent_version'}
        assert value['flow_id'] == self.start['flow_id'] and value['consent_version'] == 'v1'
        assert hashlib.sha256(value['flow_secret'].encode('ascii')).hexdigest() == self.start['flow_secret_hash']
        assert headers['x-mail-digest'] == hashlib.sha256(raw).hexdigest()
        assert headers['x-mail-generation'] == headers['x-mail-owner-generation'] == '1'
        transcript = wire(['clayrune-agent-mail-pull-http', '1', 'install-to-pull', ORIGIN, 'POST', PATH,
                           headers['x-mail-key-id'], headers['x-mail-install-id'], '1', '1',
                           headers['x-mail-timestamp'], headers['x-mail-nonce'], headers['x-mail-digest']])
        Ed25519PublicKey.from_public_bytes(decoded(self.start['public_key'])).verify(
            decoded(headers['x-mail-signature']), transcript)
        self.activation_calls.append((dict(headers), raw))
        if value['op_id'] not in self.activation_operations:
            self.activation_operations[value['op_id']] = (raw, self.now)
        original, active_since = self.activation_operations[value['op_id']]
        assert raw == original
        result = {'v': 1, 'code': 'ok', 'request_id': value['op_id'],
                  'install_id': headers['x-mail-install-id'], 'generation': 1, 'owner_generation': 1,
                  'state': 'active', 'active_since_ms': active_since}
        response = self.response(self.activation_mutate(result), headers, path)
        self.activation_after()
        return response


@pytest.fixture
def h(tmp_path):
    return ActivationHarness(tmp_path)


def test_human_enable_atomic_pull_and_base_registration(h):
    h.verified()
    before = h.row()['consent_epoch']
    result = h.client().enable()
    assert result['state'] == 'active' and result['enabled'] is True, result
    assert result['activation_attempts'] == 1 and not result['activation_pending']
    assert h.row()['state'] == 'completed' and h.row()['consent_epoch'] == before + 1
    a = h.row('mail_pull_accounts')
    assert a['local_enabled'] == 1 and a['state'] == 'active' and a['consent_epoch'] == before + 1
    assert h.store.registration_state(a['install_id'])['enabled'] == 1
    assert h.store.registration_state(a['install_id'])['generation'] == 1
    assert json.loads(h.row('mail_pull_status')['status_json'])['state'] == 'active'
    count = len(h.activation_calls)
    assert h.restart().resume()['enabled'] is True
    assert h.client().status()['enabled'] is True
    assert h.client().enable()['enabled'] is True
    assert len(h.activation_calls) == count


@pytest.mark.parametrize('allowed', [False, None, 1, 'true'])
def test_gate_is_literal_true_on_every_action(h, allowed):
    h.verified()
    h.authorized = allowed
    assert h.client().enable()['reason'] == 'human_required'
    assert not h.activation_calls and 'activation_op' not in h.row()['result_json']
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    assert h.client().status()['reason'] == 'human_required'


def test_pending_verification_and_no_flow_cannot_activate(h):
    assert h.client().enable()['reason'] == 'invalid_flow'
    h.client().start(OWNER, 'v1')
    assert h.client().enable()['reason'] == 'verification_pending'
    assert not h.activation_calls


def test_lost_response_requires_new_human_action_and_exact_body_after_restart(h):
    h.verified()
    h.activation_after = lambda: (_ for _ in ()).throw(OSError('sensitive request text'))
    result = h.client().enable()
    assert result['reason'] == 'connection_lost' and result['activation_pending']
    assert h.client().status()['reason'] == 'connection_lost'
    assert h.row()['state'] == 'enable_pending'
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    consent = h.row()['consent_epoch']
    h.authorized = False
    client = h.restart()
    for _ in range(3):
        assert client.resume()['activation_pending']
        assert client.status()['activation_pending']
    assert client.enable()['reason'] == 'human_required'
    assert len(h.activation_calls) == 1
    h.authorized = True
    h.activation_after = lambda: None
    h.now += 1000
    assert h.restart().enable()['enabled'] is True
    first, second = h.activation_calls
    assert first[1] == second[1]
    assert first[0]['x-mail-nonce'] != second[0]['x-mail-nonce']
    assert first[0]['x-mail-signature'] != second[0]['x-mail-signature']
    assert h.row()['consent_epoch'] == consent and len(h.activation_operations) == 1


def test_crash_after_remote_commit_keeps_exact_durable_operation(h):
    h.verified()
    h.activation_after = lambda: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        h.client().enable()
    assert h.row()['state'] == 'enable_pending'
    assert not h.restart().resume()['enabled']
    assert len(h.activation_calls) == 1
    h.activation_after = lambda: None
    assert h.restart().enable()['enabled'] is True
    assert h.activation_calls[0][1] == h.activation_calls[1][1]


def test_attempt_cap_is_durable_and_no_hidden_network_retry(h):
    h.verified()
    h.activation_after = lambda: (_ for _ in ()).throw(OSError())
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = h.restart().enable()
        assert result['activation_attempts'] == attempt and result['reason'] == 'connection_lost'
        assert len(h.activation_calls) == attempt
    assert h.restart().enable()['reason'] == 'request_limit'
    assert len(h.activation_calls) == MAX_ATTEMPTS
    assert h.client().status()['reason'] == 'request_limit'
    assert len(h.activation_operations) == 1


@pytest.mark.parametrize('offset', [0, 1])
def test_original_signup_enable_deadline_never_extended(h, offset):
    h.verified()
    deadline = h.row()['expires_ms']
    h.activation_after = lambda: (_ for _ in ()).throw(OSError())
    h.client().enable()
    h.now = deadline + offset
    assert h.restart().enable()['reason'] == 'flow_expired'
    assert len(h.activation_calls) == 1 and h.row()['expires_ms'] == deadline
    assert h.client().status()['reason'] == 'flow_expired'


def fence(h):
    row = h.row()
    token = json.loads(row['result_json'])['fence_token']
    with h.store._tx() as db:
        db.execute("INSERT INTO mail_pull_fences VALUES(?,?,1,?,?,'cancel','pending',NULL,?,?)",
                   ('e' * 32, row['install_id'], row['flow_id'], token, h.now, h.now))


@pytest.mark.parametrize('kind', ['fence', 'disabled', 'epoch', 'pin', 'vault', 'expired', 'registration',
                                  'pending', 'generation_pin'])
def test_delayed_signed_success_never_crosses_authority_change(h, kind):
    h.verified()
    def change():
        if kind == 'fence':
            fence(h)
        elif kind == 'pin':
            h.pin = replace(h.pin, sequence=2)
        elif kind == 'vault':
            h.master = None
        elif kind == 'expired':
            h.now = h.row()['expires_ms']
        else:
            with h.store._tx() as db:
                if kind == 'disabled':
                    db.execute("UPDATE mail_pull_accounts SET state='disabled',local_enabled=0")
                elif kind == 'epoch':
                    db.execute('UPDATE mail_pull_accounts SET consent_epoch=consent_epoch+1')
                    db.execute('UPDATE mail_pull_flows SET consent_epoch=consent_epoch+1')
                elif kind == 'pending':
                    db.execute("UPDATE mail_pull_accounts SET state='pending'")
                elif kind == 'generation_pin':
                    db.execute("UPDATE mail_pull_generations SET service_public_key=?", (encoded(b'Z' * 32),))
                else:
                    db.execute('UPDATE registrations SET generation=generation+1')
    h.activation_after = change
    assert h.client().enable()['enabled'] is False
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    assert h.row('registrations')['enabled'] == 0


def test_preexisting_fence_and_changed_base_generation_prevent_network(h):
    h.verified()
    fence(h)
    assert h.client().enable()['reason'] == 'fenced'
    assert not h.activation_calls


def test_base_generation_mismatch_is_not_repaired(h):
    h.verified()
    install = h.row()['install_id']
    with h.store._tx() as db:
        db.execute('INSERT INTO registrations VALUES(?,2,0,?)', (install, NOW / 1000))
    assert h.client().enable()['reason'] == 'stale_generation'
    assert h.store.registration_state(install)['generation'] == 2
    assert not h.activation_calls


@pytest.mark.parametrize('field,value', [('generation', 2), ('owner_generation', 2), ('generation', True),
    ('install_id', '0' * 32), ('state', 'verified'), ('request_id', '0' * 32),
    ('active_since_ms', -1), ('active_since_ms', NOW + TTL), ('extra', 'injected')])
def test_signed_wrong_result_never_enables(h, field, value):
    h.verified()
    h.activation_mutate = lambda body: {**body, field: value}
    result = h.client().enable()
    assert result['reason'] == 'response_unverified' and not result['enabled'], result
    assert h.client().status()['reason'] == 'response_unverified'
    assert h.row('registrations')['enabled'] == h.row('mail_pull_accounts')['local_enabled'] == 0


@pytest.mark.parametrize('status,code', [(403, 'consent_required'), (409, 'fenced'),
    (409, 'verification_pending'), (410, 'flow_expired'), (429, 'request_limit'), (503, 'storage_unavailable')])
def test_verified_worker_errors_are_visible_failure_only(h, status, code):
    h.verified()
    def error(method, path, headers, body):
        value = {'v': 1, 'code': code, 'request_id': json.loads(body)['op_id']}
        return h.response(value, headers, path, status)
    result = h.client(error).enable()
    assert result['reason'] == code and not result['enabled']
    assert h.client().status()['reason'] == code


def test_unsigned_error_refuses_and_logs_without_exception_secret(h, monkeypatch):
    h.verified()
    logs = []
    monkeypatch.setattr('mc.agent_mail_activation._log', lambda value, **kwargs: logs.append(value))
    result = h.client(lambda *args: (401, {}, b'{}')).enable()
    assert result['reason'] == 'response_unverified'
    assert logs == ['[agent-mail] activation response_unverified']
    ref = h.row()
    secret = h.seal.flow_secret(ref['flow_id'], ref['install_id'], 1, ref['candidate_key_id']).hex()
    def leak(*args):
        raise RuntimeError(secret)
    assert h.client(leak).enable()['reason'] == 'connection_lost'
    with h.store._read() as db:
        dump = '\n'.join(db.iterdump())
    assert secret not in dump and secret not in str(logs) and secret not in str(h.client().status())


def test_consent_commit_failure_sends_nothing(h):
    h.verified()
    fail_commit(h)
    assert h.client().enable()['reason'] == 'storage_unavailable'
    assert not h.activation_calls
    assert 'activation_op' not in h.row()['result_json']
    assert h.count('registrations') == 0
    assert h.client().status()['reason'] == 'storage_unavailable'


def test_receipt_commit_failure_leaves_both_execution_surfaces_disabled(h):
    h.verified()
    h.activation_after = lambda: fail_commit(h)
    assert h.client().enable()['reason'] == 'storage_unavailable'
    assert h.row('registrations')['enabled'] == h.row('mail_pull_accounts')['local_enabled'] == 0
    h.activation_after = lambda: None
    assert h.restart().enable()['enabled'] is True
    assert h.activation_calls[0][1] == h.activation_calls[1][1]


def test_concurrent_human_retries_cannot_overwrite_newer_receipt(h):
    h.verified()
    nested = []
    def interleave():
        h.activation_after = lambda: None
        nested.append(h.client().enable())
    h.activation_after = interleave
    assert not h.client().enable()['enabled']
    assert nested[0]['enabled'] is True
    assert h.client().status()['enabled'] is True
    assert len(h.activation_operations) == 1


def test_status_and_resume_never_call_activation_or_reauthorize(h):
    h.verified()
    h.activation_after = lambda: (_ for _ in ()).throw(OSError())
    h.client().enable()
    client = h.restart(lambda *args: pytest.fail('passive recovery sent network request'))
    client.authorize = lambda: pytest.fail('status called human gate')
    assert client.resume()['activation_pending']
    assert client.status()['activation_pending']


def test_later_base_revoke_cannot_be_hidden_by_old_active_receipt(h):
    h.verified()
    assert h.client().enable()['enabled']
    install = h.row()['install_id']
    h.store.revoke_registration(install)
    assert h.restart().status()['reason'] == 'stale_generation'
    assert not h.client().enable()['enabled']
    assert len(h.activation_calls) == 1


@pytest.mark.parametrize('skew', [-299_000, 299_000])
def test_valid_service_clock_skew_on_active_since(h, skew):
    h.verified()
    # Service time is independently signed and bounded by the v2 skew check.
    original_response = h.response
    def response(value, headers, path, status=200, **kwargs):
        if path != PATH:
            return original_response(value, headers, path, status, **kwargs)
        local_now = h.now
        h.now += skew
        value = {**value, 'active_since_ms': h.now}
        result = original_response(value, headers, path, status, **kwargs)
        h.now = local_now
        return result
    h.response = response
    assert h.client().enable()['enabled'] is True


@pytest.mark.parametrize('field,value', [('activation_op', 'bad'), ('activation_attempts', True),
    ('activation_attempts', MAX_ATTEMPTS + 1), ('activation_digest', 'bad'), ('activation_authorized_ms', 0)])
def test_corrupt_activation_metadata_is_held_without_network(h, field, value):
    h.verified()
    h.activation_after = lambda: (_ for _ in ()).throw(OSError())
    h.client().enable()
    row = h.row()
    meta = json.loads(row['result_json'])
    meta[field] = value
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_flows SET result_json=?', (json.dumps(meta),))
    assert h.restart().status()['reason'] == 'schema_corrupt'
    assert h.client().enable()['reason'] == 'schema_corrupt'
    assert len(h.activation_calls) == 1


# Drive the actual Worker adapter over stdin/stdout against local SQLite;
# preserve Signup's hosted confirmation and independently implemented v2 crypto.
ACTIVATE_NODE = NODE_HANDLER.replace(
    'const response=await http.handle',
    "const {PullActivateHTTP}=await import('./pull-activate-http.js');\n"
    "const target=p.path==='/v1/pull/activate'?new PullActivateHTTP({db,serviceOrigin:p.origin,"
    "domain:'clayrune.example',serviceKeyId:'service-key',servicePrivateKey:p.service_key,clock:()=>p.now}):http;\n"
    'const response=await target.handle').replace(
        'const db={',
        "sql.function('strftime',(format,at)=>format==='%s'?String(Math.floor(p.now/1000)):"
        "`${String(Math.floor(p.now/1000)%60).padStart(2,'0')}.${String(p.now%1000).padStart(3,'0')}`);\n"
        'const db={')


def test_actual_worker_accepts_exact_activation_replay_after_lost_receipt(h):
    service_der = h.service_key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
    state = {'confirm': False, 'lose': True, 'calls': [], 'counts': []}
    def node_transport(method, path, headers, body):
        payload = {'db': str(h.folder / 'worker.sqlite'), 'origin': ORIGIN, 'now': h.now,
                   'service_key': encoded(service_der), 'method': method, 'path': path, 'headers': headers,
                   'body': base64.b64encode(body).decode('ascii'), 'confirm': state['confirm']}
        completed = subprocess.run(['node', '--input-type=module', '-e', ACTIVATE_NODE], input=json.dumps(payload),
                                   text=True, capture_output=True, cwd=ROOT / 'workers/agent-mail', timeout=20)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result['status'] == 200, result
        state['counts'].append(result['counts'])
        if path == PATH:
            state['calls'].append((dict(headers), body))
            if state['lose']:
                raise OSError('synthetic lost activation receipt')
        return result['status'], result['headers'], base64.b64decode(result['body'])
    assert h.client(node_transport).start(OWNER, 'v1')['state'] == 'pending'
    state['confirm'] = True
    assert h.restart(node_transport).resume()['state'] == 'verified'
    state['confirm'] = False
    assert h.client(node_transport).enable()['reason'] == 'connection_lost'
    assert h.restart(node_transport).resume()['activation_pending']
    assert len(state['calls']) == 1
    state['lose'] = False
    h.now += 1000
    result = h.restart(node_transport).enable()
    assert result['enabled'] is True, result
    assert state['calls'][0][1] == state['calls'][1][1]
    assert state['calls'][0][0]['x-mail-nonce'] != state['calls'][1][0]['x-mail-nonce']
    assert state['counts'][-1]['pull_operations'] == 2  # one start + one activate
