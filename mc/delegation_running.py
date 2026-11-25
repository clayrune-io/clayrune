"""Which dispatched children are still running, per parent session.

2026-11-24: nobody noticed that a Tobin dispatched from a Clayrune website
chat was working, because the child ran in the Mission Control project. The
parent chat said nothing until the report came back, and the only view of the
child was the Floor or the other project's chat list.

This is a read-only projection of the live session map for /agent/status
(`children_running` per session, painted by static/js/children-running.js).
It spans every project on purpose: parent and child are usually not in the
same one. Once a child's turn ends it drops out, and the queued report takes
over (mc/delegation_waiting.py).

Lineage (see mc/dispatch_lineage.py): a session is a child of
  - the session that dispatched it (`_spawned_by`, recorded once), and
  - the session its report will wake (`_notify_session`), which a later
    `/agent/send` can re-arm to someone else.
The second edge is ignored when it points DOWN the dispatch chain: a child that
messages its own spawner naming itself as `notify_session` would otherwise
make the spawner show up as the child's child (the 2026-09-24 banner bug).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from mc import background_tasks as _bg_tasks
from mc import log_lines_trim as _log_lines_trim
from mc.core import _log

_ANCESTOR_LIMIT = 32


def _spawner(session: dict) -> str:
    return (session.get('_spawned_by') or '').strip()


def _is_ancestor(candidate: str, session_id: str, sessions: dict) -> bool:
    """True when `candidate` dispatched `session_id`, directly or further up."""
    seen: set[str] = set()
    current = session_id
    while current and current not in seen and len(seen) < _ANCESTOR_LIMIT:
        seen.add(current)
        current = _spawner(sessions.get(current) or {})
        if current == candidate:
            return True
    return False


def parents_of(child_id: str, child: dict, sessions: dict) -> set[str]:
    """Session ids that should see `child` as one of their running children."""
    parents = {_spawner(child)}
    notify = (child.get('_notify_session') or '').strip()
    if notify and not _is_ancestor(child_id, notify, sessions):
        parents.add(notify)
    parents.discard('')
    parents.discard(child_id)
    return parents


def run_state(session: dict) -> str:
    """'working' | 'asking' | 'background', or '' when the child is not running.

    'background' is a turn that ended while the spawner callback is still held
    on a real background job (MC-946): no report yet, so the child is not done.
    """
    if session.get('status') == 'running':
        if session.get('waiting_for_question') or session.get('waiting_for_plan_approval'):
            return 'asking'
        return 'working'
    return 'background' if _bg_tasks.waiting_label(session) else ''


def step_count(session: dict) -> int:
    """Tool calls in the child's chat so far.

    `num_turns` only moves when a turn ENDS, so it cannot count a running one.
    """
    return _log_lines_trim.count_steps(session.get('log_lines'))


def _floor_name(session: dict, labels: dict) -> str:
    """The name the Floor draws, so the two surfaces cannot disagree."""
    from mc.blueprints.floor_routes import _figure_name
    return _figure_name(session, labels)[0]


def _floor_labels() -> dict:
    from mc.blueprints.floor_routes import read_labels
    return read_labels()


def running_by_parent(
    sessions: dict,
    parent_ids: set[str],
    load_project: Callable[[str], Any],
    *,
    name_of: Optional[Callable[[dict, dict], str]] = None,
    labels_fn: Optional[Callable[[], dict]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Running children keyed by parent id, oldest first, for `parent_ids` only.

    Incognito sessions and housekeeping chores are left out, as on the Floor.
    Never raises: a status poll must not fail because of this line.
    """
    try:
        snapshot = dict(sessions)
        matches: list[tuple[str, dict, str, set[str]]] = []
        for sid, s in snapshot.items():
            if s.get('incognito') or (s.get('housekeeping') and not s.get('hivemind_id')):
                continue
            state = run_state(s)
            if not state:
                continue
            wanted = parents_of(sid, s, snapshot) & parent_ids
            if wanted:
                matches.append((sid, s, state, wanted))
        if not matches:
            return {}
        labels = (labels_fn or _floor_labels)()
        names: dict[str, str] = {}
        out: dict[str, list[dict[str, Any]]] = {}
        for sid, s, state, wanted in sorted(
                matches, key=lambda m: m[1].get('started_at') or ''):
            pid = s.get('project_id') or ''
            if pid not in names:
                names[pid] = (load_project(pid) or {}).get('name') or pid
            row = {
                'session_id': sid,
                'claude_session_id': s.get('claude_session_id') or '',
                'project_id': pid,
                'project_name': names[pid],
                'name': (name_of or _floor_name)(s, labels) or 'An agent',
                'state': state,
                'started_at': s.get('started_at') or '',
                'steps': step_count(s),
            }
            for parent in wanted:
                out.setdefault(parent, []).append(row)
        return out
    except Exception as e:
        _log(f'[delegation-running] projection failed: {type(e).__name__}: {e}', flush=True)
        return {}
