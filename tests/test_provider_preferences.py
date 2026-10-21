"""Scoped defaults preserve intent across providers, resets and persistence."""
import copy

from flask import Flask
import pytest

from mc import agent_runtime, engine_selection as es, provider_preferences as pp, state
from mc.blueprints import provider_preferences_routes as routes


def test_independent_namespaces_and_precedence():
    cfg = {'default_provider': 'codex', 'provider_defaults': {
        'codex': {'model': 'gpt-future', 'effort': 'high'},
        'claude': {'model': 'sonnet', 'effort': 'low'}}}
    project = {'provider_overrides': {'codex': {'effort': 'medium'}}}
    result = es.resolve_engine(cfg, project)
    assert (result.model, result.model_source, result.effort, result.effort_source) == (
        'gpt-future', 'global', 'medium', 'project')
    result = es.resolve_engine(cfg, project, provider_override='claude')
    assert (result.model, result.effort) == ('sonnet', 'low')
    result = es.resolve_engine(cfg, project, character={'provider': 'claude', 'model': 'opus'})
    assert (result.provider, result.model, result.model_source) == ('claude', 'opus', 'character')


def test_native_is_different_from_inherit_and_reset_never_revives_legacy():
    cfg = {'provider_defaults': {'codex': {'model': 'gpt-future', 'effort': 'high'}}}
    project = {'agent_model': 'old-custom', 'provider_overrides': {
        'codex': {'model': '', 'effort': ''}}}
    result = es.resolve_engine(cfg, project, provider_override='codex')
    assert (result.model, result.effort, result.model_source) == ('', '', 'project')
    project['provider_overrides'] = pp.patch_preferences(project, {
        'codex': {'model': None}}, project=True)
    result = es.resolve_engine(cfg, project, provider_override='codex')
    assert (result.model, result.effort) == ('gpt-future', '')
    assert project['agent_model'] == 'old-custom'


def test_materialization_preserves_foreign_terminal_native_and_generic_tiers():
    project = {'agent_model': 'claude-opus-5', 'agent_effort': 'high'}
    original = copy.deepcopy(project)
    scoped = pp.patch_preferences(project, {'codex': {'effort': 'low'}}, project=True)
    assert project == original
    assert scoped['claude']['model'] == 'claude-opus-5'
    assert scoped['codex'] == {'model': '', 'effort': 'low'}
    tier = pp.scoped_view({'agent_model': 'tier:balanced'})
    assert tier['claude']['model'] == tier['codex']['model'] == 'tier:balanced'
    assert pp.patch_preferences({'provider_defaults': scoped}, {}) == scoped


@pytest.mark.parametrize('patch', [None, [], {'unknown': {}}, {'claude': []},
    {'claude': {'permissions': 'full-auto'}}, {'codex': {'model': 'claude-opus-5'}},
    {'codex': {'effort': 'max'}}, {'gemini': {'effort': 'high'}},
    {'claude': {'model': '--evil'}}, {'claude': {'model': 42}},
    {'claude': {'model': 'tier:made-up'}}])
def test_invalid_writes_fail_without_mutation(patch):
    cfg = {'provider_defaults': {'claude': {'model': 'sonnet'}}}
    original = copy.deepcopy(cfg)
    with pytest.raises(ValueError):
        pp.patch_preferences(cfg, patch)
    assert cfg == original


def test_custom_models_and_field_patches_preserve_siblings():
    cfg = {'provider_defaults': {'codex': {'model': 'gpt-future', 'effort': 'high'},
                                  'claude': {'model': 'sonnet'}}}
    result = pp.patch_preferences(cfg, {'codex': {'model': 'organization/custom-v2'}})
    assert result['codex'] == {'model': 'organization/custom-v2', 'effort': 'high'}
    assert result['claude'] == {'model': 'sonnet'}
    assert pp.patch_preferences({'provider_defaults': result}, {'codex': None}) == {
        'claude': {'model': 'sonnet'}}


def test_continuation_preserves_native_and_exact_but_resolves_only_its_saved_tier():
    class Runtime:
        def latest_for(self, tier):
            return {'balanced': 'new-balanced-model'}.get(tier, '')
    assert pp.resolve_tracking_request(Runtime(), '') == ''
    assert pp.resolve_tracking_request(Runtime(), 'original-model') == 'original-model'
    assert pp.resolve_tracking_request(Runtime(), 'tier:balanced') == 'new-balanced-model'
    with pytest.raises(ValueError, match='unavailable'):
        pp.resolve_tracking_request(Runtime(), 'tier:best')


