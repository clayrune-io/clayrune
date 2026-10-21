"""Internal tasks use lightweight defaults without inheriting chat model pins."""
from types import SimpleNamespace

from mc import agent_runtime, provider_preferences as preferences
from mc.background_model_policy import automatic_model


def test_automatic_policy_uses_provider_fast_tier_without_a_model_call(monkeypatch):
    runtime = SimpleNamespace(latest_for=lambda tier: 'vendor-small' if tier == 'fast' else 'vendor-large')
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda provider: runtime)
    config = {'provider_defaults': {'codex': {'model': 'vendor-large', 'effort': 'high'}}}
    for role in ('scribe_model', 'condense_model', 'distiller_model'):
        assert preferences.background_model(config, 'codex', role) == 'vendor-small'
    assert config['provider_defaults']['codex']['model'] == 'vendor-large'


def test_task_policy_preserves_role_overrides_native_choices_and_claude_policy(monkeypatch):
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda provider: SimpleNamespace(latest_for=lambda tier: 'small'))
    config = {'provider_defaults': {'codex': {'background_model': 'large', 'scribe_model': ''}}}
    assert preferences.background_model(config, 'codex', 'scribe_model') == ''
    assert preferences.background_model(config, 'codex', 'condense_model') == 'large'
    assert automatic_model('claude', default='sonnet') == 'sonnet'
    assert automatic_model('claude', default='haiku') == 'haiku'


def test_missing_or_native_only_mapping_is_an_honest_native_fallback(monkeypatch):
    for runtime in (SimpleNamespace(), SimpleNamespace(latest_for=lambda tier: ''),
                    SimpleNamespace(MODEL_SELECTION=False, latest_for=lambda tier: 'unsupported')):
        monkeypatch.setattr(agent_runtime, 'get_runtime', lambda provider: runtime)
        assert automatic_model('custom') == ''


def test_ignored_foreign_legacy_role_does_not_become_native_override(monkeypatch):
    runtime = SimpleNamespace(name='codex', latest_for=lambda tier: 'vendor-small')
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda provider: runtime)
    monkeypatch.setattr(agent_runtime, 'available_runtimes', lambda: [runtime])
    legacy = {'scribe_model': 'haiku'}
    assert preferences.background_model(legacy, 'codex', 'scribe_model') == 'vendor-small'
    assert 'scribe_model' not in preferences.scoped_view(legacy).get('codex', {})


def test_restoring_automatic_only_removes_internal_task_overrides(monkeypatch):
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda provider: SimpleNamespace(latest_for=lambda tier: 'small'))
    config = {'provider_defaults': {'codex': {'model': 'chat-pin', 'effort': 'high', 'background_model': 'big'},
                                    'claude': {'model': 'opus'}}}
    config['provider_defaults'] = preferences.patch_preferences(config, {'codex': {'background_model': None}})
    assert preferences.background_model(config, 'codex', 'scribe_model') == 'small'
    assert config['provider_defaults']['codex'] == {'model': 'chat-pin', 'effort': 'high'}
    assert config['provider_defaults']['claude'] == {'model': 'opus'}
