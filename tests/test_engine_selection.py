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


# ── classify_value ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('value,expected', [
    (None, ('inherit', '')),
    ('', ('inherit', '')),
    ('   ', ('inherit', '')),
    ('tier:best', ('tier', 'best')),
    ('tier:balanced', ('tier', 'balanced')),
    ('tier:fast', ('tier', 'fast')),
    ('tier:nonsense', ('pin', 'tier:nonsense')),  # unrecognized tier name -> pin, not silently dropped
    ('opus', ('pin', 'opus')),  # bare alias: self-updating already, doesn't need 'tier:' spelling
    ('claude-opus-5-5', ('pin', 'claude-opus-5-5')),
])
def test_classify_value(value, expected):
    assert es.classify_value(value) == expected


# ── resolve_model_full: tier tracking + the 'global empty -> native' default ─

def test_global_empty_resolves_to_native_default():
    """An unset/empty global agent_model must NOT start tracking a tier —

    upgraded installs never saw the first-run model question and must not
    silently begin sending --model opus."""
    result = es.resolve_model_full('claude', {'agent_model': ''})
    assert (result.model, result.source, result.tracking) == ('', 'native', '')


def test_global_and_project_both_empty_resolves_to_native_default():
    result = es.resolve_model_full('claude', {'agent_model': ''}, {'agent_model': ''})
    assert (result.model, result.source, result.tracking) == ('', 'native', '')


def test_global_missing_key_resolves_to_native_default():
    result = es.resolve_model_full('claude', {})
    assert (result.model, result.source, result.tracking) == ('', 'native', '')


def test_global_tier_balanced():
    result = es.resolve_model_full('claude', {'agent_model': 'tier:balanced'})
    assert (result.model, result.source, result.tracking) == ('sonnet', 'global', 'balanced')


def test_project_tier_overrides_global_pin():
    result = es.resolve_model_full(
        'claude', {'agent_model': 'claude-opus-5-5'}, {'agent_model': 'tier:fast'})
    assert (result.model, result.source, result.tracking) == ('haiku', 'project', 'fast')


def test_explicit_tier_override_beats_project_and_global():
    result = es.resolve_model_full(
        'claude', {'agent_model': 'tier:best'}, {'agent_model': 'tier:fast'},
        override='tier:balanced')
    assert (result.model, result.source, result.tracking) == ('sonnet', 'explicit', 'balanced')


def test_exact_pin_is_unaffected_by_tier_machinery():
    """Backward compatible: an existing exact-id pin behaves exactly as before."""
    result = es.resolve_model_full('claude', {}, {'agent_model': 'claude-opus-5'})
    assert (result.model, result.source, result.tracking) == ('claude-opus-5', 'project', '')


def test_tier_resolution_for_non_claude_runtime():
    result = es.resolve_model_full('gemini', {'agent_model': 'tier:fast'})
    assert result.model == 'gemini-flash-lite-latest'
    assert result.tracking == 'fast'


def test_resolve_model_2tuple_backcompat_view():
    model, source = es.resolve_model('claude', {'agent_model': 'tier:best'})
    assert (model, source) == ('opus', 'global')


# ── resolve_effort ───────────────────────────────────────────────────────────

def test_effort_inherits_project_then_global():
    assert es.resolve_effort({'agent_effort': 'high'}, {}) == ('high', 'global')
    assert es.resolve_effort({'agent_effort': 'high'}, {'agent_effort': 'low'}) == ('low', 'project')


def test_effort_explicit_empty_selects_native_default():
    assert es.resolve_effort({'agent_effort': 'high'}, override='') == ('', 'native')


def test_effort_no_config_is_native_default():
    assert es.resolve_effort({}) == ('', 'native')


# ── resolve_engine: tracking + effort surfaced on ResolvedEngine ────────────

def test_resolve_engine_carries_tracking_and_effort():
    """An unset global agent_model resolves to the CLI native default, not a
    tracked tier — only effort is global here."""
    result = es.resolve_engine({'default_provider': 'claude', 'agent_effort': 'high'})
    assert result.tracking == ''
    assert result.model == ''
    assert result.model_source == 'native'
    assert (result.effort, result.effort_source) == ('high', 'global')


def test_resolve_engine_carries_tracking_when_global_tier_set():
    result = es.resolve_engine(
        {'default_provider': 'claude', 'agent_model': 'tier:best', 'agent_effort': 'high'})
    assert result.tracking == 'best'
    assert result.model == 'opus'
    assert result.model_source == 'global'
    assert (result.effort, result.effort_source) == ('high', 'global')


def test_resolve_engine_character_effort_source():
    result = es.resolve_engine({'default_provider': 'claude'},
                               character={'effort': 'max'})
    assert (result.effort, result.effort_source) == ('max', 'character')


def test_resolve_engine_explicit_effort_beats_character():
    result = es.resolve_engine({'default_provider': 'claude'},
                               character={'effort': 'max'}, effort_override='low')
    assert (result.effort, result.effort_source) == ('low', 'explicit')


def test_resolve_engine_pin_has_no_tracking():
    result = es.resolve_engine({'default_provider': 'claude'}, model_override='claude-opus-5')
    assert result.tracking == ''


# ── is_stale_pin ─────────────────────────────────────────────────────────────

def test_stale_pin_true_for_outdated_concrete_id():
    assert es.is_stale_pin('claude', 'claude-opus-5') is True


def test_stale_pin_false_for_current_head():
    assert es.is_stale_pin('claude', 'claude-opus-5-5') is False


def test_stale_pin_false_for_tier_value():
    assert es.is_stale_pin('claude', 'tier:best') is False


def test_stale_pin_false_for_bare_alias():
    assert es.is_stale_pin('claude', 'opus') is False


def test_stale_pin_false_for_empty_or_unknown_provider():
    assert es.is_stale_pin('claude', '') is False
    assert es.is_stale_pin('does-not-exist', 'claude-opus-5') is False


def test_stale_pin_false_for_model_outside_any_tier_family():
    """A Fable id (no opus/sonnet/haiku substring) has no tier_family, so it's
    never flagged stale by is_stale_pin() even though it's a concrete pin."""
    assert es.is_stale_pin('claude', 'claude-fable-5-1') is False


def test_stale_pin_never_rewrites_the_stored_value():
    """Detection only: calling is_stale_pin does not mutate anything it's
    passed, and resolve_model_full still returns the stale pin verbatim."""
    result = es.resolve_model_full('claude', {}, {'agent_model': 'claude-opus-5'})
    assert result.model == 'claude-opus-5'
    assert es.is_stale_pin('claude', result.model) is True


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
