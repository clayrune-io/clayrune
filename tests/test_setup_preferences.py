"""Setup can retain independent settings without accepting a refused model."""
import copy
import json

from flask import Flask
import pytest

from mc import agent_runtime, provider_preferences, state
from mc.blueprints import settings_routes


@pytest.fixture()
def client(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(settings_routes.bp)
    original = copy.deepcopy(state.CONFIG)
    state.CONFIG.clear()
    state.CONFIG.update({'default_provider': 'claude', 'setup_completed': True,
                         'provider_defaults': {'claude': {'model': 'sonnet'}}})
    monkeypatch.setattr(settings_routes, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr(settings_routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(settings_routes, '_require_human_passcode', lambda data: None)
    yield app.test_client(), tmp_path
    state.CONFIG.clear()
    state.CONFIG.update(original)


@pytest.mark.parametrize('provider', ['qwen', 'opencode', 'goose', 'aider', 'kiro'])
def test_no_tier_provider_keeps_other_setup_settings(client, provider):
    http, tmp_path = client
    metadata = provider_preferences.runtime_metadata(agent_runtime.get_runtime(provider))
    assert metadata['tiers'] == {}
    dest = str(tmp_path / 'backups')
    response = http.put('/api/config', json={
        'setup_save': True, 'backup_dest_dir': dest, 'backup_schedule': 'weekly',
        'default_provider': provider, 'provider_defaults': {provider: {'model': 'tier:balanced'}}})
    assert response.status_code == 200
    body = response.get_json()
    assert body['rejected']['provider_defaults']
    assert 'provider_defaults' not in body['updated']
    saved = json.loads(settings_routes.CONFIG_PATH.read_text())
    for config in (saved, state.CONFIG):
        assert config['backup_dest_dir'] == dest
        assert config['backup_schedule'] == 'weekly'
        assert config['default_provider'] == provider
        assert config['provider_defaults'] == {'claude': {'model': 'sonnet'}}
        assert config['setup_completed'] is True
        assert 'setup_save' not in config


def test_supported_setup_tier_saves_normally(client):
    http, _ = client
    response = http.put('/api/config', json={'setup_save': True, 'backup_schedule': 'daily',
        'provider_defaults': {'codex': {'model': 'tier:balanced'}}, 'default_provider': 'codex'})
    assert response.status_code == 200
    assert response.get_json()['rejected'] == {}
    assert state.CONFIG['provider_defaults']['codex']['model'] == 'tier:balanced'
    assert state.CONFIG['backup_schedule'] == 'daily'


@pytest.mark.parametrize('extra', [{}, {'setup_save': False},
    {'setup_save': True, 'agent_permission_mode': 'full-auto'}])
def test_ordinary_or_wider_batches_remain_atomic(client, extra):
    http, _ = client
    original = copy.deepcopy(state.CONFIG)
    response = http.put('/api/config', json={'backup_schedule': 'daily',
        'provider_defaults': {'qwen': {'model': 'tier:balanced'}}, **extra})
    assert response.status_code == 400
    assert state.CONFIG == original


def test_refused_effort_is_not_setup_model_recovery(client):
    http, _ = client
    original = copy.deepcopy(state.CONFIG)
    response = http.put('/api/config', json={'setup_save': True, 'backup_schedule': 'daily',
        'provider_defaults': {'qwen': {'model': 'tier:balanced', 'effort': 'invalid'}}})
    assert response.status_code == 400
    assert state.CONFIG == original


@pytest.mark.parametrize('failure', ['passcode', 'unattended', 'disk', 'backup'])
def test_setup_recovery_preserves_gates_and_persistence_validation(client, monkeypatch, failure):
    http, _ = client
    original = copy.deepcopy(state.CONFIG)
    body = {'setup_save': True, 'backup_schedule': 'daily',
            'provider_defaults': {'qwen': {'model': 'tier:balanced'}}}
    if failure == 'passcode':
        monkeypatch.setattr(settings_routes, '_require_human_passcode',
                            lambda data: ({'error': 'human only'}, 403))
    elif failure == 'unattended':
        monkeypatch.setattr(settings_routes, 'is_unattended_caller', lambda: True)
    elif failure == 'disk':
        def refuse(*args, **kwargs):
            raise OSError('disk unavailable')
        monkeypatch.setattr(settings_routes, 'write_json_atomic', refuse)
    else:
        body['backup_schedule'] = 'invalid'
    response = http.put('/api/config', json=body)
    assert response.status_code == {'passcode': 403, 'unattended': 403, 'disk': 500, 'backup': 400}[failure]
    assert state.CONFIG == original
