"""R38 (MC-1179): the Scribe's model call must not run while the project lock is held.

`_log_agent_completion_body` ends by calling `_write_session_memory`, which is
synchronous down to the provider CLI (one model call per transcript chunk, then
one to reduce). Its three callers -- the Mode A reader `finally`, the Mode B
`finish`, and the runtime exit hook -- all call it inside
`with get_manager(project_id).lock:`, so every other request for that project
(dispatch, send, stop, the delegation inbox) waited for the Scribe.

The body now hands the Scribe step to `run_or_defer`. A caller that holds the
lock calls `begin()` before taking it and `flush()` after releasing it; in
between, `run_or_defer` only queues. A caller that did not call `begin()` (boot
replay, the stop route) gets the old behaviour: the step runs inline.

The queue is thread-local, so two reader threads finishing together each flush
only their own turn. The lock order is unchanged (manager -> agent-log leaf):
the step takes no manager lock, and its agent-log write (`mark_scribed`) goes
through `_update_agent_log`'s own leaf lock, outside any other transaction.
"""
import threading
from typing import Callable, Optional

_tls = threading.local()


def begin() -> None:
    """Start collecting Scribe steps on this thread. Resets anything left over."""
    _tls.pending = []


def run_or_defer(step: Callable[[], None]) -> bool:
    """Queue `step` if this thread called `begin()`; otherwise run it now.
    Returns True when it was queued. `step` carries its own exception guard."""
    pending: Optional[list] = getattr(_tls, 'pending', None)
    if pending is None:
        step()
        return False
    pending.append(step)
    return True


def flush(log: Callable[[str], None] = print) -> None:
    """Run the queued steps, then stop collecting. Never raises: a Scribe failure
    is logged and must not skip the caller's teardown."""
    pending = getattr(_tls, 'pending', None)
    _tls.pending = None
    for step in pending or ():
        try:
            step()
        except Exception as e:  # steps carry their own guard; this is the backstop
            log(f"[completion-scribe] deferred scribe step failed: {e}")
