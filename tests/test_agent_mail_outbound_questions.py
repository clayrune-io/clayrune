"""Shared grace/channel selection and full question revision, no real mail."""
import json
import sqlite3
import threading
import time

import pytest

from test_agent_mail_outbound import out, kit, setup, env  # noqa: F401
from mc import agent_runtime, question_channel as qc, state
from mc import agent_mail_outbound_questions as adapter
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_questions import revision
from mc.agent_mail_extract import WARNING
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_store import SUBMITTED
from mc.agent_mail_wake import MailWake
from test_agent_mail_enrollment import ROOT
from test_agent_mail_ingress import envelope, signed


FORM = dict(questions=[dict(header='Date', question='Which day?', options=[dict(label='Friday'), dict(label='Monday')]),
                       dict(header='Note', question='Any detail?')])


@pytest.fixture
def channel(out, monkeypatch):
    timers, legacy = [], []
    qc._delivered.clear()
    qc._outbox.clear()
    qc._answered.clear()
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'question_channel_to', 'different-owner@example.test')
    monkeypatch.setattr(adapter, 'factory', lambda: out.outbound)
    monkeypatch.setattr(qc, '_send_email', lambda *args: legacy.append(args) or True)
    monkeypatch.setattr(qc, '_creds', lambda: pytest.fail('new mail read legacy credentials'))
    monkeypatch.setattr(qc.threading, 'Timer', lambda *args, **kwargs: (
        timers.append((args[0], args[1], kwargs['args'])) or
        type('Timer', (), dict(start=lambda self: None, daemon=True))()))
    monkeypatch.setattr(qc, 'grace_seconds', lambda project: 45)
    monkeypatch.setattr(qc, 'channel_for', lambda project: 'email')
    from mc.blueprints import project_routes
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: dict(id=pid, name='Test Project'))
    yield out, timers, legacy
    qc._delivered.clear()
    qc._outbox.clear()
    qc._answered.clear()


def raise_form(out, form=FORM):
    return agent_runtime.apply_mc_tool_blocks(out.session, '```mc:question\n' + json.dumps(form) + '\n```')


def fire(timer):
    _, callback, args = timer
    callback(*args)


def test_store_writer_must_not_block_unrelated_legacy_reply_poll(channel, monkeypatch):
    out, _, legacy = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    raise_form(out)
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    monkeypatch.setattr(qc, '_creds', lambda: None)
    selecting, polled = threading.Event(), threading.Event()
    errors = []
    original = out.outbound.question_channel

    def observed(*args):
        selecting.set()
        return original(*args)

    monkeypatch.setattr(out.outbound, 'question_channel', observed)

    def notify():
        try:
            qc.on_question_raised(out.session)
        except BaseException as exc:
            errors.append(exc)

    def poll():
        try:
            qc.poll_replies()
        except BaseException as exc:
            errors.append(exc)
        finally:
            polled.set()

    blocker = sqlite3.connect(out.store.path, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    notification = threading.Thread(target=notify)
    polling = threading.Thread(target=poll)
    notification.start()
    poll_started = False
    try:
        assert selecting.wait(2), 'notification did not reach production selector'
        polling.start()
        poll_started = True
        completed_without_database = polled.wait(0.75)
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
        notification.join(8)
        if poll_started:
            polling.join(8)
    assert not notification.is_alive() and not polling.is_alive()
    assert not errors
    assert not legacy and not out.recording.calls
    assert completed_without_database, 'SQLite contention stalls legacy reply polling'


@pytest.mark.parametrize('owner', ['agent_mail', 'legacy'])
def test_concurrent_same_question_selects_and_delivers_once(channel, monkeypatch, owner):
    out, timers, legacy = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    raise_form(out)  # parse without automatically notifying
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    selecting, release, second_started, duplicate_selection = (
        threading.Event() for _ in range(4))
    errors, selections = [], []
    original = out.outbound.question_channel

    def observed(*args):
        selections.append(args)
        if len(selections) > 1:
            duplicate_selection.set()
        selecting.set()
        if not release.wait(5):
            raise TimeoutError('test did not release selection')
        return original(*args[:-1], owner)

    monkeypatch.setattr(out.outbound, 'question_channel', observed)

    def notify(second=False):
        if second:
            second_started.set()
        try:
            qc.on_question_raised(out.session)
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=notify)
    second = threading.Thread(target=notify, args=(True,))
    first.start()
    second_launched = False
    try:
        assert selecting.wait(2)
        second.start()
        second_launched = True
        assert second_started.wait(2)
        # Give the competing notification a chance to reach selection while
        # the first is stalled; a non-atomic selector would enter twice.
        duplicate_selection.wait(0.25)
    finally:
        release.set()
        first.join(8)
        if second_launched:
            second.join(8)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert len(selections) == len(timers) == len(qc._outbox) == 1
    fire(timers[0])
    assert len(out.recording.calls) == (owner == 'agent_mail')
    assert len(legacy) == (owner == 'legacy')


