"""Unit 6 synthetic SQLite + real follow-up routes, no mail or model calls."""
import io
import json
import threading
import time
from types import SimpleNamespace

from flask import Flask
import pytest

from test_runtime_completion_log import env, _mode_b_session  # noqa: F401
from mc import agent_mail_continuation as continuation, agent_mail_pipe, agent_runtime
from mc.agent_mail_extract import WARNING
from mc.agent_mail_questions import answers, consume
from mc.agent_mail_store import MailStore, SUBMITTED, TURN_COMPLETED, UNCERTAIN
from mc.agent_mail_target import MailHeld
from mc.agent_mail_wake import MailWake
from mc.delegation_delivery import DeliveryStore

TOKEN = 'synthetic-thread-token'
TEXT = 'Friday works for me.'


@pytest.fixture
def setup(env, monkeypatch):
    ar = env['ar']
    store = MailStore(env['tmp_path'] / 'mail.sqlite')
    store.register('installation')
    session = _mode_b_session(env, 'mail-session')
    session.update(status='idle', claude_session_id='native-original', project_generation=1,
                   proc=SimpleNamespace(pid=-991, poll=lambda: None, stdin=io.StringIO()),
                   stdin_lock=threading.Lock(), _agent_cwd=str(env['tmp_path']))
    transcript = env['tmp_path'] / 'native.jsonl'
    transcript.write_text('{"role":"assistant","text":"previous conversation"}')
    runtime = SimpleNamespace(transcript_path=lambda *args: transcript)
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda *args: runtime)
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: 1)
    monkeypatch.setattr(ar, '_session_proc_alive', lambda s: s.get('process_alive', False))
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: (None, None))
    monkeypatch.setattr(ar, '_resume_unresumable', lambda *a: False)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_observe_habit_cue', lambda *a: None)
    monkeypatch.setattr(ar, '_write_usage_breakdown_turn_start_checkpoint', lambda *a: None)
    monkeypatch.setattr(ar, '_clock_wrap', lambda p, s, text, msg: text)
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda **k: '')
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a: {'block': ''})
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(env['tmp_path'] / 'delivery.sqlite'))
    store.bind_thread('installation', TOKEN, 'proj1', session['session_id'])
    wake = MailWake(store, enabled=True)
    wake.bind(TOKEN)
    return SimpleNamespace(**dict(env, store=store, session=session, wake=wake,
                                  app=Flask(__name__), transcript=transcript, runtime=runtime))


def ready(setup, eid='mail-one', text=TEXT):
    setup.store.insert_event(event_id=eid, registration_id='installation', thread_token=TOKEN,
                             message_id=f'<{eid}@example.invalid>', body=text)
    claim = setup.store.claim_for_laundering()
    assert claim is not None
    setup.store.complete_laundering(eid, claim['fence_token'], dict(
        version=1, source='owner_mail_laundered', warning=WARNING, reply_text=text,
        ambiguous_or_mixed=False, quoted_content_present=False,
        forwarded_content_present=False, event_id=eid))


def wait_for(predicate):
    end = time.monotonic() + 2
    while not predicate() and time.monotonic() < end:
        time.sleep(.005)
    assert predicate()


def test_disabled_never_claims(setup):
    ready(setup)
    assert MailWake(setup.store).process_next(setup.app)['state'] == 'disabled'
    assert setup.store.get_event('mail-one')['state'] == 'ready'


def test_continuation_can_be_imported_first_in_a_cold_process():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
                             'import mc.agent_mail_continuation; import mc.agent_runtime'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_followup_writes_laundered_text_and_matching_completion(setup):
    ready(setup)
    result = setup.wake.process_next(setup.app)
    assert result['state'] == SUBMITTED
    payload = json.loads(setup.session['proc'].stdin.getvalue())
    assert TEXT in payload['message']['content']
    assert 'Owner mail reply' in payload['message']['content']
    assert 'previous conversation' not in payload['message']['content']  # stays native
    event = setup.store.get_event('mail-one')
    assert event['turn_id'] == setup.session['_delivery_attempt_id']
    setup.session['status'] = 'idle'
    continuation.completed(setup.session)
    assert setup.store.get_event('mail-one')['state'] == TURN_COMPLETED
    assert setup.wake.process_next(setup.app)['state'] == 'idle'


