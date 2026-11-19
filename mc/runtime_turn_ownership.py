"""Serialize Mode A reader finalization with follow-up replacement."""

from contextlib import contextmanager
import threading
from typing import Callable, TYPE_CHECKING

from mc import completion_scribe
from mc.core import _log

if TYPE_CHECKING:
    from mc.agent_runtime import SessionHandle


def _lock_for(handle: 'SessionHandle'):
    get_lock = handle.meta.get('callbacks', {}).get('turn_lock')
    if get_lock is not None:
        return get_lock(handle.project_id)
    return handle.session_dict.setdefault('_runtime_turn_lock', threading.RLock())


@contextmanager
def finalizing(handle: 'SessionHandle'):
    # Completion callbacks run inside the project lock. Defer Scribe's model
    # call until the outermost lock has been released.
    with completion_scribe.window(_log), _lock_for(handle):
        yield


def retire(handle: 'SessionHandle', kill: Callable[[int], None]) -> None:
    with _lock_for(handle):
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc is not None:
            # An exited process may still have a reader draining stdout.
            session['proc'] = None
    if old_proc is not None and old_proc.poll() is None:
        kill(old_proc.pid)
