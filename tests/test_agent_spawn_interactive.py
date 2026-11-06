"""MC-1150 P3: Claude Mode B (persistent stream-json) spawns go through
`spawn_engine_proc(..., interactive=True)`.

  1. Flag OFF is the old spawn: `subprocess.Popen` gets the keyword arguments
     the Mode B sites used to pass (`stdin=PIPE`, ...). `interactive` without a
     session id, and `interactive` + `deliver`, are handled.
  2. Flag ON runs a stand-in stream-json CLI (tests/fake_claude_streamjson.py)
     under a real interactive shim: a stream-json round trip over the socket,
     two turns on one process, EOF on close, the owner connection collected so
     the shim leaves.
  3. The stdin-hang watchdog (`_bounded_stdin_write`) still ends a CLI that
     never reads stdin, when stdin is the shim socket.
  4. Interrupt: kill the CLI by pid and start a new one in the SAME session
     directory straight away; the new shim waits for the old one and starts
     past its output.
  5. A mid-turn roll (prompt written after the reader started, stdin closed)
     delivers one turn and ends with rc 0.

Process hygiene: every shim/CLI a test starts is killed at teardown by
(pid, create-time) identity. Nothing is killed by image name.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn_interactive.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402
from mc import state  # noqa: E402

from tests.test_agent_shim import Tracker, wait_for  # noqa: E402

FAKE = str(REPO / 'tests' / 'fake_claude_streamjson.py')
PY = getattr(sys, '_base_executable', sys.executable)
FLAG = 'agent_survive_restart_enabled'


@pytest.fixture
def tracker():
    t = Tracker()
    yield t
    t.reap()


@pytest.fixture
def root(tmp_path):
    S.set_root(tmp_path / 'shims')
    had = FLAG in state.CONFIG
    old = state.CONFIG.get(FLAG)
    yield tmp_path / 'shims'
    S.set_root(None)
    if had:
        state.CONFIG[FLAG] = old
    else:
        state.CONFIG.pop(FLAG, None)


def _on(on: bool) -> None:
    state.CONFIG[FLAG] = bool(on)


def _fake(*flags):
    return [PY, FAKE, *flags]


def _user(text: str) -> str:
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}) + '\n'


def _until_result(proc, limit=30.0):
    """Lines up to and including the next `result` line (the turn boundary)."""
    out, end = [], time.monotonic() + limit
    for line in proc.stdout:
        out.append(json.loads(line))
        if out[-1].get('type') == 'result':
            return out
        assert time.monotonic() < end, 'no result line'
    raise AssertionError(f'stdout ended before a result line: {out}')


@pytest.fixture
def routes():
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc.blueprints import agent_routes
    return agent_routes


# ───────────────────── 1. flag OFF is the old spawn ─────────────────────────
class _Popen:
    calls: list = []

    def __init__(self, cmd, **kw):
        type(self).calls.append((cmd, kw))
        self.stdin = MagicMock()


def test_flag_off_interactive_is_the_mode_b_popen(root, monkeypatch):
    _on(False)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    env = {'A': '1'}
    S.spawn_engine_proc(['claude', '-p'], session_id='b1', engine='claude', cwd='/w', env=env,
                        interactive=True)
    (cmd, kw), = _Popen.calls
    assert cmd == ['claude', '-p']
    assert kw['stdin'] == subprocess.PIPE
    assert kw['stdout'] == subprocess.PIPE and kw['stderr'] == subprocess.STDOUT
    assert kw['cwd'] == '/w' and kw['env'] is env
    assert kw['text'] is True and kw['encoding'] == 'utf-8' and kw['errors'] == 'replace'
    for k, v in S.POPEN_NEW_SESSION.items():
        assert kw[k] == v


def test_flag_off_not_interactive_keeps_a_closed_stdin(root, monkeypatch):
    """The ordinary Mode A resume in agent_interrupt (is_midturn False)."""
    _on(False)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    S.spawn_engine_proc(['claude'], session_id='b2', engine='claude', interactive=False)
    assert _Popen.calls[0][1]['stdin'] == subprocess.DEVNULL


def test_flag_on_without_a_session_id_spawns_the_old_way(root, monkeypatch):
    _on(True)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: pytest.fail('shim launched without a session id'))
    S.spawn_engine_proc(['claude'], session_id=None, engine='claude', interactive=True)
    assert _Popen.calls[0][1]['stdin'] == subprocess.PIPE


def test_interactive_and_deliver_are_mutually_exclusive(root):
    with pytest.raises(ValueError):
        S.spawn_engine_proc(['x'], session_id='b3', engine='claude', interactive=True,
                            deliver=lambda p: None)


def test_a_failed_interactive_shim_launch_falls_back_to_the_stdin_pipe(root, monkeypatch):
    _on(True)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: C.LaunchResult(
        ok=False, branch='x', error='boom'))
    S.spawn_engine_proc(['claude'], session_id='b4', engine='claude', interactive=True)
    assert _Popen.calls[0][1]['stdin'] == subprocess.PIPE


# ───────────────────── 2. flag ON: stream-json round trip ───────────────────
def test_flag_on_stream_json_round_trip_over_the_shim_socket(root, tracker):
    _on(True)
    p = S.spawn_engine_proc(_fake(), session_id='rt1', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'rt1'))
    assert isinstance(p, C.ShimProc) and p.args == _fake()
    first = json.loads(next(iter(p.stdout)))
    assert first['type'] == 'system' and first['subtype'] == 'init'
    for text in ('héllo ✓', 'second turn'):               # two turns on ONE process
        p.stdin.write(_user(text))
        p.stdin.flush()
        lines = _until_result(p)
        assert lines[0]['message']['content'][0]['text'] == f'echo: {text}'
    p.stdin.close()
    assert p.wait(20) == 0 and p.returncode == 0


def test_closing_the_owner_connection_lets_the_shim_leave(root, tracker):
    """An interactive shim waits for its owner to collect the exit; the
    collector thread started by the factory does it, so the next respawn finds
    the directory free instead of waiting out `shim_exit_grace_min`."""
    _on(True)
    p = S.spawn_engine_proc(_fake(), session_id='rt2', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'rt2'))
    next(iter(p.stdout))
    p.stdin.close()
    assert p.wait(20) == 0
    sdir = C.session_dir(root, 'rt2')
    assert wait_for(lambda: (lambda st: st is None or not st.shim_alive)(C.inspect(sdir)), 20), \
        'shim still alive after the CLI exited and the owner connection was collected'


# ───────────────────── 3. stdin-hang watchdog still kills the CLI ───────────
def test_stdin_hang_watchdog_kills_a_cli_that_never_reads_stdin(root, tracker, routes):
    _on(True)
    p = S.spawn_engine_proc(_fake('--hang'), session_id='hg1', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'hg1'))
    assert p.poll() is None
    big = _user('x' * (4 * 1024 * 1024))                  # far over any pipe buffer
    t0 = time.monotonic()
    res = routes._bounded_stdin_write(p, big, timeout=1.0, wait_for_ack=30)
    assert time.monotonic() - t0 < 25, 'the watchdog did not break the stuck write'
    assert res['ack'] != 'written', res
    assert wait_for(lambda: p.poll() is not None, 15), 'the CLI survived the watchdog'


# ───────────────────── 4. interrupt: kill by pid, then a new shim ───────────
def test_interrupt_kills_the_cli_and_the_same_session_gets_a_new_shim(root, tracker, routes):
    _on(True)
    a = S.spawn_engine_proc(_fake(), session_id='int1', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'int1'))
    next(iter(a.stdout))
    a.stdin.write(_user('one'))
    _until_result(a)
    old_pid = a.pid
    routes._kill_proc_background(a)                        # what agent_interrupt does
    # Straight away, as the respawn does: the old shim may still be exiting.
    b = S.spawn_engine_proc(_fake(), session_id='int1', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'int1'))
    assert isinstance(b, C.ShimProc), 'the respawn fell back to a plain Popen'
    assert b.pid != old_pid and a.poll() is not None
    first = json.loads(next(iter(b.stdout)))
    assert first['subtype'] == 'init' and first['session_id'] == f'fake-sid-{b.pid}'
    b.stdin.write(_user('two'))
    assert _until_result(b)[0]['message']['content'][0]['text'] == 'echo: two'
    b.stdin.close()
    assert b.wait(20) == 0


# ───────────────────── 5. mid-turn roll: prompt after the reader starts ─────
def test_midturn_roll_prompt_written_after_the_reader_starts(root, tracker):
    _on(True)
    p = S.spawn_engine_proc(_fake(), session_id='roll1', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'roll1'))
    out = iter(p.stdout)
    assert json.loads(next(out))['subtype'] == 'init'     # the reader is draining
    p.stdin.write(_user('carry on'))
    p.stdin.close()                                       # `-p` ends its prompt with EOF
    got = [json.loads(l) for l in out]
    assert [m['type'] for m in got] == ['assistant', 'result']
    assert p.wait(20) == 0


# ───────────────────── 6. _sysprompt_cleanup on a ShimProc ──────────────────
def test_sysprompt_cleanup_removes_the_file_when_a_shim_cli_exits(root, tracker, routes, tmp_path):
    _on(True)
    sp = tmp_path / 'sysprompt.txt'
    sp.write_text('persona', encoding='utf-8')
    p = S.spawn_engine_proc(_fake(), session_id='sp1', engine='claude', interactive=True)
    tracker.add_manifest(C.session_dir(root, 'sp1'))
    routes._sysprompt_cleanup(str(sp), p)
    next(iter(p.stdout))
    assert sp.exists(), 'the file was removed while the CLI was still running'
    p.stdin.close()
    assert wait_for(lambda: not sp.exists(), 20), 'the file outlived the CLI'