@pytest.mark.parametrize('change,reason', [
    ({'claude_session_id': 'different-native'}, 'conversation_identity_changed'),
    ({'status': 'stopped'}, 'session_closed'),
    ({'incognito': True}, 'incognito_not_persistent'),
    ({'_launch_uncertain': True}, 'provider_recovery_held'),
    ({'character': {'name': 'different'}}, 'persona_changed'),
    ({'_agent_cwd': 'missing-tree'}, 'worktree_unavailable'),
])
def test_holds_before_any_pipe_write(setup, change, reason):
    ready(setup)
    setup.session.update(change)
    result = setup.wake.process_next(setup.app)
    assert result['state'] == 'blocked' and result['reason'] == reason
    assert setup.session['proc'].stdin.getvalue() == ''


def test_missing_history_holds_before_write(setup):
    ready(setup)
    setup.transcript.unlink()
    assert setup.wake.process_next(setup.app)['reason'] == 'native_history_missing'
    assert setup.session['proc'].stdin.getvalue() == ''


def test_busy_keeps_durable_order_and_does_not_enqueue(setup):
    ready(setup)
    ready(setup, 'mail-two', 'Monday also works.')
    setup.session['status'] = 'running'
    assert setup.wake.process_next(setup.app)['state'] == 'ready'
    assert setup.store.get_event('mail-two')['state'] == 'ready'
    assert not setup.session.get('pending_followups')


def test_mail_note_keeps_plan_and_unrelated_question(setup):
    ready(setup)
    question = {'question_id': 'other', 'questions': [{'question': 'Where?'}]}
    setup.session.update(waiting_for_plan_approval=True, waiting_for_question=True,
                         pending_questions=[question], _notify_session='parent', _notify_session_sent=True)
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED
    assert setup.session['waiting_for_plan_approval']
    assert setup.session['pending_questions'] == [question]
    assert setup.session['_notify_session_sent'] is True


def test_no_ack_is_uncertain_then_late_receipt_completes_without_replay(setup, monkeypatch):
    gate = threading.Event()
    original = setup.session['proc'].stdin
    writes = []

    class SlowPipe:
        def write(self, payload):
            writes.append(payload)
            assert gate.wait(2)
            return original.write(payload)

        def flush(self):
            original.flush()

    setup.session['proc'].stdin = SlowPipe()
    monkeypatch.setattr(agent_mail_pipe, 'ACK_SECONDS', .02)
    ready(setup)
    try:
        assert setup.wake.process_next(setup.app)['state'] == UNCERTAIN
        assert len(writes) == 1
        assert setup.wake.process_next(setup.app)['state'] == 'idle'
        setup.session['status'] = 'idle'
        continuation.completed(setup.session)  # completion alone cannot accept
        assert setup.store.get_event('mail-one')['state'] == UNCERTAIN
    finally:
        gate.set()
    wait_for(lambda: setup.store.get_event('mail-one')['state'] == TURN_COMPLETED)
    assert len(writes) == 1 and TEXT in original.getvalue()


def test_same_persona_other_conversation_is_not_an_alias(setup):
    ready(setup)
    other = dict(setup.session, session_id='other', claude_session_id='other-native')
    setup.sessions['other'] = other
    setup.session['claude_session_id'] = 'lost-original'
    assert setup.wake.process_next(setup.app)['reason'] == 'conversation_identity_changed'
    assert other['proc'].stdin.getvalue() == ''


@pytest.mark.parametrize('text,questions,expected', [
    ('2', [{'options': [{'label': 'One'}, {'label': 'Two'}]}], [{'question': 0, 'options': ['Two']}]),
    ('maybe later', [{'options': [{'label': 'One'}]}], None),
    ('Friday works.', [{'question': 'When?'}], [{'question': 0, 'text': 'Friday works.'}]),
    ('2', [{'options': [{'label': 'A'}]}, {'options': [{'label': 'B'}]}], None),
    ('Q1: 1\nQ2: 1,2', [{'options': [{'label': 'A'}]}, {'multiSelect': True,
      'options': [{'label': 'X'}, {'label': 'Y'}]}],
     [{'question': 0, 'options': ['A']}, {'question': 1, 'options': ['X', 'Y']}]),
    ('1', [{'options': [{'label': '2'}, {'label': '1'}]}], None),
])
def test_question_boundaries(text, questions, expected):
    assert answers(text, questions) == expected


def test_question_revision_and_plan_gate_are_independent():
    snapshot = {'question_id': 'qid', 'questions': [{'options': [{'label': 'One'}]}]}
    changed = {'question_id': 'qid', 'questions': [{'options': [{'label': 'Two'}]}]}
    session = dict(pending_questions=[changed], waiting_for_question=True, waiting_for_plan_approval=True)
    assert consume(session, snapshot, '1') is False
    assert session['pending_questions'] == [changed]
    session['pending_questions'] = [snapshot]
    assert consume(session, snapshot, '1') is True
    assert session['waiting_for_question'] is False and session['waiting_for_plan_approval'] is True


