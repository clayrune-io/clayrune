"""MC-1150 P3 review fix (Fenn blocker 2): the two ROUTES that start an
interactive CLI, driven with `agent_survive_restart_enabled` ON against an owned
stand-in CLI (tests/fake_claude_streamjson.py) under a real interactive shim.

  1. Mode B interrupt (`agent_interrupt`, agent_routes ~14731): the respawn is a
     `ShimProc`, and the new message reaches the CLI over the shim socket.
  2. Mode A mid-turn roll (`agent_interrupt(_internal=midturn)`, ~14807): the
     handoff prompt is written to the CLI's stdin, never put on the command line,
     and arrives whole.

The route builds its real `claude` argv; the only substitution is a wrapper on
the route module's `spawn_engine_proc` that swaps argv for the stand-in (the
original is recorded and asserted on). Without it the shim would exec the real
claude, which tests/conftest.py now refuses (tests/test_no_real_shim_cli_guard.py).
The readers are stubbed as in tests/test_midturn_rollover.py (their teardown
fires Scribe/distiller one-shots); the test reads the CLI's output itself.

Process hygiene: every shim/CLI started is killed at teardown by (pid,
create-time) identity.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn_interactive_routes.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402
from mc import state  # noqa: E402

from tests import conftest  # noqa: E402
from tests.test_agent_shim import Tracker, wait_for  # noqa: E402
from tests.test_midturn_rollover import _Proc, _session  # noqa: E402

FAKE = str(REPO / 'tests' / 'fake_claude_streamjson.py')
PY = getattr(sys, '_base_executable', sys.executable)
FLAG = 'agent_survive_restart_enabled'
SID = 'worker-1'


@pytest.fixture
def tracker():
    t = Tracker()
    yield t
    t.reap()


@pytest.fixture
def renv(tmp_path, monkeypatch, tracker):
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import midturn_rollover
    from mc.blueprints import agent_routes as ar
    from mc.delegation_delivery import DeliveryStore

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 't@example.invalid'],
                 ['config', 'user.name', 't']):
        subprocess.run(['git', *args], cwd=project_path, check=True)
    (project_path / 'tracked.txt').write_text('a\n')
    subprocess.run(['git', 'add', 'tracked.txt'], cwd=project_path, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=project_path, check=True)
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}

    root = tmp_path / 'shims'
    S.set_root(root)
    monkeypatch.setitem(state.CONFIG, FLAG, True)
    monkeypatch.setitem(state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(state.CONFIG, 'context_rollover_tokens', 200_000)
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 4096))
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'FRESH CONTEXT')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda pid, line: None)
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a, **k: None)
    monkeypatch.setattr(midturn_rollover, '_log_dir', lambda: tmp_path / 'midturn_log')

    dump = tmp_path / 'cli_stdin.txt'
    routed = []                       # the argv the ROUTE built, before the swap
    real_spawn = ar.spawn_engine_proc

    def spawn_with_stand_in(cmd, **kw):
        routed.append((list(cmd), kw))
        return real_spawn([PY, FAKE, '--dump-stdin', str(dump)], **kw)

    monkeypatch.setattr(ar, 'spawn_engine_proc', spawn_with_stand_in)

    snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'ar': ar, 'app': server.app, 'routed': routed, 'dump': dump, 'pp': project_path,
               'sdir': C.session_dir(root, SID), 'tracker': tracker,
               'real_spawn': real_spawn}
    finally:
        state.agent_sessions.clear()
        state.agent_sessions.update(snapshot)
        S.set_root(None)


def _until_result(proc, limit=30):
    out = []
    for line in proc.stdout:
        out.append(json.loads(line))
        if out[-1].get('type') == 'result':
            return out
    raise AssertionError(f'stdout ended before a result line: {out}')


def _shim_proc(session):
    return session.get('proc') if isinstance(session.get('proc'), C.ShimProc) else None


def test_mode_b_interrupt_respawns_into_the_shim_and_delivers_the_message(renv):
    old = _Proc(pid=111)
    session = _session(old, mode='B', claude_session_id=None)
    state.agent_sessions[SID] = session
    with renv['app'].test_request_context(
            '/api/project/p1/agent/interrupt', method='POST',
            json={'session_id': SID, 'message': 'please switch to the other file'}):
        resp = renv['ar'].agent_interrupt('p1')
    assert resp.get_json()['ok'] is True
    assert wait_for(lambda: _shim_proc(session) is not None, 30), \
        f"respawn never reached the shim: {session['log_lines']}"
    renv['tracker'].add_manifest(renv['sdir'])
    proc = session['proc']

    (cmd, kw), = renv['routed']
    assert cmd[0] == 'claude' and kw['interactive'] is True and kw['session_id'] == SID
    assert json.loads(next(iter(proc.stdout)))['subtype'] == 'init'
    reply = _until_result(proc)
    assert 'please switch to the other file' in json.dumps(reply)
    assert session['status'] == 'running' and '_interrupting' not in session
    proc.stdin.close()
    assert proc.wait(20) == 0


def test_mode_a_midturn_roll_sends_the_handoff_over_stdin_not_the_command_line(renv):
    big_task = 'Investigate the flaky importer. ' + ('detail ' * 3000)      # ~21k chars
    assert len(big_task) > 10_000
    old = _Proc(pid=222)
    session = _session(old, mode='A', task=big_task)
    state.agent_sessions[SID] = session
    payload, status = renv['ar'].agent_interrupt('p1', _internal={
        'session_id': SID, 'message': renv['ar']._midturn.ROLL_MESSAGE, 'midturn': True,
        'proc': old, 'tokens': 250_000,
        'build_state': lambda cwd: renv['ar']._midturn.build_roll_state_block(session, cwd)})
    assert status == 200, payload
    assert wait_for(lambda: _shim_proc(session) is not None, 30), \
        f"roll never reached the shim: {session['log_lines']}"
    renv['tracker'].add_manifest(renv['sdir'])
    proc = session['proc']

    (cmd, kw), = renv['routed']
    assert cmd[0] == 'claude' and '-p' in cmd and kw['interactive'] is True
    assert sum(len(c) + 1 for c in cmd) < 8191, 'the roll put its prompt on the command line'
    assert not any(big_task in c for c in cmd)
    assert proc.wait(30) == 0                       # `-p`: prompt then EOF, CLI exits clean
    sent = renv['dump'].read_text(encoding='utf-8')
    assert big_task in sent and 'Mid-task rollover state' in sent
    assert session['session_id'] == SID and '_mt_roll_requested' not in session


def test_without_the_stand_in_the_route_is_refused_before_a_real_claude_starts(renv, monkeypatch):
    """The failure Fenn found: flag ON + the route's own argv reaches a detached
    shim whose CLI is the real `claude`. conftest must refuse it at the spec."""
    monkeypatch.setattr(renv['ar'], 'spawn_engine_proc', renv['real_spawn'])
    before = len(conftest._REAL_CLI_ATTEMPTS)
    session = _session(_Proc(pid=333), mode='B', claude_session_id=None)
    state.agent_sessions[SID] = session
    with renv['app'].test_request_context(
            '/api/project/p1/agent/interrupt', method='POST',
            json={'session_id': SID, 'message': 'hello'}):
        renv['ar'].agent_interrupt('p1')
    assert wait_for(lambda: len(conftest._REAL_CLI_ATTEMPTS) > before, 30), session['log_lines']
    assert wait_for(lambda: any('supervisor shim' in l for l in session['log_lines']), 10), \
        session['log_lines']
    assert not (renv['sdir'] / 'shim.json').exists(), 'a shim was started for a real claude'
    conftest._REAL_CLI_ATTEMPTS[before:] = []        # provoked on purpose; keep the autouse check quiet
