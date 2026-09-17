"""Read-only recovery status listing tests."""
import json

from flask import Flask

from mc.blueprints import agent_routes as ar
from mc.delegation_delivery import DeliveryStore


def _payload(event_id, child='child', summary='secret full result'):
    return {'event_id': event_id, 'child_session_id': child, 'who': 'worker',
            'status': 'completed', 'task': 'private task', 'summary': summary,
            'provider': 'codex', 'model': 'gpt-5.6-luna', 'message': summary}


def test_status_list_is_project_scoped_paginated_and_payload_free(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    for event_id in ('e1', 'e2', 'e3'):
        store.enqueue(event_id, 'project-a', 'parent-a',
                     _payload(event_id, summary='DO NOT LEAK ' + event_id))
    store.enqueue('other', 'project-b', 'parent-b', _payload('other'))
    blocked = store.claim_outbox()
    assert blocked
    store._finish('outbox', blocked['event_id'], blocked['fence_token'], False,
                  'SECRET-CREDENTIAL task=result', 'blocked')
    app = Flask('delivery-status')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delivery_store', store)
    client = app.test_client()

    first = client.get('/api/project/project-a/agent/delegation/status-list?limit=2')
    assert first.status_code == 200
    body = first.get_json()
    assert body['total'] == 3
    assert len(body['items']) == 2
    assert all(item['state'] in ('pending', 'blocked', 'uncertain', 'recovery_required')
               for item in body['items'])
    assert all(key not in json.dumps(body) for key in
               ('DO NOT LEAK', 'SECRET-CREDENTIAL', 'private task', 'message',
                'payload', 'summary'))

    second = client.get('/api/project/project-a/agent/delegation/status-list?limit=2&offset=2')
    assert second.get_json()['total'] == 3
    assert len(second.get_json()['items']) == 1
    other = client.get('/api/project/project-b/agent/delegation/status-list')
    assert other.get_json()['total'] == 1
    wrong = client.get('/api/project/wrong/agent/delegation/status-list')
    assert wrong.status_code == 200
    assert wrong.get_json()['total'] == 0


def test_status_list_rejects_invalid_pagination_without_mutation(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    store.enqueue('e1', 'p', 'parent', _payload('e1'))
    app = Flask('delivery-status-validation')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delivery_store', store)
    for query in ('limit=0', 'limit=101', 'offset=-1'):
        response = app.test_client().get('/api/project/p/agent/delegation/status-list?' + query)
        assert response.status_code == 400
    assert store.status('outbox', 'e1', 'p')['state'] == 'pending'


def test_status_list_order_is_deterministic_for_same_event_across_tables(tmp_path):
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3', clock=lambda: 10.0)
    payload = _payload('same')
    store.enqueue('same', 'p', 'parent', payload)
    assert store.accept('same', 'p', 'parent', payload) is True
    result = store.list_recovery_status('p', limit=10)
    assert [(item['table'], item['event_id']) for item in result['items']] == [
        ('inbox', 'same'), ('outbox', 'same')]
