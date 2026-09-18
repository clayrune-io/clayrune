"""Offline fixture tests; no complete/version-pinned Gemini stream is claimed."""
import ast
from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path

import pytest

import mc.gemini_capture as module
from mc.gemini_capture import CaptureSourceConflict, FORMAT_VERSION, GeminiCapture


def decoder():
    return GeminiCapture(format_version=FORMAT_VERSION)


def feed(frame, capture=None, seq=0, ref=None):
    return (capture or decoder()).feed(source_reference=ref or f'frame/{seq}',
                                      sequence=seq, frame_json=json.dumps(frame, ensure_ascii=False))


def values(events, kind):
    return [e.payload for e in events if e.kind == kind]


def fixture(name):
    source = Path(__file__).with_name('test_claude_runtime.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    call = next(n for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == 'dumps')
    return ast.literal_eval(call.args[0])


def test_real_fixture_tool_args_id_and_output_are_canonical_not_ui_preview():
    capture = decoder()
    call = feed(fixture('test_gemini_parse_event_tool_use_canonical_fields'), capture)
    result = feed(fixture('test_gemini_parse_event_tool_result_correlates_via_tool_id'), capture, 1)
    assert values(call, 'tool_call') == [{'call_id':'bash-123','name':'Bash','input':{'command':'ls'}}]
    assert values(result, 'tool_result') == [{'call_id':'bash-123','output':'file1.txt','is_error':False}]
    assert not values(call + result, 'capture_gap')


def test_huge_output_exact_whitespace_and_nested_arguments_are_not_truncated():
    capture = decoder()
    call = fixture('test_gemini_parse_event_tool_use_canonical_fields')
    call['parameters'] = {'command':' ls\n ', 'options':['a', {'b':'🙂'}]}
    call_events = feed(call, capture)
    result = fixture('test_gemini_parse_event_tool_result_correlates_via_tool_id')
    result['output'] = ' \n' + 'full result🙂\n' * 50000 + '\t '
    output = feed(result, capture, 1)
    assert values(call_events,'tool_call')[0]['input'] == call['parameters']
    assert values(output,'tool_result')[0]['output'] == result['output']


@pytest.mark.parametrize('role', ['user','assistant'])
def test_message_text_is_exact_evidence_not_invented_message_id_or_finality(role):
    text = ' \nexact text🙂\t '
    events = feed({'type':'message','role':role,'content':text,'session_id':'native-session'})
    observation = next(p['value'] for p in values(events,'provider_observation') if p['name']=='gemini.unclassified_text')
    assert observation['text'] == text
    assert observation['completeness'] == 'unverified'
    assert observation['provenance'] == ('provider_echo' if role=='user' else 'provider_output')
    assert values(events,'capture_gap')
    assert not values(events,'assistant_message') and not values(events,'user_message')
    assert any(p['name']=='gemini.session' and p['value']['session_id']=='native-session'
               for p in values(events,'provider_observation'))


def test_partial_marker_without_fixture_identity_semantics_is_not_guessed_delta():
    events = feed({'type':'message','role':'assistant','content':'part','delta':True})
    assert values(events,'capture_gap')
    assert not values(events,'message_delta')
    assert any(p['value'].get('text')=='part' for p in values(events,'provider_observation')
               if isinstance(p['value'],dict))


@pytest.mark.parametrize('missing', ['tool_id','status','output'])
def test_tool_result_missing_required_evidence_is_gap_not_success(missing):
    native = fixture('test_gemini_parse_event_tool_result_correlates_via_tool_id')
    del native[missing]
    events = feed(native)
    assert values(events,'capture_gap')
    assert not values(events,'tool_result')


def test_unknown_tool_status_keeps_output_as_unclassified_evidence():
    native = fixture('test_gemini_parse_event_tool_result_correlates_via_tool_id')
    native['status'] = 'future-status'
    events = feed(native)
    assert not values(events,'tool_result')
    assert values(events,'capture_gap')
    assert any(p['name']=='gemini.unclassified_tool_output' and p['value']['output']=='file1.txt'
               for p in values(events,'provider_observation'))


def test_terminal_error_and_usage_are_independent_observations():
    events = feed({'type':'result','status':'error','error':{'type':'Error','message':' exact error\n'},
                   'stats':{'total_tokens':0}})
    observations = values(events,'provider_observation')
    assert any(p['name']=='gemini.error' and p['value']['message']==' exact error\n' for p in observations)
    usage = next(p['value'] for p in observations if p['name']=='gemini.usage')
    assert usage == {'basis':'result.stats','aggregation':'native_report_not_summed','report':{'total_tokens':0}}
    assert not values(events,'capture_gap')


def test_fixture_terminal_without_status_is_boundary_not_assumed_success():
    native = fixture('test_gemini_parse_event_turn_end')
    events = feed(native)
    assert values(events,'capture_gap')
    terminal = next(p['value'] for p in values(events,'provider_observation') if p['name']=='gemini.native_terminal')
    assert terminal['outcome'] == 'unverified'


@pytest.mark.parametrize('raw', ['not JSON', '{"type":', '[]', '{"x":NaN}',
    '{"type":"tool_use","tool_id":"c","tool_name":"x","parameters":{"n":1e999}}',
    '{"type":"init","type":"done"}', '['*2000+'0'+']'*2000])
def test_malformed_duplicate_nonfinite_and_deep_frames_are_gaps(raw):
    events = decoder().feed(source_reference='x',sequence=0,frame_json=raw)
    assert [e.kind for e in events] == ['capture_gap']


def test_unknown_config_is_not_persisted():
    events = feed({'type':'init','session_id':'s','config':{'secret':'never-store-this'}})
    assert values(events,'capture_gap')
    assert all('never-store-this' not in event.payload_json for event in events)


def test_unknown_event_and_missing_call_id_are_explicit_gaps():
    assert values(feed({'type':'future','raw':'not assistant'}),'capture_gap')
    assert values(feed({'type':'tool_use','name':'x','input':{}}),'capture_gap')


def test_equal_content_different_sources_is_not_deduplicated():
    capture = decoder()
    frame = {'type':'content','text':'repeat'}
    assert feed(frame,capture)
    assert feed(frame,capture) == ()
    assert feed(frame,capture,1)
    with pytest.raises(CaptureSourceConflict):
        feed({'type':'content','text':'different'},capture,1)


def test_source_sequence_gaps_and_out_of_order():
    capture = decoder()
    assert values(feed({'type':'init','session_id':'s'},capture,2),'capture_gap')
    with pytest.raises(CaptureSourceConflict):
        feed({'type':'init','session_id':'s'},capture,1)


def test_batch_failure_rolls_back_mutable_state_and_source_ack(monkeypatch):
    capture = decoder()
    original = capture._events
    def fail(*args):
        raise RuntimeError('injected serialization failure')
    monkeypatch.setattr(capture,'_events',fail)
    call = fixture('test_gemini_parse_event_tool_use_canonical_fields')
    with pytest.raises(RuntimeError):
        feed(call,capture)
    assert not capture._open_calls and not capture._sources
    assert capture._last_sequence == -1
    monkeypatch.setattr(capture,'_events',original)
    assert values(feed(call,capture),'tool_call')


def test_finish_missing_terminal_or_open_tool_does_not_infer_success():
    capture = decoder()
    feed(fixture('test_gemini_parse_event_tool_use_canonical_fields'),capture)
    events = capture.finish(source_reference='eof',sequence=1,exit_status=0)
    assert {p['reason'] for p in values(events,'capture_gap')} == {'missing_native_terminal_result','unfinished_native_tools'}
    assert not values(events,'tool_result')
    assert values(events,'provider_observation')[0] == {'name':'transport_eof','value':{'exit_status':0}}


def test_native_error_terminal_and_eof_are_not_reported_as_success():
    capture = decoder()
    feed({'type':'result','status':'error','error':{'message':'failed'}},capture)
    events = capture.finish(source_reference='eof',sequence=1,exit_status=1)
    assert not values(events,'capture_gap')
    assert events[0].payload['value']['exit_status'] == 1


def test_finish_replay_conflicts_failure_retry_and_post_eof_input(monkeypatch):
    capture = decoder()
    original = capture._events
    monkeypatch.setattr(capture,'_events',lambda *a: (_ for _ in ()).throw(RuntimeError('fail')))
    with pytest.raises(RuntimeError):
        capture.finish(source_reference='eof',sequence=0,exit_status=None)
    assert not capture._sources and not capture._finished
    monkeypatch.setattr(capture,'_events',original)
    assert capture.finish(source_reference='eof',sequence=0,exit_status=None)
    assert capture.finish(source_reference='eof',sequence=0,exit_status=None)==()
    with pytest.raises(CaptureSourceConflict):
        capture.finish(source_reference='eof',sequence=0,exit_status=0)
    with pytest.raises(CaptureSourceConflict):
        capture.feed(source_reference='eof',sequence=0,frame_json='null')
    with pytest.raises(CaptureSourceConflict):
        feed({'type':'done'},capture,1)


def test_envelope_is_immutable_and_payload_copy_is_independent():
    event = feed({'type':'init','session_id':'s'})[0]
    with pytest.raises(FrozenInstanceError):
        event.source_sequence = 10
    payload = event.payload
    payload['value']['session_id']='changed'
    assert event.payload['value']['session_id']=='s'


def test_module_never_imports_runtime_ui_or_io():
    imports = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node,ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node,ast.ImportFrom):
            imports.add(node.module)
    assert imports <= {'__future__','dataclasses','hashlib','json','typing','mc.conversation_contract'}
    with pytest.raises(ValueError):
        GeminiCapture(format_version='latest-gemini-all-versions')
