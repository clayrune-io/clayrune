"""Offline fixture schemas from test_provider_runtimes and tool-call-parity.

These tests are not CLI safety certification and launch no runtime.
"""
import ast
from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path

import pytest

from mc.codex_capture import CodexCapture, CaptureSourceConflict, UnsupportedCaptureFormat
from mc.conversation_contract import validate_protocol_event


NEW = 'codex-exec-jsonl-0.151'
OLD = 'codex-exec-jsonl-0.133'


def feed(frame, *, decoder=None, sequence=0, reference=None):
    decoder = decoder or CodexCapture(format_version=NEW)
    return decoder.feed(source_reference=reference or f'fixture/frame/{sequence}',
                        sequence=sequence, frame_json=json.dumps(frame, ensure_ascii=False))


def payloads(events, kind):
    return [event.payload for event in events if event.kind == kind]


def test_flat_new_message_keeps_exact_text_native_id_and_immutable_payload():
    # Native shape: tests/test_provider_runtimes.py:525.
    text = ' \nHi Ron.\t\n🙂\n '
    events = feed({'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': text}})
    message = next(event for event in events if event.kind == 'assistant_message')
    assert message.payload == {'message_id': 'item_0', 'block_id': 'item_0/text',
                               'text': text, 'completeness': 'final'}
    with pytest.raises(FrozenInstanceError):
        message.kind = 'thinking'
    copy = message.payload
    copy['text'] = 'modified'
    assert message.payload['text'] == text
    for event in events:
        validate_protocol_event(event.kind, event.payload, event.protocol_version)


def test_huge_shell_output_and_exact_arguments_survive_call_result_pair():
    # Native shape: tests/test_provider_runtimes.py:534 and parity test sequence.
    output = ' \n' + ('full tool output🙂\n' * 50000) + '\tend\n '
    command = 'powershell -Command "Write-Output hi"\n'
    events = feed({'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'command_execution',
        'command': command, 'aggregated_output': output, 'exit_code': 0, 'status': 'completed'}})
    assert payloads(events, 'tool_call') == [{'call_id': 'item_1', 'name': 'shell', 'input': {'command': command}}]
    assert payloads(events, 'tool_result') == [{'call_id': 'item_1', 'output': output, 'is_error': False}]
    assert not payloads(events, 'capture_gap')


def test_nonzero_exit_is_error_not_assumed_success():
    events = feed({'type': 'item.completed', 'item': {'id': 'shell', 'type': 'command_execution',
        'command': 'exit 2', 'aggregated_output': 'denied', 'exit_code': 2}})
    assert payloads(events, 'tool_result')[0]['is_error'] is True


def test_old_mixed_blocks_preserve_text_and_every_native_call():
    # 0.133 content[] family, including native tool_use blocks supported by old parser.
    events = feed({'type': 'item.completed', 'item': {'id': 'message1', 'type': 'message', 'content': [
        {'type': 'output_text', 'text': ' before '},
        {'type': 'tool_use', 'id': 'call1', 'name': 'read', 'input': {'path': 'a', 'options': [1, 2]}},
        {'type': 'text', 'text': '\nafter\n'},
        {'type': 'tool_use', 'id': 'call2', 'name': 'custom', 'input': 'raw non-JSON args\n'},
    ]}}, decoder=CodexCapture(format_version=OLD))
    messages = payloads(events, 'assistant_message')
    assert [p['text'] for p in messages] == [' before ', '\nafter\n']
    assert [p['block_id'] for p in messages] == ['message1/content/0', 'message1/content/2']
    assert [p['call_id'] for p in payloads(events, 'tool_call')] == ['call1', 'call2']
    assert payloads(events, 'tool_call')[1]['input'] == 'raw non-JSON args\n'


def test_old_fixture_without_native_item_id_emits_gap_not_fabricated_message():
    # Existing exact minimal 0.133 test fixture lacks item.id: capture cannot invent it.
    events = feed({'type': 'item.completed', 'item': {'type': 'message',
        'content': [{'type': 'output_text', 'text': 'Hello from Codex!'}]}}, decoder=CodexCapture(format_version=OLD))
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'assistant_message')


def test_legacy_explicit_user_role_is_never_misattributed_as_assistant():
    events = feed({'type': 'item.completed', 'item': {'id': 'm', 'type': 'message', 'role': 'user',
        'content': [{'type': 'text', 'text': 'user input'}]}}, decoder=CodexCapture(format_version=OLD))
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'assistant_message')


