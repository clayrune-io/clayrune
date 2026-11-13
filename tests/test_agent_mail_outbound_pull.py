"""Pull authority/receipt races with synthetic keys, real verifier and no mail."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from test_agent_mail_outbound import out, kit, setup, env, prepare, request  # noqa: F401
from test_agent_mail_pull_receiver import INSTALL, MANIFEST
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_ownership import OwnershipHeld
from mc import state


@pytest.mark.parametrize('sql', [
    "UPDATE mail_pull_accounts SET local_enabled=0",
    "UPDATE mail_pull_accounts SET state='pending'",
    "UPDATE mail_pull_accounts SET state='verified'",
    "UPDATE mail_pull_accounts SET state='frozen'",
    "UPDATE mail_pull_accounts SET state='deleted'",
    "UPDATE mail_pull_accounts SET consent_version=''",
    "UPDATE mail_pull_generations SET state='fenced'",
    "UPDATE mail_pull_generations SET owner_generation=2",
    "UPDATE mail_pull_generations SET key_id='other-key'",
    "UPDATE mail_pull_generations SET seal_flow_id='00000000000000000000000000000000'",
    "UPDATE mail_pull_generations SET service_key_id='other-service'",
    "UPDATE mail_pull_generations SET manifest_digest='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
    "UPDATE registrations SET enabled=0",
    "UPDATE registrations SET generation=2",
])
def test_authority_refusal_cannot_create_intent_or_send(out, sql):
    with out.store._tx() as db:
        db.execute(sql)
    with pytest.raises(EnrollmentRefused):
        prepare(out)
    assert not out.recording.calls
    assert out.kit.rows('mail_outbound_intents') == []


def test_fence_is_denial_even_before_remote_ack(out):
    out.kit.fence_row()
    with pytest.raises(EnrollmentRefused, match='fenced'):
        prepare(out)
    assert not out.recording.calls


@pytest.mark.parametrize('mutation', ['digest', 'origin', 'expired', 'missing'])
def test_service_review_never_falls_back_to_cached_pin(out, mutation):
    if mutation == 'digest':
        out.kit.review = lambda: (out.kit.pin, '0' * 64)
    elif mutation == 'origin':
        out.kit.review = lambda: (replace(out.kit.pin, origin='https://other.example.test'), MANIFEST)
    elif mutation == 'expired':
        out.kit.now = out.kit.pin.expires_at_ms
    else:
        out.outbound.reviewed_service = None
    with pytest.raises(EnrollmentRefused):
        prepare(out)
    assert not out.recording.calls


@pytest.mark.parametrize('sql', [
    "UPDATE mail_pull_threads SET owner_generation=2",
    "UPDATE mail_pull_threads SET state='revoked'",
    "UPDATE mail_pull_threads SET continuation_digest='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
    "UPDATE mail_pull_threads SET project_id='other'",
    "UPDATE mail_pull_threads SET session_id='other'",
    "UPDATE mail_pull_threads SET thread_expires_ms=thread_expires_ms+1",
    "UPDATE mail_pull_threads SET dedupe_until_ms=dedupe_until_ms+1",
    "DELETE FROM mail_pull_threads",
    "UPDATE mail_continuations SET snapshot='{}'",
])
def test_exact_pull_thread_binding_required_at_send(out, sql):
    first = prepare(out)
    with out.store._tx() as db:
        db.execute(sql)
    with pytest.raises(EnrollmentRefused):
        out.outbound.send(first['intent_id'])
    assert not out.recording.calls


def test_epoch_change_revokes_prepared_intent_after_reenable(out):
    first = prepare(out)
    with out.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET consent_epoch=consent_epoch+1')
    assert out.outbound.send(first['intent_id'])['state'] == 'revoked'
    assert not out.recording.calls


@pytest.mark.parametrize('change', ['provider_empty', 'provider_blank', 'provider_control', 'thread',
                                     'intent', 'extra', 'signature', 'bare_uncertain', 'wrong_mid'])
def test_invalid_response_never_becomes_sent_or_replayed(out, change):
    first = prepare(out)
    def alter(status, headers, raw):
        value = json.loads(raw)
        if change == 'signature':
            return status, dict(headers, **{'x-mail-signature': 'bad'}), raw
        if change.startswith('provider_'):
            value['provider_id'] = {'provider_empty': '', 'provider_blank': ' ', 'provider_control': 'bad\n'}[change]
        elif change in ('thread', 'intent'):
            value[change + '_id'] = 'different'
        elif change == 'extra':
            value['to'] = 'other@example.test'
        elif change == 'bare_uncertain':
            status = 409
            value = {k: value[k] for k in ('v', 'code', 'request_id')}
            value['code'] = 'uncertain'
        else:
            value['message_id'] = 'other@example.test'
        _, _, h, request_raw = out.recording.calls[-1]
        return out.kit.response('/v1/pull/send', h, json.loads(request_raw), value, status)
    out.kit.change = alter
    result = out.outbound.send(first['intent_id'])
    assert result['state'] == 'uncertain' and result['message_id'] == ''
    assert out.outbound.send(first['intent_id']) == result
    assert len(out.recording.calls) == 1


@pytest.mark.parametrize('receipt', ['reserved', 'sending', 'uncertain', 'failed', 'revoked'])
def test_signed_nonaccepted_receipt_is_never_acceptance(out, receipt):
    first = prepare(out)
    out.recording.code = receipt
    result = out.outbound.send(first['intent_id'])
    assert result['state'] == 'uncertain' and result['transport_state'] == 'uncertain'
    assert result['message_id'] == ''
    assert out.outbound.send(first['intent_id']) == result
    assert len(out.recording.calls) == 1


def test_legacy_constructor_refuses_without_calling_private_authority(kit, setup, monkeypatch):
    core = kit.active()
    def forbidden(*args, **kwargs):
        pytest.fail('legacy authority called')
    for name in ('_enabled', '_row', '_current', '_fresh_candidate', 'prerequisites'):
        monkeypatch.setattr(core, name, forbidden)
    legacy = Outbound(core, enabled=True)
    with pytest.raises(EnrollmentRefused, match='legacy_retired'):
        legacy.prepare('proj1', setup.session['session_id'], request())


def test_dynamic_gate_applies_to_already_cached_object(out):
    flags = [True]
    out.outbound.enabled = lambda: flags[0]
    first = prepare(out)
    flags[0] = False
    with pytest.raises(EnrollmentRefused, match='outbound_disabled'):
        out.outbound.send(first['intent_id'])
    assert not out.recording.calls


def test_question_ownership_capacity_survives_disable_and_cleanup(out, monkeypatch):
    from mc.agent_mail_ownership import CAPACITY_KEY
    monkeypatch.setitem(state.CONFIG, CAPACITY_KEY, 1)
    question = {'questions': [{'question': 'First'}]}
    assert out.outbound.question_channel('proj1', 'session', question, 'agent_mail') == 'agent_mail'
    out.core.disable()
    out.outbound.cleanup()
    assert out.outbound.question_channel('proj1', 'session', question, 'legacy') == 'agent_mail'
    with pytest.raises(OwnershipHeld):
        out.outbound.question_channel('proj1', 'session', {'questions': [{'question': 'Second'}]}, 'legacy')


def test_reopened_sender_never_uses_current_owner_for_old_intent(out):
    first = prepare(out)
    with out.store._tx() as db:
        db.execute("UPDATE mail_pull_accounts SET owner_generation=2,owner_address='other@example.test',consent_epoch=2")
        db.execute('UPDATE mail_pull_generations SET owner_generation=2')
    assert out.kit.outbound().send(first['intent_id'])['state'] == 'revoked'
    assert not out.recording.calls


def test_fractional_clock_preserves_exact_pull_and_base_expiry(out):
    out.store.clock = lambda: out.kit.now / 1000 + 0.123456
    first = prepare(out)
    with out.store._read() as db:
        base = db.execute('SELECT expires_at FROM thread_bindings').fetchone()[0]
        pull = db.execute('SELECT thread_expires_ms FROM mail_pull_threads').fetchone()[0]
    assert base * 1000 == pull
    received = []
    out.recording.hook = lambda h, raw: received.append(out.kit.receive(json.loads(raw)))
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'
    assert len(received) == 1
    assert out.store.get_event(received[0][1])['session_id'] == out.session['session_id']
