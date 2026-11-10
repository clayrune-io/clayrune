"""Fenn's unit-7b review probes, using fixed fixtures and real route policy."""
from dataclasses import replace
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask
import pytest

from test_agent_mail_enrollment import kit, manifest, OWNER, PUBLIC, MASTER, ORIGIN, b64  # noqa: F401
from mc import state
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_manifest import manifest_digest
from mc.agent_mail_enrollment_revocations import CancelledAssignment
from mc.agent_mail_service_manifest import verify_service_manifest
from mc.agent_mail_store import MailStoreError
from mc.blueprints import agent_mail_enrollment_routes as routes, local_auth


def test_corrupt_candidate_cannot_pin_key_absent_from_signed_manifest(kit):
    core = kit.pending()
    replacement = b64(Ed25519PrivateKey.from_private_bytes(b'x' * 32).public_key().public_bytes_raw())
    with core.store._tx() as db:
        candidate = json.loads(db.execute('SELECT candidate FROM mail_enrollment_flow').fetchone()[0])
        assert candidate['public_key'] == PUBLIC
        candidate['public_key'] = replacement
        db.execute('UPDATE mail_enrollment_flow SET candidate=?', (json.dumps(candidate),))
    core = kit.core()
    with pytest.raises(EnrollmentRefused):
        core.enable()
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()
    assert not core.store.registration_state('install1')['enabled']


def test_disable_during_service_assignment_queues_actual_remote_identity(kit):
    core = kit.core()
    remote_identity = 'service_assigned_install'
    kit.adapter.pending_hook = lambda value: replace(value, install=remote_identity)
    kit.adapter.begin_hook = lambda: kit.core().disable()
    with pytest.raises(EnrollmentRefused, match='lifecycle_raced'):
        core.start(OWNER, *manifest())
    core = kit.core()
    core.retry_revocations()
    assert (remote_identity, 1) in kit.adapter.revokes
    assert ('install1', 1) not in kit.adapter.revokes
    assert core.status()['revocations_pending'] == 0


def test_cancel_before_remote_begin_can_start_fresh_first_generation(kit):
    core = kit.core()
    kit.master = None
    with pytest.raises(EnrollmentRefused, match='vault_locked_or_unconfigured'):
        core.start(OWNER, *manifest())
    assert kit.adapter.begins == []
    core.disable()
    core.retry_revocations()
    kit.master = MASTER
    kit.adapter.pending_hook = lambda value: replace(value, install='first_service_install', generation=1)
    assert kit.core().start(OWNER, *manifest())['stage'] == 'pending'


def test_forged_origin_must_not_override_unattended_refusal_with_valid_proof(kit, monkeypatch):
    # Dave's refined decision exempts disable/revoke; the original probe's
    # valid-passcode forged-Origin request now targets enable instead.
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    monkeypatch.setattr(state, 'agent_sessions', {
        'review-fixture': {'status': 'running', 'trigger_type': 'dispatch'}})
    # The old helper holds the same registry object; exercise it too pre-fix.
    from mc import unattended
    monkeypatch.setattr(unattended, 'agent_sessions', state.agent_sessions)
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'human-fixture')
    core = kit.pending()
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=lambda: core))
    response = app.test_client().post(routes.BASE + '/enable', json={'passcode': 'human-fixture'},
                                      headers={'Origin': 'http://localhost:5199'})
    assert response.status_code == 403, response.json
    assert not core.status()['enabled']


@pytest.mark.parametrize('operation', ['verify', 'enable'])
@pytest.mark.parametrize('damage', ['public_key', 'fingerprint', 'key_id', 'expires_at_ms',
                                  'signed_payload', 'signature', 'digest', 'approval', 'all_cache_fields'])