def test_legacy_missing_tool_id_is_gap_but_other_blocks_are_retained():
    events = feed({'type': 'item.completed', 'item': {'id': 'm', 'type': 'message', 'content': [
        {'type': 'text', 'text': 'retained'}, {'type': 'tool_use', 'name': 'read', 'input': {}}]}},
        decoder=CodexCapture(format_version=OLD))
    assert payloads(events, 'assistant_message')[0]['text'] == 'retained'
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'tool_call')


def test_text_updates_are_exact_partial_snapshots_not_guessed_deltas():
    decoder = CodexCapture(format_version=NEW)
    results = []
    for seq, (phase, text) in enumerate([('started', ''), ('updated', 'hel'), ('updated', 'hello'), ('completed', 'hello!')]):
        results += feed({'type': f'item.{phase}', 'item': {'id': 'm', 'type': 'agent_message', 'text': text}},
                        decoder=decoder, sequence=seq)
    assert [p['text'] for p in payloads(results, 'assistant_message')] == ['', 'hel', 'hello', 'hello!']
    assert [p['completeness'] for p in payloads(results, 'assistant_message')] == ['partial'] * 3 + ['final']
    assert not payloads(results, 'message_delta')


def test_shell_start_update_completion_keep_all_outputs_and_identity():
    decoder = CodexCapture(format_version=NEW)
    results = []
    for seq, phase in enumerate(['started', 'updated', 'completed']):
        item = {'id': 's', 'type': 'command_execution', 'command': 'echo x',
                'aggregated_output': ['','x','x\n'][seq], 'exit_code': 0 if seq == 2 else None}
        results += feed({'type': f'item.{phase}', 'item': item}, decoder=decoder, sequence=seq)
    observations = payloads(results, 'provider_observation')
    snapshots = [p['value'] for p in observations if p['name'] == 'codex.shell.output_snapshot']
    assert [s['output'] for s in snapshots] == ['', 'x']
    assert all(s['completeness'] == 'partial' for s in snapshots)
    assert payloads(results, 'tool_result') == [{'call_id': 's', 'output': 'x\n', 'is_error': False}]
    assert len(payloads(results, 'tool_call')) == 3


@pytest.mark.parametrize('bad_exit', [None, '0', True])
def test_unknown_shell_exit_preserves_output_but_never_fabricates_success(bad_exit):
    events = feed({'type': 'item.completed', 'item': {'id': 's', 'type': 'command_execution',
        'command': 'x', 'aggregated_output': 'all output', 'exit_code': bad_exit}})
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'tool_result')
    assert any(p['name'] == 'codex.shell.output_snapshot' and p['value']['output'] == 'all output'
               for p in payloads(events, 'provider_observation'))


def test_exposed_reasoning_text_is_not_trimmed():
    events = feed({'type': 'item.completed', 'item': {'id': 'r0', 'type': 'reasoning', 'text': '  exposed\n'}})
    assert payloads(events, 'thinking')[0]['text'] == '  exposed\n'


def test_turn_boundaries_usage_and_error_are_observations_not_assistant_text():
    decoder = CodexCapture(format_version=NEW)
    frames = [{'type': 'thread.started', 'thread_id': 'native-thread'}, {'type': 'turn.started'},
              {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 2}},
              {'type': 'turn.failed', 'error': {'message': ' exact failure\n'}}]
    events = [e for seq, frame in enumerate(frames) for e in feed(frame, decoder=decoder, sequence=seq)]
    values = payloads(events, 'provider_observation')
    usage = next(p['value'] for p in values if p['name'] == 'codex.usage')
    assert usage == {'basis': 'turn.completed', 'aggregation': 'native_report_not_summed',
                     'usage': {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 2}}
    assert not payloads(events, 'assistant_message')
    assert not payloads(events, 'capture_gap')


def test_unknown_init_config_is_not_persisted():
    events = feed({'type': 'thread.started', 'thread_id': 't', 'config': {'secret': 'do-not-store'}})
    assert payloads(events, 'capture_gap')
    assert all('do-not-store' not in e.payload_json for e in events)


@pytest.mark.parametrize('frame', [
    {'type': 'future_event', 'content': 'not fabricated assistant text'},
    {'type': 'item.updated', 'item': {'id': 'x', 'type': 'future_item', 'secret': 'not retained'}},
    {'type': 'item.completed', 'item': {'id': 'x', 'type': {'secret': 'not retained'}}},
    {'type': 'item.completed', 'item': {'id': 'm', 'type': 'agent_message', 'text': ['unsupported']}},
    {'type': 'thread.started'},
])
def test_unknown_or_malformed_shapes_are_explicit_gaps(frame):
    events = feed(frame)
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'assistant_message')
    assert all('not retained' not in e.payload_json for e in events)


