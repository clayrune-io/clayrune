"""Provider-neutral capture ingress: real decoder, bridge, and SQLite only."""
import json

import pytest

from mc.agent_runtime import CodexRuntime, ProviderCapabilities, SessionHandle, _mode_a_reader
from mc.capture_ingress import CaptureProvenance, build_capture_ingress
from mc.codex_capture import CodexCapture, CaptureSourceConflict
from mc.conversation_store import ConversationStore
from mc.execution_lifecycle import LifecycleConflict


@pytest.fixture
def claimed(tmp_path):
    store = ConversationStore(tmp_path / 'canonical.sqlite')
    engine = {'provider': 'codex', 'model': 'requested', 'effort': 'high'}
    store.create_lifecycle_conversation('project', 'conversation', engine=engine,
                                        event_id='create')
    state = store.accept_request('project', 'conversation', request_id='request',
        user_message={'text': 'hello'}, engine=engine, provenance={'origin': 'interactive'},
        expected_revision=0, event_id='accept')
    state, owner = store.claim_owner('project', 'conversation', owner_id='owner',
                                     expected_revision=state.revision, event_id='owner')
    _, token = store.claim_attempt(owner, request_id='request', attempt_id='attempt',
                                   expected_revision=state.revision, event_id='claim')
    provenance = CaptureProvenance('codex', 'native-thread-7', 'mc-session-7', engine,
                                   token.privacy_generation)
    return store, token, provenance


def test_codex_raw_decoder_bridge_preserves_mixed_native_evidence(claimed):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    assert ingress is not None
    ingress.ingest(source_reference='raw/0', sequence=0, frame_json=json.dumps({
        'type': 'item.completed', 'item': {'id': 'shell-1', 'type': 'command_execution',
        'command': 'echo exact', 'aggregated_output': 'tool output ðŸ§±', 'exit_code': 0}}))
    ingress.ingest(source_reference='raw/1', sequence=1, frame_json=json.dumps({
        'type': 'item.completed', 'item': {'id': 'answer-1', 'type': 'agent_message',
        'text': 'assistant exact'}}))
    events = store.read_events('project', 'conversation')
    assert any(e.kind == 'assistant_message' and e.payload['message_id'] == 'answer-1' for e in events)
    result = next(e for e in events if e.kind == 'tool_result')
    assert result.payload == {'call_id': 'shell-1', 'output': 'tool output ðŸ§±', 'is_error': False}
    assert provenance.native_session_id != provenance.mc_session_id


def test_gap_and_eof_do_not_claim_complete_coverage(claimed):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    ingress.ingest(source_reference='raw/0', sequence=0,
                   frame_json=json.dumps({'type': 'turn.started'}))
    ingress.ingest(source_reference='raw/2', sequence=2, frame_json='not-json')
    ingress.finish(source_reference='eof', sequence=3, exit_status=0)
    events = store.read_events('project', 'conversation')
    assert any(e.kind == 'capture_gap' for e in events)
    assert any(e.kind == 'provider_observation' and e.payload['name'] == 'transport_eof' for e in events)
    assert store.lifecycle_state('project', 'conversation').coverage_complete is False


def test_failed_persistence_retains_exact_batch_and_reopens_idempotently(claimed, monkeypatch):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    original = store.append_evidence_batch
    calls = {'n': 0}
    def fail_once(*args, **kwargs):
        if calls['n'] == 0:
            calls['n'] += 1
            raise OSError('temporary SQLite failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'append_evidence_batch', fail_once)
    raw = json.dumps({'type': 'item.completed', 'item': {'id': 'answer',
                      'type': 'agent_message', 'text': 'retry exact'}})
    with pytest.raises(OSError):
        ingress.ingest(source_reference='raw/retry', sequence=0, frame_json=raw)
    assert len(ingress.pending) == 2
    ingress.retry_pending()
    reopened = ConversationStore(store.db_path)
    replay = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=reopened, token=token,
        provenance=provenance)
    replayed = replay.ingest(source_reference='raw/retry', sequence=0, frame_json=raw)
    assert len(replayed) == 2
    assert [item[0].event_id for item in replayed] == ['raw/retry:0', 'raw/retry:1']
    assert len([e for e in reopened.read_events('project', 'conversation') if e.kind == 'assistant_message']) == 1


