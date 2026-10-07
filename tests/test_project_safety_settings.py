"""MC-1076: safety settings cannot disable the gate through project CRUD.

Exercise the real blueprint with throwaway project files, never live settings.
"""
from __future__ import annotations

import json

import pytest
from flask import Flask

from mc import caller_attribution as ca, project_safety_settings as safety, state
from mc.blueprints import project_routes as pr


VALUES = {
    'merge_requires_review': False,
    'worktree_isolation': False,
    'worktree_base_branch': 'other-base',
    'worktree_shared_runtime': False,
    'code_sync_branch': 'other-sync',
    'steward_mode': 'on',
    'steward_objective': 'Changed objective',
    'steward_cadence_minutes': 5,
}
BROWSER = {'Origin': 'http://localhost:5199'}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    projects = tmp_path / 'projects'
    projects.mkdir()
    record = projects / 'testproj.json'
    record.write_text(json.dumps({
        'id': 'testproj', 'name': 'Original', 'backlog': [],
        'merge_requires_review': True, 'last_updated': 'unchanged',
    }), encoding='utf-8')
    monkeypatch.setattr(pr, 'DATA_DIR', projects)
    monkeypatch.setitem(state.CONFIG, 'auto_workspace_base', str(tmp_path / 'workspace'))
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *args: ca.Attribution(ca.UNATTRIBUTED))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(pr.bp)
    with app.test_client() as c:
        yield c, record, tmp_path


def test_exact_safety_key_scope():
    assert safety.PROJECT_SAFETY_KEYS == set(VALUES)


@pytest.mark.parametrize('key', VALUES)
@pytest.mark.parametrize('origin', [False, True])
def test_attributed_agent_cannot_write_each_safety_key(client, monkeypatch, key, origin):
    c, record, _ = client
    before = record.read_bytes()
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))
    r = c.post('/api/project/testproj', json={key: VALUES[key]},
               headers=BROWSER if origin else {})
    assert r.status_code == 403
    assert r.get_json()['protected_keys'] == [key]
    assert key in r.get_json()['error']
    assert record.read_bytes() == before


def test_mixed_body_is_refused_before_any_record_mutation(client, monkeypatch):
    c, record, _ = client
    before = record.read_bytes()
    saves = []
    monkeypatch.setattr(pr, 'save_project', lambda *args: saves.append(args))
    r = c.post('/api/project/testproj', json={
        **VALUES, 'name': 'Must not apply', 'log_msg': 'Must not log',
        'default_character': '', 'source': 'ui', 'client': 'mobile',
    })
    assert r.status_code == 403
    assert r.get_json()['protected_keys'] == sorted(VALUES)
    assert all(key in r.get_json()['error'] for key in VALUES)
    assert record.read_bytes() == before
    assert saves == []


def test_refused_create_has_no_record_workspace_or_lifecycle_effects(client, monkeypatch):
    c, record, root = client

    def unexpected(*args):
        pytest.fail('refusal must precede project creation side effects')

    monkeypatch.setattr(pr, 'get_manager', unexpected)
    monkeypatch.setattr(pr, 'save_project', unexpected)
    r = c.post('/api/project/newproj', json={
        'name': 'Must not create', 'merge_requires_review': False,
    })
    assert r.status_code == 403
    assert not (record.parent / 'newproj.json').exists()
    assert not (root / 'workspace').exists()


@pytest.mark.parametrize('value', [None, True, False])
def test_protected_key_presence_is_refused_regardless_of_value(client, value):
    c, record, _ = client
    before = record.read_bytes()
    r = c.post('/api/project/testproj', json={'merge_requires_review': value})
    assert r.status_code == 403
    assert record.read_bytes() == before


@pytest.mark.parametrize('key', VALUES)
@pytest.mark.parametrize('status', [ca.UNATTRIBUTED, ca.UNAVAILABLE])
def test_human_browser_can_write_each_safety_key(client, monkeypatch, key, status):
    c, record, _ = client
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(status))
    r = c.post('/api/project/testproj', json={key: VALUES[key], 'name': 'Human edit'},
               headers=BROWSER)
    assert r.status_code == 200
    saved = json.loads(record.read_text(encoding='utf-8'))
    assert saved[key] == VALUES[key]
    assert saved['name'] == 'Human edit'


def test_agent_can_still_write_ordinary_fields_without_attribution_lookup(client, monkeypatch):
    c, record, _ = client

    def unexpected(*args):
        pytest.fail('ordinary writes must not require caller attribution')

    monkeypatch.setattr(ca, 'attribute_caller', unexpected)
    r = c.post('/api/project/testproj', json={'name': 'Agent edit', 'log_msg': 'Progress'})
    assert r.status_code == 200
    saved = json.loads(record.read_text(encoding='utf-8'))
    assert saved['name'] == 'Agent edit'
    assert saved['activity_log'][0]['msg'] == 'Progress'
    assert saved['merge_requires_review'] is True