def test_manifest_or_candidate_corruption_fails_closed_at_both_authority_steps(kit, operation, damage):
    core = kit.pending()
    replacement = b64(Ed25519PrivateKey.from_private_bytes(b'x' * 32).public_key().public_bytes_raw())
    with core.store._tx() as db:
        row = dict(db.execute('SELECT * FROM mail_enrollment_flow').fetchone())
        cached = json.loads(row['candidate'])
        evidence = json.loads(row['manifest'])
        if damage == 'signed_payload':
            evidence['payload'][7] = replacement
            db.execute('UPDATE mail_enrollment_flow SET manifest=?', (json.dumps(evidence),))
        elif damage == 'signature':
            evidence['signature'] = b64(b'0' * 64)
            db.execute('UPDATE mail_enrollment_flow SET manifest=?', (json.dumps(evidence),))
        elif damage in {'digest', 'approval'}:
            field = 'manifest_digest' if damage == 'digest' else 'confirmed_digest'
            db.execute(f'UPDATE mail_enrollment_flow SET {field}=?', ('a' * 64,))
        else:
            changes = {'public_key': replacement, 'fingerprint': 'a' * 64,
                       'key_id': 'replacement', 'expires_at_ms': row['manifest_checked_ms'] + 300000}
            cached.update(changes if damage == 'all_cache_fields' else {damage: changes[damage]})
            db.execute('UPDATE mail_enrollment_flow SET candidate=?', (json.dumps(cached),))
    core = kit.core()
    before = len(kit.adapter.verifies)
    with pytest.raises(EnrollmentRefused, match='manifest_evidence_invalid'):
        core.verify('code') if operation == 'verify' else core.enable()
    assert len(kit.adapter.verifies) == before
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()
    assert not core.store.registration_state('install1')['enabled']
    # Integrity failure cannot disable the human kill switch.
    assert core.disable()['stage'] == 'disabled'


def test_fresh_signed_replacement_cannot_reuse_old_human_approval(kit):
    from dataclasses import asdict
    core = kit.pending()
    replacement = b64(Ed25519PrivateKey.from_private_bytes(b'x' * 32).public_key().public_bytes_raw())
    payload, signature = manifest(sequence=2, public=replacement)
    candidate = verify_service_manifest(payload, signature, root_public_key=PUBLIC,
        service_origin=ORIGIN, store=core.manifests, now_ms=kit.now, enabled=True)
    with core.store._tx() as db:
        db.execute('UPDATE mail_enrollment_flow SET manifest=?,manifest_digest=?,candidate=?',
                   (json.dumps({'payload': payload, 'signature': signature}), manifest_digest(payload),
                    json.dumps(asdict(candidate))))
    with pytest.raises(EnrollmentRefused, match='manifest_evidence_invalid'):
        kit.core().enable()
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()


def test_disclosure_confirmation_and_pin_share_authenticated_evidence(kit):
    core = kit.pending()
    disclosure = core.review()
    assert disclosure['manifest_digest'] == manifest_digest(manifest()[0])
    assert core.enable()['service_fingerprint'] == disclosure['service_fingerprint']
    with core.store._read() as db:
        row = db.execute('SELECT manifest_digest,confirmed_digest FROM mail_enrollment_flow').fetchone()
        pin = db.execute('SELECT public_key FROM mail_service_pins').fetchone()
    assert row[0] == row[1] == disclosure['manifest_digest']
    assert pin[0] == PUBLIC


def test_corruption_during_verify_cannot_accept_previously_valid_receipt(kit):
    core = kit.pending()
    def corrupt():
        with core.store._tx() as db:
            db.execute("UPDATE mail_enrollment_flow SET candidate='{}'")
    kit.adapter.verify_hook = corrupt
    with pytest.raises(EnrollmentRefused, match='lifecycle_raced'):
        core.verify('code')
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()


@pytest.mark.parametrize('lost_response', [False, True])
def test_flow_cancel_resolves_assignment_after_restart_even_with_lost_response(kit, lost_response):
    core = kit.core()
    kit.adapter.pending_hook = lambda value: replace(value, install='real_remote', generation=7)
    def remote_hook():
        kit.core().disable()
        if lost_response:
            raise OSError('response lost after service assigned')
    kit.adapter.begin_hook = remote_hook
    reason = 'remote_begin_retryable' if lost_response else 'lifecycle_raced'
    with pytest.raises(EnrollmentRefused, match=reason):
        core.start(OWNER, *manifest())
    kit.master = None  # cancellation must not require the old seal/vault
    core = kit.core()
    kit.adapter.fail_cancel = True
    with pytest.raises(EnrollmentRefused, match='remote_cancel_retryable'):
        core.retry_revocations()
    assert core.status()['revocations_pending'] == 1 and kit.adapter.revokes == []
    kit.adapter.fail_cancel, kit.adapter.fail_revoke = False, True
    with pytest.raises(EnrollmentRefused, match='remote_revoke_retryable'):
        core.retry_revocations()
    with core.store._read() as db:
        row = dict(db.execute('SELECT * FROM mail_enrollment_revocations').fetchone())
    assert (row['install'], row['generation'], row['resolved'], row['done']) == ('real_remote', 7, 1, 0)
    cancels = len(kit.adapter.cancels)
    kit.adapter.fail_revoke = False
    assert kit.core().retry_revocations()['revocations_pending'] == 0
    assert len(kit.adapter.cancels) == cancels
    assert kit.adapter.revokes == [('real_remote', 7), ('real_remote', 7)]
    assert not kit.core().status()['enabled']