def test_mc_question_parser_arms_shared_grace_once(channel):
    out, timers, legacy = channel
    assert raise_form(out)['paused']
    qc.on_question_raised(out.session)  # Claude's common turn boundary also calls this
    assert len(timers) == 1 and timers[0][0] == 45
    assert not out.recording.calls
    fire(timers[0])
    fire(timers[0])
    assert len(out.recording.calls) == 1 and not legacy
    assert 'accepted' in out.session['log_lines'][-1]
    with out.store._read() as db:
        bound = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
    assert bound['question'] == out.session['pending_questions'][-1]
    body = json.loads(out.recording.calls[0][3])['body']
    assert 'Q2. Any detail?' in body and '1. Friday' in body
    assert 'Project: proj1' in body and 'Conversation: fixture' in body


@pytest.mark.parametrize('action', ['attended', 'answered', 'revision', 'channel_off'])
def test_grace_rechecks_current_attendance_and_form(channel, monkeypatch, action):
    out, timers, legacy = channel
    if action == 'channel_off':
        monkeypatch.setattr(qc, 'channel_for', lambda project: 'off')
    raise_form(out)
    if action == 'channel_off':
        assert not timers
        return
    if action == 'attended':
        out.session['_last_sse_poll_time'] = time.time()
    elif action == 'answered':
        out.session['waiting_for_question'] = False
    else:
        out.session['pending_questions'][0]['questions'][0]['question'] = 'Changed revision'
    fire(timers[0])
    assert not out.recording.calls and not legacy


def test_same_id_revised_form_gets_distinct_immutable_thread(channel):
    out, timers, legacy = channel
    raise_form(out)
    first = json.loads(json.dumps(out.session['pending_questions'][0]))
    fire(timers[0])
    out.session['pending_questions'][0]['questions'][0]['question'] = 'Revised question'
    qc.on_question_raised(out.session)
    assert len(timers) == 2
    fire(timers[0])  # old timer cannot email the new form
    fire(timers[1])
    assert len(out.recording.calls) == 2 and not legacy
    a, b = [json.loads(call[3]) for call in out.recording.calls]
    assert a['intent_id'] != b['intent_id'] and a['thread_id'] != b['thread_id']
    assert revision(first) != revision(out.session['pending_questions'][0])


@pytest.mark.parametrize('failure', ['uncertain', 'exception', 'unavailable', 'disabled', 'blocked'])
def test_selected_new_channel_never_falls_back_to_legacy(channel, monkeypatch, failure):
    out, timers, legacy = channel
    raise_form(out)
    if failure == 'uncertain':
        out.recording.code = 'uncertain'
    elif failure == 'exception':
        out.recording.hook = lambda *args: (_ for _ in ()).throw(OSError('do not log private text'))
    elif failure == 'unavailable':
        monkeypatch.setattr(adapter, 'factory', None)
    elif failure == 'disabled':
        monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    else:
        out.core.disable()
    fire(timers[0])
    fire(timers[0])
    assert not legacy and len(out.recording.calls) <= 1
    expected = 'uncertain' if failure in ('uncertain', 'exception') else 'blocked'
    assert expected in out.session['log_lines'][-1]
    assert out.session['waiting_for_question']


def test_legacy_poll_cannot_resolve_new_question(channel):
    out, timers, _ = channel
    raise_form(out)
    fire(timers[0])
    qid = out.session['pending_questions'][0]['question_id']
    assert not qc.handle_reply(f'[Clayrune question] q:{qid[:8]}', '1',
                                message_id='reply@example.test', from_header='owner@example.test')
    assert out.session['waiting_for_question']
    assert qc.poll_replies() == 0  # no legacy credential read or IMAP connection


@pytest.mark.parametrize('change', ['disabled', 'uninstalled'])
@pytest.mark.parametrize('already_sent', [False, True])
def test_new_owner_reentry_after_adapter_unavailable(channel, monkeypatch, change, already_sent):
    out, timers, legacy = channel
    raise_form(out)
    if already_sent:
        fire(timers[0])
    original_keys = set(qc._outbox)
    if change == 'disabled':
        monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    else:
        monkeypatch.setattr(adapter, 'factory', None)
    qc.on_question_raised(out.session)  # repeat the real turn-boundary entry point
    assert set(qc._outbox) == qc._delivered == original_keys
    assert len(timers) == 1
    fire(timers[0])
    assert not legacy
    assert len(out.recording.calls) == int(already_sent)