@pytest.mark.parametrize('raw', ['{"type":', 'diagnostic stderr text', '[]',
                                 '{"type":"turn.started","type":"error"}', '{"n":NaN}'])
def test_partial_or_invalid_json_never_becomes_assistant_output(raw):
    events = CodexCapture(format_version=NEW).feed(source_reference='f0', sequence=0, frame_json=raw)
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'assistant_message')


def test_same_source_replay_is_idempotent_but_conflicting_reuse_raises():
    decoder = CodexCapture(format_version=NEW)
    frame = {'type': 'turn.started'}
    assert feed(frame, decoder=decoder)
    assert feed(frame, decoder=decoder) == ()
    with pytest.raises(CaptureSourceConflict):
        feed({'type': 'thread.started', 'thread_id': 'x'}, decoder=decoder)
    with pytest.raises(CaptureSourceConflict):
        feed(frame, decoder=decoder, sequence=1, reference='fixture/frame/0')


def test_equal_text_at_distinct_source_references_is_not_deduplicated():
    decoder = CodexCapture(format_version=NEW)
    frame = {'type': 'item.completed', 'item': {'id': 'm', 'type': 'agent_message', 'text': 'same'}}
    a = feed(frame, decoder=decoder)
    b = feed(frame, decoder=decoder, sequence=1)
    assert payloads(a, 'assistant_message') == payloads(b, 'assistant_message')
    assert a[0].source_reference != b[0].source_reference


def test_source_gaps_are_explicit_and_backwards_frames_rejected():
    decoder = CodexCapture(format_version=NEW)
    feed({'type': 'turn.started'}, decoder=decoder)
    assert payloads(feed({'type': 'turn.started'}, decoder=decoder, sequence=2), 'capture_gap')
    with pytest.raises(CaptureSourceConflict):
        feed({'type': 'turn.started'}, decoder=decoder, sequence=1)


def test_format_is_explicit_and_wrong_family_is_not_silently_reinterpreted():
    with pytest.raises(UnsupportedCaptureFormat):
        CodexCapture(format_version='any-latest-codex')
    events = feed({'type': 'item.completed', 'item': {'id': 'm', 'type': 'agent_message', 'text': 'x'}},
                  decoder=CodexCapture(format_version=OLD))
    assert payloads(events, 'capture_gap')
    assert not payloads(events, 'assistant_message')


def test_no_lossy_ui_runtime_or_io_dependency():
    import mc.codex_capture as module
    tree = ast.parse(inspect.getsource(module))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports <= {'__future__', 'dataclasses', 'hashlib', 'json', 'typing', 'mc.conversation_contract'}


@pytest.mark.parametrize('test_name,format_version,expected_kind', [
    ('test_parse_event_thread_started', NEW, 'provider_observation'),
    ('test_parse_event_agent_message_flat_text', NEW, 'assistant_message'),
    ('test_parse_event_reasoning_is_thinking', NEW, 'thinking'),
    ('test_parse_event_item_completed_message', OLD, 'capture_gap'),
])
def test_existing_repository_fixture_literals_without_invoking_ui_parser(test_name, format_version, expected_kind):
    # Consume the source test's actual literal JSON fixture, not its lossy
    # AgentEvent output and not a live CLI. The old message intentionally has
    # no native item ID; stricter canonical capture correctly exposes that gap.
    source = Path(__file__).with_name('test_provider_runtimes.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TestCodexRuntime')
    test = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == test_name)
    call = next(n for n in ast.walk(test) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == 'dumps')
    native_frame = ast.literal_eval(call.args[0])
    events = feed(native_frame, decoder=CodexCapture(format_version=format_version))
    assert payloads(events, expected_kind)


@pytest.mark.parametrize('raw', [
    '{"type":"item.completed","item":{"id":"m","type":"message","content":[{"type":"tool_use","id":"c","name":"x","input":{"n":1e999}}]}}',
    '[' * 2000 + '0' + ']' * 2000,
])
def test_overflow_and_excessive_recursion_become_malformed_gaps(raw):
    decoder = CodexCapture(format_version=OLD)
    events = decoder.feed(source_reference='bad', sequence=0, frame_json=raw)
    assert [event.kind for event in events] == ['capture_gap']
    assert decoder.feed(source_reference='bad', sequence=0, frame_json=raw) == ()


