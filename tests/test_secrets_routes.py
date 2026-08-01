"""Secrets vault HTTP surface — mc/blueprints/secrets_routes.py.

The load-bearing assertion here is negative: **no route returns a plaintext
value**. Everything else is ordinary CRUD.
"""

import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    from mc import secrets_store
    from mc.blueprints import secrets_routes
    secrets_store._dispensed.clear()
    app = Flask(__name__)
    app.register_blueprint(secrets_routes.bp)
    return app.test_client()


def _create(client, **over):
    body = {'name': 'reddit.password', 'value': SECRET,
            'description': 'launch account'}
    body.update(over)
    return client.post('/api/secrets', json=body)


def test_create_and_list(client):
    assert _create(client).status_code == 200
    r = client.get('/api/secrets')
    assert r.status_code == 200
    data = r.get_json()
    assert [s['name'] for s in data['secrets']] == ['reddit.password']
    assert data['secrets'][0]['placeholder'] == '{{secret:reddit.password}}'
    assert data['key_backend'] == 'file'
    assert data['key_at_rest_warning']  # file backend must warn


def test_no_route_returns_the_plaintext(client):
    _create(client)
    client.post('/api/secrets/check',
                json={'text': 'x {{secret:reddit.password}}'})
    for path in ('/api/secrets', '/api/secrets/audit'):
        assert SECRET not in client.get(path).get_data(as_text=True), path
    # …including the write and edit responses themselves.
    assert SECRET not in _create(client).get_data(as_text=True)
    patched = client.patch('/api/secrets/reddit.password',
                           json={'description': 'renamed'})
    assert SECRET not in patched.get_data(as_text=True)


def test_create_rejects_missing_value(client):
    r = client.post('/api/secrets', json={'name': 'a.b'})
    assert r.status_code == 400


def test_create_rejects_bad_name(client):
    r = client.post('/api/secrets', json={'name': 'Bad Name', 'value': 'xxxxxx'})
    assert r.status_code == 400


def test_patch_metadata_preserves_the_value(client):
    _create(client)
    r = client.patch('/api/secrets/reddit.password',
                     json={'scope': 'mission_control', 'allow_unattended': False})
    assert r.status_code == 200
    assert r.get_json()['scope'] == 'mission_control'
    assert r.get_json()['allow_unattended'] is False

    from mc import secrets_store
    assert secrets_store.get_secret_value(
        'reddit.password', consumer='test',
        project_id='mission_control') == SECRET


def test_patch_unknown_is_404(client):
    assert client.patch('/api/secrets/nope.none', json={}).status_code == 404


def test_delete(client):
    _create(client)
    assert client.delete('/api/secrets/reddit.password').status_code == 200
    assert client.delete('/api/secrets/reddit.password').status_code == 404
    assert client.get('/api/secrets').get_json()['secrets'] == []


def test_audit_records_the_write(client):
    _create(client)
    records = client.get('/api/secrets/audit').get_json()['records']
    assert records[0]['event'] == 'set'
    assert records[0]['name'] == 'reddit.password'


def test_check_reports_resolvability_without_decrypting(client):
    _create(client, scope='alpha')
    r = client.post('/api/secrets/check',
                    json={'text': 'login {{secret:reddit.password}} '
                                  'and {{secret:absent.one}}',
                          'project_id': 'alpha'})
    data = r.get_json()
    assert data['resolvable'] is False
    by_name = {x['name']: x for x in data['referenced']}
    assert by_name['reddit.password']['ok'] is True
    assert by_name['absent.one']['reason'] == 'not_found'
    # A check must not count as a use.
    assert client.get('/api/secrets').get_json()['secrets'][0]['use_count'] == 0


def test_check_flags_out_of_scope_and_unattended(client):
    _create(client, scope='alpha', allow_unattended=False)
    out = client.post('/api/secrets/check',
                      json={'text': '{{secret:reddit.password}}',
                            'project_id': 'beta'}).get_json()
    assert out['referenced'][0]['reason'] == 'out_of_scope'
    un = client.post('/api/secrets/check',
                     json={'text': '{{secret:reddit.password}}',
                           'project_id': 'alpha',
                           'unattended': True}).get_json()
    assert un['referenced'][0]['reason'] == 'unattended_blocked'
