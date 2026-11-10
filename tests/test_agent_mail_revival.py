"""Exact ended-session mail delivery through the existing Claude revive path."""
import json
import threading
from types import SimpleNamespace

from flask import Flask
import pytest

from test_runtime_delivery_truth import app_env, revive_env, _Proc, _Stdin, CSID  # noqa: F401
from mc import agent_mail_continuation as continuation, agent_mail_pipe, agent_runtime
from mc.agent_mail_extract import WARNING
from mc.agent_mail_store import MailStore
from mc.agent_mail_wake import MailWake

TEXT = 'Friday works for me.'


@pytest.fixture
def mail_revival(revive_env, tmp_path, monkeypatch):
    ar, project = revive_env['ar'], revive_env['project']
    sid = 'revsid000001'
    source = dict(ar._load_agent_log('p1')[0], project_id='p1', status='completed',
                  project_generation=1, _agent_cwd=project['project_path'])
    revive_env['sessions'][sid] = source
    history = tmp_path / 'native.jsonl'
    history.write_text('{"type":"assistant","text":"retained history"}')
    runtime = SimpleNamespace(transcript_path=lambda *a: history)
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda *a: runtime)
    monkeypatch.setattr(ar, '_resume_unresumable', lambda *a: False)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_observe_habit_cue', lambda *a: None)
    monkeypatch.setattr(ar.get_manager('p1'), 'ensure_guardian', lambda: None)
    store = MailStore(tmp_path / 'mail.sqlite')
    store.register('installation')
    store.bind_thread('installation', 'thread-token', 'p1', sid)
    wake = MailWake(store, enabled=True)
    wake.bind('thread-token')
    del revive_env['sessions'][sid]
    store.insert_event(event_id='reply', registration_id='installation', thread_token='thread-token',
                       message_id='<reply@example.invalid>', body=TEXT)
    claim = store.claim_for_laundering()
    store.complete_laundering('reply', claim['fence_token'], dict(
        version=1, source='owner_mail_laundered', warning=WARNING, reply_text=TEXT,
        ambiguous_or_mixed=False, quoted_content_present=False,
        forwarded_content_present=False, event_id='reply'))
    commands = []

    @continuation.spawn_boundary
    def spawn(cmd, **kwargs):
        commands.append((list(cmd), kwargs))
        return revive_env['spawned']['proc']

    monkeypatch.setattr(ar, 'spawn_engine_proc', spawn)
    return SimpleNamespace(**dict(revive_env, wake=wake, store=store,
                                  commands=commands, app=Flask(__name__)))


def test_ended_claude_b_revives_native_conversation_and_reads_text(mail_revival):
    fixture = mail_revival
    pipe = _Stdin()
    fixture.spawned['proc'] = _Proc(pipe)
    assert fixture.wake.process_next(fixture.app)['state'] == 'submitted'
    assert len(fixture.commands) == 1
    command, kwargs = fixture.commands[0]
    assert command[command.index('-r') + 1] == CSID
    assert kwargs['cwd'] == fixture.project['project_path']
    assert TEXT in json.loads(pipe.written[0])['message']['content']
    resumed = fixture.sessions['revsid000001']
    assert resumed['claude_session_id'] == CSID
    assert resumed.get('_notify_session', '') == ''
    resumed['status'] = 'idle'
    fixture.ar._write_usage_breakdown_turn_checkpoint(resumed)
    assert fixture.store.get_event('reply')['state'] == 'turn_completed'
    assert fixture.killed == []


def test_ended_claude_b_timeout_keeps_child_then_late_ack_reconciles(mail_revival, monkeypatch):
    fixture = mail_revival
    event = threading.Event()
    pipe = _Stdin(gate=event)
    proc = _Proc(pipe)
    fixture.spawned['proc'] = proc
    monkeypatch.setattr(agent_mail_pipe, 'ACK_SECONDS', .02)
    try:
        assert fixture.wake.process_next(fixture.app)['state'] == 'uncertain'
        resumed = fixture.sessions['revsid000001']
        assert resumed['proc'] is proc and fixture.killed == []
        assert fixture.wake.process_next(fixture.app)['state'] == 'idle'
        assert len(fixture.commands) == 1
    finally:
        event.set()
    assert resumed['_mail_delivery'].done.wait(2)
    # done can already be set by uncertainty; finish the actual writer before
    # asserting its durable late receipt, with a bounded test-only join.
    from test_agent_mail_wake import wait_for
    wait_for(lambda: fixture.store.get_event('reply')['state'] == 'submitted')
    assert TEXT in pipe.written[0] and len(fixture.commands) == 1


def test_ended_claude_cannot_cold_start_when_history_disappears(mail_revival):
    fixture = mail_revival
    fixture.spawned['proc'] = _Proc(_Stdin())
    fixture.ar._save_agent_log('p1', [{'session_id': 'revsid000001', 'provider': 'claude',
                                     'claude_session_id': 'different-native'}])
    assert fixture.wake.process_next(fixture.app)['state'] == 'blocked'
    assert fixture.commands == []


def test_ended_claude_a_delivers_only_with_exact_native_resume(mail_revival, monkeypatch):
    fixture = mail_revival
    fixture.project['use_streaming_agent'] = False
    monkeypatch.setattr(fixture.ar, '_read_agent_stream', lambda *a: None)
    fixture.spawned['proc'] = _Proc(_Stdin())
    assert fixture.wake.process_next(fixture.app)['state'] == 'submitted'
    command = fixture.commands[0][0]
    assert command[command.index('-r') + 1] == CSID
    assert TEXT in command[command.index('-p') + 1]
    resumed = fixture.sessions['revsid000001']
    assert resumed['mode'] == 'A' and resumed['claude_session_id'] == CSID
    resumed['status'] = 'completed'
    continuation.completed(resumed)
    assert fixture.store.get_event('reply')['state'] == 'turn_completed'


def test_fast_mode_a_acceptance_is_not_rejected_by_postlaunch_stamp(mail_revival, monkeypatch):
    fixture = mail_revival
    fixture.project['use_streaming_agent'] = False
    monkeypatch.setattr(fixture.ar, '_read_agent_stream', lambda *a: None)
    fixture.spawned['proc'] = _Proc(_Stdin())
    assert fixture.wake.process_next(fixture.app)['state'] == 'submitted'
    scope = fixture.sessions['revsid000001']['_mail_delivery']
    assert scope.session is fixture.sessions['revsid000001']
    assert 'body' not in scope.event
    with fixture.store._read() as db:
        receipt = db.execute('SELECT * FROM mail_turn_receipts WHERE event_id=?', ('reply',)).fetchone()
    assert receipt['attempt_id'] == scope.attempt and receipt['accepted'] == 1