def test_native_only_runtime_does_not_offer_an_inert_model_control():
    runtime = agent_runtime.get_runtime('kiro')
    assert pp.runtime_metadata(runtime)['model_selection'] is False
    with pytest.raises(ValueError, match='managed by the provider'):
        pp.patch_preferences({}, {'kiro': {'model': 'some-model'}})
    assert pp.patch_preferences({}, {'kiro': {'model': ''}})['kiro']['model'] == ''
    with pytest.raises(es.EngineSelectionError, match='manages model selection'):
        es.resolve_engine({'default_provider': 'kiro'}, model_override='some-model')


def test_background_defaults_are_separate_and_explicit_native_wins():
    cfg = {'provider_defaults': {'codex': {'model': 'gpt-large', 'background_model': 'gpt-small'},
                                  'claude': {'background_model': 'sonnet', 'scribe_model': ''}}}
    assert pp.background_model(cfg, 'codex', 'scribe_model') == 'gpt-small'
    assert pp.background_model(cfg, 'claude', 'scribe_model', default='haiku') == ''
    assert pp.background_model(cfg, 'claude', 'condense_model', default='haiku') == 'sonnet'
    assert pp.background_model({'condense_model': 'sonnet'}, 'codex', 'condense_model') == agent_runtime.get_runtime('codex').latest_for('fast')


def test_preview_and_execution_share_resolution_without_execution(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail('Preview must not execute a provider')
    monkeypatch.setattr(agent_runtime.subprocess, 'Popen', refuse)
    monkeypatch.setattr(agent_runtime.subprocess, 'run', refuse)
    cfg = {'default_provider': 'codex', 'provider_defaults': {'codex': {'model': 'future-model'}}}
    before = copy.deepcopy(cfg)
    view = routes.preference_preview(cfg)
    codex = next(p for p in view['providers'] if p['name'] == 'codex')
    assert codex['effective']['model'] == es.resolve_engine(cfg).model
    assert cfg == before


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from mc.blueprints import settings_routes as settings
    app = Flask(__name__)
    app.register_blueprint(settings.bp)
    app.register_blueprint(routes.bp)
    original = copy.deepcopy(state.CONFIG)
    state.CONFIG.clear()
    state.CONFIG.update({'default_provider': 'codex', 'agent_model': 'tier:balanced'})
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr(settings, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(settings, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(routes, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(pp, 'BACKUP_ROOT', tmp_path / 'preference-backups')
    yield app.test_client(), settings
    state.CONFIG.clear()
    state.CONFIG.update(original)


def test_http_write_preview_reset_and_legacy_write_refusal(client):
    http, settings = client
    response = http.put('/api/config', json={'provider_defaults': {'codex': {'model': 'future-model'}}})
    assert response.status_code == 200
    assert state.CONFIG['provider_defaults']['claude']['model'] == 'tier:balanced'
    preview = http.post('/api/engine/preview', json={}).get_json()
    assert (preview['provider'], preview['model']) == ('codex', 'future-model')
    response = http.put('/api/config', json={'provider_defaults': {'codex': {'model': None}}})
    assert response.status_code == 200
    assert http.post('/api/engine/preview', json={}).get_json()['model'] == ''
    assert http.put('/api/config', json={'agent_model': 'sonnet'}).status_code == 409
    assert http.get('/api/engine/preferences?project_id=missing').status_code == 404


def test_unconfigured_preview_requires_a_provider_instead_of_inventing_claude(client):
    http, _settings = client
    state.CONFIG['default_provider'] = ''
    response = http.post('/api/engine/preview', json={})
    assert response.status_code == 400
    assert 'No provider selected' in response.get_json()['error']


def test_human_gate_and_failed_persistence_do_not_mutate_preferences(client, monkeypatch):
    http, settings = client
    original = copy.deepcopy(state.CONFIG)
    monkeypatch.setattr(settings, '_require_human_passcode', lambda data: ({'error': 'human only'}, 403))
    assert http.put('/api/config', json={'provider_defaults': {'codex': {'model': ''}}}).status_code == 403
    assert state.CONFIG == original
    monkeypatch.setattr(settings, '_require_human_passcode', lambda data: None)
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(settings, 'write_json_atomic', fail)
    assert http.put('/api/config', json={'provider_defaults': {'codex': {'model': ''}}}).status_code == 500
    assert state.CONFIG == original


def test_migration_backup_is_deduplicated_and_contains_only_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, 'BACKUP_ROOT', tmp_path / 'backups')
    record = {'agent_model': 'tier:balanced', 'agent_effort': 'high', 'other_data': 'private task text'}
    path = pp.backup_legacy_preferences(record, identity='global')
    assert path and path.is_file()
    assert pp.backup_legacy_preferences(record, identity='global') == path
    assert len(list(path.parent.glob('*.json'))) == 1
    assert 'private task text' not in path.read_text()
    assert pp.backup_legacy_preferences({'provider_defaults': {}}, identity='global') is None
