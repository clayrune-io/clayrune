"""MC-1150 P2: Mode A engine spawns go through `spawn_engine_proc`.

Three things are proven here, none of them with a mocked CLI except where the
test says so:

  1. Flag OFF is the old spawn. `subprocess.Popen` gets the keyword arguments
     every Mode A site used to pass, and `deliver` runs on the Popen result.
  2. Flag ON runs the CLI under a real supervisor shim and hands back a
     complete `ShimProc`: output, return code, prompt delivery, a second turn
     in the same session directory starting past the first turn's output.
  3. Every Mode A runtime (gemini, qwen, codex, opencode, goose, aider, kiro)
     produces the same session outcome in both flag states, for dispatch and
     for a follow-up turn, driven against a fake CLI (tests/fake_engine_cli.py).
     Killing the server mid-turn leaves the engine log growing.

Process hygiene: every shim/CLI/server a test starts is killed at teardown by
(pid, create-time) identity. Nothing is killed by image name.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_runtime as ar  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402
from mc import state  # noqa: E402

from tests.test_agent_shim import Tracker, wait_for  # noqa: E402

STANDIN = str(REPO / 'tests' / 'agent_shim_standin.py')
FAKE = str(REPO / 'tests' / 'fake_engine_cli.py')
SERVER = str(REPO / 'tests' / 'agent_spawn_server_standin.py')
PY = getattr(sys, '_base_executable', sys.executable)
FLAG = 'agent_survive_restart_enabled'


@pytest.fixture
def tracker():
    t = Tracker()
    yield t
    t.reap()


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A private shims root, and the flag restored afterwards."""
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


def _read_all(proc, limit=30.0):
    out, end = [], time.monotonic() + limit
    for line in proc.stdout:
        out.append(line)
        assert time.monotonic() < end, 'stdout did not end'
    return out


# ───────────────────── 1. flag OFF is the old spawn ─────────────────────────
class _Popen:
    calls: list = []

    def __init__(self, cmd, **kw):
        type(self).calls.append((cmd, kw))
        self.stdin = MagicMock()
        self.cmd = cmd


@pytest.mark.parametrize('with_deliver', [True, False])
def test_flag_off_calls_popen_with_the_kwargs_every_site_used(root, monkeypatch, with_deliver):
    _on(False)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    seen = []
    env = {'A': '1'}
    p = S.spawn_engine_proc(['x', '--y'], session_id='s1', engine='qwen', cwd='/w', env=env,
                            deliver=(lambda proc: seen.append(proc)) if with_deliver else None)
    (cmd, kw), = _Popen.calls
    assert cmd == ['x', '--y']
    assert kw['stdin'] == (subprocess.PIPE if with_deliver else subprocess.DEVNULL)
    assert kw['stdout'] == subprocess.PIPE and kw['stderr'] == subprocess.STDOUT
    assert kw['cwd'] == '/w' and kw['env'] is env
    assert kw['text'] is True and kw['encoding'] == 'utf-8' and kw['errors'] == 'replace'
    assert kw['creationflags'] == ar._POPEN_FLAGS and kw['startupinfo'] is ar._STARTUPINFO
    for k, v in S.POPEN_NEW_SESSION.items():
        assert kw[k] == v
    assert seen == ([p] if with_deliver else [])


def test_flag_on_without_a_session_id_still_spawns_the_old_way(root, monkeypatch):
    _on(True)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: pytest.fail('shim launched without a session id'))
    S.spawn_engine_proc(['x'], session_id=None, engine='qwen')
    assert len(_Popen.calls) == 1


# ───────────────────── 2. flag ON returns a complete ShimProc ───────────────
def _child(*flags):
    return [PY, STANDIN, 'child', *flags]


