"""Offline tests for the separately identified Codex rollout envelope."""
import json
import sqlite3

import pytest

from mc import execution_lifecycle as lifecycle
from mc.capture_replay import SourceRecoveryError, replay_file_prefix
from mc.codex_rollout_capture import CodexRolloutCapture, RolloutSourceConflict
from mc.conversation_store import ConversationStore, EventConflict, StaleAttempt


FORMAT = 'codex-rollout-jsonl-0.153'


def _frames():
    return [
        {'type': 'session_meta', 'payload': {'id': 'native-thread', 'session_id': 'native-thread',
                                             'cwd': '/fixture', 'cli_version': '0.153.4'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'id': 'msg-1',
            'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'answer æ¼¢'}]}},
        {'type': 'response_item', 'payload': {'type': 'reasoning', 'id': 'reason-1',
            'summary': [{'type': 'summary_text', 'text': 'visible reasoning'}],
            'content': None, 'encrypted_content': 'opaque-not-captured'}},
        {'type': 'response_item', 'payload': {'type': 'function_call', 'id': 'call-item',
            'call_id': 'call-native', 'name': 'shell', 'arguments': '{"command":["bash","-lc","printf x"]}'}},
        {'type': 'response_item', 'payload': {'type': 'function_call', 'id': 'call-item',
            'call_id': 'call-native', 'name': 'shell', 'arguments': '{"command":["bash","-lc","printf x"]}'}},
        {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'call-native',
            'output': '{"output":"x\\n","metadata":{"exit_code":0}}'}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'turn-native'}},
    ]


def _claimed(tmp_path):
    store = ConversationStore(tmp_path / 'rollout.sqlite')
    engine = {'provider': 'codex', 'model': 'rollout-model', 'effort': 'high',
              'settings': {'nested': {'flag': True}}}
    store.create_lifecycle_conversation('project', 'conversation', engine=engine, event_id='create')
    state = store.accept_request('project', 'conversation', request_id='request',
        user_message={'text': 'input'}, engine=engine, provenance={'origin': 'interactive'},
        expected_revision=0, event_id='accept')
    state, owner = store.claim_owner('project', 'conversation', owner_id='owner',
        expected_revision=state.revision, event_id='owner')
    _, token = store.claim_attempt(owner, request_id='request', attempt_id='attempt',
        expected_revision=state.revision, event_id='attempt')
    path = tmp_path / 'rollout.jsonl'
    path.write_text(''.join(json.dumps(frame, ensure_ascii=False) + '\n' for frame in _frames()), encoding='utf-8')
    return store, token, path


def _replay(store, token, path):
    return replay_file_prefix(path=path, store=store, token=token, provider='codex',
        format_version=FORMAT, source_id='native-rollout', incarnation='inc-1',
        decoder_factory=lambda: CodexRolloutCapture(format_version=FORMAT))


def _events(store):
    return store.read_events('project', 'conversation')


def test_rollout_replay_preserves_exact_bodies_and_suppresses_mirrored_call(tmp_path):
    store, token, path = _claimed(tmp_path)
    cursor = _replay(store, token, path)
    assert cursor.cursor_sequence == len(_frames()) - 1
    events = _events(store)
    assert next(e for e in events if e.kind == 'assistant_message').payload == {
        'message_id': 'msg-1', 'block_id': 'msg-1/content/0', 'text': 'answer æ¼¢', 'completeness': 'final'}
    assert next(e for e in events if e.kind == 'thinking').payload == {
        'message_id': 'reason-1', 'block_id': 'reason-1/summary/0',
        'text': 'visible reasoning', 'completeness': 'final'}
    assert next(e for e in events if e.kind == 'tool_call').payload == {
        'call_id': 'call-native', 'name': 'shell',
        'input': {'command': ['bash', '-lc', 'printf x']}}
    assert next(e for e in events if e.kind == 'tool_result').payload == {
        'call_id': 'call-native', 'output': {'output': 'x\n', 'metadata': {'exit_code': 0}},
        'is_error': False}
    assert sum(e.kind == 'tool_call' and e.payload['call_id'] == 'call-native' for e in events) == 1
    with sqlite3.connect(store.db_path) as db:
        source = db.execute('SELECT provider,format_version,source_id,incarnation FROM capture_sources').fetchone()
    assert source == ('codex', FORMAT, 'native-rollout', 'inc-1')


