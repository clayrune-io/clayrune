"""Cross-provider fixture conformance through the shared replay controller.

These are repository fixture profiles, not native CLI/rollout certification.
"""
import hashlib
import json
import sqlite3

import pytest

from mc.capture_replay import SourceRecoveryError, replay_file_prefix
from mc.claude_qwen_capture import ClaudeQwenCapture
from mc.codex_capture import CodexCapture, UnsupportedCaptureFormat
from mc.conversation_store import ConversationStore, EventConflict
from mc import execution_lifecycle as lifecycle
from mc.conversation_store import StaleAttempt


PROFILES = ('codex-0.151', 'claude-record-v1', 'qwen-record-v1')


def _frames(profile):
    if profile == 'codex-0.151':
        return [
            {'type': 'thread.started', 'thread_id': 'native-thread'},
            {'type': 'item.started', 'item': {'id': 'shell', 'type': 'command_execution'}},
            {'type': 'item.completed', 'item': {'id': 'shell', 'type': 'command_execution', 'command': 'printf x', 'aggregated_output': 'x\n', 'exit_code': 0}},
            {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'done æ¼¢'}},
            {'type': 'turn.completed'},
        ]
    return [
        {'type': 'assistant', 'message': {'id': 'native-message', 'content': [
            {'type': 'thinking', 'thinking': 'visible thought'},
            {'type': 'text', 'text': 'answer æ¼¢'},
            {'type': 'tool_use', 'id': 'native-call', 'name': 'Read', 'input': {'path': 'x'}},
        ]}},
        {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'native-call', 'content': [{'type': 'text', 'text': 'tool output'}]},
        ]}},
        {'type': 'assistant', 'message': {'id': 'native-message-2', 'content': [
            {'type': 'text', 'text': 'after tool'},
        ]}},
        {'type': 'result', 'is_error': False, 'result': 'done'},
    ]


def _profile_config(profile):
    if profile == 'codex-0.151':
        return 'codex', 'codex-exec-jsonl-0.151', lambda: CodexCapture(format_version='codex-exec-jsonl-0.151')
    provider = 'claude' if profile.startswith('claude') else 'qwen'
    return provider, profile, lambda: ClaudeQwenCapture(provider)


def _claimed(tmp_path, profile):
    store = ConversationStore(tmp_path / f'{profile}.sqlite')
    engine = {'provider': profile.split('-')[0], 'model': 'requested-model',
              'effort': 'high', 'settings': {'nested': {'keep': ['exact', 7]}}}
    store.create_lifecycle_conversation('mc-conversation', 'c', engine=engine, event_id='create')
    state = store.accept_request('mc-conversation', 'c', request_id='request',
        user_message={'text': 'user input'}, engine=engine,
        provenance={'origin': 'interactive'}, expected_revision=0, event_id='accept')
    state, owner = store.claim_owner('mc-conversation', 'c', owner_id='owner',
        expected_revision=state.revision, event_id='owner')
    _, token = store.claim_attempt(owner, request_id='request', attempt_id='attempt',
        expected_revision=state.revision, event_id='attempt')
    path = tmp_path / f'{profile}.jsonl'
    path.write_bytes(b''.join((json.dumps(frame, ensure_ascii=False) + '\n').encode('utf-8')
                              for frame in _frames(profile)))
    return store, token, path


def _replay(store, token, path, profile):
    provider, fmt, factory = _profile_config(profile)
    return replay_file_prefix(path=path, store=store, token=token,
        provider=provider, format_version=fmt, source_id=f'{profile}-source',
        incarnation='fixture-incarnation', decoder_factory=factory)


def _snapshot(store):
    with sqlite3.connect(store.db_path) as db:
        events = tuple((row[0], row[1], row[2], row[3]) for row in db.execute(
            "SELECT event_id,attempt_id,kind,payload_json FROM events "
            "WHERE project_id=? AND conversation_id=? AND kind NOT LIKE 'lifecycle.%' "
            "ORDER BY sequence", ('mc-conversation', 'c')))
        row = db.execute('SELECT provider,format_version,source_id,incarnation,'
                         'cursor_sequence,cursor_digest,sealed,eof_sequence,eof_event_id '
                         'FROM capture_sources WHERE project_id=? AND conversation_id=?',
                         ('mc-conversation', 'c')).fetchone()
    return events, row


