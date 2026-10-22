"""Real reconciler tests with fake thread/sleep/OS calls; no live inhibitors."""
from __future__ import annotations

import json
import types

import pytest

from mc import wake_lock

_REAL_ENGAGE = wake_lock._engage
_REAL_INHIBITOR_DEAD = wake_lock._inhibitor_dead


@pytest.fixture(autouse=True)
def _stub_os(monkeypatch):
    calls = {"engage": 0, "release": 0, "logs": []}
    monkeypatch.setattr(wake_lock, "_engage", lambda: calls.__setitem__("engage", calls["engage"] + 1) or True)
    monkeypatch.setattr(wake_lock, "_release", lambda: calls.__setitem__("release", calls["release"] + 1) or True)
    monkeypatch.setattr(wake_lock, "_inhibitor_dead", lambda: False)
    monkeypatch.setattr(wake_lock, "_log", lambda message, **kw: calls["logs"].append(message))
    monkeypatch.setattr(wake_lock, "_engaged", False)
    monkeypatch.setattr(wake_lock, "_proc", None)
    yield calls


def _run_reconciler(monkeypatch, ticks, *, interval_s=None, after_tick=None):
    """Capture start's actual target and run exactly len(ticks) iterations."""
    class EndLoop(BaseException):
        pass

    targets, threads, registered, sleeps, held = [], [], [], [], []

    def fake_thread(**kwargs):
        threads.append(kwargs)
        return types.SimpleNamespace(start=lambda: targets.append(kwargs["target"]))

    def enabled():
        value = ticks[len(sleeps)]
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else value

    def sleep(seconds):
        sleeps.append(seconds)
        held.append(wake_lock._engaged)
        if after_tick:
            after_tick(len(sleeps))
        if len(sleeps) == len(ticks):
            raise EndLoop

    # Replace local module references, not process-global threading/time calls.
    monkeypatch.setattr(wake_lock, "threading", types.SimpleNamespace(Thread=fake_thread))
    monkeypatch.setattr(wake_lock, "time", types.SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(wake_lock, "atexit", types.SimpleNamespace(register=registered.append))
    kwargs = {} if interval_s is None else {"interval_s": interval_s}
    wake_lock.start(is_enabled=enabled, **kwargs)
    assert len(targets) == 1
    assert threads[0]["name"] == "wake-lock" and threads[0]["daemon"] is True
    with pytest.raises(EndLoop):
        targets[0]()
    return types.SimpleNamespace(held=held, sleeps=sleeps, registered=registered)


def test_enabled_idle_startup_holds_across_ticks(monkeypatch, _stub_os):
    from mc import state
    monkeypatch.setattr(state, "agent_sessions", {})
    def activity(tick):
        # Start idle, then work starts and ends without changing the setting.
        state.agent_sessions.clear()
        if tick == 1:
            state.agent_sessions["example"] = {"status": "running"}
    result = _run_reconciler(monkeypatch, [True, True, True], after_tick=activity)
    assert result.held == [True, True, True]
    assert result.sleeps == [20, 20, 20]
    assert _stub_os["engage"] == 1
    assert _stub_os["release"] == 0


def test_disabled_startup_and_live_off_on(monkeypatch, _stub_os):
    result = _run_reconciler(monkeypatch, [False, True, True, False, False, True])
    assert result.held == [False, True, True, False, False, True]
    assert _stub_os["engage"] == 2
    assert _stub_os["release"] == 1


def test_reads_live_setting_instead_of_caching_startup(monkeypatch):
    config = {"keep_awake_enabled": True}
    def flip(tick):
        config["keep_awake_enabled"] = tick != 1
    result = _run_reconciler(monkeypatch, [lambda: config["keep_awake_enabled"]] * 3,
                             after_tick=flip)
    assert result.held == [True, False, True]


@pytest.mark.parametrize("operation,ticks,expected", [
    ("engage", [True, True, True], [False, True, True]),
    ("release", [True, False, False], [True, True, False]),
])
def test_failed_os_call_retries_next_tick(monkeypatch, operation, ticks, expected):
    answers = iter([False, True])
    monkeypatch.setattr(wake_lock, f"_{operation}", lambda: next(answers))
    assert _run_reconciler(monkeypatch, ticks).held == expected


def test_config_read_failure_logs_and_loop_recovers(monkeypatch, _stub_os):
    result = _run_reconciler(monkeypatch, [RuntimeError("config unavailable"), True])
    assert result.held == [False, True]
    assert any("reconcile error: config unavailable" in line for line in _stub_os["logs"])


@pytest.mark.parametrize("interval,expected", [(1, 5), (35, 35)])
def test_configured_poll_interval_has_floor(monkeypatch, interval, expected):
    assert _run_reconciler(monkeypatch, [False], interval_s=interval).sleeps == [expected]


def test_shutdown_callback_releases_enabled_idle_lock(monkeypatch, _stub_os):
    result = _run_reconciler(monkeypatch, [True])
    assert result.registered == [wake_lock.release_now]
    result.registered[0]()
    assert wake_lock._engaged is False
    assert _stub_os["release"] == 1
    wake_lock.release_now()
    assert _stub_os["release"] == 1


def test_dead_posix_child_is_reengaged_by_real_loop(monkeypatch, _stub_os):
    monkeypatch.setattr(wake_lock.sys, "platform", "darwin")
    monkeypatch.setattr(wake_lock, "_inhibitor_dead", _REAL_INHIBITOR_DEAD)
    child = types.SimpleNamespace(exit_code=None)
    monkeypatch.setattr(wake_lock, "_proc", types.SimpleNamespace(poll=lambda: child.exit_code))
    result = _run_reconciler(monkeypatch, [True, True, True],
        after_tick=lambda tick: setattr(child, "exit_code", 1 if tick == 1 else None))
    assert result.held == [True, True, True]
    assert _stub_os["engage"] == 2
    assert any("inhibitor process died" in line for line in _stub_os["logs"])


@pytest.mark.parametrize("operation", ["engage", "release"])
@pytest.mark.parametrize("error", [False, True])
def test_windows_failure_is_logged(monkeypatch, _stub_os, operation, error):
    flags = []
    def execution_state(value):
        flags.append(value)
        if error:
            raise OSError("OS refused assertion")
        return 0
    monkeypatch.setattr(wake_lock.ctypes, "windll", types.SimpleNamespace(
        kernel32=types.SimpleNamespace(SetThreadExecutionState=execution_state)), raising=False)
    assert getattr(wake_lock, f"_{operation}_windows")() is False
    assert flags == [wake_lock._ES_CONTINUOUS | (wake_lock._ES_SYSTEM_REQUIRED if operation == "engage" else 0)]
    detail = "OS refused assertion" if error else "SetThreadExecutionState returned 0"
    assert any(f"windows {operation} failed: {detail}" in line for line in _stub_os["logs"])


def test_windows_reconciler_retries_and_clears_on_same_thread(monkeypatch):
    import threading
    calls = []
    answers = iter([0, 1, 0, 1])
    def execution_state(flags):
        calls.append((threading.get_ident(), flags))
        return next(answers)
    monkeypatch.setattr(wake_lock.ctypes, "windll", types.SimpleNamespace(
        kernel32=types.SimpleNamespace(SetThreadExecutionState=execution_state)), raising=False)
    monkeypatch.setattr(wake_lock.sys, "platform", "win32")
    monkeypatch.setattr(wake_lock, "_engage", wake_lock._engage_windows)
    monkeypatch.setattr(wake_lock, "_release", wake_lock._release_windows)
    result = _run_reconciler(monkeypatch, [True, True, True, False, False])
    assert result.held == [False, True, True, True, False]
    assert len({thread for thread, _ in calls}) == 1
    assert [flags for _, flags in calls] == [0x80000001, 0x80000001, 0x80000000, 0x80000000]


def test_posix_inhibitors_are_parent_death_bound_without_display_assertions(monkeypatch):
    spawned = []
    def fake_popen(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        return types.SimpleNamespace(stdin=None, poll=lambda: None)
    monkeypatch.setattr(wake_lock.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(wake_lock.sys, "platform", "darwin")
    assert _REAL_ENGAGE() is True
    assert spawned[-1][0] == ["caffeinate", "-i", "-w", str(wake_lock.os.getpid())]
    assert spawned[-1][1]["stdin"] == wake_lock.subprocess.DEVNULL
    monkeypatch.setattr(wake_lock.sys, "platform", "linux")
    assert _REAL_ENGAGE() is True
    assert spawned[-1][0] == ["systemd-inhibit", "--what=sleep", "--who=Clayrune",
        "--why=Keep machine awake is enabled", "--mode=block", "cat"]
    assert spawned[-1][1]["stdin"] == wake_lock.subprocess.PIPE


def test_missing_posix_tool_logs_failure(monkeypatch, _stub_os):
    def missing(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(wake_lock.subprocess, "Popen", missing)
    assert wake_lock._engage_subprocess(["caffeinate", "-i"]) is False
    assert any("caffeinate not found" in line for line in _stub_os["logs"])


@pytest.mark.parametrize("pipe_error,timeout", [(False, False), (True, False), (False, True)])
def test_subprocess_release_closes_pipe_and_reaps_child(monkeypatch, _stub_os, pipe_error, timeout):
    calls = []
    def close():
        calls.append("close")
        if pipe_error:
            raise OSError("pipe close failed")
    def wait(**kwargs):
        calls.append("wait")
        if timeout and calls.count("wait") == 1:
            raise wake_lock.subprocess.TimeoutExpired("inhibitor", 5)
    child = types.SimpleNamespace(stdin=types.SimpleNamespace(close=close),
        terminate=lambda: calls.append("terminate"), wait=wait, kill=lambda: calls.append("kill"))
    monkeypatch.setattr(wake_lock, "_proc", child)
    assert wake_lock._release_subprocess() is True
    assert calls == ["close", "terminate", "wait"] + (["kill", "wait"] if timeout else [])
    assert wake_lock._proc is None
    if pipe_error:
        assert any("close inhibitor stdin failed: pipe close failed" in line for line in _stub_os["logs"])


def test_subprocess_release_failure_retains_child_for_retry(monkeypatch, _stub_os):
    def fail():
        raise OSError("terminate failed")
    child = types.SimpleNamespace(stdin=None, terminate=fail)
    monkeypatch.setattr(wake_lock, "_proc", child)
    assert wake_lock._release_subprocess() is False
    assert wake_lock._proc is child
    assert any("release failed: terminate failed" in line for line in _stub_os["logs"])
    child.terminate = lambda: None
    child.wait = lambda **kw: None
    assert wake_lock._release_subprocess() is True
    assert wake_lock._proc is None


def test_setting_round_trips_through_real_config_route(tmp_path, monkeypatch):
    """Use a temporary store; existing passcode gate tested in its own suite."""
    from flask import Flask
    from mc import state
    from mc.blueprints import settings_routes as sr
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(state, "CONFIG", {"keep_awake_enabled": False})
    monkeypatch.setattr(sr, "CONFIG_PATH", config_path)
    monkeypatch.setattr(sr, "is_unattended_caller", lambda: False)
    monkeypatch.setattr(sr, "_require_human_passcode", lambda data: None)
    app = Flask(__name__)
    app.register_blueprint(sr.bp)
    client = app.test_client()
    for enabled in (True, False):
        response = client.put("/api/config", json={"keep_awake_enabled": enabled})
        assert response.status_code == 200
        assert response.get_json()["updated"] == ["keep_awake_enabled"]
        assert state.CONFIG["keep_awake_enabled"] is enabled
        assert json.loads(config_path.read_text(encoding="utf-8"))["keep_awake_enabled"] is enabled
        assert client.get("/api/config").get_json()["keep_awake_enabled"] is enabled
