"""MC-1150 P2, the three blockers of Fenn's re-review of f9c502f3, one test group each.

  1. `_capture_prompt` must wait for the prompt to be CLOSED, not for the thread
     that started the writer to die (`deliver` normally just starts a thread).
  2. The shim-failure fallback must not write the captured prompt into the plain
     Popen's pipe on the caller's thread: a CLI that fills stdout before reading
     a large prompt deadlocks it, because the caller has not started its reader.
  3. The uncertain-launch refusal runs whenever there is a session id,
     independent of `agent_survive_restart_enabled` (toggling the flag off must
     not let a retry run beside an unresolved uncertain CLI).

No real CLI is launched and no process is signalled.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn_p2_rereview.py -q
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402
from mc import agent_uncertain_launch as U  # noqa: E402

from tests.test_agent_shim import wait_for  # noqa: E402
from tests.test_agent_spawn import _Popen, _on, root  # noqa: E402,F401  (root is a fixture)
from tests.test_agent_spawn_review_fixes import _Procs, _uncertain_launch  # noqa: E402


# ───────── 1. capture waits for the close, not for the thread that started the writer ─────────
def test_capture_waits_for_a_writer_thread_that_closes_stdin_after_deliver_returned():
    """Fenn's probe: deliver() starts a writer thread and returns at once; the old loop
    left when that runner thread died and launched an empty prompt 31ms in."""
    def deliver(h):
        def writer():
            time.sleep(0.4)
            h.stdin.write('late prompt')
            h.stdin.close()                                   # blocks until settle()
        threading.Thread(target=writer, daemon=True).start()
    t0 = time.monotonic()
    cap = S._capture_prompt(deliver, 'cap1', 'codex')
    try:
        assert cap.done.is_set() and cap.payload() == b'late prompt'
        assert time.monotonic() - t0 >= 0.35                  # it really waited for the writer
    finally:
        cap.settle(True)


def test_capture_still_raises_a_delivery_exception_without_waiting_out_the_timeout(monkeypatch):
    monkeypatch.setattr(S, '_CAPTURE_WAIT_S', 30.0)

    def deliver(h):
        raise ValueError('boom')
    t0 = time.monotonic()
    with pytest.raises(ValueError, match='boom'):
        S._capture_prompt(deliver, 'cap2', 'codex')
    assert time.monotonic() - t0 < 5.0


def test_capture_gives_up_on_a_writer_that_never_closes(monkeypatch):
    monkeypatch.setattr(S, '_CAPTURE_WAIT_S', 0.3)
    cap = S._capture_prompt(lambda h: None, 'cap3', 'codex')
    assert not cap.done.is_set()
    with pytest.raises(BrokenPipeError):                      # settled False: a late close() fails
        cap.close()


# ───────── 2. fallback: the pipe write must not run on the caller's thread ─────────
class _BlockingPopen(_Popen):
    """A CLI that has filled its stdout and reads no stdin until the caller drains it:
    the first write blocks until `gate` is set."""
    gate = threading.Event()
    wrote: list = []

    def __init__(self, cmd, **kw):
        super().__init__(cmd, **kw)

        def write(data):
            if not type(self).gate.wait(10):
                raise TimeoutError('gate never opened')
            type(self).wrote.append(data)
        self.stdin.buffer.write.side_effect = write


@pytest.mark.parametrize('defer', [False, True])
def test_the_fallback_prompt_write_does_not_block_the_caller_that_has_to_start_its_reader(
        root, monkeypatch, defer):
    _on(True)
    _BlockingPopen.gate = threading.Event()
    _BlockingPopen.wrote = []
    monkeypatch.setattr(S.subprocess, 'Popen', _BlockingPopen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: C.LaunchResult(False, 'x', error='no breakaway'))
    closed: list = []

    def deliver(h):
        def writer():
            h.stdin.write('big prompt')
            try:
                h.stdin.close()
            except BrokenPipeError as e:
                closed.append(e)
            else:
                closed.append(None)
        threading.Thread(target=writer, daemon=True).start()
    out: list = []

    def call():
        out.append(S.spawn_engine_proc(['x'], session_id='fb-block', engine='codex', deliver=deliver,
                                       defer_delivery=defer))
    t = threading.Thread(target=call, daemon=True)
    t.start()
    try:
        t.join(2.0)
        assert not t.is_alive(), 'spawn_engine_proc blocked on the fallback pipe write'
        proc = out[0]
        assert _BlockingPopen.wrote == []                     # the reader gets to start first
        S.finish_delivery(proc)                               # no-op unless defer_delivery held the feed
    finally:
        _BlockingPopen.gate.set()                             # "the caller drained stdout"
    assert wait_for(lambda: closed, timeout=5)
    assert closed == [None]                                   # close() returned: delivery was truthful
    assert _BlockingPopen.wrote == [b'big prompt']
    proc.stdin.close.assert_called_once()


def test_a_deferred_fallback_feeds_nothing_until_finish_delivery(root, monkeypatch):
    _on(True)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    monkeypatch.setattr(C, 'launch', lambda *a, **k: C.LaunchResult(False, 'x', error='no breakaway'))
    closed: list = []

    def deliver(h):
        def writer():
            h.stdin.write('p')
            h.stdin.close()
            closed.append(True)
        threading.Thread(target=writer, daemon=True).start()
    proc = S.spawn_engine_proc(['x'], session_id='fb-defer', engine='codex', deliver=deliver,
                               defer_delivery=True)
    time.sleep(0.3)
    assert closed == [] and not proc.stdin.buffer.write.called
    S.finish_delivery(proc)
    assert wait_for(lambda: closed, timeout=5)
    proc.stdin.buffer.write.assert_called_once_with(b'p')


# ───────── 3. the uncertain-launch refusal does not depend on the flag ─────────
@pytest.mark.parametrize('flag', [False, True])
def test_an_unresolved_uncertain_launch_refuses_the_session_with_the_flag_in_either_state(
        root, monkeypatch, flag):
    d, res = _uncertain_launch(root, 'unc-flag', manifest=False)
    _Procs(set()).install(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    with pytest.raises(RuntimeError, match='not starting a second one'):
        S.spawn_engine_proc(['fake'], session_id='unc-flag', engine='codex', root=root, cfg={'agent_survive_restart_enabled': True})
    assert U.pending(d)['launch_id'] == 'L1'                  # the flag-ON launch left the marker
    # An operator turns the feature off. The marker still guards the session.
    monkeypatch.setattr(S, '_popen', lambda *a, **k: pytest.fail('started a CLI beside an uncertain one'))
    with pytest.raises(RuntimeError, match='unresolved'):
        S.spawn_engine_proc(['fake'], session_id='unc-flag', engine='codex', root=root,
                            cfg={'agent_survive_restart_enabled': flag})


def test_with_the_flag_off_a_confirmable_marker_is_cleared_and_the_spawn_proceeds(root, monkeypatch):
    d, res = _uncertain_launch(root, 'unc-clear', manifest=False)
    _Procs(set()).install(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    with pytest.raises(RuntimeError):
        S.spawn_engine_proc(['fake'], session_id='unc-clear', engine='codex', root=root,
                            cfg={'agent_survive_restart_enabled': True})
    # The launch's exit record shows it never started a CLI: the marker is confirmable now.
    C.write_json_atomic(d / 'exit.json', dict(launch_id='L1'))
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    S.spawn_engine_proc(['fake'], session_id='unc-clear', engine='codex', root=root,
                        cfg={'agent_survive_restart_enabled': False})
    assert len(_Popen.calls) == 1 and not U.marker_path(d).exists()


def test_with_the_flag_off_and_no_session_directory_the_spawn_is_the_plain_popen(root, monkeypatch):
    _on(False)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    S.spawn_engine_proc(['x'], session_id='never-seen', engine='qwen', cfg={'agent_survive_restart_enabled': False})
    assert len(_Popen.calls) == 1
    assert not (root / 'never-seen').exists()                 # the check did not create anything