@pytest.mark.parametrize('text,consume_expected', [('1', True), ('maybe Friday', False), ('2', False)])
def test_only_matching_question_is_consumed_after_real_write(setup, text, consume_expected):
    question = {'question_id': 'sent-form', 'questions': [{'options': [{'label': 'One'}]}]}
    setup.session.update(pending_questions=[question], waiting_for_question=True,
                         waiting_for_plan_approval=True)
    setup.store.bind_thread('installation', 'question-token', 'proj1', 'mail-session', kind='question')
    setup.wake.bind('question-token', question=question)
    setup.store.insert_event(event_id='question-reply', registration_id='installation',
                             thread_token='question-token', message_id='<question@example.invalid>', body=text)
    claim = setup.store.claim_for_laundering()
    setup.store.complete_laundering('question-reply', claim['fence_token'], dict(
        version=1, source='owner_mail_laundered', warning=WARNING, reply_text=text,
        ambiguous_or_mixed=False, quoted_content_present=False,
        forwarded_content_present=False, event_id='question-reply'))
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED
    wait_for(lambda: bool(setup.session.get('pending_questions')) != consume_expected)
    assert setup.session['waiting_for_plan_approval'] is True


def test_revocation_during_preparation_prevents_the_write(setup, monkeypatch):
    ready(setup)
    def revoke(*args):
        setup.store.revoke_registration('installation')
        return {'block': ''}
    monkeypatch.setattr(setup.ar._memory_turn, 'refresh_for_turn', revoke)
    assert setup.wake.process_next(setup.app)['state'] == 'revoked'
    assert setup.session['proc'].stdin.getvalue() == ''


def test_held_event_is_never_claimed_and_blocks_later_event(setup):
    ready(setup)
    claimed = setup.store.claim_for_submission()
    setup.store.fail('mail-one', claimed['fence_token'], 'review_required', retryable=False)
    ready(setup, 'mail-two')
    assert setup.wake.process_next(setup.app)['state'] == 'idle'
    assert setup.session['proc'].stdin.getvalue() == ''


def test_stored_binding_cannot_be_replaced_with_current_identity(setup):
    setup.session['claude_session_id'] = 'other-native'
    with pytest.raises(MailHeld, match='continuation_binding_conflict'):
        setup.wake.bind(TOKEN)


def test_schema_loss_fails_closed(setup):
    from mc.agent_mail_store import MailStoreError
    with setup.store._tx() as db:
        db.execute('DROP TABLE mail_turn_receipts')
    with pytest.raises(MailStoreError):
        MailStore(setup.store.path)


def test_restart_with_accepted_receipt_and_matching_completion_never_replays(setup):
    ready(setup)
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED
    token = setup.session['_delivery_attempt_id']
    setup.ar._save_agent_log('proj1', [dict(session_id='mail-session', provider='claude',
        claude_session_id='native-original', status='completed', delivery_attempt_id=token)])
    setup.sessions.clear()
    reopened = MailWake(MailStore(setup.store.path), enabled=True)
    assert reopened.process_next(setup.app)['state'] == 'idle'
    assert reopened.store.get_event('mail-one')['state'] == TURN_COMPLETED


def test_terminal_log_alone_never_accepts_unknown_delivery(setup, monkeypatch):
    gate = threading.Event()
    from test_runtime_delivery_truth import _Stdin
    setup.session['proc'].stdin = _Stdin(gate=gate)
    monkeypatch.setattr(agent_mail_pipe, 'ACK_SECONDS', .01)
    ready(setup)
    try:
        assert setup.wake.process_next(setup.app)['state'] == UNCERTAIN
        token = setup.session['_delivery_attempt_id']
        setup.ar._save_agent_log('proj1', [dict(session_id='mail-session', provider='claude',
            claude_session_id='native-original', status='completed', delivery_attempt_id=token)])
        reopened = MailWake(MailStore(setup.store.path), enabled=True)
        assert reopened.process_next(setup.app)['state'] == 'idle'
        assert reopened.store.get_event('mail-one')['state'] == UNCERTAIN
    finally:
        gate.set()
    wait_for(lambda: setup.store.get_event('mail-one')['state'] == SUBMITTED)


def test_v2_migration_preserves_event_but_never_infers_a_continuation(setup):
    ready(setup)
    with setup.store._tx() as db:
        db.execute('DROP TABLE mail_turn_receipts')
        db.execute('DROP TABLE mail_continuations')
        db.execute('PRAGMA user_version=2')
    reopened = MailWake(MailStore(setup.store.path), enabled=True)
    assert reopened.process_next(setup.app)['reason'] == 'continuation_binding_missing'
    assert reopened.store.get_event('mail-one', include_content=True)['body'] == TEXT


