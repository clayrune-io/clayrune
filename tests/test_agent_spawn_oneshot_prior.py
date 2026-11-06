"""MC-1150 P2 inherited defect (Fenn, 2026-11-05): the oneshot path in
`agent_spawn.spawn_engine_proc` may reuse a session directory only when the
previous shim AND CLI are POSITIVELY gone (`process_state == 'gone'`).

It used to check `inspect()`'s folded booleans (`shim_alive or cli_alive`) and
then `process_state` for the SHIM only. A positively-gone shim plus a CLI whose
create-time lookup was denied (`process_state == 'unknown'`, folded to
`cli_alive=False`) let `write_spec` delete shim.json, discarding the only record
of the surviving CLI's pid/create-time. Same rule as
`agent_spawn_interactive._await_prior` (tests/test_agent_spawn_interactive_prior.py).
No OS process is started by any test here.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn_oneshot_prior.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim as H  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402

from tests.test_agent_spawn import _Popen  # noqa: E402

ON = {'agent_survive_restart_enabled': True}
SHIM_PID, CLI_PID = 101, 102
SHIM_CT, CLI_CT = 1.0, 2.0


def _prior(tmp_path, *, exited):
    d = C.session_dir(tmp_path, 'oneshot')
    d.mkdir(parents=True)
    H.write_json_atomic(d / 'shim.json', dict(
        schema=H.SCHEMA, launch_id='old', shim_pid=SHIM_PID, shim_ct=SHIM_CT,
        cli_pid=CLI_PID, cli_ct=CLI_CT))
    if exited:
        H.write_json_atomic(d / 'exit.json', dict(launch_id='old', rc=0))
    return d


def _world(monkeypatch, *, create_time, absent=()):
    """Fake the OS: `create_time(pid)` is the live creation time (None = lookup
    failed), `absent` the pids positively proven not to exist."""
    monkeypatch.setattr(H, 'proc_create_time', create_time)
    monkeypatch.setattr(H, '_pid_absent', lambda pid: pid in absent)


def _spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(S, '_PRIOR_SHIM_GONE_S', 0.2)
    launched: list = []
    monkeypatch.setattr(C, 'launch', lambda *a, **k: launched.append(1) or C.LaunchResult(
        True, 'probe', proc=SimpleNamespace(), spec={'launch_id': 'new'}))
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    _Popen.calls = []
    proc = S.spawn_engine_proc(['fake'], session_id='oneshot', engine='codex',
                               root=tmp_path, cfg=ON)
    return proc, launched


@pytest.mark.parametrize('exited', [False, True])
def test_gone_shim_with_an_unverifiable_cli_keeps_its_records(tmp_path, monkeypatch, exited):
    """Fenn's probe: shim proven absent, CLI create-time lookup denied and its
    absence unproven. No launch, shim.json (and exit.json) untouched, plain Popen."""
    d = _prior(tmp_path, exited=exited)
    before = C.read_json(d / 'shim.json')
    _world(monkeypatch, create_time=lambda pid: None, absent={SHIM_PID})
    prior = C.inspect(d)
    assert prior.shim_alive is False and prior.cli_alive is False     # the folded booleans
    assert H.process_state(CLI_PID, CLI_CT) == 'unknown'

    proc, launched = _spawn(tmp_path, monkeypatch)

    assert launched == []
    assert isinstance(proc, _Popen) and len(_Popen.calls) == 1
    assert C.read_json(d / 'shim.json') == before
    assert (d / 'exit.json').exists() is exited


def test_unverifiable_shim_with_a_gone_cli_still_keeps_its_records(tmp_path, monkeypatch):
    d = _prior(tmp_path, exited=True)
    _world(monkeypatch, create_time=lambda pid: None, absent={CLI_PID})
    _, launched = _spawn(tmp_path, monkeypatch)
    assert launched == []
    assert (d / 'shim.json').exists() and (d / 'exit.json').exists()


@pytest.mark.parametrize('exited', [False, True])
def test_positively_gone_shim_and_cli_reuse_the_directory(tmp_path, monkeypatch, exited):
    _prior(tmp_path, exited=exited)
    _world(monkeypatch, create_time=lambda pid: None, absent={SHIM_PID, CLI_PID})
    _, launched = _spawn(tmp_path, monkeypatch)
    assert launched == [1]


def test_pids_reused_by_other_processes_count_as_gone(tmp_path, monkeypatch):
    _prior(tmp_path, exited=True)
    _world(monkeypatch, create_time=lambda pid: 9999.0)
    _, launched = _spawn(tmp_path, monkeypatch)
    assert launched == [1]
