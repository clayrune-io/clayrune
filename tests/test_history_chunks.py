"""Bounded captured-history transport, including incomplete Unicode tool output."""
from dataclasses import replace
import json

import pytest

from mc.conversation_store import ConversationStore, ConversationUnavailable
from mc import execution_lifecycle as life


@pytest.fixture
def history(tmp_path):
    store = ConversationStore(tmp_path / 'capture.sqlite')
    engine = {'provider': 'fake'}
    store.create_lifecycle_conversation('p','c',engine=engine,event_id='create')
    state = store.accept_request('p','c',request_id='r',user_message={'text':'input'},
        engine=engine,provenance={'origin':'interactive'},expected_revision=0,event_id='accept')
    state,owner = store.claim_owner('p','c',owner_id='o',expected_revision=state.revision,event_id='owner')
    _,token = store.claim_attempt(owner,request_id='r',attempt_id='a',expected_revision=state.revision,event_id='claim')
    payload = {'call_id':'tool','output':'🧱漢字' * 25000,'is_error':False}
    event,_ = store.append_evidence(token,event_id='output',kind='tool_result',payload=payload)
    return store,token,event,payload


def test_large_output_roundtrips_in_bounded_utf8_chunks(history):
    store,token,event,payload = history
    snapshot = store.history_snapshot('p','c')
    with pytest.raises(life.LifecycleConflict,match='coverage'):
        store.snapshot('p','c')
    pieces = []
    offset = 0
    while True:
        chunk = store.read_history_chunk(snapshot,event.sequence,offset=offset,max_bytes=997)
        assert 0 < len(chunk.data) <= 997
        assert chunk.event_id == 'output' and chunk.protocol_version == 1
        pieces.append(chunk.data)
        offset = chunk.next_offset
        if chunk.complete:
            break
    assert json.loads(b''.join(pieces)) == payload
    assert len(pieces) > 200


def test_later_appends_do_not_expand_snapshot(history):
    store,token,event,_ = history
    snapshot = store.history_snapshot('p','c')
    next_event,_ = store.append_evidence(token,event_id='gap',kind='capture_gap',
        payload={'reason':'unsupported record','source_reference':'offset:12'})
    assert store.read_history_chunk(snapshot,event.sequence).total_bytes > 100000
    with pytest.raises(ValueError):
        store.read_history_chunk(snapshot,next_event.sequence)


def test_delete_restore_revokes_old_chunk_snapshot(history):
    store,_,event,_ = history
    snapshot = store.history_snapshot('p','c')
    state = store.lifecycle_state('p','c')
    state = store.set_lifecycle_deleted('p','c',True,expected_revision=state.revision,event_id='delete')
    with pytest.raises(ConversationUnavailable):
        store.read_history_chunk(snapshot,event.sequence)
    store.set_lifecycle_deleted('p','c',False,expected_revision=state.revision,event_id='restore')
    with pytest.raises(life.LifecycleConflict):
        store.read_history_chunk(snapshot,event.sequence)
    assert store.read_history_chunk(store.history_snapshot('p','c'),event.sequence).data


@pytest.mark.parametrize('bounds', [{'offset':-1},{'offset':10**9},{'max_bytes':0},
                                   {'max_bytes':True},{'max_bytes':1048577}])
def test_invalid_chunk_bounds(history,bounds):
    store,_,event,_ = history
    with pytest.raises(ValueError):
        store.read_history_chunk(store.history_snapshot('p','c'),event.sequence,**bounds)


def test_cross_project_snapshot_cannot_read_other_project(history):
    store,_,event,_ = history
    snapshot = replace(store.history_snapshot('p','c'),project_id='other')
    with pytest.raises(ConversationUnavailable):
        store.read_history_chunk(snapshot,event.sequence)


@pytest.mark.parametrize('watermark', ['invalid', True, -1])
def test_invalid_snapshot_boundary_rejected(history,watermark):
    store,_,event,_ = history
    snapshot = replace(store.history_snapshot('p','c'),high_water=watermark)
    with pytest.raises(ValueError):
        store.read_history_chunk(snapshot,event.sequence)