def test_canonical_owner_requires_same_native_and_context(setup):
    ready(setup)
    source = setup.sessions.pop('mail-session')
    owner = dict(source, session_id='canonical-owner')
    setup.sessions['canonical-owner'] = owner
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED
    with setup.store._read() as db:
        receipt = db.execute('SELECT session_id FROM mail_turn_receipts WHERE event_id=?', ('mail-one',)).fetchone()
    assert receipt['session_id'] == 'canonical-owner'


def test_unknown_http_mail_fields_cannot_construct_internal_scope(setup):
    assert continuation.current() is None
    # Only local service.activate can construct a scope; there is no parser
    # for request-provided mail-origin, event ids or exact-conversation flags.
    with setup.app.test_request_context('/api/project/proj1/agent/followup', method='POST',
            json={'_mail_origin': True, '_exact_conversation': 'anything', 'event_id': 'mail-one'}):
        assert continuation.current() is None


def test_legacy_event_cannot_acquire_current_identity_after_receipt(setup):
    ready(setup)
    with setup.store._tx() as db:
        db.execute('DELETE FROM mail_continuations')
    with pytest.raises(MailHeld, match='continuation_binding_too_late'):
        setup.wake.bind(TOKEN)


def test_mail_receipt_reservation_failure_precedes_agent_log_takeover(setup, monkeypatch):
    from mc.delivery_attempt import DeliveryAttempt
    setup.ar._log_agent_dispatch_pending(setup.session, strict=True)
    before = setup.ar._load_agent_log('proj1', strict=True)
    ready(setup)
    event = setup.store.claim_for_submission()
    with setup.store._read() as db:
        bound = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
    scope = continuation.Continuation(setup.wake, event, bound, setup.project,
                                      'mail-session', setup.session)
    def refuse(*args):
        raise OSError('synthetic receipt disk failure')
    monkeypatch.setattr(scope, 'reserve', refuse)
    with continuation.activate(scope), pytest.raises(OSError, match='receipt disk failure'):
        DeliveryAttempt.capture(setup.session)
    assert setup.ar._load_agent_log('proj1', strict=True) == before


def test_new_generation_and_revocation_purge_question_snapshot(setup):
    with setup.store._read() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_continuations').fetchone()[0] == 1
    setup.store.revoke_registration('installation')
    with setup.store._read() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_continuations').fetchone()[0] == 0


def test_postchild_factory_failure_cannot_roll_back_durable_admission(setup):
    from mc.delivery_attempt import DeliveryAttempt
    ready(setup)
    event = setup.store.claim_for_submission()
    with setup.store._read() as db:
        bound = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
    scope = continuation.Continuation(setup.wake, event, bound, setup.project,
                                      'mail-session', setup.session)

    @continuation.spawn_boundary
    def spawn(*args, **kwargs):
        with setup.store._read() as db:
            assert db.execute('SELECT started FROM mail_turn_receipts').fetchone()[0] == 1
        raise RuntimeError('synthetic failure after a possible child')

    with continuation.activate(scope):
        DeliveryAttempt('reserved-attempt').stamp(setup.session)
        with pytest.raises(RuntimeError, match='possible child'):
            spawn(['claude', '-r', 'native-original', '-p', 'text'],
                  engine='claude', session_id='mail-session', cwd=str(setup.tmp_path))
    with setup.store._read() as db:
        assert db.execute('SELECT started FROM mail_turn_receipts').fetchone()[0] == 1
    scope.unknown()
    assert setup.store.get_event('mail-one')['state'] == UNCERTAIN


