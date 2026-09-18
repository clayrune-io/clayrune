"""Focused checks for feature-level provider selection.

These tests use an in-memory runtime so a route/helper cannot accidentally
fall back to a Claude subprocess when an explicit foreign provider is chosen.
"""

from pathlib import Path

import pytest


class _Result:
    def __init__(self, text):
        self.text = text


class _Runtime:
    name = 'test-provider'

    def __init__(self, text='answer'):
        self.text = text
        self.calls = []

    def model_supported(self, model):
        return True

    def model_choices(self):
        return []

    def oneshot(self, **kwargs):
        self.calls.append(kwargs)
        return _Result(self.text)


def test_run_text_transform_forwards_selected_model_and_effort(monkeypatch):
    from mc import agent_runtime

    runtime = _Runtime('selected output')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)

    answer = agent_runtime.run_text_transform(
        runtime.name, prompt='write the artifact', model='native-pro',
        effort='high', stdin_text='source data', cwd=str(Path.cwd()))

    assert answer == 'selected output'
    assert runtime.calls[0]['model'] == 'native-pro'
    assert runtime.calls[0]['effort'] == 'high'
    assert runtime.calls[0]['stdin_text'] == 'source data'


def test_character_helper_does_not_send_foreign_engine_to_scribe(monkeypatch):
    from mc import agent_runtime, state
    from mc.blueprints import character_routes as routes

    runtime = _Runtime('## Voice\n\nA distinct voice.')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')
    monkeypatch.setattr(
        routes, '_scribe_call',
        lambda *args: pytest.fail('foreign provider reached Claude Scribe'))

    result = routes._character_model_call(
        {'provider': runtime.name, 'model': 'native-pro', 'effort': 'medium'},
        None, 'voice prompt', 'role payload')

    assert result.startswith('## Voice')
    assert runtime.calls[0]['model'] == 'native-pro'
    assert runtime.calls[0]['effort'] == 'medium'
    assert runtime.calls[0]['stdin_text'] == 'role payload'


def test_guide_engine_uses_explicit_provider_and_model(monkeypatch):
    from mc import agent_runtime, state
    from mc.blueprints import guide_routes as routes

    runtime = _Runtime('guide answer')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')

    engine, effort = routes._guide_engine(
        {'provider': runtime.name, 'model': 'native-pro', 'effort': 'low'})

    assert engine.provider == runtime.name
    assert engine.model == 'native-pro'
    assert effort == 'low'