def test_flag_on_delivers_the_prompt_and_returns_a_complete_proc(root, tracker):
    _on(True)
    prompt = 'héllo\nsecond line ✓\nthird'
    p = S.spawn_engine_proc(_child('--cat'), session_id='on1', engine='qwen',
                            deliver=lambda h: (h.stdin.write(prompt), h.stdin.close(), None)[-1])
    tracker.add_manifest(C.session_dir(root, 'on1'))
    assert isinstance(p, C.ShimProc)
    assert p.args == _child('--cat') and p.stderr is None and p.pid
    lines = _read_all(p)
    # The CLI reads stdin to EOF and echoes it: byte-for-byte what a text-mode
    # Popen pipe would have carried (os.linesep translation included).
    assert ''.join(lines).replace('\r\n', '\n') == 'got:' + prompt.replace('\n', os.linesep).replace('\r\n', '\n')
    assert p.wait(15) == 0 and p.returncode == 0 and p.poll() == 0


def test_flag_on_without_deliver_gives_the_cli_a_closed_stdin(root, tracker):
    _on(True)
    p = S.spawn_engine_proc(_child('--lines', '3', '--interval', '0.01', '--rc', '3'),
                            session_id='on2', engine='aider')
    tracker.add_manifest(C.session_dir(root, 'on2'))
    assert [l.strip() for l in _read_all(p)] == ['line 0', 'line 1', 'line 2']
    assert p.wait(15) == 3


def test_second_turn_in_the_same_session_starts_past_the_first_turns_output(root, tracker):
    _on(True)
    a = S.spawn_engine_proc(_child('--lines', '2', '--interval', '0.01'), session_id='on3', engine='goose')
    tracker.add_manifest(C.session_dir(root, 'on3'))
    assert len(_read_all(a)) == 2
    a.wait(15)
    b = S.spawn_engine_proc(_child('--lines', '1', '--interval', '0.01', '--rc', '7'),
                            session_id='on3', engine='goose')
    tracker.add_manifest(C.session_dir(root, 'on3'))
    assert [l.strip() for l in _read_all(b)] == ['line 0']          # not 3 lines
    assert b.wait(15) == 7


def test_a_live_previous_shim_in_the_session_falls_back_to_popen(root, tracker, monkeypatch):
    _on(True)
    a = S.spawn_engine_proc(_child('--lines', '40', '--interval', '0.1'), session_id='on4', engine='kiro')
    tracker.add_manifest(C.session_dir(root, 'on4'))
    assert isinstance(a, C.ShimProc)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    b = S.spawn_engine_proc(['x'], session_id='on4', engine='kiro')
    assert isinstance(b, _Popen) and len(_Popen.calls) == 1
    a.kill()


def test_a_failed_shim_launch_falls_back_to_popen_and_writes_the_captured_prompt(root, monkeypatch):
    _on(True)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: C.LaunchResult(False, 'x', error='no breakaway'))
    got, after_close = [], []

    def deliver(h):
        got.append(type(h.stdin).__name__)
        h.stdin.write('prompt')
        h.stdin.close()
        after_close.append(True)                                    # only reached if close() returned
    p = S.spawn_engine_proc(['x'], session_id='fb1', engine='codex', deliver=deliver)
    assert isinstance(p, _Popen) and len(_Popen.calls) == 1
    assert _Popen.calls[0][1]['stdin'] == subprocess.PIPE
    assert wait_for(lambda: after_close, timeout=5)
    # Captured once and written to the fallback pipe; the delivery code is not run again.
    assert got == ['_CaptureStdin']
    p.stdin.buffer.write.assert_called_once_with(b'prompt')
    p.stdin.close.assert_called_once()


def test_a_fallback_pipe_that_rejects_the_prompt_fails_the_delivery(root, monkeypatch):
    _on(True)

    class _Broken(_Popen):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, **kw)
            self.stdin.buffer.write.side_effect = BrokenPipeError('gone')
    monkeypatch.setattr(S.subprocess, 'Popen', _Broken)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: C.LaunchResult(False, 'x', error='no breakaway'))
    seen = []

    def deliver(h):
        h.stdin.write('prompt')
        try:
            h.stdin.close()
        except BrokenPipeError as e:
            seen.append(e)
    S.spawn_engine_proc(['x'], session_id='fb3', engine='codex', deliver=deliver)
    assert wait_for(lambda: seen, timeout=5)