@pytest.mark.parametrize('provider', ['codex', 'qwen', 'gemini'])
@pytest.mark.parametrize('ended', [False, True])
@pytest.mark.parametrize('trigger', ['manual', 'schedule'])
def test_native_runtime_followup_receives_exact_resume_and_text(setup, monkeypatch, provider, ended, trigger):
    from pathlib import Path
    from test_runtime_delivery_truth import _Stdin, _Proc
    from mc import session_mcp_launch
    runtime = {'codex': agent_runtime.CodexRuntime, 'qwen': agent_runtime.QwenRuntime,
               'gemini': agent_runtime.GeminiRuntime}[provider]()
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: Path('fake-provider'))
    monkeypatch.setattr(runtime, 'transcript_path', lambda *a: setup.transcript)
    monkeypatch.setattr(runtime, 'session_model', lambda handle: '')
    if provider == 'codex':
        monkeypatch.setattr(runtime, '_gate_model', lambda model: ('', ''))
    if provider == 'qwen':
        monkeypatch.setattr(runtime, '_settings_auth_env', lambda: {})
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda *a: runtime)
    monkeypatch.setattr(agent_runtime.vendor_fence, 'check_launch', lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime.vendor_fence, 'prepare_env', lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime.vendor_fence, 'prepare_command', lambda n, s, cmd, **k: cmd)
    monkeypatch.setattr(session_mcp_launch, 'prepare', lambda n, cmd, **k: (cmd, {}))
    monkeypatch.setattr(agent_runtime, '_mode_a_reader', lambda *a: None)
    monkeypatch.setattr(agent_runtime, '_kill_pid', lambda *a: None)
    if provider == 'gemini':
        monkeypatch.setattr(runtime, '_read_stream', lambda *a: None)
    monkeypatch.setattr(setup.ar._native_followup_context, 'refresh_stash', lambda *a, **k: None)
    monkeypatch.setattr(setup.ar._native_followup_context, 'message_for_turn', lambda p, s, m: m)
    monkeypatch.setattr(setup.ar, '_bridge_images_for_blind_model', lambda m, *a, **k: m)
    monkeypatch.setattr(setup.ar, '_followup_cwd_for', lambda *a: str(setup.tmp_path))
    setup.session.update(provider=provider, mode='A', provider_session_id='native-thread',
                         _system_prompt='', agent_model='', trigger_type=trigger,
                         trigger_id='schedule-one' if trigger == 'schedule' else '')
    setup.store.bind_thread('installation', 'native-token', 'proj1', 'mail-session')
    setup.wake.bind('native-token')
    setup.store.insert_event(event_id='native-mail', registration_id='installation',
                             thread_token='native-token', message_id='<native@example.invalid>', body=TEXT)
    claim = setup.store.claim_for_laundering()
    setup.store.complete_laundering('native-mail', claim['fence_token'], dict(
        version=1, source='owner_mail_laundered', warning=WARNING, reply_text=TEXT,
        ambiguous_or_mixed=False, quoted_content_present=False,
        forwarded_content_present=False, event_id='native-mail'))
    pipe = _Stdin()
    proc = _Proc(pipe)
    commands = []

    @continuation.spawn_boundary
    def spawn(cmd, *, deliver=None, **kwargs):
        commands.append(list(cmd))
        if deliver:
            deliver(proc)
        return proc

    monkeypatch.setattr(agent_runtime, 'spawn_engine_proc', spawn)
    if ended:
        setup.ar._save_agent_log('proj1', [dict(
            session_id='mail-session', provider=provider, provider_session_id='native-thread',
            status='completed', project_generation=1, character=None, source='',
            agent_cwd=str(setup.tmp_path), task='previous task', trigger_type=trigger,
            trigger_id=setup.session['trigger_id'])])
        del setup.sessions['mail-session']
    assert setup.wake.process_next(setup.app)['state'] == SUBMITTED
    assert len(commands) == 1 and 'native-thread' in commands[0]
    assert len(pipe.written) == 1 and TEXT in pipe.written[0] and pipe.closed
    resumed = setup.sessions['mail-session']
    assert resumed['trigger_type'] == trigger
    if provider == 'codex' and ended and trigger == 'schedule':
        assert resumed['_codex_unattended_sandbox'] is True
    resumed['status'] = 'completed'
    continuation.completed(resumed)
    assert setup.store.get_event('native-mail')['state'] == TURN_COMPLETED


@pytest.mark.parametrize('command', [['claude', '-p', 'text'],
                                   ['claude', '-r', 'other-native', '-p', 'text']])
def test_exact_constraint_refuses_before_provider_factory(setup, command):
    from mc.delivery_attempt import DeliveryAttempt
    ready(setup)
    event = setup.store.claim_for_submission()
    with setup.store._read() as db:
        bound = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
    scope = continuation.Continuation(setup.wake, event, bound, setup.project,
                                       'mail-session', setup.session)
    called = []

    @continuation.spawn_boundary
    def spawn(*args, **kwargs):
        called.append(args)

    with continuation.activate(scope):
        DeliveryAttempt('reserved-attempt').stamp(setup.session)
        with pytest.raises(MailHeld, match='exact_resume_required'):
            spawn(command, engine='claude', session_id='mail-session', cwd=str(setup.tmp_path))
    assert called == []
    with setup.store._read() as db:
        assert db.execute('SELECT started FROM mail_turn_receipts').fetchone()[0] == 0
