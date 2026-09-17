"""Engine selection never launches a CLI or spends another provider's quota."""
from dataclasses import FrozenInstanceError

import pytest

from mc import agent_runtime, engine_selection as es


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail('Engine resolution must not execute or probe a provider')
    monkeypatch.setattr(agent_runtime.subprocess, 'run', refuse)
    monkeypatch.setattr(agent_runtime.subprocess, 'Popen', refuse)


def test_unconfigured_is_not_implicitly_claude():
    with pytest.raises(es.EngineSelectionError, match='No provider selected'):
        es.resolve_engine({})


def test_legacy_default_requires_explicit_opt_in():
    result = es.resolve_engine({}, legacy_default='claude')
    assert result.provider == 'claude'
    assert result.provider_source == 'legacy'


@pytest.mark.parametrize('source', ['override', 'project', 'global', 'character'])
def test_invalid_provider_never_falls_back(source):
    kwargs = {'config': {'default_provider': 'claude'}, 'legacy_default': 'claude'}
    if source == 'override':
        kwargs['provider_override'] = 'does-not-exist'
    elif source == 'global':
        kwargs['config'] = {'default_provider': 'does-not-exist'}
    else:
        kwargs[source] = {'provider': 'does-not-exist'}
    with pytest.raises(es.EngineSelectionError, match='No fallback'):
        es.resolve_engine(**kwargs)


def test_precedence_and_normalization():
    cfg = {'default_provider': 'claude'}
    project = {'provider': 'gemini'}
    character = {'provider': 'qwen'}
    result = es.resolve_engine(cfg, project, character=character, provider_override=' Codex ')
    assert (result.provider, result.provider_source) == ('codex', 'explicit')
    assert es.resolve_engine(cfg, project, character=character).provider == 'qwen'
    assert es.resolve_engine(cfg, project).provider == 'gemini'
    assert es.resolve_engine(cfg).provider == 'claude'


def test_foreign_inherited_model_is_omitted_not_translated():
    result = es.resolve_engine({'default_provider': 'codex', 'agent_model': 'claude-opus-5'})
    assert (result.provider, result.model, result.model_source) == ('codex', '', 'native')


@pytest.mark.parametrize('model', ['claude-opus-5', 'gemini-2.5-pro'])
def test_foreign_explicit_model_is_rejected(model):
    with pytest.raises(es.EngineSelectionError, match='matching provider/model'):
        es.resolve_engine({'default_provider': 'codex'}, model_override=model)


def test_character_engine_is_not_silently_remapped_on_provider_override():
    with pytest.raises(es.EngineSelectionError, match='matching provider/model'):
        es.resolve_engine({}, provider_override='codex', character={
            'provider': 'claude', 'model': 'claude-opus-5'})


def test_explicit_custom_model_is_not_limited_to_catalog():
    result = es.resolve_engine({'default_provider': 'codex'}, model_override='future-model')
    assert result.model == 'future-model'


def test_explicit_empty_model_does_not_inherit():
    result = es.resolve_engine({'default_provider': 'codex', 'agent_model': 'gpt-6-astra'},
                               model_override='')
    assert result.model == ''
    assert result.model_source == 'native'


def test_supported_inherited_model_and_immutable_result():
    result = es.resolve_engine({'default_provider': 'codex'}, {'agent_model': 'gpt-5.6-sol'})
    assert result.model == 'gpt-5.6-sol'
    assert result.model_source == 'project'
    with pytest.raises(FrozenInstanceError):
        result.model = 'gpt-6-astra'


def test_character_model_source():
    result = es.resolve_engine({'default_provider': 'codex'},
                               character={'model': 'gpt-5.6-sol'})
    assert result.model_source == 'character'


@pytest.mark.parametrize('model', ['haiku', 'sonnet', 'opus'])
def test_native_claude_aliases_are_not_owned_by_other_runtime_catalogs(model):
    assert es.resolve_engine({'default_provider': 'claude'}, model_override=model).model == model


@pytest.mark.parametrize('value', [[], 12, True, {}])
def test_invalid_values_do_not_become_shell_arguments(value):
    with pytest.raises(es.EngineSelectionError, match='must be strings'):
        es.resolve_engine({'default_provider': value})


def test_skills_use_inherited_global_provider(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setitem(ar.state.CONFIG, 'default_provider', 'codex')
    monkeypatch.setattr(ar._skills, 'list_skills', lambda *args: [{
        'name': 'project-memory', 'description': 'Find shared memories',
        'path': '/shared/SKILL.md', 'scope': 'project'}])
    project = {'id': 'p', 'project_path': '/shared'}
    assert 'project-memory' in ar._skills_catalog_block(project)
    assert ar._skills_catalog_block(dict(project, provider='claude')) == ''


def test_explicit_runtime_context_does_not_mutate_project_seed(monkeypatch, tmp_path):
    from mc.blueprints import agent_routes as ar
    from types import SimpleNamespace
    from threading import RLock
    captured = {}
    project = {'id': 'p', 'project_path': str(tmp_path), 'provider': 'claude'}
    runtime = SimpleNamespace(name='codex', dispatch=lambda **kw: captured.update(dispatch=kw))
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda _: runtime)
    monkeypatch.setattr(ar, '_resolve_runtime_model', lambda *a, **k: '')
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(
        lock=RLock(), session_ids=set(), ensure_guardian=lambda: None))
    monkeypatch.setattr(ar, '_build_agent_context',
                        lambda p, **kw: captured.update(context=p) or 'shared-memory')
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, 'agent_sessions', {})
    ar._dispatch_via_runtime(project, 'hello', provider_name='codex')
    assert captured['context']['provider'] == 'codex'
    assert project['provider'] == 'claude'
    assert captured['dispatch']['system_prompt'] == 'shared-memory'
