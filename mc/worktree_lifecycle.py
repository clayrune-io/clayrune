"""MC-1165: one exclusion between "resume a session" and "merge back + remove its
worktree", and the one lock order that goes with it.

Removing an isolated session's worktree is only safe if the session cannot start
running between the check that it is terminal and the removal. Every path that
removes a worktree after such a check (`agent_routes._worktree_cold_review_done`,
`_worktree_retry_removal`) runs inside `removal_window`; every resume that spawns
a process for an isolated session runs inside `resuming` from the moment it
picks the launch directory (`mc/isolated_launch.py`) until the session reads
`running`. A resume that arrives mid-window waits for it, then finds the tree
gone and recreates it rather than being pulled out from under a live process.
`live_process_refs` cannot do this job: it excludes agent processes on purpose
(the ending turn's own process is still registered).

THE LOCK ORDER (review 3 finding 1): the project lock first, then the session's
lifecycle lock. Both windows take them in that order and `resuming` takes the
project lock too, so a resume can no longer hold the lifecycle lock while
something inside it reaches for the project lock. That is structural, not an
audit: the project lock is re-entrant, so every project-lock acquisition below
the window is the same thread re-entering a lock it already owns. The earlier
version of this docstring said the guardian "takes only the second" and that
indirect calls were checked; they were not. `_auto_dispatch_followup` built its
system prompt while holding the lifecycle lock, and that reaches
`coordination_routes.sibling_activity -> ProjectAgentManager.iter_sessions`,
which takes the project lock: lifecycle -> project against the cold callback's
project -> lifecycle. Anything that collects project state (context, prompts)
is built BEFORE the window opens; only the cwd decision, the spawn and the flip
to `running` happen inside it.

Resume paths, audited 2026-11-07 (MC-1165 round 4):
- `_auto_dispatch_followup` (the reader thread's queued-follow-up drain, the
  guardian's recovery, the guardian's stuck-queue replay) and
  `_auto_recover_failed_resume` (Mode A and B): spawn, THEN flip to `running`
  -> hold `resuming` across both.
- HTTP follow-up / interrupt / dispatch with a reused id: flip `running` under
  the project lock BEFORE they spawn, so the window (which holds the same lock)
  refuses a terminal session that is not terminal any more. They still pick
  their cwd through `resuming` (`agent_routes._launch_cwd`), because that is
  where a missing tree is recreated or the launch refused.
- `_note_self_started_turn` (a background-task wake) only fires from `idle`,
  which the window already refuses.

Both locks are re-entrant. Per-session locks are never dropped (one small
object per session id for the life of the process).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Generator

_guard = threading.Lock()
_locks: dict[str, Any] = {}
_held = threading.local()


def _lock_for(session_id: str):
    with _guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = _locks[session_id] = threading.RLock()
        return lock


def holds(session_id: str) -> bool:
    """True when THIS thread is inside a window for `session_id`."""
    return bool(getattr(_held, 'sids', {}).get(session_id or '', 0))


@contextmanager
def _window(project_lock: Any, session_id: str) -> Generator[None, None, None]:
    sid = session_id or ''
    with project_lock:
        with _lock_for(sid):
            sids = _held.__dict__.setdefault('sids', {})
            sids[sid] = sids.get(sid, 0) + 1
            try:
                yield
            finally:
                sids[sid] -= 1
                if not sids[sid]:
                    del sids[sid]


def resuming(project_lock: Any, session_id: str):
    """Held by a resume from choosing the launch directory until the session
    reads `running` (spawn first, flip second)."""
    return _window(project_lock, session_id)


def removal_window(project_lock: Any, session_id: str):
    """Held from "is the session terminal?" through the worktree's removal."""
    return _window(project_lock, session_id)
