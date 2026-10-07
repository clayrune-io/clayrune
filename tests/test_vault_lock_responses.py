"""Locked-vault hints preserve API refusals and never read or unlock the vault."""
import pytest
from flask import Flask, jsonify

from mc import desk_engines, secrets_store
from mc.blueprints import desk_routes, vault_lock_responses


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(vault_lock_responses.bp)
    app.register_blueprint(desk_routes.bp)
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: pytest.fail('no vault probe in response hook'))
    return app


@pytest.mark.parametrize('path,body,status', [
    ('/api/desk/connect/x/start', {'error': 'Unlock first', 'code': 'vault_locked'}, 409),
    ('/api/desk/engines/jobs', {'error': 'Your vault is locked. Unlock it; your saved key is still there.', 'code': 'not_connected', 'vault_entry': 'key'}, 409),
    ('/api/secrets/exec', {'error': 'vault_locked', 'message': 'Unlock first'}, 423),
    ('/api/passkeys/credentials', {'error': 'passkey_vault_locked'}, 503),
])
def test_refusal_is_flagged_with_status_and_original_contract(app, path, body, status):
    app.add_url_rule(path, 'fixture', lambda: (jsonify(body), status))
    response = app.test_client().get(path)
    assert response.status_code == status
    assert response.get_json() == {**body, 'vault_locked': True}


@pytest.mark.parametrize('path', ['/api/desk/engines/estimate', '/api/desk/engines/render/estimate'])
def test_actual_studio_not_connected_response(app, monkeypatch, path):
    def refused(*args, **kwargs):
        raise desk_engines.NotConnected('gemini', 'Your vault is locked. Unlock it; your saved key is still there.', 'gemini-api')
    monkeypatch.setattr(desk_engines, 'estimate', refused)
    monkeypatch.setattr(desk_engines, 'estimate_render', refused)
    response = app.test_client().post(path, json={})
    assert response.status_code == 409
    body = response.get_json()
    assert body['vault_locked'] is True and body['code'] == 'not_connected'
    assert body['vault_entry'] == 'gemini-api'


def test_nested_status_and_unlocked_siblings(app):
    body = {'engines': [{'state': 'vault_locked'}, {'state': 'connected'}],
            'coverage': [{'state': 'not_connected', 'reason': 'Your vault is locked.'}],
            'error': {'code': 'wrong_passphrase'}}
    app.add_url_rule('/api/desk/status', 'fixture', lambda: jsonify(body))
    out = app.test_client().get('/api/desk/status').get_json()
    assert out['engines'] == [{'state': 'vault_locked', 'vault_locked': True}, {'state': 'connected'}]
    assert out['coverage'][0]['vault_locked'] is True
    assert 'vault_locked' not in out and out['error'] == body['error']


@pytest.mark.parametrize('body', [{'code': 'not_connected', 'error': 'Sign in again'},
                                 {'state': 'unlocked'}, {'error': 'bad_passcode'},
                                 {'message': 'Vault is unlocked.'}])
def test_unrelated_answers_do_not_gain_a_lock_flag(app, body):
    app.add_url_rule('/api/desk/status', 'fixture', lambda: jsonify(body))
    assert app.test_client().get('/api/desk/status').get_json() == body


def test_non_json_and_other_api_surfaces_are_untouched(app):
    app.add_url_rule('/api/desk/text', 'text', lambda: ('vault locked', 409))
    app.add_url_rule('/api/projects', 'projects', lambda: jsonify({'message': 'vault locked'}))
    client = app.test_client()
    assert client.get('/api/desk/text').data == b'vault locked'
    assert client.get('/api/projects').get_json() == {'message': 'vault locked'}
