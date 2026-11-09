"""Total runtime deadlines and legacy per-leg timeouts; external I/O is fake."""
import subprocess
from types import SimpleNamespace

import pytest

from mc import agent_runtime as rt
from mc import transform_deadline


@pytest.fixture
def legs(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=1000.0)
    calls = []
    runtime = rt.ClaudeRuntime()
    monkeypatch.setattr(transform_deadline.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(runtime, '_oneshot_argv', lambda **kw: ['synthetic-claude'])
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._transform_sandbox, 'transform_env', lambda: {})
    monkeypatch.setattr(rt._api_credits_offload, 'note_subscription_call', lambda **kw: None)

    def configure(api_seconds=0, cli_seconds=0, *, api_success=False, cli_timeout=False):
        def api(**kw):
            calls.append(('api', kw['timeout']))
            clock.now += api_seconds
            return rt.OneshotResult(text='API reply') if api_success else None

        def cli(argv, **kw):
            calls.append(('cli', kw['timeout']))
            clock.now += cli_seconds
            if cli_timeout:
                raise subprocess.TimeoutExpired(argv, kw['timeout'])
            return SimpleNamespace(returncode=0, stdout='CLI reply', stderr='')

        monkeypatch.setattr(rt._api_credits_offload, 'try_offload', api)
        monkeypatch.setattr(rt.subprocess, 'run', cli)
        monkeypatch.setattr(rt._transform_process_deadline, 'run', cli)

    return runtime, clock, calls, configure


@pytest.mark.parametrize('explicit_none', [False, True])
def test_without_deadline_keeps_full_timeout_for_both_legs(legs, explicit_none):
    runtime, clock, calls, configure = legs
    configure(api_seconds=25, cli_seconds=6)
    out = runtime.oneshot(prompt='Extract', timeout=25,
                          **({'deadline': None} if explicit_none else {}))
    assert out.text == 'CLI reply'
    assert clock.now == 1031
    assert calls == [('api', 25), ('cli', 25)]


def test_without_deadline_cli_timeout_still_returns_none(legs):
    runtime, _, calls, configure = legs
    configure(api_seconds=25, cli_seconds=25, cli_timeout=True)
    assert runtime.oneshot(prompt='Extract', timeout=25) is None
    assert runtime.last_error.startswith('timeout after 25s')
    assert calls == [('api', 25), ('cli', 25)]


@pytest.mark.parametrize('api_seconds', [0, 5, 22])
def test_fallback_gets_only_remaining_total_budget(legs, api_seconds):
    runtime, _, calls, configure = legs
    configure(api_seconds=api_seconds, cli_seconds=2)
    assert runtime.oneshot(prompt='Extract', timeout=25, deadline=1025).text == 'CLI reply'
    assert calls == [('api', 25), ('cli', 25 - api_seconds)]


@pytest.mark.parametrize('api_seconds', [22.001, 24, 25, 26])
def test_fallback_below_three_seconds_is_not_started(legs, api_seconds):
    runtime, _, calls, configure = legs
    configure(api_seconds=api_seconds)
    with pytest.raises(TimeoutError):
        runtime.oneshot(prompt='Extract', timeout=25, deadline=1025)
    assert calls == [('api', 25)]


@pytest.mark.parametrize('remaining', [0, 0.5])
def test_insufficient_deadline_starts_no_leg(legs, remaining):
    runtime, _, calls, configure = legs
    configure()
    with pytest.raises(TimeoutError):
        runtime.oneshot(prompt='Extract', deadline=1000 + remaining)
    assert calls == []


def test_remaining_budget_preserves_fractional_seconds(legs):
    runtime, _, calls, configure = legs
    configure(api_seconds=3, cli_seconds=2)
    assert runtime.oneshot(prompt='Extract', timeout=25, deadline=1008.5).text == 'CLI reply'
    assert calls == [('api', 8.5), ('cli', 5.5)]


@pytest.mark.parametrize('api_success,api_seconds,cli_seconds,cli_timeout', [
    (True, 25, 0, False), (False, 10, 15, False), (False, 10, 15, True),
])
def test_late_results_and_cli_timeout_raise_timeout(legs, api_success, api_seconds,
                                                   cli_seconds, cli_timeout):
    runtime, _, _, configure = legs
    configure(api_seconds, cli_seconds, api_success=api_success, cli_timeout=cli_timeout)
    with pytest.raises(TimeoutError):
        runtime.oneshot(prompt='Extract', timeout=25, deadline=1025)


def test_timely_api_result_skips_cli(legs):
    runtime, _, calls, configure = legs
    configure(api_seconds=20, api_success=True)
    assert runtime.oneshot(prompt='Extract', timeout=25, deadline=1025).text == 'API reply'
    assert calls == [('api', 25)]


def test_transform_refuses_silently_dropping_unsupported_deadline(monkeypatch):
    runtime = SimpleNamespace(oneshot=lambda *, prompt, system_prompt, model,
                              max_turns, stdin_text, cwd: rt.OneshotResult(text='unused'))
    monkeypatch.setattr(rt, 'get_runtime', lambda provider: runtime)
    monkeypatch.setattr(rt, '_authorize_text_transform', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='does not support transform deadlines'):
        rt.run_text_transform('synthetic', prompt='Extract', deadline=1025)
