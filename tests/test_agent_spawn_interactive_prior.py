"""MC-1150 P3 review fix (Fenn blocker 1): an interactive spawn may reuse a
session directory only when the previous shim AND CLI are POSITIVELY gone.

`C.inspect` reports `shim_alive` / `cli_alive` through `same_process`, which is
also False when the create-time lookup FAILS (access denied). That is "cannot
tell", not "gone"; `write_spec` deletes shim.json / exit.json, and a live shim
still writes its final manifest into them. `_await_prior` therefore asks
`process_state` (three-valued) and treats 'unknown' as alive, as P2's
`_prior_shim_gone` does. No OS process is started by any test here.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn_interactive_prior.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim as H  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402
from mc import agent_spawn_interactive as I  # noqa: E402

SHIM_PID, CLI_PID = 101, 102
SHIM_CT, CLI_CT = 1.0, 2.0


@pytest.fixture
def sdir(tmp_path):
    d = C.session_dir(tmp_path, 'prior')
    d.mkdir(parents=True)
    H.write_json_atomic(d / 'shim.json', dict(
        schema=H.SCHEMA, launch_id='old', shim_pid=SHIM_PID, shim_ct=SHIM_CT,
        cli_pid=CLI_PID, cli_ct=CLI_CT))
    H.write_json_atomic(d / 'exit.json', dict(launch_id='old', rc=0))
    return d


def _world(monkeypatch, *, create_time, absent=()):
    """Fake the OS: `create_time(pid)` is the live creation time (None = lookup
    failed), `absent` the pids positively proven not to exist."""
    monkeypatch.setattr(H, 'proc_create_time', create_time)
    monkeypatch.setattr(H, '_pid_absent', lambda pid: pid in absent)


def _no_collect(monkeypatch):
    monkeypatch.setattr(I, '_collect_prior', lambda sdir: None)


def test_failed_identity_lookup_is_not_proof_the_shim_is_gone(sdir, monkeypatch):
    """The review's repro: CLI proven absent, shim's create-time lookup denied."""
    _world(monkeypatch, create_time=lambda pid: None, absent={CLI_PID})
    _no_collect(monkeypatch)
    prior = C.inspect(sdir)
    assert prior.shim_alive is False and prior.cli_alive is False   # the folded booleans
    assert H.process_state(SHIM_PID, SHIM_CT) == 'unknown'
    assert I._await_prior(sdir, 'prior', 'claude', timeout=0.2) is False


def test_failed_identity_lookup_on_the_cli_is_not_proof_either(sdir, monkeypatch):
    _world(monkeypatch, create_time=lambda pid: None, absent={SHIM_PID})
    _no_collect(monkeypatch)
    assert I._await_prior(sdir, 'prior', 'claude', timeout=0.2) is False


def test_positively_gone_prior_is_reusable(sdir, monkeypatch):
    _world(monkeypatch, create_time=lambda pid: None, absent={SHIM_PID, CLI_PID})
    assert I._await_prior(sdir, 'prior', 'claude', timeout=0.2) is True


def test_pid_reused_by_another_process_counts_as_gone(sdir, monkeypatch):
    """A different start time at the recorded pid means the recorded process left."""
    _world(monkeypatch, create_time=lambda pid: 9999.0)
    assert I._await_prior(sdir, 'prior', 'claude', timeout=0.2) is True


def test_still_running_prior_is_not_reusable(sdir, monkeypatch):
    _world(monkeypatch, create_time=lambda pid: {SHIM_PID: SHIM_CT, CLI_PID: CLI_CT}[pid])
    _no_collect(monkeypatch)
    assert I._await_prior(sdir, 'prior', 'claude', timeout=0.2) is False


def test_no_manifest_means_nothing_to_wait_for(tmp_path):
    assert I._await_prior(C.session_dir(tmp_path, 'fresh'), 'fresh', 'claude', timeout=0.2) is True


def test_a_prior_that_leaves_during_the_wait_is_reused(sdir, monkeypatch):
    """The bounded wait still works: a kill in flight lands and the dir is reused."""
    gone_after = {'n': 0}

    def create_time(pid):
        gone_after['n'] += 1
        return {SHIM_PID: SHIM_CT, CLI_PID: CLI_CT}[pid] if gone_after['n'] <= 4 else None
    _world(monkeypatch, create_time=create_time, absent={SHIM_PID, CLI_PID})
    _no_collect(monkeypatch)
    assert I._await_prior(sdir, 'prior', 'claude', timeout=5.0) is True


def test_unknown_prior_keeps_its_records_and_spawns_the_fallback(sdir, monkeypatch):
    """End to end through spawn_engine_proc: no launch, no write_spec, the old
    shim.json / exit.json survive, and the caller gets the plain Popen."""
    _world(monkeypatch, create_time=lambda pid: None, absent={CLI_PID})
    _no_collect(monkeypatch)
    real_wait = I._await_prior
    monkeypatch.setattr(I, '_await_prior', lambda *a, **k: real_wait(*a, timeout=0.2))
    launched = []

    def fake_launch(base, session_id, **kw):
        launched.append(session_id)
        C.write_spec(C.session_dir(base, session_id), dict(launch_id='new', argv=['fake']))
        return C.LaunchResult(ok=True, branch='probe', proc=SimpleNamespace(args=None))
    monkeypatch.setattr(C, 'launch', fake_launch)
    popen = MagicMock(name='Popen')
    monkeypatch.setattr(S.subprocess, 'Popen', popen)

    S.spawn_engine_proc(['fake'], session_id='prior', engine='claude', root=sdir.parent,
                        cfg={'agent_survive_restart_enabled': True}, interactive=True)

    assert launched == []
    assert (sdir / 'shim.json').exists() and (sdir / 'exit.json').exists()
    assert popen.call_count == 1 and popen.call_args.kwargs['stdin'] == subprocess.PIPE