def test_unresolved_cancel_mismatch_cannot_complete_known_identity(kit, monkeypatch):
    core = kit.active()
    core.disable()
    monkeypatch.setattr(kit.adapter, 'cancel_flow', lambda flow: CancelledAssignment('wrong_remote', 1))
    with pytest.raises(EnrollmentRefused, match='remote_cancel_scope_mismatch'):
        kit.core().retry_revocations()
    assert core.status()['revocations_pending'] == 1 and kit.adapter.revokes == []


def test_many_never_sent_cancellations_preserve_generation_one(kit):
    core = kit.core()
    kit.master = None
    flows = set()
    for _ in range(3):
        with pytest.raises(EnrollmentRefused, match='vault_locked_or_unconfigured'):
            core.start(OWNER, *manifest())
        with core.store._read() as db:
            flows.add(db.execute('SELECT flow FROM mail_enrollment_flow').fetchone()[0])
        assert core.disable()['revocations_pending'] == 0
        assert core.retry_revocations()['stage'] == 'disabled'
        assert core.store.registration_state('install1')['generation'] == 1
    assert len(flows) == 3
    assert kit.adapter.begins == kit.adapter.cancels == kit.adapter.revokes == []
    kit.master = MASTER
    kit.adapter.pending_hook = lambda value: replace(value, install='first_service', generation=1)
    core.start(OWNER, *manifest())
    core.confirm(core.review())
    core.verify('code')
    assert core.enable()['generation'] == 1


def test_empty_remote_cancel_does_not_revoke_placeholder_and_seal_binding_stays_stable(kit):
    core = kit.core()
    kit.adapter.fail_begin = True
    with pytest.raises(EnrollmentRefused, match='remote_begin_retryable'):
        core.start(OWNER, *manifest())
    core.disable()
    assert core.retry_revocations()['revocations_pending'] == 0
    assert kit.adapter.revokes == []
    kit.adapter.fail_begin = False
    kit.adapter.pending_hook = lambda value: replace(value, install='actual_first_service', generation=1)
    core.start(OWNER, *manifest())
    core.confirm(core.review())
    core.verify('code')
    assert core.enable()['generation'] == 1
    with core.store._read() as db:
        row = dict(db.execute('SELECT * FROM mail_enrollment_flow').fetchone())
    assert row['seal_generation'] == 2 and row['generation'] == 1


@pytest.fixture
def real_web(kit, monkeypatch):
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'human-fixture')
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=kit.core))
    return app.test_client()


@pytest.mark.parametrize('action', sorted(routes.ENABLE_ACTIONS))
@pytest.mark.parametrize('trigger', ['dispatch', 'schedule', '', None])
def test_every_enable_direction_action_ignores_forged_caller_claims(real_web, action, trigger):
    state.agent_sessions['blocker'] = {'status': 'running', 'trigger_type': trigger}
    response = real_web.post(routes.BASE + '/' + action,
        json={'passcode': 'human-fixture', 'trigger_type': 'manual', 'unattended': False,
              'session_id': 'manual-session', 'source': 'ui'},
        headers={'Origin': 'http://localhost:5199', 'Cookie': 'human=1', 'X-Session-Id': 'manual-session'})
    assert response.status_code == 403
    assert response.json == {'error': 'human_required', 'blocking_sessions': [
        {'session_id': 'blocker', 'trigger': trigger or 'unknown'}]}


def test_refusal_lists_all_running_nonmanual_and_missing_trigger_sessions(real_web):
    state.agent_sessions.update({
        'z-dispatch': {'status': 'running', 'trigger_type': 'dispatch'},
        'a-unknown': {'status': 'running'},
        'idle': {'status': 'idle', 'trigger_type': 'dispatch'},
        'human': {'status': 'running', 'trigger_type': 'manual'},
    })
    response = real_web.post(routes.BASE + '/enable', json={'passcode': 'human-fixture'})
    assert response.status_code == 403
    assert response.json['blocking_sessions'] == [
        {'session_id': 'a-unknown', 'trigger': 'unknown'},
        {'session_id': 'z-dispatch', 'trigger': 'dispatch'}]