@pytest.mark.parametrize('initial', ['disabled', 'uninstalled'])
@pytest.mark.parametrize('already_sent', [False, True])
def test_legacy_owner_reentry_after_adapter_available(channel, monkeypatch, initial, already_sent):
    out, timers, legacy = channel
    installed = adapter.factory
    if initial == 'disabled':
        monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    else:
        monkeypatch.setattr(adapter, 'factory', None)
    raise_form(out)
    qc.on_question_raised(out.session)
    if already_sent:
        fire(timers[0])
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    monkeypatch.setattr(adapter, 'factory', installed)
    qc.on_question_raised(out.session)
    qid = out.session['pending_questions'][0]['question_id']
    assert set(qc._outbox) == qc._delivered == {qid}
    assert 'form' not in qc._outbox[qid] and 'agent_mail' not in qc._outbox[qid]
    assert '_offline_question_channels' not in out.session
    assert len(timers) == 1
    if not already_sent:
        fire(timers[0])
    assert len(legacy) == 1 and not out.recording.calls


@pytest.mark.parametrize('change', ['disabled', 'uninstalled'])
def test_session_owner_survives_notification_cache_loss(channel, monkeypatch, change):
    out, timers, legacy = channel
    raise_form(out)
    qc._delivered.clear()
    qc._outbox.clear()
    if change == 'disabled':
        monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    else:
        monkeypatch.setattr(adapter, 'factory', None)
    qc.on_question_raised(out.session)
    assert len(qc._outbox) == 1
    assert next(iter(qc._outbox.values()))['agent_mail'] is True
    fire(timers[-1])
    assert not legacy and not out.recording.calls


def test_new_flag_does_not_arm_legacy_without_explicit_existing_hook(channel, monkeypatch):
    out, timers, _ = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', False)
    raise_form(out)
    assert not timers
    qc.on_question_raised(out.session)
    assert len(timers) == 1
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', True)
    # Selection is fixed at raise time, so a setting flip cannot add a second transport.
    key = next(iter(qc._outbox))
    assert qc._outbox[key].get('agent_mail', False) is False


def test_question_changed_between_prepare_and_send_is_held(channel):
    out, _, _ = channel
    raise_form(out)
    question = out.session['pending_questions'][0]
    prepared = out.outbound.prepare('proj1', out.session['session_id'], dict(
        request_id='manual-question', kind='question', subject='Question', body='Which day?'), question=question)
    question['questions'][0]['options'][0]['label'] = 'Tuesday'
    with pytest.raises(EnrollmentRefused, match='question_changed'):
        out.outbound.send(prepared['intent_id'])
    assert not out.recording.calls


def test_free_text_form_kept_exactly(channel):
    out, timers, legacy = channel
    raise_form(out, dict(questions=[dict(question='Tell me more')]))
    fire(timers[0])
    assert not legacy and len(out.recording.calls) == 1
    with out.store._read() as db:
        question = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])['question']
    assert question == out.session['pending_questions'][0]


@pytest.mark.parametrize('original', ['agent_mail', 'legacy'])
def test_channel_ownership_survives_restart_and_flag_flip(channel, monkeypatch, original):
    out, timers, legacy = channel
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', original == 'agent_mail')
    raise_form(out)
    qc.on_question_raised(out.session)
    form = out.session['pending_questions'][0]
    assert adapter.selected(out.session, form) == (original == 'agent_mail')
    # Runtime caches gone; only the SQLite channel receipt remains.
    out.session.pop('_offline_question_channels')
    qc._delivered.clear()
    qc._outbox.clear()
    monkeypatch.setitem(state.CONFIG, 'agent_mail_outbound_enabled', original != 'agent_mail')
    assert adapter.selected(out.session, form) == (original == 'agent_mail')


def test_immediate_free_text_reply_consumes_only_its_recorded_form(channel):
    out, timers, legacy = channel
    raise_form(out, dict(questions=[dict(question='What detail?')]))
    form = out.session['pending_questions'][0]
    captured = []
    def reply(headers, raw):
        value = json.loads(raw)
        ev, event_id = out.kit.receive(value, text='Friday works for me.')
        claim = out.store.claim_for_laundering()
        out.store.complete_laundering(event_id, claim['fence_token'], dict(
            version=1, source='owner_mail_laundered', warning=WARNING, reply_text=ev['text'],
            ambiguous_or_mixed=False, quoted_content_present=False,
            forwarded_content_present=False, event_id=event_id))
        assert MailWake(out.store, enabled=True).process_next(out.setup.app)['state'] == SUBMITTED
        captured.append(event_id)
    out.recording.hook = reply
    fire(timers[0])
    assert len(captured) == 1 and not legacy
    assert not out.session['waiting_for_question']
    assert form not in out.session['pending_questions']
