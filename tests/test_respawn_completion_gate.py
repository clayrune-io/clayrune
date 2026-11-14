"""WinError 267 (2026-11-14): during an MC-1002 respawn the killed proc's
reader still owned the session, ran its terminal path and removed the
session's worktree just before the new proc launched into it.
`_completion_owned_by` closes the reader's terminal path while the respawn
handoff window is open, and only then."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402,F401  (registers the blueprint)
from mc.blueprints import agent_routes as ar  # noqa: E402


class _P:
    pass


def test_owner_without_respawn_completes():
    p = _P()
    assert ar._completion_owned_by({'proc': p}, p) is True


def test_owner_during_respawn_window_does_not_complete():
    p = _P()
    s = {'proc': p, '_respawn_in_flight': time.time()}
    assert ar._session_owned_by(s, p) is True      # stream loop keeps draining
    assert ar._completion_owned_by(s, p) is False  # but no teardown


def test_stale_respawn_flag_does_not_suppress_completion_forever():
    p = _P()
    s = {'proc': p, '_respawn_in_flight': time.time() - ar._RESPAWN_WINDOW_MAX_S - 1}
    assert ar._completion_owned_by(s, p) is True


def test_replaced_proc_never_completes():
    old, new = _P(), _P()
    assert ar._completion_owned_by({'proc': new}, old) is False


def test_all_terminal_gates_use_completion_ownership():
    import inspect
    for fn in (ar._read_agent_stream, ar._read_agent_stream_b):
        src = inspect.getsource(fn)
        assert '_credits_replayed and _completion_owned_by(session, my_proc)' in src, fn.__name__
        assert '_credits_replayed and _session_owned_by(session, my_proc)' not in src, fn.__name__