def test_pending_batch_blocks_decoder_cursor_and_is_immutable(claimed):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    original = store.append_evidence_batch
    store.append_evidence_batch = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk'))
    raw = json.dumps({'type': 'item.completed', 'item': {'id': 'a',
                      'type': 'agent_message', 'text': 'immutable'}})
    with pytest.raises(OSError):
        ingress.ingest(source_reference='raw/0', sequence=0, frame_json=raw)
    pending = ingress.pending
    pending[0][2]['name'] = 'mutated'
    with pytest.raises(RuntimeError):
        ingress.ingest(source_reference='raw/1', sequence=1, frame_json=raw)
    with pytest.raises(RuntimeError):
        ingress.finish(source_reference='eof', sequence=1, exit_status=0)
    assert ingress.pending[0][2]['name'] != 'mutated'
    store.append_evidence_batch = original
    ingress.retry_pending()
    assert len(store.read_events('project', 'conversation')) == 6


def test_conflicting_replay_and_revocation_are_rejected(claimed):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    raw = json.dumps({'type': 'item.completed', 'item': {'id': 'answer',
                      'type': 'agent_message', 'text': 'one'}})
    original = store.append_evidence_batch
    def fail(*args, **kwargs):
        raise OSError('temporary failure')
    store.append_evidence_batch = fail
    with pytest.raises(OSError):
        ingress.ingest(source_reference='raw/0', sequence=0, frame_json=raw)
    store.append_evidence_batch = original
    ingress.retry_pending()
    with pytest.raises(CaptureSourceConflict):
        ingress.ingest(source_reference='raw/0', sequence=0, frame_json=raw.replace('one', 'two'))
    store.append_evidence_batch = fail
    with pytest.raises(OSError):
        ingress.ingest(source_reference='raw/1', sequence=1, frame_json=raw)
    store.append_evidence_batch = original
    state = store.lifecycle_state('project', 'conversation')
    store.set_lifecycle_deleted('project', 'conversation', True,
                                 expected_revision=state.revision, event_id='delete')
    with pytest.raises(LifecycleConflict):
        ingress.retry_pending()


def test_incognito_bypasses_before_decoder_or_store_use():
    class Explodes:
        def __getattribute__(self, name):
            raise AssertionError('incognito touched capture dependency')
    assert build_capture_ingress(incognito=True, decoder=Explodes(), store=Explodes(),
                                 token=Explodes(), provenance=Explodes()) is None


def test_mode_a_reader_calls_raw_callback_before_lossy_parse_and_fails_loudly():
    class Proc:
        def __init__(self, lines):
            self.stdout = iter(lines)
        def wait(self):
            return 0
    runtime = CodexRuntime()
    session = {'proc': None, 'status': 'running', 'log_lines': [], 'process_alive': True}
    handle = SessionHandle('mc', 'codex', 'A', '.', 'p', session,
                           started_at='now', capabilities=ProviderCapabilities('codex', 'Codex'),
                           meta={'callbacks': {}})
    proc = Proc([json.dumps({'type': 'turn.started'}) + '\n',
                 json.dumps({'type': 'item.completed', 'item': {
                     'id': 'a', 'type': 'agent_message', 'text': 'visible'}}) + '\n'])
    session['proc'] = proc
    seen = []
    handle.meta['callbacks']['on_raw_record'] = lambda line, seq, mc, s: seen.append((line, seq, mc))
    _mode_a_reader(proc, handle, runtime)
    assert [item[1] for item in seen] == [0, 1]
    assert session['status'] == 'completed'
    assert session['log_lines'] == ['visible']

    session2 = {'proc': None, 'status': 'running', 'log_lines': [], 'process_alive': True}
    handle2 = SessionHandle('mc2', 'codex', 'A', '.', 'p', session2,
                            started_at='now', capabilities=ProviderCapabilities('codex', 'Codex'),
                            meta={'callbacks': {
                                'on_raw_record': lambda *args: (_ for _ in ()).throw(
                                    OSError('capture unavailable'))}})
    proc2 = Proc([json.dumps({'type': 'turn.started'}) + '\n'])
    session2['proc'] = proc2
    _mode_a_reader(proc2, handle2, runtime)
    assert session2['status'] == 'error'
    assert session2['_capture_error'] == 'capture unavailable'


