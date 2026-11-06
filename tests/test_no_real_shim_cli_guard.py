"""tests/conftest.py must stop a test from launching a real model CLI through
the supervisor shim (MC-1150 P3 review, Fenn blocker 2).

The Popen guard (tests/test_no_real_cli_guard.py) only sees this process's
Popen. With `agent_survive_restart_enabled` ON the CLI is started by the shim in
a fresh interpreter, so the guard sits on `agent_shim_client.spawn_detached`,
where the argv is still readable in the spec the launch just wrote. No test
here starts a process: `spawn_detached` is handed a stub `popen`.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim as H  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from tests import conftest  # noqa: E402


class _StubPopen:
    calls: list = []

    def __init__(self, argv, **kw):
        type(self).calls.append(argv)

    def wait(self, timeout=None):
        return 0


@pytest.fixture(autouse=True)
def _stub_popen():
    _StubPopen.calls = []
    yield


@pytest.fixture()
def expect_blocked():
    before = len(conftest._REAL_CLI_ATTEMPTS)
    yield
    del conftest._REAL_CLI_ATTEMPTS[before:]


def _spec(tmp_path, argv):
    sdir = C.session_dir(tmp_path, 's1')
    C.write_spec(sdir, C.build_spec(argv=argv, cwd=None, mode=H.MODE_INTERACTIVE,
                                    session_id='s1', engine='claude', env_names=[],
                                    owner_pid=1, timers=C.timers_from_config({})))
    return sdir


@pytest.mark.parametrize('argv', [
    ['claude', '-p'],
    [r'C:\Users\x\AppData\npm\claude.CMD', '--dangerously-skip-permissions'],
    ['/usr/local/bin/codex', 'exec'],
    ['gemini.cmd', '--yolo'],
    ['qwen'],
])
def test_a_real_cli_in_the_spec_is_blocked_before_any_process_starts(tmp_path, expect_blocked, argv):
    sdir = _spec(tmp_path, argv)
    with pytest.raises(conftest.RealCliSpawnBlocked):
        C.spawn_detached(sdir, popen=_StubPopen)
    assert _StubPopen.calls == [], 'the trampoline was started for a real CLI'
    assert len(conftest._REAL_CLI_ATTEMPTS) >= 1


def test_launch_is_blocked_at_the_same_boundary(tmp_path, expect_blocked):
    """The route-level path: launch() -> write_spec -> spawn_detached."""
    with pytest.raises(conftest.RealCliSpawnBlocked):
        C.launch(tmp_path, 's2', argv=['claude', '-p'], cwd=None, mode=H.MODE_INTERACTIVE)
    assert not (C.session_dir(tmp_path, 's2') / 'shim.json').exists()


def test_an_owned_stand_in_cli_is_not_blocked(tmp_path):
    sdir = _spec(tmp_path, [sys.executable, str(REPO / 'tests' / 'fake_claude_streamjson.py')])
    before = len(conftest._REAL_CLI_ATTEMPTS)
    assert C.spawn_detached(sdir, popen=_StubPopen).ok
    assert len(_StubPopen.calls) == 1
    assert len(conftest._REAL_CLI_ATTEMPTS) == before


def test_the_explicit_integration_opt_in_lets_a_real_cli_through(tmp_path, monkeypatch):
    monkeypatch.setenv('MC_LIVE_CLI_TESTS', '1')
    sdir = _spec(tmp_path, ['claude', '-p'])
    before = len(conftest._REAL_CLI_ATTEMPTS)
    assert C.spawn_detached(sdir, popen=_StubPopen).ok      # stub: nothing real starts
    assert len(_StubPopen.calls) == 1
    assert len(conftest._REAL_CLI_ATTEMPTS) == before


def test_a_blocked_attempt_is_recorded_even_if_the_raise_is_swallowed(tmp_path):
    sdir = _spec(tmp_path, ['claude'])
    before = len(conftest._REAL_CLI_ATTEMPTS)
    try:
        C.spawn_detached(sdir, popen=_StubPopen)
    except conftest.RealCliSpawnBlocked:
        pass
    assert len(conftest._REAL_CLI_ATTEMPTS) == before + 1
    del conftest._REAL_CLI_ATTEMPTS[before:]