@pytest.mark.parametrize('origin', [False, True])
def test_disable_and_revoke_remain_open_to_passcode_holder_while_sessions_run(kit, real_web, origin):
    core = kit.active()
    state.agent_sessions['busy'] = {'status': 'running', 'trigger_type': 'dispatch'}
    state.agent_sessions['unknown'] = {'status': 'running'}
    headers = {'Origin': 'http://localhost:5199'} if origin else {}
    for action in ('disable', 'retry-revocations'):
        refused = real_web.post(routes.BASE + '/' + action, json={}, headers=headers)
        assert refused.status_code == 403
    assert core.status()['enabled']
    response = real_web.post(routes.BASE + '/disable', json={'passcode': 'human-fixture'}, headers=headers)
    assert response.status_code == 200 and response.json['revocations_pending'] == 1
    assert not core.status()['enabled']
    response = real_web.post(routes.BASE + '/retry-revocations', json={'passcode': 'human-fixture'}, headers=headers)
    assert response.status_code == 200 and response.json['revocations_pending'] == 0
    assert kit.adapter.revokes == [('install1', 1)]


@pytest.mark.parametrize('sessions', [{}, {
    'manual': {'status': 'running', 'trigger_type': 'manual'},
    'idle': {'status': 'idle', 'trigger_type': 'dispatch'},
    'finished': {'status': 'complete'},
}])
def test_idle_manual_only_or_no_sessions_allow_valid_human_enable(kit, real_web, sessions):
    kit.pending()
    state.agent_sessions.update(sessions)
    response = real_web.post(routes.BASE + '/enable', json={'passcode': 'human-fixture'})
    assert response.status_code == 200 and response.json['enabled']


def test_original_origin_exemption_is_unchanged_for_other_callers(real_web, monkeypatch):
    from mc import unattended
    monkeypatch.setattr(unattended, 'agent_sessions', {
        'dispatch': {'status': 'running', 'trigger_type': 'dispatch'}})
    with real_web.application.test_request_context(headers={'Origin': 'http://localhost:5199'}):
        assert unattended.is_unattended_caller() is False
    with real_web.application.test_request_context():
        assert unattended.is_unattended_caller() is True


def test_current_floor_digest_is_required_even_when_sequence_is_unchanged(kit):
    core = kit.pending()
    with core.store._tx() as db:
        db.execute('UPDATE mail_enrollment_manifests SET digest=?', ('a' * 64,))
    with pytest.raises(EnrollmentRefused, match='manifest_stale'):
        kit.core().enable()
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()


def test_cancel_reconciliation_write_fault_preserves_unresolved_work(kit):
    core = kit.core()
    kit.adapter.pending_hook = lambda value: replace(value, install='actual_remote')
    kit.adapter.begin_hook = lambda: kit.core().disable()
    with pytest.raises(EnrollmentRefused, match='lifecycle_raced'):
        core.start(OWNER, *manifest())
    with core.store._tx() as db:
        db.execute("CREATE TRIGGER fail_resolve BEFORE UPDATE ON mail_enrollment_revocations "
                   "BEGIN SELECT RAISE(ABORT,'storage fault'); END")
    with pytest.raises(MailStoreError):
        core.retry_revocations()
    assert core.status()['revocations_pending'] == 1 and kit.adapter.revokes == []
    with core.store._tx() as db:
        db.execute('DROP TRIGGER fail_resolve')
    assert kit.core().retry_revocations()['revocations_pending'] == 0
    assert kit.adapter.revokes == [('actual_remote', 1)]


def test_cancel_fence_prevents_a_delayed_begin_after_queue_completion(kit):
    core = kit.active()
    context, secret = kit.adapter.begins[0]
    core.disable()
    core.retry_revocations()
    with pytest.raises(OSError, match='flow cancelled'):
        kit.adapter.begin(context, secret)
    assert kit.core().status()['stage'] == 'disabled'


def test_old_candidate_schema_is_refused_without_reset_or_migration(kit):
    core = kit.core()
    with core.store._tx() as db:
        db.execute('UPDATE mail_enrollment_version SET version=1')
    with pytest.raises(EnrollmentRefused, match='lifecycle_schema_corrupt'):
        kit.core()