def test_real_reader_decoder_bridge_sqlite_and_eof_path(claimed):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    class Proc:
        def __init__(self, lines):
            self.stdout = iter(lines)
        def wait(self):
            return 0
    lines = [
        {'type': 'thread.started', 'thread_id': 'native-thread-7'},
        {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'raw exact'}},
        {'type': 'turn.completed'},
    ]
    session = {'proc': None, 'status': 'running', 'log_lines': [], 'process_alive': True}
    callbacks = {'on_raw_record': lambda line, seq, mc, s: ingress.ingest(
        source_reference=f'raw/{seq}', sequence=seq, frame_json=line),
                 'on_raw_eof': lambda seq, rc, mc, s: ingress.finish(
                     source_reference='eof', sequence=seq, exit_status=rc)}
    handle = SessionHandle('mc-session-7', 'codex', 'A', '.', 'project', session,
                           started_at='now', capabilities=ProviderCapabilities('codex', 'Codex'),
                           meta={'callbacks': callbacks})
    proc = Proc([json.dumps(item) + '\n' for item in lines])
    session['proc'] = proc
    _mode_a_reader(proc, handle, CodexRuntime())
    events = store.read_events('project', 'conversation')
    assert any(e.kind == 'assistant_message' and e.payload['text'] == 'raw exact' for e in events)
    assert any(e.kind == 'provider_observation' and e.payload['name'] == 'transport_eof' for e in events)
    assert session['status'] == 'completed'


def test_reader_capture_failure_drains_later_frames_without_claiming_success(claimed):
    store, token, provenance = claimed
    ingress = build_capture_ingress(incognito=False, decoder=CodexCapture(
        format_version='codex-exec-jsonl-0.151'), store=store, token=token,
        provenance=provenance)
    original = store.append_evidence_batch
    store.append_evidence_batch = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('full'))
    class Proc:
        def __init__(self, lines):
            self.lines = list(lines)
            self.stdout = iter(self.lines)
        def wait(self):
            return 7
    raw = [json.dumps({'type': 'item.completed', 'item': {
        'id': f'a{i}', 'type': 'agent_message', 'text': f'frame-{i}'}}) + '\n' for i in range(40)]
    session = {'proc': None, 'status': 'running', 'log_lines': [], 'process_alive': True}
    callbacks = {'on_raw_record': lambda line, seq, mc, s: ingress.ingest(
        source_reference=f'raw/{seq}', sequence=seq, frame_json=line),
                 'on_raw_eof': lambda seq, rc, mc, s: ingress.finish(
                     source_reference='eof', sequence=seq, exit_status=rc)}
    handle = SessionHandle('mc', 'codex', 'A', '.', 'project', session,
                           started_at='now', capabilities=ProviderCapabilities('codex', 'Codex'),
                           meta={'callbacks': callbacks})
    proc = Proc(raw)
    session['proc'] = proc
    _mode_a_reader(proc, handle, CodexRuntime())
    assert len(proc.lines) == 40
    assert session['capture_status'] == 'incomplete'
    assert session['status'] == 'error'
    assert len(ingress.pending) == 2
    store.append_evidence_batch = original
