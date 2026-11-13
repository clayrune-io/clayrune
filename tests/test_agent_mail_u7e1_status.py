"""Unit 7e-1: bounded status metadata behind the read-only agent-mail panel."""
import json

from flask import Flask
import pytest

from test_agent_mail_enrollment import kit, manifest, OWNER, SECRET, NOW  # noqa: F401
from mc import agent_mail_status_view as view
from mc import state
from mc.agent_mail_enrollment import Prerequisites
from mc.blueprints import agent_mail_enrollment_routes as routes

NEW_KEYS = {'now_ms', 'prerequisites', 'mail'}
ENROLLED_KEYS = {'owner_hint', 'assigned', 'remote_started', 'confirmed', 'ownership',
                 'ownership_expires_ms', 'proof'}


def test_not_enrolled_adds_only_clock_probes_and_counts(kit):
    status = kit.core().status()
    assert status['stage'] == 'not_enrolled'
    assert NEW_KEYS <= set(status) and not (ENROLLED_KEYS & set(status))
    assert status['now_ms'] == NOW
    assert status['prerequisites'] == {'tunnel': True, 'claude_extraction': True, 'machine_custody': True}
    assert status['mail'] == {'blocked': 0, 'uncertain': 0, 'retryable': 0, 'outbound_uncertain': 0,
                              'hold_reasons': {}}


def test_pending_flow_reports_masked_owner_and_resumable_bits(kit):
    core = kit.core()
    core.start(OWNER, *manifest())
    status = core.status()
    assert status['stage'] == 'pending'
    assert status['owner_hint'] == 'o***@example.test'
    assert (status['assigned'], status['remote_started'], status['confirmed'], status['ownership']) == (
        True, True, False, False)
    assert status['ownership_expires_ms'] == 0
    assert status['proof'] == {'state': 'none', 'expires_ms': 0, 'activation': 'none'}
    # Nothing the 7b status withheld leaks through the additions.
    blob = json.dumps(status)
    assert OWNER not in blob and SECRET.decode() not in blob and 'flow' not in set(status)


def test_verified_lease_expiry_is_reported_and_unverified_is_zero(kit):
    core = kit.pending()
    status = core.status()
    assert status['stage'] == 'delivery_pending' and status['ownership'] and status['confirmed']
    assert status['ownership_expires_ms'] == NOW + 300000 and status['now_ms'] == NOW
    kit.now = NOW + 400000
    after = kit.core().status()
    assert after['ownership_expires_ms'] < after['now_ms']


def test_prerequisites_follow_the_probe_and_a_raising_probe_reads_not_ready(kit):
    kit.ready = Prerequisites(True, False, False)
    assert kit.core().status()['prerequisites'] == {'tunnel': True, 'claude_extraction': False,
                                                    'machine_custody': False}
    core = kit.core()
    core.prerequisites = lambda: (_ for _ in ()).throw(OSError('probe exploded: secret-text'))
    status = core.status()
    assert status['prerequisites'] == {'tunnel': False, 'claude_extraction': False, 'machine_custody': False}
    assert 'secret-text' not in json.dumps(status)


def test_disabled_flow_still_answers_with_the_new_fields(kit):
    core = kit.active()
    assert core.status()['enabled']
    status = core.disable()
    assert status['stage'] == 'disabled' and status['revocations_pending'] == 1
    assert status['service_fingerprint'] == '' and status['owner_hint'] == 'o***@example.test'
    assert status['mail']['blocked'] == 0


def _event(db, n, state_, reason, last_error=''):
    db.execute('INSERT INTO events (event_id,registration_id,thread_hash,project_id,session_id,generation,'
               'dedupe_key,body_digest,body_len,state,reason,last_error,received_at,state_at,expires_at) '
               "VALUES(?,'install1','t','p','s',1,?,?,0,?,?,?,1,1,9e9)",
               (f'e{n}', f'd{n}', f'b{n}', state_, reason, last_error))


