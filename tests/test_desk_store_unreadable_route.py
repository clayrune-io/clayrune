"""MC-1187 follow-up F2: StoreUnreadable reaches a Desk route as a 503 with a closed body.

An unreadable desk.json is set aside and writers raise `StoreUnreadable`
(`mc/desk_store_guard.py`). That used to reach most Desk routes as a bare 500 with
no body a person could read. One blueprint-level handler (`mc/blueprints/
desk_store_unreadable_routes.py`) now answers every Desk route with a 503
`{"error": "desk_store_unreadable", "message", "path"}`; `path` is the file name
only. The Desk connect package's own `StoreUnreadable` is a different class and is
not touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from mc import desk as _desk
from mc import desk_store_guard as guard
from mc.blueprints import desk_routes, desk_store_unreadable_routes as handler_bp
from mc.desk_connect import custom_connection_store

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    guard._unmoved.clear()
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: False)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['PROPAGATE_EXCEPTIONS'] = False      # answer an unhandled error as the 500 a browser sees
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(handler_bp.bp)

    @app.route('/api/desk/connect/custom-probe')
    def _other_class():
        raise custom_connection_store.StoreUnreadable('the approvals record is unreadable')
    yield app.test_client()
    guard._unmoved.clear()


def _corrupt(tmp_path_store):
    _desk.create_campaign('t', 'thesis')
    good = _desk.STORE_PATH.read_text(encoding='utf-8')
    _desk.STORE_PATH.write_text(good[:len(good) // 2], encoding='utf-8')


def test_a_write_after_a_quarantine_is_a_503_with_the_closed_body(client):
    _corrupt(None)
    assert client.get('/api/desk/campaigns').status_code == 200          # the read sets the file aside
    r = client.post('/api/desk/campaigns', json={'title': 'x', 'thesis': 'y'})
    assert r.status_code == 503, r.get_data(as_text=True)
    body = r.get_json()
    assert set(body) == {'error', 'message', 'path'}
    assert body['error'] == 'desk_store_unreadable'
    assert body['path'].startswith('desk.json.unreadable-')
    assert '/' not in body['path'] and chr(92) not in body['path']
    assert str(_desk.STORE_PATH.parent) not in r.get_data(as_text=True)
    assert body['message'] and 'Traceback' not in body['message']


def test_another_desk_write_route_answers_the_same_way(client):
    _corrupt(None)
    client.get('/api/desk/campaigns')
    r = client.post('/api/desk/accounts', json={'platform': 'blog', 'identity': 'b'})
    assert r.status_code == 503 and r.get_json()['error'] == 'desk_store_unreadable'


def test_a_store_that_could_not_be_moved_names_desk_json(client, monkeypatch):
    _corrupt(None)
    def _denied(self, target):
        raise PermissionError(32, 'in use')
    monkeypatch.setattr(Path, 'rename', _denied)
    client.get('/api/desk/campaigns')
    r = client.post('/api/desk/campaigns', json={'title': 'x', 'thesis': 'y'})
    assert r.status_code == 503 and r.get_json()['path'] == 'desk.json'


def test_a_healthy_store_is_not_affected(client):
    r = client.post('/api/desk/campaigns', json={'title': 'x', 'thesis': 'y'})
    assert r.status_code in (200, 201), r.get_data(as_text=True)


def test_the_connect_packages_own_store_unreadable_is_left_alone(client):
    r = client.get('/api/desk/connect/custom-probe')
    assert r.status_code == 500