def test_rollout_mid_tool_reopen_and_duplicate_replay_are_idempotent(tmp_path):
    store, token, path = _claimed(tmp_path)
    frames = _frames()
    path.write_text(''.join(json.dumps(frame) + '\n' for frame in frames[:4]), encoding='utf-8')
    assert _replay(store, token, path).cursor_sequence == 3
    path.write_text(''.join(json.dumps(frame) + '\n' for frame in frames), encoding='utf-8')
    reopened = ConversationStore(store.db_path)
    assert _replay(reopened, token, path).cursor_sequence == 6
    before = [(e.event_id, e.kind, e.payload_json) for e in _events(reopened)]
    _replay(reopened, token, path)
    after = [(e.event_id, e.kind, e.payload_json) for e in _events(reopened)]
    assert after == before
    assert sum(e.kind == 'tool_call' and e.payload['call_id'] == 'call-native' for e in _events(reopened)) == 1
    assert sum(e.kind == 'tool_result' and e.payload['call_id'] == 'call-native' for e in _events(reopened)) == 1


def test_rollout_source_mismatch_and_changed_mirror_fail_closed(tmp_path):
    store, token, path = _claimed(tmp_path)
    _replay(store, token, path)
    with pytest.raises(EventConflict):
        replay_file_prefix(path=path, store=store, token=token, provider='codex',
            format_version=FORMAT, source_id='other-source', incarnation='inc-1',
            decoder_factory=lambda: CodexRolloutCapture(format_version=FORMAT))
    decoder = CodexRolloutCapture(format_version=FORMAT)
    decoder.feed(source_reference='r/1', sequence=0,
                 frame_json=json.dumps(_frames()[1]))
    changed = dict(_frames()[1])
    changed['payload'] = dict(changed['payload'])
    changed['payload']['content'] = [{'type': 'output_text', 'text': 'changed'}]
    with pytest.raises(RolloutSourceConflict, match='mirrored'):
        decoder.feed(source_reference='r/2', sequence=1, frame_json=json.dumps(changed))


def test_rollout_partial_tail_owner_privacy_and_incognito_fences(tmp_path):
    store, token, path = _claimed(tmp_path)
    path.write_bytes(path.read_bytes() + b'{"type":"response_item"')
    with pytest.raises(SourceRecoveryError, match='incomplete trailing'):
        _replay(store, token, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT cursor_sequence FROM capture_sources').fetchone()[0] == len(_frames()) - 1
    store, token, path = _claimed(tmp_path / 'fences')
    _replay(store, token, path)
    state = store.lifecycle_state('project', 'conversation')
    store.set_lifecycle_deleted('project', 'conversation', True,
        expected_revision=state.revision, event_id='delete')
    with pytest.raises(lifecycle.LifecycleConflict, match='deleted'):
        _replay(store, token, path)


def test_rollout_stale_owner_and_incognito_no_io(tmp_path):
    store, token, path = _claimed(tmp_path)
    _replay(store, token, path)
    state = store.lifecycle_state('project', 'conversation')
    store.claim_owner('project', 'conversation', owner_id='new-owner',
        expected_revision=state.revision, event_id='takeover')
    with pytest.raises(StaleAttempt):
        _replay(store, token, path)

    class NoIO:
        def __getattribute__(self, name):
            raise AssertionError('incognito touched dependency')
    assert replay_file_prefix(path=NoIO(), store=NoIO(), token=NoIO(), provider='codex',
        format_version=FORMAT, source_id='s', incarnation='i', decoder_factory=NoIO,
        incognito=True) is None


def test_rollout_eof_is_observation_not_coverage(tmp_path):
    decoder = CodexRolloutCapture(format_version=FORMAT)
    decoder.feed(source_reference='complete', sequence=0,
                 frame_json=json.dumps(_frames()[-1]))
    events = decoder.finish(source_reference='eof', sequence=1, exit_status=0)
    assert events[0].payload['value']['coverage_claim'] is False
    assert not any(event.kind == 'capture_gap' for event in events)


def test_unknown_records_blocks_user_and_malformed_reasoning_are_explicit_gaps():
    decoder = CodexRolloutCapture(format_version=FORMAT)
    unknown = decoder.feed(source_reference='unknown', sequence=0,
        frame_json=json.dumps({'type': 'response_item', 'payload': {'type': 'future_item'}}))
    assert unknown[0].kind == 'capture_gap'
    assert unknown[0].payload['source_reference'] == 'unknown'
    user = decoder.feed(source_reference='user', sequence=1,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'message', 'id': 'user-native', 'role': 'user',
            'content': [{'type': 'input_text', 'text': 'native input'}]}}))
    assert user[0].payload == {'name': 'native_user_message', 'value': {
        'text': 'native input', 'native_message_id': 'user-native', 'source_role': 'user'}}
    bad_reasoning = decoder.feed(source_reference='reasoning', sequence=2,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'reasoning', 'id': 'reason', 'summary': [{'bad': True}]}}))
    assert bad_reasoning[0].payload['reason'] == 'reasoning_block_missing_text'
    bad_block = decoder.feed(source_reference='block', sequence=3,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'message', 'id': 'message-2', 'role': 'assistant',
            'content': [{'type': 'future_text', 'text': 'not silently dropped'}]}}))
    assert bad_block[0].payload['reason'] == 'unsupported_message_block'


