"""MC-1080 routing writes: real blueprint, passcode helper, throwaway state."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import caller_attribution as ca, project_routing_guard as routing, state
from mc.blueprints import local_auth as la, project_routes as pr


BROWSER = {'Origin': 'http://localhost:5199'}
PASSCODE = 'routing-test-proof'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    projects = tmp_path / 'projects'
    projects.mkdir()
    record = projects / 'testproj.json'
    record.write_text(json.dumps({
        'id': 'testproj', 'name': 'Original', 'backlog': [],
        'project_path': str(tmp_path / 'original'), 'last_updated': 'unchanged',
    }), encoding='utf-8')
    monkeypatch.setattr(pr, 'DATA_DIR', projects)
    monkeypatch.setattr(pr, 'get_manager', lambda pid: SimpleNamespace(lock=threading.Lock()))
    monkeypatch.setattr(pr, '_runtime_lifecycle_service', None)
    monkeypatch.setitem(state.CONFIG, 'auto_workspace_base', str(tmp_path / 'workspace'))
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(la, '_LOCAL_AUTH_FAILS', {})
    la._local_auth_set_passcode(PASSCODE)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(ca.UNATTRIBUTED))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(pr.bp)
    with app.test_client() as c:
        yield c, record, tmp_path


def test_exact_routing_scope():
    assert routing.PROJECT_ROUTING_KEYS == {'id', 'project_path'}


@pytest.mark.parametrize('key', ['id', 'project_path'])
@pytest.mark.parametrize('value_kind', ['changed', 'unchanged', 'null'])
@pytest.mark.parametrize('caller', ['no_origin', 'attributed_forged_origin'])
def test_agent_refused_whole_write_before_side_effects(client, monkeypatch, key, value_kind, caller):
    c, record, root = client
    before = record.read_bytes()
    old = json.loads(before)
    value = None if value_kind == 'null' else old[key] if value_kind == 'unchanged' else 'rewritten'
    if caller == 'attributed_forged_origin':
        monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))

    def unexpected(*args):
        pytest.fail('refusal must precede persistence, lifecycle, and passcode checks')

    monkeypatch.setattr(pr, 'save_project', unexpected)
    monkeypatch.setattr(pr, 'get_manager', unexpected)
    monkeypatch.setattr(routing, '_require_human_passcode', unexpected)
    r = c.post('/api/project/testproj', json={
        key: value, 'name': 'Must not apply', 'log_msg': 'Must not log',
        'source': 'ui', 'client': 'mobile', 'passcode': PASSCODE,
    }, headers=BROWSER if caller != 'no_origin' else {})
    assert r.status_code == 403
    assert r.get_json()['protected_keys'] == [key]
    assert key in r.get_json()['error']
    assert record.read_bytes() == before
    assert not (root / 'workspace').exists()


@pytest.mark.parametrize('headers', [{}, BROWSER])
def test_agent_create_refused_without_record_workspace_or_lifecycle(client, monkeypatch, headers):
    c, record, root = client
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(ca.ATTRIBUTED, 'builder'))
    def unexpected(*args):
        pytest.fail('refused create must have no side effects')
    monkeypatch.setattr(pr, 'save_project', unexpected)
    monkeypatch.setattr(pr, 'get_manager', unexpected)
    r = c.post('/api/project/newproj', json={
        'id': 'other', 'project_path': '', 'name': 'Refused', 'passcode': PASSCODE,
    }, headers=headers)
    assert r.status_code == 403
    assert r.get_json()['protected_keys'] == ['id', 'project_path']
    assert not (record.parent / 'newproj.json').exists()
    assert not (root / 'workspace').exists()


@pytest.mark.parametrize('proof,error,code', [(None, 'bad_passcode', 403), ('wrong', 'bad_passcode', 403),
                                           (7, 'bad_passcode', 403), ([], 'bad_passcode', 403)])
def test_forged_origin_needs_human_proof(client, proof, error, code):
    c, record, root = client
    before = record.read_bytes()
    r = c.post('/api/project/testproj', json={'id': 'rewritten', 'project_path': '',
                                            'passcode': proof, 'name': 'Refused'}, headers=BROWSER)
    assert r.status_code == code
    assert r.get_json()['error'] == error
    assert record.read_bytes() == before
    assert not (root / 'workspace').exists()


def test_no_configured_passcode_refuses_create(client, monkeypatch):
    c, record, root = client
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', root / 'absent.json')
    r = c.post('/api/project/newproj', json={'project_path': '', 'passcode': PASSCODE}, headers=BROWSER)
    assert r.status_code == 403
    assert r.get_json()['error'] == 'passcode_required'
    assert not (record.parent / 'newproj.json').exists()
    assert not (root / 'workspace').exists()


def test_throttled_proof_has_no_side_effects(client, monkeypatch):
    c, record, _ = client
    before = record.read_bytes()
    monkeypatch.setattr(la, '_local_auth_try_passcode', lambda *args: None)
    r = c.post('/api/project/testproj', json={'project_path': '', 'passcode': PASSCODE}, headers=BROWSER)
    assert r.status_code == 429
    assert r.get_json()['error'] == 'too_many_attempts'
    assert record.read_bytes() == before


@pytest.mark.parametrize('status', [ca.UNATTRIBUTED, ca.UNAVAILABLE])
@pytest.mark.parametrize('action', ['create_blank', 'import', 'edit_path', 'edit_id'])
def test_human_routing_write_succeeds_without_persisting_proof(client, monkeypatch, status, action):
    c, record, root = client
    monkeypatch.setattr(ca, 'attribute_caller', lambda *args: ca.Attribution(status))
    pid = 'newproj' if action in ('create_blank', 'import') else 'testproj'
    body = {'name': 'Human edit', 'passcode': PASSCODE}
    if action == 'edit_id':
        body['id'] = 'human-id'
    else:
        body['project_path'] = '' if action == 'create_blank' else str(root / 'selected')
    r = c.post(f'/api/project/{pid}', json=body, headers=BROWSER)
    assert r.status_code == 200, r.get_json()
    saved = json.loads((record.parent / f'{pid}.json').read_text(encoding='utf-8'))
    assert saved['name'] == 'Human edit'
    assert 'passcode' not in saved
    if action == 'create_blank':
        assert (root / 'workspace' / pid).is_dir()
    elif action == 'edit_id':
        assert saved['id'] == 'human-id'
    else:
        assert saved['project_path'] == body['project_path']


def test_ordinary_agent_write_skips_guard_and_drops_proof(client, monkeypatch):
    c, record, _ = client
    def unexpected(*args):
        pytest.fail('ordinary fields must not require attribution or passcode')
    monkeypatch.setattr(ca, 'attribute_caller', unexpected)
    monkeypatch.setattr(routing, '_require_human_passcode', unexpected)
    r = c.post('/api/project/testproj', json={'name': 'Agent progress', 'passcode': 'must-not-save'})
    assert r.status_code == 200
    saved = json.loads(record.read_text(encoding='utf-8'))
    assert saved['name'] == 'Agent progress'
    assert saved['id'] == 'testproj'
    assert 'passcode' not in saved
