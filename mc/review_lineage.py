"""Reviewer independence by dispatch lineage (MC-1075 follow-up, MC-1077).

`merge_review_gate.record()` already refuses a reviewer that IS the branch
owner. That is not independence: a builder can dispatch a child, ask it to
review the branch, and the child is a different session id that was spun up
by, briefed by, and (via `notify_session`) reports to the author. A child --
or a grandchild -- of the owner is the author vouching for the author.

So the review route also walks the reviewer's dispatch ancestry and refuses
when the branch owner is anywhere in it.

Where a session's spawner is recorded (all of it in `mc/blueprints/agent_routes`):

  * live:    `agent_sessions[sid]['_spawned_by']` -- set once at dispatch/revive.
  * durable: the agent-log row's `dispatched_by_session_id` (same value, written
    at dispatch and completion), with `_durable_spawner_of_record()` as the one
    reader that also handles pre-fix rows. The re-armable `_notify_session` /
    `spawned_by_session_id` is a callback target, not lineage, and is NOT read
    here except through that helper's guarded legacy fallback.

Fails closed. Lineage that cannot be established is a refusal, never a pass:
a session on the chain that is neither live nor in any readable agent log, an
unreadable log, or a chain longer than any real delegation tree.

Known limit: the recorded spawner is whatever the dispatching caller passed as
`notify_session` (see `agent_dispatch`), not the OS-attributed caller. A
builder that dispatches a reviewer WITHOUT naming itself leaves no lineage to
walk. This module closes the recorded-lineage bypass only.

The walk (`owner_in_ancestry`) is pure so it is testable without Flask or
sessions; `check()` is the production wiring the route calls.
"""
from __future__ import annotations

from typing import Callable, Iterable

from mc.core import _log
from mc.merge_review_gate import ReviewRefused

# A real delegation tree is a handful of levels deep. Past this the data is
# corrupt or adversarial, and a refusal is the safe answer.
MAX_NODES = 200


class LineageUnreadable(Exception):
    """The ancestry of a session cannot be established."""


SpawnerOf = Callable[[str], Iterable[str]]


def owner_in_ancestry(reviewer: str, owner: str, spawner_of: SpawnerOf) -> list[str]:
    """The dispatch path from `reviewer` up to `owner` (reviewer first, owner
    last) when `owner` is an ancestor of `reviewer`, else [].

    `spawner_of(sid)` returns the spawner session ids recorded for `sid` (empty
    for a session nobody dispatched) and raises LineageUnreadable for a session
    it has no record of. Raises LineageUnreadable when the walk exceeds
    MAX_NODES. Breadth-first with a visited set, so a polluted mutual-spawner
    cycle terminates instead of looping."""
    seen = {reviewer}
    queue: list[list[str]] = [[reviewer]]
    while queue:
        path = queue.pop(0)
        for parent in spawner_of(path[-1]):
            parent = (parent or '').strip()
            if not parent or parent in seen:
                continue
            if parent == owner:
                return path + [parent]
            seen.add(parent)
            if len(seen) > MAX_NODES:
                raise LineageUnreadable(f'ancestry exceeds {MAX_NODES} sessions')
            queue.append(path + [parent])
    return []


def _production_spawner_of() -> SpawnerOf:
    """`spawner_of` over live sessions plus every project's agent log.

    Imported lazily: agent_routes is the monolith that owns the log format and
    `_durable_spawner_of_record`, and this module must stay importable without
    it (tests, tools)."""
    from mc import state
    from mc.blueprints import agent_routes as ar

    rows: dict[str, dict] | None = None

    def load_rows() -> dict[str, dict]:
        # Newest row per session across all projects: a spawner can sit in a
        # different project from the reviewer. An unreadable log is skipped
        # here and surfaces as LineageUnreadable only if a session turns out
        # to be recorded nowhere else.
        by_sid: dict[str, dict] = {}
        data_dir = ar.DATA_DIR
        if data_dir is None:
            return by_sid
        for path in sorted(data_dir.glob('*_agent_log.json')):
            pid = path.name[:-len('_agent_log.json')]
            try:
                if not ar._agent_log_is_readable(pid):
                    _log(f'[review-lineage] {path.name} unreadable; skipped')
                    continue
                log = ar._load_agent_log(pid)
            except Exception as e:
                _log(f'[review-lineage] {path.name} could not be read: {e}')
                continue
            for row in log if isinstance(log, list) else []:
                sid = row.get('session_id') if isinstance(row, dict) else ''
                if sid and sid not in by_sid:
                    by_sid[sid] = row
        return by_sid

    def spawner_of(sid: str) -> list[str]:
        nonlocal rows
        found: list[str] = []
        known = False
        live = state.agent_sessions.get(sid)
        if isinstance(live, dict):
            known = True
            found.append((live.get('_spawned_by') or '').strip())
        if rows is None:
            rows = load_rows()
        row = rows.get(sid)
        if row is not None:
            known = True
            found.append(ar._durable_spawner_of_record(row, rows))
        if not known:
            raise LineageUnreadable(f'no live session or agent-log row for {sid[:12]}')
        return [s for s in found if s and s != sid]

    return spawner_of


def check(reviewer: str, owner: str, spawner_of: SpawnerOf | None = None) -> None:
    """Raise ReviewRefused unless `reviewer` is independent of `owner` by
    dispatch lineage. A reviewer that IS the owner is left to the gate's own
    self-review refusal."""
    if not reviewer or reviewer == owner:
        return
    try:
        path = owner_in_ancestry(reviewer, owner, spawner_of or _production_spawner_of())
    except LineageUnreadable as e:
        raise ReviewRefused(
            403, f'cannot establish the dispatch lineage of the reviewer ({e}); '
                 f'a review is refused when its independence cannot be shown')
    except Exception as e:
        _log(f'[review-lineage] walk failed for reviewer {reviewer[:12]}: {e}')
        raise ReviewRefused(
            403, 'cannot establish the dispatch lineage of the reviewer; a review '
                 'is refused when its independence cannot be shown')
    if path:
        raise ReviewRefused(
            403, 'reviewer refused: this session descends from the branch owner '
                 f'by dispatch ({" -> ".join(s[:12] for s in reversed(path))}); a '
                 'session the builder dispatched is not an independent reviewer',
            lineage=[s[:12] for s in reversed(path)])