def test_a_teardown_that_left_a_cli_possibly_running_refuses_to_start_another(root, monkeypatch):
    _on(True)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: C.LaunchResult(False, 'x', cli_running=True, error='late'))
    with pytest.raises(RuntimeError, match='not starting a second one'):
        S.spawn_engine_proc(['x'], session_id='fb2', engine='codex')
    assert _Popen.calls == []


def test_a_missing_cli_binary_raises_file_not_found_in_both_states(root):
    for on in (False, True):
        _on(on)
        with pytest.raises(FileNotFoundError):
            S.spawn_engine_proc([str(root / 'no-such-cli')], session_id=f'nf{int(on)}', engine='qwen')


def test_capture_stdin_behaves_like_a_text_mode_popen_pipe():
    cap = S._CaptureStdin()
    assert not cap.closed
    cap.write('a\nb é')
    cap.settle(True)                                                # close() waits for the outcome
    cap.close()
    assert cap.closed and cap.done.is_set()
    assert cap.payload() == ('a' + os.linesep + 'b é').encode('utf-8')
    with pytest.raises(ValueError):
        cap.write('late')
    cap.close()                                                     # idempotent


# ───────────── LogTail now matches text=True newline handling ───────────────
@pytest.mark.parametrize('raw,expect', [
    (b'one\r\ntwo\r\n', ['one\n', 'two\n']),
    (b'one\ntwo\n', ['one\n', 'two\n']),
    (b'a\rb\r\nc', ['a\n', 'b\n', 'c']),
    (b'tail\r', ['tail\n']),
])
def test_logtail_uses_universal_newlines(tmp_path, raw, expect):
    f = tmp_path / 'out.log'
    f.write_bytes(raw)
    t = C.LogTail(f, finished=lambda: True)
    assert list(t) == expect and t.offset == len(raw)


def test_logtail_holds_back_a_trailing_cr_until_the_lf_arrives(tmp_path):
    f = tmp_path / 'out.log'
    f.write_bytes(b'abc\r')
    done = []
    t = C.LogTail(f, finished=lambda: bool(done), poll_s=0.01)
    got = []
    th = threading.Thread(target=lambda: got.extend(t), daemon=True)
    th.start()
    time.sleep(0.2)
    assert got == []                                                # \r may be half of \r\n
    with open(f, 'ab') as fh:
        fh.write(b'\ndef\n')
    time.sleep(0.2)
    done.append(1)
    th.join(5)
    assert got == ['abc\n', 'def\n']


# ───────── 3. every Mode A runtime, both flag states, fake CLI ──────────────
ENGINES = ['gemini', 'qwen', 'codex', 'opencode', 'goose', 'aider', 'kiro']


def _outcome(eng, tmp_path, on):
    """Dispatch + one follow-up against the fake CLI; return what the session ends up with."""
    rt = ar.get_runtime(eng)
    assert rt is not None
    saved = (rt.resolve_binary, rt.build_command)
    rt.resolve_binary = lambda: PY                                  # type: ignore[assignment]
    rt.build_command = lambda *a, **k: [PY, FAKE, eng]              # type: ignore[assignment]
    try:
        sid = f'm_{eng}_{int(on)}'
        sd = {'session_id': sid, 'status': 'running', 'log_lines': [], 'project_id': 'p'}
        h = rt.dispatch(project_path=str(tmp_path), task='Hello, world.', system_prompt='', mode='A',
                        model='', mc_session_id=sid, session_dict=sd, project_id='p',
                        register_process=lambda *a, **k: None)
        first = _await_turn(sd)
        kinds = [type(sd['proc']).__name__]
        before = len(sd['log_lines'])
        sd['status'] = 'idle'
        rt.write_followup(h, 'second message')
        time.sleep(0.3)
        second = _await_turn(sd)
        kinds.append(type(sd['proc']).__name__)
        return {
            'kinds': kinds,
            'first': first, 'second': second,
            'lines1': [str(x) for x in list(sd['log_lines'])[:before]],
            'lines2': [str(x) for x in list(sd['log_lines'])[before:]],
            'procs': [sd['proc']],
        }
    finally:
        rt.resolve_binary, rt.build_command = saved                 # type: ignore[assignment]


