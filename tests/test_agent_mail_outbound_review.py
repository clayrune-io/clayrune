"""Unit 7c review regressions; suite isolation must precede fixture imports."""
from dataclasses import replace
import os

if (os.environ.get('MC_REMOTE_ENABLED') != '0' or not os.environ.get('MC_DATA_DIR')
        or not os.environ.get('CLAYRUNE_HOME')):
    raise RuntimeError('Load tests/conftest.py isolation before application fixtures')

import pytest

from test_agent_mail_outbound import out, kit, setup, env, prepare  # noqa: F401
from test_agent_mail_outbound_questions import channel, raise_form, fire
from mc import question_channel as qc, state
from mc import agent_mail_outbound_questions as adapter
from mc.agent_mail_enrollment_keys import EnrollmentRefused


@pytest.mark.parametrize('enabled,has_factory', [(False, True), (False, False), (True, False)])
def test_legacy_revised_same_qid_sends_once_and_original_reply_reaches_answer(
        channel, monkeypatch, enabled, has_factory):
    fixture, timers, sent = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', enabled)
    if not has_factory:
        monkeypatch.setattr(adapter, 'factory', None)
    raise_form(fixture)
    qc.on_question_raised(fixture.session)
    assert len(timers) == 1
    fire(timers[0])
    assert len(sent) == 1
    qid = fixture.session['pending_questions'][0]['question_id']
    assert set(qc._outbox) == qc._delivered == {qid}
    fixture.session['pending_questions'][0]['questions'][0]['question'] = 'Revised day?'
    qc.on_question_raised(fixture.session)
    assert len(timers) == 1 and len(sent) == 1
    calls = []
    monkeypatch.setattr(qc, '_answer', lambda *args: calls.append(args) or True)
    subject, _, recipient, mid = sent[0]
    assert qc.handle_reply(subject, '1', message_id='reply@example.test',
                           in_reply_to=mid, from_header=recipient)
    assert calls == [(qid, 'Friday')]
    assert not fixture.recording.calls


@pytest.mark.parametrize('deadline', ['both', 'intent', 'thread'])
def test_thread_expiring_during_signing_must_not_send(out, monkeypatch, deadline):
    # Fenn's exact case is "both": 1s TTL and a 1.1s advance inside prove().
    out.store.config = replace(out.store.config, thread_ttl_seconds=100 if deadline == 'thread' else 1)
    intent = prepare(out)
    if deadline != 'both':
        with out.store._tx() as db:
            db.execute('UPDATE thread_bindings SET expires_at=?',
                       (out.store.clock() + (100 if deadline == 'intent' else 1),))
    original = out.core.seal.prove
    def slow_sign(*args, **kwargs):
        result = original(*args, **kwargs)
        out.kit.now += 1100
        return result
    monkeypatch.setattr(out.core.seal, 'prove', slow_sign)
    result = out.outbound.send(intent['intent_id'])
    assert not out.recording.calls
    assert result['state'] == result['transport_state'] == 'expired'
    with out.store._read() as db:
        row = db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?',
                         (intent['intent_id'],)).fetchone()
    assert row['payload'] is None and row['attempt'] == ''


def test_target_validity_is_rechecked_after_signing(out, monkeypatch):
    intent = prepare(out)
    original, proved = out.store._validate_target, []
    prove = out.core.seal.prove
    def sign(*args, **kwargs):
        result = prove(*args, **kwargs)
        proved.append(True)
        return result
    def target(*args, **kwargs):
        return 'thread_revoked' if proved else original(*args, **kwargs)
    monkeypatch.setattr(out.core.seal, 'prove', sign)
    monkeypatch.setattr(out.store, '_validate_target', target)
    with pytest.raises(EnrollmentRefused):
        out.outbound.send(intent['intent_id'])
    assert proved and not out.recording.calls
