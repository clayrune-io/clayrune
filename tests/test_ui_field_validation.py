"""MC-1183: accepting routes refuse unsafe values before any state write."""
import json
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import state
from mc.blueprints import project_routes as pr, settings_routes as sr, hivemind_routes as hr


@pytest.fixture()
def client(tmp_path, monkeypatch):
    projects = tmp_path / 'projects'
    projects.mkdir()
    record = projects / 'fixture.json'
    record.write_text(json.dumps({'id': 'fixture', 'name': 'Fixture', 'backlog': [
        {'id': 'item', 'text': 'Original', 'status': 'old-status', 'priority': 'old-priority'}
    ]}), encoding='utf8')
    monkeypatch.setattr(pr, 'DATA_DIR', projects)
    monkeypatch.setattr(pr, 'get_manager', lambda _: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(pr, '_runtime_lifecycle_service', None)
    monkeypatch.setitem(state.CONFIG, 'auto_workspace_base', str(tmp_path / 'workspace'))
    settings = {'domains': [{'id': 'fixture', 'color': 'old-color', 'bg': 'old-bg'}]}
    monkeypatch.setattr(sr, '_load_settings', lambda *, for_update=False: json.loads(json.dumps(settings)))
    monkeypatch.setattr(sr, '_save_settings', lambda value: settings.update(value))
    monkeypatch.setattr(hr, '_hm_load_manifest', lambda _: {'project_id': 'fixture'})
    monkeypatch.setattr(hr, '_hm_load_workstream', lambda *a: {'id': 'ws', 'title': 'Original'})
    def no_write(*a, **kw):
        pytest.fail('refused workstream must not be saved')
    monkeypatch.setattr(hr, '_hm_save_workstream', no_write)
    app = Flask(__name__)
    app.config['TESTING'] = True
    for bp in [pr.bp, sr.bp, hr.bp]:
        app.register_blueprint(bp)
    with app.test_client() as c:
        yield c, record, settings, tmp_path


@pytest.mark.parametrize('field', ['status', 'priority'])
@pytest.mark.parametrize('bad', ["x');window.hit=1;//", '<img src=x>', None, [], {}])
@pytest.mark.parametrize('method,path', [('post', '/api/project/fixture/backlog'), ('patch', '/api/project/fixture/backlog/item')])
def test_backlog_refuses_bad_fields_without_touching_legacy(client, field, bad, method, path):
    c, record, _, _ = client
    before = record.read_bytes()
    r = getattr(c, method)(path, json={'text': 'Must not write', field: bad})
    assert r.status_code == 400
    assert field in r.get_json()['error']
    assert record.read_bytes() == before


@pytest.mark.parametrize('field,values', [('status', ['open', 'in_progress', 'blocked', 'done', 'wontdo']), ('priority', ['low', 'normal', 'high', 'medium', 'P1'])])
def test_backlog_accepts_supported_values(client, field, values):
    c, record, _, _ = client
    for value in values:
        r = c.patch('/api/project/fixture/backlog/item', json={field: value})
        assert r.status_code == 200
        item = json.loads(record.read_text(encoding='utf8'))['backlog'][0]
        assert item[field] == value
        assert item['priority' if field == 'status' else 'status'].startswith('old-')


@pytest.mark.parametrize('pid', ["bob's_site", 'bad"id', '../bad', 'BadId', 'x' * 65])
def test_project_id_refused_before_workspace_creation(client, pid):
    # see test_reserved_underscore_project_ids_pass_the_shape_rule for the ids that must pass
    c, _, _, root = client
    from urllib.parse import quote
    r = c.post('/api/project/' + quote(pid, safe=''), json={'name': 'Must not create'})
    # Slash-containing ids cannot match this route at all.
    assert r.status_code == (404 if '/' in pid else 400)
    assert not (root / 'workspace').exists()


@pytest.mark.parametrize('value', ['red" onmouseover="x', 'url(javascript:x)', None, [], {}])
@pytest.mark.parametrize('key', ['color', 'bg'])
@pytest.mark.parametrize('target', ['project', 'domain-add', 'domain-patch'])
def test_color_refused_without_writes(client, value, key, target):
    c, record, settings, root = client
    before_record = record.read_bytes()
    before_settings = json.dumps(settings)
    if target == 'project':
        r = c.post('/api/project/fixture', json={'modal_color': {key: value}})
    elif target == 'domain-add':
        r = c.post('/api/settings/domains/add', json={'id': 'new', key: value})
    else:
        r = c.patch('/api/settings/domains/fixture', json={key: value})
    assert r.status_code == 400
    assert key in r.get_json()['error']
    assert record.read_bytes() == before_record
    assert json.dumps(settings) == before_settings
    assert not (root / 'workspace').exists()


@pytest.mark.parametrize('color', ['#abc', '#aabbcc', '#aabbccdd', 'var(--accent-dim)'])
def test_valid_color_is_saved_without_rewriting_legacy_neighbor(client, color):
    c, _, settings, _ = client
    r = c.patch('/api/settings/domains/fixture', json={'color': color})
    assert r.status_code == 200
    assert settings['domains'][0] == {'id': 'fixture', 'color': color, 'bg': 'old-bg'}


def test_unrelated_edits_preserve_legacy_values(client):
    c, record, _, _ = client
    data = json.loads(record.read_text(encoding='utf8'))
    data['modal_color'] = {'color': 'old unsafe " color', 'bg': 'old-bg'}
    record.write_text(json.dumps(data), encoding='utf8')
    assert c.post('/api/project/fixture', json={'name': 'Renamed'}).status_code == 200
    saved = json.loads(record.read_text(encoding='utf8'))
    assert saved['modal_color'] == data['modal_color']
    assert saved['backlog'] == data['backlog']


@pytest.mark.parametrize('value', ["x');alert(1)//", '', None, [], 'Uppercase'])
def test_human_project_id_body_still_requires_allowlist(client, monkeypatch, value):
    c, record, _, _ = client
    monkeypatch.setattr(pr, 'require_human_routing_write', lambda *a, **kw: None)
    before = record.read_bytes()
    r = c.post('/api/project/fixture', json={'id': value})
    assert r.status_code == 400
    assert 'id must match' in r.get_json()['error']
    assert record.read_bytes() == before


@pytest.mark.parametrize('value', ['<img src=x>', '1', 1.5, True, None, []])
@pytest.mark.parametrize('target', ['inline', 'create', 'update'])
def test_workstream_priority_refused_before_save(client, value, target):
    c, _, _, _ = client
    if target == 'inline':
        r = c.post('/api/hivemind/create', json={'goal': 'Fixture', 'project_id': 'fixture', 'workstreams': [{'title': 'Fixture', 'priority': value}]})
    elif target == 'create':
        r = c.post('/api/hivemind/hm/workstreams/create', json={'title': 'Fixture', 'priority': value})
    else:
        r = c.put('/api/hivemind/hm/workstreams/ws', json={'priority': value})
    assert r.status_code == 400
    assert 'priority' in r.get_json()['error']


@pytest.mark.parametrize('pid', ['_ideas', '_incognito', 'mission_control', 'a'])
def test_reserved_underscore_project_ids_pass_the_shape_rule(pid):
    # `_ideas` and `_incognito` are real workspaces on every install.
    from mc.ui_field_validation import validate_project_fields
    assert validate_project_fields(pid, {}) is None
    assert validate_project_fields('fixture', {'id': pid}) is None
