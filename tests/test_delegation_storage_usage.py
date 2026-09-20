"""WARN-ONLY logical delivery payload accounting."""
import json

from flask import Flask

from mc.blueprints import agent_routes as ar
from mc.delegation_delivery import DeliveryStore


def _payload(event_id: str, text: str = 'é🙂 result') -> dict:
    return {
        'event_id': event_id, 'child_session_id': 'child', 'status': 'completed',
        'task': 'private task', 'summary': text, 'message': text,
        'provider': 'codex', 'model': 'gpt-5.6-luna',
    }


def _client(store, monkeypatch):
    app = Flask('delivery-usage')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delivery_store', store)
    return app.test_client()


def test_usage_is_exact_utf8_per_table_and_survives_reopen(tmp_path):
    path = tmp_path / 'delegation_delivery.sqlite3'
    store = DeliveryStore(path)
    payload = _payload('e1')
    encoded = store._payload('project-a', 'parent-a', payload).encode('utf-8')
    store.record_completion_source('e1', 'project-a', 'parent-a', payload)
    assert store.enqueue('e1', 'project-a', 'parent-a', payload) is True
    assert store.accept('e1', 'project-a', 'parent-a', payload) is True
    usage = store.payload_usage('project-a')
    assert usage['row_count'] == 3
    assert {name: item['payload_bytes'] for name, item in usage['tables'].items()} == {
        'completion_sources': len(encoded), 'outbox': len(encoded), 'inbox': len(encoded)}
    reopened = DeliveryStore(path)
    assert reopened.payload_usage('project-a') == usage


def test_usage_is_project_scoped_and_delete_removes_delivery_rows(tmp_path):
    path = tmp_path / 'delegation_delivery.sqlite3'
    store = DeliveryStore(path)
    payload = _payload('a')
    store.record_completion_source('a', 'project-a', 'parent-a', payload)
    store.enqueue('a', 'project-a', 'parent-a', payload)
    other = _payload('b')
    store.enqueue('b', 'project-b', 'parent-b', other)
    assert store.payload_usage('project-a')['row_count'] == 2
    assert store.payload_usage('project-b')['row_count'] == 1
    store.revoke_project('project-a')
    assert store.payload_usage('project-a') == {
        'tables': {'completion_sources': {'row_count': 0, 'payload_bytes': 0},
                   'outbox': {'row_count': 0, 'payload_bytes': 0},
                   'inbox': {'row_count': 0, 'payload_bytes': 0}},
        'row_count': 0, 'payload_bytes': 0}
    assert store.payload_usage('project-b')['row_count'] == 1


def test_threshold_equality_over_and_disabled_are_advisory_only(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    payload = _payload('e1')
    client = _client(store, monkeypatch)
    # Keep the warning active while every durable completion write occurs.
    monkeypatch.setitem(ar.state.CONFIG, 'delegation_payload_warning_bytes', 1)
    store.record_completion_source('e1', 'p', 'parent', payload)
    assert store.enqueue('e1', 'p', 'parent', payload) is True
    assert store.accept('e1', 'p', 'parent', payload) is True
    exact = store.payload_usage('p')
    assert exact['payload_bytes'] > 1
    assert client.get('/api/project/p/agent/delegation/status-list').get_json()['usage']['warning'] is True
    # Exact duplicate writes/retries are idempotent and cannot alter counts.
    assert store.record_completion_source('e1', 'p', 'parent', payload) is None
    assert store.enqueue('e1', 'p', 'parent', payload) is False
    assert store.accept('e1', 'p', 'parent', payload) is False
    assert store.payload_usage('p') == exact
    # Equality warns; one byte over does too; 0 disables warning only.
    monkeypatch.setitem(ar.state.CONFIG, 'delegation_payload_warning_bytes', exact['payload_bytes'])
    assert client.get('/api/project/p/agent/delegation/status-list').get_json()['usage']['warning'] is True
    monkeypatch.setitem(ar.state.CONFIG, 'delegation_payload_warning_bytes', exact['payload_bytes'] + 1)
    assert client.get('/api/project/p/agent/delegation/status-list').get_json()['usage']['warning'] is False
    monkeypatch.setitem(ar.state.CONFIG, 'delegation_payload_warning_bytes', 0)
    assert client.get('/api/project/p/agent/delegation/status-list').get_json()['usage']['warning'] is False
    # The warning never gates either side of delivery or rewrites the payload.
    with store._db() as db:
        stored = db.execute('SELECT payload FROM outbox WHERE event_id=?', ('e1',)).fetchone()[0]
    assert stored == store._payload('p', 'parent', payload)


def test_usage_read_failure_is_unknown_not_zero_or_payload_leak(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    store.enqueue('secret-event', 'p', 'parent', _payload('secret-event', 'SECRET RESULT'))
    client = _client(store, monkeypatch)
    def broken(_project_id):
        raise RuntimeError('SECRET RESULT credentials=do-not-leak')
    monkeypatch.setattr(store, 'payload_usage', broken)
    response = client.get('/api/project/p/agent/delegation/status-list')
    body = response.get_json()
    assert response.status_code == 200
    assert body['usage'] == {
        'status': 'unknown', 'reason_code': 'usage_unavailable',
        'reason': 'Payload usage is unavailable; warning state is unknown.'}
    assert 'SECRET RESULT' not in json.dumps(body)
    assert 'credentials' not in json.dumps(body)