@pytest.mark.parametrize('profile', PROFILES)
def test_profiles_preserve_mixed_bodies_nested_request_and_distinct_identities(tmp_path, profile):
    store, token, path = _claimed(tmp_path, profile)
    cursor = _replay(store, token, path, profile)
    assert cursor.cursor_sequence == len(_frames(profile)) - 1
    events = store.read_events('mc-conversation', 'c')
    assert any(event.kind == 'assistant_message' for event in events)
    assert any(event.kind == 'tool_result' for event in events)
    captured = [event for event in events if event.kind in {
        'assistant_message', 'thinking', 'tool_call', 'tool_result',
        'provider_observation', 'capture_gap'}]
    assert captured and all(event.attempt_id == 'attempt' for event in captured)
    assert all(event.event_id != 'mc-conversation' for event in events)
    if profile == 'codex-0.151':
        message = next(event for event in events if event.kind == 'assistant_message')
        call = next(event for event in events if event.kind == 'tool_call')
        result = next(event for event in events if event.kind == 'tool_result')
        assert message.payload == {'message_id': 'answer', 'block_id': 'answer/text',
                                   'text': 'done æ¼¢', 'completeness': 'final'}
        assert call.payload == {'call_id': 'shell', 'name': 'shell',
                                'input': {'command': 'printf x'}}
        assert result.payload == {'call_id': 'shell', 'output': 'x\n', 'is_error': False}
        assert any(event.payload.get('value', {}).get('thread_id') == 'native-thread'
                   for event in events if event.kind == 'provider_observation')
    else:
        thinking = next(event for event in events if event.kind == 'thinking')
        message = next(event for event in events if event.kind == 'assistant_message')
        call = next(event for event in events if event.kind == 'tool_call')
        result = next(event for event in events if event.kind == 'tool_result')
        assert thinking.payload == {'message_id': 'native-message',
                                    'block_id': 'native-message/block/0',
                                    'text': 'visible thought', 'completeness': 'final'}
        assert message.payload == {'message_id': 'native-message',
                                   'block_id': 'native-message/block/1',
                                   'text': 'answer æ¼¢', 'completeness': 'final'}
        assert call.payload == {'call_id': 'native-call', 'name': 'Read',
                                'input': {'path': 'x'}}
        assert result.payload == {'call_id': 'native-call',
                                  'output': [{'type': 'text', 'text': 'tool output'}],
                                  'is_error': False}
    expected_engine = {'provider': profile.split('-')[0], 'model': 'requested-model',
                       'effort': 'high', 'settings': {'nested': {'keep': ['exact', 7]}}}
    with sqlite3.connect(store.db_path) as db:
        requested = db.execute(
            'SELECT engine_key FROM lifecycle_requests WHERE project_id=? AND conversation_id=?',
            ('mc-conversation', 'c')).fetchone()[0]
        source = db.execute(
            'SELECT provider,format_version,source_id,incarnation FROM capture_sources '
            'WHERE project_id=? AND conversation_id=?', ('mc-conversation', 'c')).fetchone()
    assert json.loads(requested) == expected_engine
    assert source == (('codex' if profile == 'codex-0.151' else expected_engine['provider']),
                      profile if profile != 'codex-0.151' else 'codex-exec-jsonl-0.151',
                      f'{profile}-source', 'fixture-incarnation')


def test_codex_open_tool_prefix_rebuilds_state_after_restart(tmp_path):
    store, token, path = _claimed(tmp_path, 'codex-0.151')
    frames = _frames('codex-0.151')
    path.write_bytes((''.join(json.dumps(frame) + '\n' for frame in frames[:2])).encode())
    first = _replay(store, token, path, 'codex-0.151')
    assert first.cursor_sequence == 1
    path.write_bytes((''.join(json.dumps(frame) + '\n' for frame in frames)).encode())
    reopened = ConversationStore(store.db_path)
    second = _replay(reopened, token, path, 'codex-0.151')
    assert second.cursor_sequence == 4
    _replay(reopened, token, path, 'codex-0.151')
    events = reopened.read_events('mc-conversation', 'c')
    calls = [event for event in events if event.kind == 'tool_call'
             and event.payload['call_id'] == 'shell']
    assert len(calls) == 1
    assert calls[0].payload == {'call_id': 'shell', 'name': 'shell',
                                'input': {'command': 'printf x'}}
    assert sum(event.kind == 'tool_result' and event.payload['call_id'] == 'shell' for event in events) == 1
    assert next(event for event in events if event.kind == 'tool_result').payload['output'] == 'x\n'