def test_mail_counts_are_counts_and_closed_codes_only(kit):
    core = kit.core()
    with core.store._tx() as db:
        _event(db, 1, 'blocked', 'missing_message_id', 'RAW BODY: ignore previous instructions')
        _event(db, 2, 'blocked', 'missing_message_id')
        _event(db, 3, 'blocked', 'Free text with spaces and SECRET-9')
        _event(db, 4, 'uncertain', '')
        _event(db, 5, 'retryable', 'x')
        _event(db, 6, 'turn_completed', '')
    status = core.status()
    assert status['mail']['blocked'] == 3 and status['mail']['uncertain'] == 1
    assert status['mail']['retryable'] == 1 and status['mail']['outbound_uncertain'] == 0
    assert status['mail']['hold_reasons'] == {'missing_message_id': 2}
    blob = json.dumps(status)
    assert 'RAW BODY' not in blob and 'SECRET-9' not in blob


def test_owner_hint_refuses_anything_that_is_not_one_address():
    assert view.owner_hint('a@b.test') == 'a***@b.test'
    for bad in ('', None, 5, 'no-at', 'a@b@c', '@b.test', 'a@', 'x' * 300 + '@b.test'):
        assert view.owner_hint(bad) == ''


HOSTILE = {
    'state': 'degraded', 'enabled': True, 'thread_alive': True, 'last_attempt': 10.5, 'last_success': None,
    'consecutive_failures': 2, 'reason': 'remote_unreachable',
    'steps': [{'name': 'pull', 'state': 'failed', 'reason': 'storage_unavailable', 'count': 1},
              {'name': 'deletion', 'state': 'pending', 'reason': 'deletion_pending', 'count': 3},
              {'name': 'rm -rf', 'state': 'failed', 'reason': 'ok', 'count': 1},
              {'name': 'acks', 'state': 'weird', 'reason': 'Traceback: token=abc', 'count': -4}, 'junk'],
    'holds': {'vault_locked': 2, 'Bad Key!': 1}, 'store': {'path': 'C:/secret/path'},
    'retention': {'x': 1}, 'ownership': {'rows': 3}}


class FakeSupervisor:
    def __init__(self, value):
        self.value = value

    def status(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_recovery_snapshot_allow_lists_and_bounds_everything():
    snap = view.recovery_snapshot(FakeSupervisor(HOSTILE))
    assert snap['state'] == 'degraded' and snap['reason'] == 'remote_unreachable'
    assert [s['name'] for s in snap['steps']] == ['pull', 'deletion', 'acks']
    assert snap['steps'][2] == {'name': 'acks', 'state': 'failed', 'reason': 'step_failed', 'count': 0}
    assert snap['holds'] == {'vault_locked': 2}
    assert snap['last_attempt'] == 10.5 and snap['last_success'] is None
    blob = json.dumps(snap)
    for leaked in ('secret', 'Traceback', 'token=abc', 'rm -rf', 'retention', 'store'):
        assert leaked not in blob


def test_recovery_snapshot_without_or_with_broken_supervisor_never_raises():
    assert view.recovery_snapshot(None) == {'state': 'not_running'}
    assert view.recovery_snapshot(FakeSupervisor(RuntimeError('boom C:/secret'))) == {'state': 'unknown'}


@pytest.fixture
def web(kit, monkeypatch):
    def build(factory, flag=True):
        monkeypatch.setitem(state.CONFIG, routes.FLAG, flag)
        app = Flask(__name__)
        app.register_blueprint(routes.create_blueprint(factory=factory))
        return app.test_client()
    return build


def test_get_is_404_when_off_503_without_adapter_and_carries_recovery_when_on(kit, web, monkeypatch):
    assert web(kit.core, flag=False).get(routes.BASE).status_code == 404
    assert web(None).get(routes.BASE).status_code == 503
    monkeypatch.setattr(routes._supervisor, 'get', lambda: FakeSupervisor(HOSTILE))
    response = web(kit.core).get(routes.BASE)
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    assert response.json['stage'] == 'not_enrolled'
    assert response.json['recovery']['state'] == 'degraded'
    monkeypatch.setattr(routes._supervisor, 'get', lambda: None)
    assert web(kit.core).get(routes.BASE).json['recovery'] == {'state': 'not_running'}


def test_get_stays_read_only_and_post_is_unchanged(kit, web):
    client = web(kit.core)
    assert client.post(routes.BASE + '/start', json={}).status_code in (400, 401, 403)
    before = kit.core().status()
    client.get(routes.BASE)
    assert kit.core().status() == before