def test_custom_raw_input_and_unknown_output_status_are_lossless():
    decoder = CodexRolloutCapture(format_version=FORMAT)
    call = decoder.feed(source_reference='custom-call', sequence=0,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'call_id': 'custom-1', 'name': 'apply_patch',
            'input': '*** Begin Patch\\n+raw diff\\n'}}))
    assert call[0].payload == {'call_id': 'custom-1', 'name': 'apply_patch',
                               'input': '*** Begin Patch\\n+raw diff\\n'}
    output = decoder.feed(source_reference='custom-output', sequence=1,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'custom-1',
            'output': 'opaque provider output'}}))
    assert output[0].payload == {'name': 'codex.tool_output', 'value': {
        'call_id': 'custom-1', 'raw_output': 'opaque provider output', 'is_error': 'unknown'}}
    assert output[1].payload['reason'] == 'tool_result_status_unknown'
    failed = decoder.feed(source_reference='failed-output', sequence=2,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'call_id': 'failed',
            'output': '{"output":"bad","metadata":{"exit_code":2}}'}}))
    assert failed[0].payload['is_error'] is True


def test_custom_raw_records_round_trip_through_replay_store(tmp_path):
    store, token, path = _claimed(tmp_path)
    frames = [_frames()[0], {
        'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'call_id': 'custom-store',
            'name': 'apply_patch', 'input': '*** Begin Patch\\n+keep raw\\n'}}, {
        'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'custom-store',
            'output': 'opaque result'}}]
    path.write_text(''.join(json.dumps(frame) + '\n' for frame in frames), encoding='utf-8')
    _replay(store, token, path)
    events = _events(store)
    assert next(e for e in events if e.kind == 'tool_call').payload['input'] == '*** Begin Patch\\n+keep raw\\n'
    assert next(e for e in events if e.kind == 'provider_observation' and
                e.payload['name'] == 'codex.tool_output').payload['value']['raw_output'] == 'opaque result'


def test_validation_failure_does_not_poison_semantic_retry():
    decoder = CodexRolloutCapture(format_version=FORMAT)
    with pytest.raises((ValueError, TypeError)):
        decoder.feed(source_reference='bad', sequence=0,
            frame_json='{"type":"response_item","payload":{"type":"function_call",'
                       '"call_id":"same","name":"x","arguments":NaN}}')
    events = decoder.feed(source_reference='good', sequence=0,
        frame_json=json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call', 'call_id': 'same', 'name': 'x', 'arguments': '{}'}}))
    assert events[0].payload['call_id'] == 'same'


def test_sealed_replay_rejects_appended_rollout(tmp_path):
    store, token, path = _claimed(tmp_path)
    _replay(store, token, path)
    store.seal_capture_source(token, eof_sequence=len(_frames()), exit_status=0, event_id='eof')
    path.write_text(path.read_text(encoding='utf-8') + json.dumps(_frames()[1]) + '\n', encoding='utf-8')
    with pytest.raises(EventConflict, match='sealed'):
        _replay(store, token, path)


def test_unsupported_rollout_format_fails_before_source_binding(tmp_path):
    store, token, path = _claimed(tmp_path)
    before = _events(store)
    with pytest.raises(ValueError, match='unsupported'):
        replay_file_prefix(path=path, store=store, token=token, provider='codex',
            format_version='codex-rollout-unknown', source_id='unknown', incarnation='one',
            decoder_factory=lambda: CodexRolloutCapture(format_version='codex-rollout-unknown'))
    assert _events(store) == before
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM capture_sources').fetchone()[0] == 0