@pytest.mark.parametrize('exit_status', [0, 1, None])
def test_transport_exit_alone_never_proves_native_completion(exit_status):
    events = CodexCapture(format_version=NEW).finish(source_reference='eof', sequence=0, exit_status=exit_status)
    assert payloads(events, 'provider_observation')[0]['value']['exit_status'] == exit_status
    assert any('without observed native terminal' in p['reason'] for p in payloads(events, 'capture_gap'))
    assert not payloads(events, 'tool_result')


def test_finish_after_native_completion_has_no_inferred_gap_or_tool_success():
    decoder = CodexCapture(format_version=NEW)
    feed({'type':'item.completed', 'item': {'id':'m','type':'agent_message','text':'done'}}, decoder=decoder)
    feed({'type':'turn.completed', 'usage':{'input_tokens':1,'output_tokens':1}}, decoder=decoder, sequence=1)
    events = decoder.finish(source_reference='eof', sequence=2, exit_status=0)
    assert [e.kind for e in events] == ['provider_observation']
    assert events[0].payload['value']['native_terminal_observed'] is True


def test_finish_records_open_item_even_with_native_terminal_event():
    decoder = CodexCapture(format_version=NEW)
    feed({'type':'item.started','item':{'id':'shell','type':'command_execution','command':'x'}}, decoder=decoder)
    feed({'type':'turn.completed'}, decoder=decoder, sequence=1)
    events = decoder.finish(source_reference='eof', sequence=2, exit_status=0)
    assert events[0].payload['value']['incomplete_item_ids'] == ['shell']
    assert any('open or incompletely' in p['reason'] for p in payloads(events, 'capture_gap'))
    assert not payloads(events, 'tool_result')


def test_completed_item_closes_open_state_before_finish():
    decoder = CodexCapture(format_version=NEW)
    item = {'id':'s','type':'command_execution','command':'x'}
    feed({'type':'item.started','item':item}, decoder=decoder)
    feed({'type':'item.completed','item':dict(item, aggregated_output='full', exit_code=0)}, decoder=decoder, sequence=1)
    feed({'type':'turn.completed'}, decoder=decoder, sequence=2)
    events = decoder.finish(source_reference='eof', sequence=3, exit_status=0)
    assert not payloads(events, 'capture_gap')
    assert events[0].payload['value']['incomplete_item_ids'] == []


def test_new_turn_after_completed_turn_requires_another_terminal_result():
    decoder = CodexCapture(format_version=NEW)
    feed({'type':'turn.completed'}, decoder=decoder)
    feed({'type':'turn.started'}, decoder=decoder, sequence=1)
    events = decoder.finish(source_reference='eof', sequence=2, exit_status=0)
    assert any('without observed' in p['reason'] for p in payloads(events, 'capture_gap'))


def test_failed_native_turn_is_terminal_but_not_success():
    decoder = CodexCapture(format_version=NEW)
    feed({'type':'turn.failed','error':{'message':'denied'}}, decoder=decoder)
    events = decoder.finish(source_reference='eof', sequence=1, exit_status=1)
    assert not payloads(events, 'capture_gap')
    assert events[0].payload['value']['exit_status'] == 1


def test_finish_replay_conflicts_and_domain_separation():
    decoder = CodexCapture(format_version=NEW)
    feed({'type':'turn.completed'}, decoder=decoder)
    assert decoder.finish(source_reference='eof', sequence=1, exit_status=0)
    assert decoder.finish(source_reference='eof', sequence=1, exit_status=0) == ()
    with pytest.raises(CaptureSourceConflict):
        decoder.finish(source_reference='eof', sequence=1, exit_status=1)
    with pytest.raises(CaptureSourceConflict):
        decoder.feed(source_reference='eof', sequence=1, frame_json='0')
    with pytest.raises(CaptureSourceConflict):
        decoder.feed(source_reference='new', sequence=2, frame_json='{}')
    with pytest.raises(CaptureSourceConflict):
        decoder.finish(source_reference='another-eof', sequence=2, exit_status=0)


def test_earlier_capture_gap_and_skipped_finish_sequence_are_explicit():
    decoder = CodexCapture(format_version=NEW)
    feed({'type':'unknown'}, decoder=decoder)
    feed({'type':'turn.completed'}, decoder=decoder, sequence=1)
    events = decoder.finish(source_reference='eof', sequence=3, exit_status=0)
    reasons = [p['reason'] for p in payloads(events, 'capture_gap')]
    assert any('sequence gap' in r for r in reasons)
    assert any('earlier gaps' in r for r in reasons)
