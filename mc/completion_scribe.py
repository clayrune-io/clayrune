"""R38 (MC-1179): the Scribe's model call must not run while the project lock is held.

`_log_agent_completion_body` ends by calling `_write_session_memory`, which is
synchronous down to the provider CLI (one model call per transcript chunk, then
one to reduce). Its three callers -- the Mode A reader `finally`, the Mode B
`finish`, and the runtime exit hook -- all call it inside
`with get_manager(project_id).lock:`, so every other request for that project
(dispatch, send, stop, the delegation inbox) waited for the Scribe.

The body now hands the Scribe step to `run_or_defer`. A caller that holds the
lock opens a `window()` around it:

    with _completion_scribe.window(_log), get_manager(pid).lock:
        ...

`with` releases the lock first and then closes the window, which runs the queued
steps; `window` flushes in a `finally`, so a caller that raises still gets its
Scribe step run rather than lost. Windows nest on a thread (a depth counter): an
inner window neither discards the outer one's queue nor switches deferral off,
and only the outermost close runs the steps. A caller with no window (boot
replay, the stop route) gets the old behaviour: the step runs inline.

The state is thread-local, so two reader threads finishing together each run only
their own turn's steps. Lock order is unchanged (manager -> agent-log leaf): the
step takes no manager lock, and its agent-log write (`mark_scribed`) goes through
`_update_agent_log`'s own leaf lock, outside any other transaction.
"""
import threading
from contextlib import contextmanager
from typing import Callable

_tls = threading.local()


def begin() -> None:
    """Open a collecting window on this thread. Nests: only the outermost
    `flush()` runs what was queued."""
    if getattr(_tls, 'depth', 0) == 0:
        _tls.pending = []
    _tls.depth = getattr(_tls, 'depth', 0) + 1


def run_or_defer(step: Callable[[], None]) -> bool:
    """Queue `step` if this thread has an open window; otherwise run it now.
    Returns True when it was queued. `step` carries its own exception guard."""
    if getattr(_tls, 'depth', 0) > 0:
        _tls.pending.append(step)
        return True
    step()
    return False


def flush(log: Callable[[str], None] = print) -> None:
    """Close one window level. The outermost close runs the queued steps. A flush
    with no open window is a no-op. Never raises: a Scribe failure is logged and
    must not skip the caller's teardown."""
    depth = getattr(_tls, 'depth', 0)
    if depth <= 0:
        return
    _tls.depth = depth - 1
    if _tls.depth > 0:
        return
    pending, _tls.pending = _tls.pending, []
    for step in pending:
        try:
            step()
        except Exception as e:  # steps carry their own guard; this is the backstop
            log(f"[completion-scribe] deferred scribe step failed: {e}")


@contextmanager
def window(log: Callable[[str], None] = print):
    """`begin()` on entry, `flush()` on exit, including when the body raises."""
    begin()
    try:
        yield
    finally:
        flush(log)