def _await_turn(sd, limit=30.0):
    end = time.monotonic() + limit
    while time.monotonic() < end and sd.get('status') == 'running':
        time.sleep(0.05)
    assert sd.get('status') != 'running', 'turn never finished'
    return sd['status']


def _strip_bytes(lines):
    """prompt-bytes differs by os.linesep only; everything else must match exactly."""
    return [l.split('prompt-bytes=')[0] if 'prompt-bytes=' in l else l for l in lines]


@pytest.mark.parametrize('eng', ENGINES)
def test_runtime_outcome_is_identical_with_the_flag_off_and_on(eng, root, tracker, tmp_path, monkeypatch):
    # Codex claims history before delivery; this standalone factory suite does
    # not import server.wire(). Give its real writer a temporary destination.
    from mc.blueprints import agent_routes
    history = tmp_path / 'history'
    history.mkdir()
    monkeypatch.setattr(agent_routes, 'DATA_DIR', history)
    _on(False)
    off = _outcome(eng, tmp_path, False)
    _on(True)
    on = _outcome(eng, tmp_path, True)
    tracker.add_manifest(C.session_dir(root, f'm_{eng}_1'))
    assert off['kinds'] == ['Popen', 'Popen']
    assert on['kinds'] == ['ShimProc', 'ShimProc']
    assert (on['first'], on['second']) == (off['first'], off['second'])
    assert _strip_bytes(on['lines1']) == _strip_bytes(off['lines1'])
    assert _strip_bytes(on['lines2']) == _strip_bytes(off['lines2'])   # second turn: no replay of turn 1
    if eng != 'gemini':                                              # gemini's reader expects stream-json
        assert any(f'{eng}: line 1' in l for l in on['lines1'])
        assert any(f'{eng}: line 1' in l for l in on['lines2'])
    assert on['procs'][0].returncode == 0 or on['procs'][0].poll() == 0


def _kill_server(pid):
    """Kill the stand-in server we started (by its recorded pid, never by name)."""
    try:
        import psutil
        psutil.Process(pid).kill()
    except ImportError:
        os.kill(pid, 9)
    except Exception:
        pass


# ───────── kill the server mid-turn: the engine log keeps growing ───────────
def test_killing_the_server_mid_turn_leaves_the_engine_log_growing(tmp_path, tracker):
    shims = tmp_path / 'shims'
    out = tmp_path / 'server.json'
    # sys.executable, like pytest itself: a base-python child of this process is refused
    # job breakaway on a launcher-wrapped host and would fall back to a plain Popen.
    srv = subprocess.Popen([sys.executable, SERVER, str(shims), str(out), 'killme', '80', '0.1'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rec = wait_for(lambda: (json.loads(out.read_text()) if out.exists() and out.stat().st_size else None),
                       timeout=30)
        assert rec and rec.get('shim_proc') is True, rec
        sdir = C.session_dir(shims, 'killme')
        tracker.add_manifest(sdir)
        log = sdir / 'out.log'

        def size():
            try:
                return log.stat().st_size
            except OSError:
                return 0
        assert wait_for(lambda: size() > 0, timeout=15)
        _kill_server(rec['pid'])                                     # what a restart does to the server
        srv.kill()
        srv.wait(10)
        at_kill = size()
        grew = wait_for(lambda: size() > at_kill + 30, timeout=15)
        m = C.read_json(sdir / 'shim.json') or {}
        assert grew, f'engine log stopped growing after the server died ({at_kill} -> {size()})'
        assert m.get('cli_pid')
    finally:
        if srv.poll() is None:
            srv.kill()
