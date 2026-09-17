"""Offline capture tests: repository transcript fixtures plus protocol records."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from mc.claude_qwen_capture import ClaudeQwenCapture,CaptureSourceConflict
from mc.conversation_contract import validate_protocol_event


@pytest.fixture(params=['claude','qwen'])
def decoder(request):
    return ClaudeQwenCapture(request.param)


def feed(decoder,record,sequence=0):
    events = decoder.feed(f'record-{sequence}',sequence,json.dumps(record,ensure_ascii=False))
    for event in events:
        validate_protocol_event(event.kind,event.payload)
    return events


def test_mixed_blocks_and_tool_result_exact(decoder):
    text = '  Unicode 漢😀\n' * 10000
    events = feed(decoder,{'type':'assistant','message':{'id':'msg-native','content':[
        {'type':'thinking','thinking':' exposed thought\n'},
        {'type':'text','text':text},
        {'type':'tool_use','id':'call-native','name':'Read','input':{'path':'file'}},
        {'type':'text','text':' after tool  '}]}})
    assert [e.kind for e in events] == ['thinking','assistant_message','tool_call','assistant_message']
    assert events[1].payload['text'] == text
    assert events[1].payload['message_id'] == 'msg-native'
    assert events[1].payload['block_id'] == 'msg-native/block/1'
    output = [{'type':'text','text':' full result\n'},{'type':'image','source':{'data':'encoded'}}]
    result = feed(decoder,{'type':'user','message':{'content':[
        {'type':'tool_result','tool_use_id':'call-native','content':output,'is_error':True}]}},1)
    assert result[0].payload == {'call_id':'call-native','output':output,'is_error':True}


def test_qwen_fixture_tool_use_and_user_result(decoder):
    # Same stream-json shapes as tests/test_provider_runtimes.py Qwen tests;
    # native tool IDs remain capturable even when message identity is absent.
    call = feed(decoder,{'type':'assistant','session_id':'s1','message':{'content':[
        {'type':'tool_use','id':'call_1','name':'run_shell_command','input':{'command':'ls'}}]}})
    result = feed(decoder,{'type':'user','session_id':'s1','message':{'content':[
        {'type':'tool_result','tool_use_id':'call_1','content':'out\n'}]}},1)
    assert call[0].payload['call_id'] == result[0].payload['call_id'] == 'call_1'
    assert result[0].payload['is_error'] is False


def test_prompt_echo_is_not_original_user(decoder):
    events = feed(decoder,{'type':'user','isMeta':True,'message':{'content':' injected system instructions\n'}})
    assert events[0].kind == 'provider_observation'
    assert events[0].payload == {'name':'provider_prompt_echo','value':{'text':' injected system instructions\n','origin':'unknown'}}


@pytest.mark.parametrize('record,reason',[
    ({'type':'assistant','message':{'content':[{'type':'text','text':'not silently dropped'}]}},'assistant_missing_message_identity'),
    ({'type':'assistant','message':{'id':'m','content':[{'type':'unknown','secret':'not dumped'}]}},'unsupported_content_block'),
    ({'type':'system','subtype':'other','config':{'secret':'not dumped'}},'unsupported_record_type'),
    ({'type':'user','message':{'content':[{'type':'tool_result','content':'missing native id'}]}},'tool_result_missing_identity_or_content'),
])
def test_unknown_or_missing_identity_is_explicit_gap(decoder,record,reason):
    events = feed(decoder,record)
    assert events[0].payload['reason'] == reason
    assert 'not dumped' not in events[0].payload_json


def test_no_raw_init_fallback(decoder):
    events = feed(decoder,{'type':'system','subtype':'init','model':'observed','api_key':'secret','mcp_servers':[{'env':{'TOKEN':'secret'}}]})
    assert [e.payload for e in events] == [{'name':'observed_model','value':'observed'}]
    assert 'secret' not in repr(events)


def test_immutable_and_source_not_text_dedup(decoder):
    frame = json.dumps({'type':'assistant','message':{'id':'m','content':[{'type':'text','text':'same'}]}})
    first = decoder.feed('first',0,frame)
    assert decoder.feed('first',0,frame) == ()
    second = decoder.feed('second',1,frame)
    assert first[0].payload == second[0].payload
    assert first[0].source_reference != second[0].source_reference
    first[0].payload['text'] = 'mutated'
    assert first[0].payload['text'] == 'same'
    with pytest.raises(FrozenInstanceError):
        first[0].kind = 'other'
    with pytest.raises(CaptureSourceConflict):
        decoder.feed('first',0,'{}')


def stream(decoder,event,sequence):
    return feed(decoder,{'type':'stream_event','event':event},sequence)


def test_stream_text_delta_and_final_share_address_without_trimming(decoder):
    assert stream(decoder,{'type':'message_start','message':{'id':'native-message'}},0) == ()
    stream(decoder,{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},1)
    one = stream(decoder,{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':' hello\n'}},2)
    two = stream(decoder,{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'world  '}},3)
    final = stream(decoder,{'type':'content_block_stop','index':0},4)
    assert one[0].kind == two[0].kind == 'message_delta'
    assert one[0].payload['delta_index'] == 0 and two[0].payload['delta_index'] == 1
    assert final[0].payload['text'] == ' hello\nworld  '
    assert final[0].payload['block_id'] == one[0].payload['block_id']
    assert stream(decoder,{'type':'message_stop'},5) == ()


def test_stream_tool_input_fragments_are_preserved(decoder):
    stream(decoder,{'type':'message_start','message':{'id':'m'}},0)
    stream(decoder,{'type':'content_block_start','index':2,'content_block':{'type':'tool_use','id':'call','name':'Read','input':{}}},1)
    partial = stream(decoder,{'type':'content_block_delta','index':2,'delta':{'type':'input_json_delta','partial_json':'{"path":'}},2)
    stream(decoder,{'type':'content_block_delta','index':2,'delta':{'type':'input_json_delta','partial_json':'" file "}'}},3)
    final = stream(decoder,{'type':'content_block_stop','index':2},4)
    assert partial[0].payload['value']['partial_json'] == '{"path":'
    assert final[0].payload == {'call_id':'call','name':'Read','input':{'path':' file '}}


def test_orphan_delta_and_partial_stop_are_gaps(decoder):
    assert stream(decoder,{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'lost'}},0)[0].kind == 'capture_gap'
    stream(decoder,{'type':'message_start','message':{'id':'m'}},1)
    stream(decoder,{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},2)
    assert stream(decoder,{'type':'message_stop'},3)[0].payload['reason'] == 'stream_stopped_with_unfinished_blocks'


def test_repo_real_transcript_fixture_retains_separate_native_blocks():
    decoder = ClaudeQwenCapture('claude')
    fixture = Path(__file__).parent/'fixtures'/'stop_hook_real_transcript.jsonl'
    events = []
    for index,line in enumerate(fixture.read_text(encoding='utf-8').splitlines()):
        events.extend(decoder.feed(f'fixture/record/{index}',index,line))
    texts = [e for e in events if e.kind == 'assistant_message']
    thoughts = [e for e in events if e.kind == 'thinking']
    assert texts and thoughts
    assert any('DRAFT ONE:' in e.payload['text'] for e in texts)
    assert thoughts[0].payload['text'] == ''
    assert thoughts[0].payload['block_id'].endswith('/block/0')
    assert texts[0].payload['block_id'].endswith('/block/1')
    assert not any(e.kind == 'user_message' for e in events)


@pytest.mark.parametrize('frame',['not json','null','{"type":"assistant","x":NaN}','{"type":"assistant","x":1e999}'])
def test_malformed_record_does_not_dump_raw(decoder,frame):
    events = decoder.feed('invalid',0,frame)
    assert events[0].kind == 'capture_gap'
    assert frame not in events[0].payload_json


def test_malformed_delta_cannot_produce_false_final(decoder):
    stream(decoder,{'type':'message_start','message':{'id':'m'}},0)
    stream(decoder,{'type':'content_block_start','index':0,'content_block':{'type':'text','text':'known'}},1)
    events = stream(decoder,{'type':'content_block_delta','index':0,'delta':{'type':'unknown','secret':'not preserved'}},2)
    assert events[0].kind == 'capture_gap'
    final = stream(decoder,{'type':'content_block_stop','index':0},3)
    assert final[0].payload['completeness'] == 'partial'
    assert final[0].payload['text'] == 'known'


@pytest.mark.parametrize('sequence',[2,10])
def test_nonzero_start_reports_missing_source(decoder,sequence):
    events = feed(decoder,{'type':'system','subtype':'init','model':'m'},sequence)
    assert events[0].payload['reason'] == 'missing_source_sequence'
    assert events[1].payload['value'] == 'm'


def test_source_gap_keeps_existing_block_partial(decoder):
    stream(decoder,{'type':'message_start','message':{'id':'m'}},0)
    stream(decoder,{'type':'content_block_start','index':0,'content_block':{'type':'text','text':'prefix'}},1)
    events = stream(decoder,{'type':'content_block_stop','index':0},3)
    assert events[0].payload['reason'] == 'missing_source_sequence'
    assert events[1].payload['completeness'] == 'partial'


@pytest.mark.parametrize('frame',['{"type":"user","type":"assistant"}','['*2000+']'*2000],ids=['duplicate-keys','deep-json'])
def test_duplicate_keys_and_deep_json_are_explicit_gaps(decoder,frame):
    result = decoder.feed('frame',0,frame)
    assert result[0].payload['reason'] in ('malformed_json','record_not_object')
    assert decoder.feed('frame',0,frame) == ()


def test_failed_emit_rolls_back_stream_state_and_source_ack(decoder,monkeypatch):
    import mc.claude_qwen_capture as capture
    stream(decoder,{'type':'message_start','message':{'id':'m'}},0)
    stream(decoder,{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},1)
    record = {'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'one'}}}
    original = capture.validate_protocol_event
    def fail(*args,**kwargs):
        raise RuntimeError('injected evidence validation failure')
    with monkeypatch.context() as patch:
        patch.setattr(capture,'validate_protocol_event',fail)
        with pytest.raises(RuntimeError):
            feed(decoder,record,2)
    event = feed(decoder,record,2)[0]
    assert event.payload['delta_index'] == 0
    final = stream(decoder,{'type':'content_block_stop','index':0},3)[0]
    assert final.payload['text'] == 'one'


def test_eof_zero_without_native_terminal_is_gap(decoder):
    events = decoder.finish('eof',0,0)
    assert any(e.kind == 'capture_gap' and e.payload['reason']=='missing_native_terminal_result' for e in events)
    assert decoder.finish('eof',0,0) == ()
    with pytest.raises(CaptureSourceConflict):
        decoder.finish('eof',0,1)


def test_eof_preserves_unfinished_stream_without_finalizing(decoder):
    stream(decoder,{'type':'message_start','message':{'id':'m'}},0)
    stream(decoder,{'type':'content_block_start','index':0,'content_block':{'type':'text','text':'partial'}},1)
    feed(decoder,{'type':'result','is_error':False},2)
    events = decoder.finish('eof',3,0)
    assert [e.kind for e in events] == ['provider_observation','capture_gap']
    assert events[-1].payload['reason'] == 'unfinished_native_stream'
    assert not any(e.kind == 'assistant_message' for e in events)


def test_terminal_result_plus_eof_records_transport_not_domain_success(decoder):
    feed(decoder,{'type':'result','is_error':False},0)
    events = decoder.finish('eof',1,0)
    assert [e.payload for e in events] == [{'name':'transport_eof','value':{'exit_status':0}}]
    with pytest.raises(CaptureSourceConflict):
        feed(decoder,{'type':'result','is_error':False},2)
