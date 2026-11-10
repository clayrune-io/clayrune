"""MC-1148 P11: read-only Floor summaries; no model activity or run mutation.

Projection functions accept canonical snapshots. ``read_projects`` is the lazy,
flagged storage adapter; all grouping/counting/ordering stays in this module.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from mc import state
from mc.core import _log
from mc.test_run_store import OUTCOMES, TERMINAL_STATES, TestRunStore

FLAG = 'test_run_progress_enabled'
MAX_RUNS = 100


def hidden_owner(session: Mapping[str, Any]) -> bool:
    return bool(session.get('incognito') or session.get('_incognito')
                or session.get('_codex_unattended_sandbox')
                or (session.get('housekeeping') and not session.get('hivemind_id')))


def _measured(run: Mapping[str, Any]) -> bool:
    total, completed = run.get('selected_total'), run.get('completed')
    return (run.get('capture') == 'complete'
            and isinstance(total, int) and not isinstance(total, bool) and total >= 0
            and isinstance(completed, int) and not isinstance(completed, bool)
            and 0 <= completed <= total)


def summarize(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate only complete, same-runner/same-unit known denominators.

    Parents and children must already be reduced to independent roots. Percent
    is weighted by units, never averaged from individual run percentages.
    """
    items = sorted(runs, key=lambda r: (r['created_at'], r['run_id']))
    compatible = bool(items) and all(_measured(r) for r in items) and len({
        (r['runner'], r['unit']) for r in items}) == 1
    total = sum(r['selected_total'] for r in items) if compatible else None
    completed = sum(r['completed'] for r in items) if compatible else None
    percent = 100 * completed / total if total and completed is not None else None
    collecting = (len(items) == 1 and items[0]['runner'] == 'pytest'
                  and items[0]['total_revision'] == 0 and items[0]['capture'] == 'complete')
    phase = ('finalizing' if total and completed == total else 'progress' if total
             else 'multiple' if len(items) > 1 else 'collecting' if collecting else 'unknown')
    # Only display data; never expose cwd, process identity, output or receipts.
    keys = ('run_id', 'owner_session_id', 'parent_run_id', 'runner', 'unit',
            'selected_total', 'completed', 'counts', 'capture', 'total_revision',
            'total_recalculated', 'collection_errors', 'updated_at')
    return {
        'available': True, 'active_count': len(items), 'phase': phase,
        'runner': items[0]['runner'] if compatible else None,
        'unit': items[0]['unit'] if compatible else None,
        'selected_total': total, 'completed': completed, 'percent': percent,
        'counts': {k: sum(r['counts'][k] for r in items) for k in OUTCOMES} if compatible else None,
        'capture': 'complete' if all(r['capture'] == 'complete' for r in items) else 'partial',
        'total_recalculated': any(r['total_recalculated'] for r in items),
        'runs': [{**{k: r[k] for k in keys}, 'counts': dict(r['counts'])}
                 for r in items[:MAX_RUNS]],
        'runs_omitted': max(0, len(items) - MAX_RUNS),
    }


def project(project_id: str, snapshots: Iterable[Mapping[str, Any]],
            sessions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Keep active owners linked, without transferring absent owners' runs.

    A manifest and its child describe the same work at different unit levels.
    Only independent active roots contribute to the Floor aggregate.
    """
    active = {r['run_id']: r for r in snapshots
              if r['project_id'] == project_id and r['state'] not in TERMINAL_STATES
              and not hidden_owner(sessions.get(r['owner_session_id'], {}))}
    roots = []
    for run in active.values():
        parent = active.get(run['parent_run_id'])
        if parent is None or parent['owner_session_id'] != run['owner_session_id']:
            roots.append(run)
    owners: dict[str, list[Mapping[str, Any]]] = {}
    ownerless, unavailable = 0, 0
    for run in roots:
        sid = run['owner_session_id']
        owner = sessions.get(sid)
        if sid is None:
            ownerless += 1
        elif owner is None or owner.get('project_id') != project_id or owner.get('session_id') != sid:
            unavailable += 1
        else:
            owners.setdefault(sid, []).append(run)
    return {'summary': {**summarize(roots), 'ownerless_count': ownerless,
                        'unavailable_owner_count': unavailable},
            'owners': {sid: summarize(runs) for sid, runs in owners.items()}}


def _all_runs(store: TestRunStore, project_id: str) -> Iterable[dict[str, Any]]:
    return store.all_snapshots(project_id)


def read_projects(projects: Iterable[Mapping[str, Any]],
                  sessions: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]] | None:
    """Scan the canonical store once per room, without caching route projections.

    The store caches only validated snapshot copies across requests, keyed by
    file path/mtime_ns/size. Changed records replay before reuse; owner/privacy
    classification and room summaries are rebuilt from current sessions.

    Disabled means no import of the routes/storage adapter and no added fields.
    A failed read is explicitly unavailable, never an invented empty success.
    """
    if state.CONFIG.get(FLAG) is not True:
        return None
    from mc.blueprints.test_run_routes import _storage

    result = {}
    owners = dict(sessions)
    for room in projects:
        pid = room.get('id')
        if not pid or pid == '_incognito' or room.get('_is_incognito_project'):
            continue
        try:
            result[pid] = project(pid, _all_runs(_storage(), pid), owners)
        except Exception as exc:
            _log(f'[test-run-floor] project read failed ({pid}): {exc}', flush=True)
            result[pid] = {'summary': {**summarize([]), 'available': False,
                                      'error': 'test_runs_unavailable',
                                      'ownerless_count': 0, 'unavailable_owner_count': 0},
                           'owners': {}}
    return result


def owner_summary(projections: Mapping[str, Any] | None, session: Mapping[str, Any]) -> dict | None:
    if projections is None:
        return None
    return projections.get(session.get('project_id') or '', {}).get('owners', {}).get(session.get('session_id'))


def retain_owner(projections: Mapping[str, Any] | None, session: Mapping[str, Any]) -> bool:
    return not hidden_owner(session) and owner_summary(projections, session) is not None


def room_visible(summary: Mapping[str, Any] | None) -> bool:
    return summary is not None and (bool(summary.get('active_count')) or not summary.get('available'))


def figure_rank(figure: Mapping[str, Any]) -> tuple[int, str]:
    rank = (0 if figure['state'] == 'asking' else 1 if figure['state'] == 'working'
            or figure.get('test_runs', {}).get('active_count') else 2)
    return rank, figure.get('started_at') or ''


def room_rank(room: Mapping[str, Any]) -> tuple[int, str]:
    states = {f['state'] for f in room['figures']}
    rank = (0 if 'asking' in states else 1 if 'working' in states
            or room.get('test_runs', {}).get('active_count') else 2)
    return rank, (room['name'] or '').lower()
