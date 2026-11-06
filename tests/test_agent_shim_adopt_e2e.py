"""MC-1150 P4 end-to-end: a server process is killed mid-turn, a new one adopts.

Real pieces: `QwenRuntime.dispatch`, the supervisor shim, `spawn_engine_proc`,
the shared Mode A reader, `agent_shim_adopt.claim_all/start_all`, and for the
hivemind case the real orchestrator loop. The only stand-ins are the CLI (a
fake engine that prints N lines) and the server wrapper
(tests/agent_survive_server_standin.py).

Process hygiene: every server/shim/CLI started here is killed at teardown by
(pid, create-time) identity; the dying server is killed by its recorded pid.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_adopt_e2e.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim_client as C  # noqa: E402

from tests.test_agent_shim import Tracker, wait_for  # noqa: E402
from tests.test_agent_spawn import _kill_server  # noqa: E402

STANDIN = str(REPO / 'tests' / 'agent_survive_server_standin.py')
SID = 'e2e_survive'


@pytest.fixture
def tracker():
    t = Tracker()
    yield t
    t.reap()


def _json(path):
    try:
        return json.loads(path.read_text()) if path.exists() and path.stat().st_size else None
    except ValueError:
        return None


def _run_start(work, tracker, lines, interval, hm=False):
    args = [sys.executable, STANDIN, 'start', str(work), SID, str(lines), str(interval)]
    if hm:
        args.append('--hm')
    srv = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec = wait_for(lambda: _json(work / 'start.json'), timeout=40)
    if not rec:
        srv.kill()
    assert rec and rec.get('shim_proc') is True, rec
    tracker.add_manifest(C.session_dir(work / 'shims', SID))
    return srv, rec


def _run_adopt(work, hm=False):
    args = [sys.executable, STANDIN, 'adopt', str(work), SID]
    if hm:
        args.append('--hm')
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = p.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        p.kill()
        raise
    rep = _json(work / 'adopt.json')
    assert rep is not None, f'adopt server wrote no report (rc={p.returncode}):\n{out[-3000:]}'
    return rep


def _completions(work):
    f = work / 'completions.log'
    rows = f.read_text().splitlines() if f.exists() else []
    return [r for r in rows if not r.startswith('body-raised')]


def test_server_killed_mid_turn_new_server_adopts_and_completes_once(tmp_path, tracker):
    srv, rec = _run_start(tmp_path, tracker, lines=40, interval=0.15)
    sdir = C.session_dir(tmp_path / 'shims', SID)
    try:
        assert wait_for(lambda: (sdir / 'out.log').exists() and (sdir / 'out.log').stat().st_size > 0, 15)
        _kill_server(rec['pid'])                       # what a restart does to the server
        srv.kill()
        srv.wait(10)
        assert not (sdir / 'exit.json').exists(), 'CLI finished before the restart; test is not mid-turn'
        assert _completions(tmp_path) == [], 'completion fired in the server that died'
        rep = _run_adopt(tmp_path)
    finally:
        if srv.poll() is None:
            srv.kill()
    assert rep['claimed'] == [SID] and rep['started'] == [SID], rep
    assert rep['adopted'] is True
    assert rep['status'] == 'completed', rep
    done = _completions(tmp_path)
    assert len(done) == 1, f'completion must fire exactly once, got {done}'
    assert done[0].split()[1] == SID and done[0].split()[2] == 'completed'
    # Nothing between the checkpoint and the end was lost; the first turn's own
    # lines are all there, including the last one.
    text = '\n'.join(rep['lines'])
    for n in range(40):
        assert f'qwen: line {n}' in text, f'line {n} missing after adoption'


def test_cli_finishing_during_the_gap_is_collected_and_the_hivemind_worker_completes(tmp_path, tracker):
    srv, rec = _run_start(tmp_path, tracker, lines=6, interval=0.05, hm=True)
    sdir = C.session_dir(tmp_path / 'shims', SID)
    try:
        _kill_server(rec['pid'])
        srv.kill()
        srv.wait(10)
        assert wait_for(lambda: (sdir / 'exit.json').exists(), 30), 'CLI never finished in the gap'
        assert _completions(tmp_path) == []
        rep = _run_adopt(tmp_path, hm=True)
    finally:
        if srv.poll() is None:
            srv.kill()
    assert rep['started'] == [SID], rep
    assert rep['status'] == 'completed', rep
    assert len(_completions(tmp_path)) == 1, _completions(tmp_path)
    assert rep['ws_status'] == 'completed', 'orchestrator never saw the adopted worker finish'
    text = '\n'.join(rep['lines'])
    assert 'qwen: line 5' in text
