"""Claude model -> minimum CLI guard (mc/claude_cli_min_version.py).

The twin of the Codex gpt-6 gate: below the floor the --model flag is dropped
and a warning names the model and the CLI; an unreadable version passes the
model through.
"""
from types import SimpleNamespace

import pytest

from mc import claude_cli_min_version as m
from mc.agent_runtime import ClaudeRuntime

FABLE = 'claude-fable-5-2'


def _run_returning(text):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(stdout=text, stderr='')
    run.calls = calls
    return run


@pytest.fixture(autouse=True)
def _clear_cache():
    m._version_cache.clear()
    yield
    m._version_cache.clear()


def test_version_tuple_parses_claude_output():
    assert m.version_tuple('2.1.322 (Claude Code)') == (2, 1, 322)
    assert m.version_tuple('garbage') is None and m.version_tuple(None) is None


@pytest.mark.parametrize('ver,gated', [
    ('2.1.311 (Claude Code)', True),    # measured: runs on an unknown model
    ('2.1.320 (Claude Code)', True),
    ('2.1.321 (Claude Code)', False),   # the floor itself passes
    ('2.1.322 (Claude Code)', False),
    ('2.2.0 (Claude Code)', False),
    ('3.0.0 (Claude Code)', False),
])
def test_gate_model_at_the_floor(ver, gated):
    model, warn = m.gate_model(FABLE, ver)
    if gated:
        assert model == '' and FABLE in warn and '2.1.321' in warn
        assert ver.split()[0] in warn
    else:
        assert (model, warn) == (FABLE, '')


def test_unreadable_version_passes_the_model_through():
    assert m.gate_model(FABLE, None) == (FABLE, '')
    assert m.gate_model(FABLE, 'no digits here') == (FABLE, '')


def test_models_without_a_floor_are_never_gated():
    for model in ('claude-fable-5-1', 'claude-opus-5-5', 'opus', ''):
        assert m.gate_model(model, '1.0.0') == (model, '')


def test_context_suffix_is_the_same_model():
    assert m.floor_for('claude-fable-5-2[1m]') == (2, 1, 321)
    assert m.gate_model('claude-fable-5-2[1m]', '2.1.300')[0] == ''


def test_probe_is_skipped_for_models_without_a_floor():
    run = _run_returning('2.1.300 (Claude Code)')
    assert m.gate_model_for_binary('claude-opus-5-5', 'claude', run=run) == ('claude-opus-5-5', '')
    assert run.calls == []


def test_probe_is_memoised_and_never_raises():
    run = _run_returning('2.1.300 (Claude Code)')
    assert m.gate_model_for_binary(FABLE, 'claude-x', run=run)[0] == ''
    assert m.gate_model_for_binary(FABLE, 'claude-x', run=run)[0] == ''
    assert len(run.calls) == 1

    def boom(*a, **k):
        raise OSError('no such file')
    m._version_cache.clear()
    assert m.gate_model_for_binary(FABLE, 'claude-y', run=boom) == (FABLE, '')


def _build(monkeypatch, version_text, model=FABLE):
    monkeypatch.setattr(m, 'installed_version', lambda binary, **kw: version_text)
    return ClaudeRuntime().build_command(model=model)


def test_build_command_drops_model_on_an_old_cli(monkeypatch):
    cmd = _build(monkeypatch, '2.1.311 (Claude Code)')
    assert '--model' not in cmd and FABLE not in cmd


def test_build_command_keeps_model_on_a_new_or_unknown_cli(monkeypatch):
    for ver in ('2.1.322 (Claude Code)', None):
        cmd = _build(monkeypatch, ver)
        assert cmd[cmd.index('--model') + 1] == FABLE


def test_build_command_other_models_untouched_on_an_old_cli(monkeypatch):
    cmd = _build(monkeypatch, '2.1.100 (Claude Code)', model='claude-opus-5-5')
    assert cmd[cmd.index('--model') + 1] == 'claude-opus-5-5'
