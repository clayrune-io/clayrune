"""Real SQLite transactions, without a CLI, server, account or production data."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from mc.conversation_store import (
    APPLICATION_ID, AttemptToken, ConversationStore, ConversationUnavailable,
    EventConflict, SchemaError, StaleAttempt,
)


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / 'state' / 'conversations.sqlite3')


def begin(store, project='p', conversation='c', event='start'):
    token = store.begin_attempt(project, conversation, user_message={'text': 'Hello'}, engine={'provider': 'example', 'model': 'specific'}, event_id=event)
    assert isinstance(token, AttemptToken)
    return token


def append(store, token, event='answer', payload=None):
    return store.append_event(token, event_id=event, kind='assistant_message', payload=payload if payload is not None else {'text': 'Hello back'})


def test_missing_read_and_incognito_never_create(store):
    assert store.read_events('p', 'c') == []
    assert store.get_conversation('p', 'c') is None
    assert store.begin_attempt('p', 'c', user_message={}, engine={}, event_id='s', incognito=True) is None
    assert not store.db_path.parent.exists()
    with pytest.raises(ValueError):
        store.begin_attempt('p', 'c', user_message={}, engine={}, event_id='s', incognito='false')


def test_full_payload_reopen_and_mutation_isolation(store):
    token = begin(store)
    text = '漢字😀\n' * 50000
    payload = {'text': text, 'nested': {'tools': [1, 'value']}}
    event = append(store, token, payload=payload)
    payload['text'] = 'mutated'
    event.payload['text'] = 'mutated again'
    reopened = ConversationStore(store.db_path)
    events = reopened.read_events('p', 'c')
    assert [e.sequence for e in events] == [1, 2]
    assert events[1].payload['text'] == text
    assert events[0].payload['engine']['model'] == 'specific'
    assert reopened.get_conversation('p', 'c')['active_attempt'] == token.attempt_id


def test_idempotency_and_conflicts(store):
    token = begin(store)
    assert begin(store) == token
    event = append(store, token)
    assert append(store, token) == event
    assert len(store.read_events('p', 'c')) == 2
    with pytest.raises(EventConflict):
        append(store, token, payload={'text': 'different'})
    with pytest.raises(EventConflict):
        store.append_event(token, event_id='answer', kind='tool_result', payload=event.payload)
    with pytest.raises(EventConflict):
        store.append_event(token, event_id='answer', kind=event.kind, payload=event.payload, timestamp='different')
    with pytest.raises(EventConflict):
        store.begin_attempt('p', 'c', user_message={'text': 'different'}, engine={}, event_id='start')


def test_stale_attempt_and_scope(store):
    old = begin(store)
    append(store, old)
    current = begin(store, event='next')
    assert current != old
    with pytest.raises(StaleAttempt):
        append(store, old)  # Even duplicate stale callbacks are rejected.
    with pytest.raises(StaleAttempt):
        begin(store)
    with pytest.raises(ConversationUnavailable):
        append(store, replace(current, project_id='foreign'))
    assert store.read_events('foreign', 'c') == []
    assert store.get_conversation('foreign', 'c') is None
    other = begin(store, project='foreign')
    assert other.attempt_id != current.attempt_id
    assert len(store.read_events('foreign', 'c')) == 1


def test_retry_has_one_user_message_and_frozen_request_input(store):
    kwargs = dict(user_message={'text': 'One request'}, engine={'provider': 'example'}, request_id='request')
    old = store.begin_attempt('p', 'c', event_id='first-attempt', **kwargs)
    retry = store.begin_attempt('p', 'c', event_id='retry-attempt', expected_attempt=old, **kwargs)
    assert retry != old
    assert store.begin_attempt('p', 'c', event_id='retry-attempt', **kwargs) == retry
    events = store.read_events('p', 'c')
    assert events[0].payload['user_message'] == {'text': 'One request'}
    assert events[1].payload == {'request_id': 'request', 'engine': {'provider': 'example'}}
    for changed in ({'user_message': {'text': 'changed'}}, {'engine': {'provider': 'other'}}):
        with pytest.raises(EventConflict):
            store.begin_attempt('p', 'c', event_id='another-retry', **(kwargs | changed))
    assert len(store.read_events('p', 'c')) == 2


def test_delayed_retry_cannot_supersede_new_request(store):
    kwargs = dict(user_message={'text': 'A'}, engine={}, request_id='a')
    a = store.begin_attempt('p', 'c', event_id='a-start', **kwargs)
    b = begin(store, event='b-start')
    for expected in (None, a, b, replace(b, project_id='other')):
        with pytest.raises(StaleAttempt):
            store.begin_attempt('p', 'c', event_id='a-retry', expected_attempt=expected, **kwargs)
        assert store.get_conversation('p', 'c')['active_attempt'] == b.attempt_id
        assert len(store.read_events('p', 'c')) == 2


def test_competing_retries_compare_and_swap(store):
    kwargs = dict(user_message={'text': 'A'}, engine={}, request_id='a')
    first = store.begin_attempt('p', 'c', event_id='first', **kwargs)
    def retry(i):
        try:
            return ConversationStore(store.db_path).begin_attempt('p', 'c', event_id=f'retry-{i}', expected_attempt=first, **kwargs)
        except StaleAttempt:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(retry, range(8)))
    winners = [token for token in results if token is not None]
    assert len(winners) == 1
    assert store.get_conversation('p', 'c')['active_attempt'] == winners[0].attempt_id
    assert len(store.read_events('p', 'c')) == 2


def test_retry_after_restore_requires_new_user_request(store):
    kwargs = dict(user_message={'text': 'A'}, engine={}, request_id='a')
    first = store.begin_attempt('p', 'c', event_id='first', **kwargs)
    store.delete_conversation('p', 'c')
    store.restore_conversation('p', 'c')
    with pytest.raises(StaleAttempt):
        store.begin_attempt('p', 'c', event_id='retry', expected_attempt=first, **kwargs)
    assert store.get_conversation('p', 'c')['active_attempt'] is None
    assert len(store.read_events('p', 'c')) == 1


def test_parallel_instances_and_conversations(store):
    tokens = [begin(store, conversation=f'c{i}') for i in range(4)]
    def write(i):
        instance = ConversationStore(store.db_path)
        append(instance, tokens[i % 4], event=f'event-{i}')
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write, range(80)))
    for i in range(4):
        events = store.read_events('p', f'c{i}')
        assert [e.sequence for e in events] == list(range(1, 22))
        assert len({e.event_id for e in events}) == 21


def test_parallel_initial_creation_and_same_id(store):
    def create(_):
        return begin(ConversationStore(store.db_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(create, range(16)))
    assert len(set(tokens)) == 1
    assert len(store.read_events('p', 'c')) == 1


def test_pagination_stable_while_new_events_arrive(store):
    token = begin(store)
    append(store, token, event='a')
    first = store.read_events('p', 'c', limit=1)
    append(store, token, event='b')
    second = store.read_events('p', 'c', after=first[-1].sequence, limit=1000)
    assert [e.sequence for e in first + second] == [1, 2, 3]
    assert store.read_events('p', 'c', after=3) == []


def test_tombstone_denies_resurrection_and_restore_revokes_token(store):
    token = begin(store)
    store.delete_conversation('p', 'c')
    assert store.read_events('p', 'c') == []
    assert store.get_conversation('p', 'c') is None
    assert len(store.read_events('p', 'c', include_deleted=True)) == 1
    with pytest.raises(ConversationUnavailable):
        append(store, token)
    with pytest.raises(ConversationUnavailable):
        begin(store, event='new')
    store.restore_conversation('p', 'c')
    with pytest.raises(StaleAttempt):
        append(store, token)
    current = begin(store, event='new')
    store.restore_conversation('p', 'c')  # No-op restore does not revoke a valid token.
    append(store, current)
    assert len(store.read_events('p', 'c')) == 3


def test_begin_rolls_back_conversation_and_attempt_on_insert_failure(store, monkeypatch):
    def fail(*args):
        raise RuntimeError('injected write failure')
    with monkeypatch.context() as scoped:
        scoped.setattr(store, '_insert', fail)
        with pytest.raises(RuntimeError):
            begin(store)
    # Initial schema creation also rolls back. Initialize a valid different row.
    old = begin(store)
    with monkeypatch.context() as scoped:
        scoped.setattr(store, '_insert', fail)
        with pytest.raises(RuntimeError):
            begin(store, event='next')
    append(store, old)
    assert [e.event_id for e in store.read_events('p', 'c')] == ['start', 'answer']


@pytest.mark.parametrize('version,app,unknown_table', [(2, APPLICATION_ID, False), (0, 0, True), (1, 0, False)])
def test_unknown_schema_refused_without_modification(store, version, app, unknown_table):
    store.db_path.parent.mkdir()
    with sqlite3.connect(store.db_path) as db:
        db.execute(f'PRAGMA user_version={version}')
        db.execute(f'PRAGMA application_id={app}')
        if unknown_table:
            db.execute('CREATE TABLE unrelated (secret TEXT)')
    before = store.db_path.read_bytes()
    with pytest.raises(SchemaError):
        begin(store)
    with pytest.raises(SchemaError):
        store.read_events('p', 'c')
    assert store.db_path.read_bytes() == before


@pytest.mark.parametrize('payload', [{'x': float('nan')}, {'x': float('inf')}, {1: 'invalid'}, {'x': {1, 2}}, {'x': (1, 2)}])
def test_invalid_json_rejected_without_events(store, payload):
    token = begin(store)
    with pytest.raises((ValueError, TypeError)):
        append(store, token, payload=payload)
    assert len(store.read_events('p', 'c')) == 1


@pytest.mark.parametrize('identifier', ['', ' ', 'bad\x00id', 'x' * 513, None])
def test_invalid_ids_do_not_create_store(store, identifier):
    with pytest.raises(ValueError):
        begin(store, project=identifier)
    assert not store.db_path.exists()


def test_reserved_kind_and_bad_cursor(store):
    token = begin(store)
    with pytest.raises(ValueError):
        store.append_event(token, event_id='fake', kind='attempt_started', payload={})
    for arguments in ({'after': -1}, {'after': True}, {'limit': 0}, {'limit': 10001}):
        with pytest.raises(ValueError):
            store.read_events('p', 'c', **arguments)


@pytest.mark.parametrize('kind,payload', [('user_message', {}), ('assistant_message', {'text': 42}), ('tool_call', {'name': 'read', 'input': {}}), ('tool_result', {'call_id': 'call'}), ('thinking', {'text': 'thinking', 'attachments': [{'path': 'file'}]})])
def test_invalid_content_contract_cannot_enter_store(store, kind, payload):
    token = begin(store)
    with pytest.raises(ValueError):
        store.append_event(token, event_id='bad', kind=kind, payload=payload)
    assert len(store.read_events('p', 'c')) == 1


def test_invalid_user_content_does_not_create_store(store):
    with pytest.raises(ValueError):
        store.begin_attempt('p', 'c', user_message={}, engine={}, event_id='bad')
    assert not store.db_path.exists()