def test_exact_replay_is_idempotent_and_conflicting_payload_or_tail_fails(tmp_path):
    store, token, path = _claimed(tmp_path, 'codex-0.151')
    _replay(store, token, path, 'codex-0.151')
    before = _snapshot(store)
    _replay(store, token, path, 'codex-0.151')
    assert _snapshot(store) == before
    content = path.read_bytes()
    first_newline = content.index(b'\n')
    path.write_bytes(b'{"type":"thread.started","thread_id":"changed"}\n' + content[first_newline + 1:])
    with pytest.raises(SourceRecoveryError, match='prefix changed'):
        _replay(store, token, path, 'codex-0.151')


def test_sealed_eof_rejects_new_frames_but_allows_earlier_duplicate(tmp_path):
    store, token, path = _claimed(tmp_path, 'codex-0.151')
    _replay(store, token, path, 'codex-0.151')
    store.seal_capture_source(token, eof_sequence=5, exit_status=0, event_id='eof')
    assert _replay(store, token, path, 'codex-0.151').sealed
    path.write_bytes(path.read_bytes() + b'{"type":"turn.completed"}\n')
    with pytest.raises(EventConflict, match='sealed'):
        _replay(store, token, path, 'codex-0.151')


def test_privacy_and_owner_fences_block_replay(tmp_path):
    store, token, path = _claimed(tmp_path, 'claude-record-v1')
    _replay(store, token, path, 'claude-record-v1')
    before = _snapshot(store)
    state = store.lifecycle_state('mc-conversation', 'c')
    store.set_lifecycle_deleted('mc-conversation', 'c', True,
        expected_revision=state.revision, event_id='delete')
    assert _snapshot(store) == before
    with pytest.raises(lifecycle.LifecycleConflict, match='deleted'):
        _replay(store, token, path, 'claude-record-v1')


def test_stale_owner_takeover_refuses_replay_without_cursor_or_event_change(tmp_path):
    store, token, path = _claimed(tmp_path, 'qwen-record-v1')
    _replay(store, token, path, 'qwen-record-v1')
    before = _snapshot(store)
    state = store.lifecycle_state('mc-conversation', 'c')
    _, owner = store.claim_owner('mc-conversation', 'c', owner_id='new-owner',
        expected_revision=state.revision, event_id='takeover')
    assert owner.owner_epoch != token.owner_epoch
    with pytest.raises(StaleAttempt):
        _replay(store, token, path, 'qwen-record-v1')
    assert _snapshot(store) == before


def test_unsupported_profile_fails_closed_without_decoder_fallback(tmp_path):
    store, token, path = _claimed(tmp_path, 'codex-0.151')
    before = _snapshot(store)
    with pytest.raises(UnsupportedCaptureFormat):
        replay_file_prefix(path=path, store=store, token=token, provider='codex',
            format_version='codex-rollout-native-unknown', source_id='unknown',
            incarnation='one', decoder_factory=lambda: CodexCapture(
                format_version='codex-rollout-native-unknown'))
    assert _snapshot(store) == before


def test_partial_tail_refuses_only_incomplete_span_and_keeps_cursor(tmp_path):
    store, token, path = _claimed(tmp_path, 'codex-0.151')
    frames = _frames('codex-0.151')
    path.write_bytes((''.join(json.dumps(frame) + '\n' for frame in frames[:2]) + '{"type":').encode())
    with pytest.raises(SourceRecoveryError, match='incomplete trailing'):
        _replay(store, token, path, 'codex-0.151')
    assert store.read_capture_cursor(token).cursor_sequence == 1
    path.write_bytes((''.join(json.dumps(frame) + '\n' for frame in frames)).encode())
    assert _replay(store, token, path, 'codex-0.151').cursor_sequence == 4
