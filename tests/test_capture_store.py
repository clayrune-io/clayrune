"""Native fixture record -> protocol batch -> real SQLite -> full projection."""
import json

import pytest

from mc.codex_capture import CodexCapture
from mc.claude_qwen_capture import ClaudeQwenCapture
from mc.conversation_store import ConversationStore, EventConflict
from mc.conversation_projection import project_conversation


@pytest.fixture
def claimed(tmp_path):
    store = ConversationStore(tmp_path / 'capture.sqlite')
    engine = {'provider':'fixture','model':'pinned'}
    store.create_lifecycle_conversation('p','c',engine=engine,event_id='create')
    state = store.accept_request('p','c',request_id='r',user_message={'text':'original request'},
        engine=engine,provenance={'origin':'interactive'},expected_revision=0,event_id='accept')
    state,owner = store.claim_owner('p','c',owner_id='owner',expected_revision=state.revision,event_id='owner')
    _,token = store.claim_attempt(owner,request_id='r',attempt_id='attempt',expected_revision=state.revision,event_id='claim')
    return store,token


def batch(events):
    return [(f'{e.source_reference}:{e.event_index}',e.kind,e.payload) for e in events]


@pytest.mark.parametrize('provider', ['codex','claude','qwen'])
def test_native_mixed_output_survives_storage_and_projection(claimed,provider):
    store,token = claimed
    if provider == 'codex':
        decoder = CodexCapture(format_version='codex-exec-jsonl-0.151')
        frames = [{'type':'item.completed','item':{'id':'call','type':'command_execution',
            'command':'read','aggregated_output':'Full 🧱 output\n' * 2000,'exit_code':0}},
            {'type':'item.completed','item':{'id':'answer','type':'agent_message','text':' Answer\n'}}]
    else:
        decoder = ClaudeQwenCapture(provider)
        frames = [{'type':'assistant','message':{'id':'answer','content':[
            {'type':'text','text':' Answer\n'},{'type':'tool_use','id':'call','name':'read','input':{'path':'x'}}]}},
            {'type':'user','uuid':'result','message':{'content':[
                {'type':'tool_result','tool_use_id':'call','content':'Full 🧱 output\n' * 2000,'is_error':False}]}}]
    for index,frame in enumerate(frames):
        events = decoder.feed(source_reference=f'frame:{index}',sequence=index,frame_json=json.dumps(frame))
        store.append_evidence_batch(token,batch(events))
    view = project_conversation(store.read_events('p','c'))
    assert any(block.text == ' Answer\n' for block in view.blocks)
    output = [event.payload['output'] for event in store.read_events('p','c') if event.kind == 'tool_result']
    assert output == ['Full 🧱 output\n' * 2000]
    assert not view.issues


def test_batch_insert_failure_is_all_or_nothing_and_retryable(claimed,monkeypatch):
    store,token = claimed
    events = [('frame:0','tool_call',{'call_id':'call','name':'read','input':{}}),
              ('frame:1','tool_result',{'call_id':'call','output':'whole output','is_error':False})]
    before = store.lifecycle_state('p','c')
    insert = store._lifecycle_event
    def fail_second(db,state,event_id,*args,**kwargs):
        if event_id == 'frame:1':
            raise OSError('disk failure')
        return insert(db,state,event_id,*args,**kwargs)
    monkeypatch.setattr(store,'_lifecycle_event',fail_second)
    with pytest.raises(OSError):
        store.append_evidence_batch(token,events)
    assert store.lifecycle_state('p','c') == before
    assert not any(event.event_id.startswith('frame:') for event in store.read_events('p','c'))
    monkeypatch.setattr(store,'_lifecycle_event',insert)
    saved = store.append_evidence_batch(token,events)
    assert len(saved) == 2
    assert store.append_evidence_batch(token,events) == saved


def test_batch_conflict_rolls_back_preceding_new_events(claimed):
    store,token = claimed
    payload = {'name':'observed_model','value':'v1'}
    store.append_evidence(token,event_id='old',kind='provider_observation',payload=payload)
    before = store.lifecycle_state('p','c')
    with pytest.raises(EventConflict):
        store.append_evidence_batch(token,[('new','provider_observation',payload),
            ('old','provider_observation',{'name':'observed_model','value':'different'})])
    assert store.lifecycle_state('p','c') == before


def test_prepared_batch_is_detached_from_caller_mutation(claimed,monkeypatch):
    store,token = claimed
    payload = {'call_id':'call','name':'read','input':{'path':'original'}}
    insert = store._lifecycle_event
    def mutate_caller(*args,**kwargs):
        payload['input']['path'] = 'changed'
        return insert(*args,**kwargs)
    monkeypatch.setattr(store,'_lifecycle_event',mutate_caller)
    saved = store.append_evidence_batch(token,[('frame','tool_call',payload)])
    assert saved[0][0].payload['input']['path'] == 'original'
