"""MC-1165: one exclusion between "resume a session" and "merge back + remove its
worktree".

Removing an isolated session's worktree is only safe if the session cannot start
running between the check that it is terminal and the removal. Every path that
removes a worktree after such a check (`agent_routes._worktree_cold_review_done`,
`_worktree_retry_removal`) runs inside `removal_window`; every resume that does
NOT already flip the session to `running` under the project lock runs inside
`resuming`. A resume that arrives mid-window waits for it, then finds the tree
gone (`agent_worktree_followup` recreates it) rather than being pulled out from
under a live process. `live_process_refs` cannot do this job: it excludes agent
processes on purpose (the ending turn's own process is still registered).

Resume paths, audited 2026-11-07 (MC-1165 review 2):
- HTTP follow-up / interrupt / Mode A auto-recover / dispatch with a reused id:
  flip `running` while holding the project lock -> excluded by the window, which
  holds the same lock.
- `_auto_dispatch_followup` (the reader thread's queued-follow-up drain, the
  guardian's recovery, the guardian's stuck-queue replay): spawns, THEN flips to
  `running`, and the guardian two calls hold no lock -> wrapped in `resuming`.
- `_note_self_started_turn` (a background-task wake) only fires from `idle`,
  which the window already refuses.

Lock order is always project lock first, then the session's lifecycle lock; the
guardian takes only the second, so nothing here nests the other way round.
Both are re-entrant. Per-session locks are never dropped (one small object per
session id for the life of the process).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

_guard = threading.Lock()
_locks: dict[str, Any] = {}


def _lock_for(session_id: str):
    with _guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = _locks[session_id] = threading.RLock()
        return lock


@contextmanager
def resuming(session_id: str) -> Iterator[None]:
    """Held by a resume that spawns before it marks the session `running`."""
    with _lock_for(session_id or ''):
        yield


@contextmanager
def removal_window(project_lock: Any, session_id: str) -> Iterator[None]:
    """Held from "is the session terminal?" through the worktree's removal."""
    with project_lock:
        with _lock_for(session_id or ''):
            yield
