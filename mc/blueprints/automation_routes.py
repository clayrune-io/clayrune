"""Consent-first automation suggestions — routes (MC-915).

Three routes, and the shape of them IS the safety property:

  * GET  /api/automation/suggestions            — refresh + list what is PENDING
  * POST /api/automation/suggestions/<id>/accept  — the human's explicit yes
  * POST /api/automation/suggestions/<id>/dismiss — the human's latched no

There is no fourth route, and in particular there is no way to create a schedule
from a suggestion other than a human POSTing to /accept. The miner
(mc.automation_suggestions) cannot reach the schedules store at all; this file
is the only bridge, and the bridge is one explicit request wide. Clayrune's
binding rail — a human on at least one side of every learning loop, and learning
that never expands what the agent may do — holds by construction rather than by
convention. Do not add an "auto-accept", a "trusted suggestion" flag, or a
background accept: that turns the queue into the exact silent self-modification
the rail exists to stop.

ACCEPT DOES NOT WRITE A SCHEDULE ITSELF. It calls
`scheduler_routes.create_schedule_from_spec` — the same function
`POST /api/schedules` calls. One job engine, one validator, one next_run
computation. See the note on that function.

Cross-family deps arrive via wire() (the 1.13 scheduler_routes precedent):
the agent-log reader lives in agent_routes, the project list in project_routes.
"""
from typing import Callable, Optional

from flask import Blueprint, jsonify, request

from mc import automation_suggestions as _sugg
from mc.core import _log
from mc.blueprints import scheduler_routes as _sched

bp = Blueprint('automation_routes', __name__)

# -- wired by server.py (see wire()) ------------------------------------------
_load_agent_log: Callable[[str], list] = None  # type: ignore[assignment]
load_projects: Callable[[], list] = None  # type: ignore[assignment]
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]


def wire(*, load_agent_log_fn, load_projects_fn, load_project_fn,
         store_path=None):
    global _load_agent_log, load_projects, load_project
    _load_agent_log = load_agent_log_fn
    load_projects = load_projects_fn
    load_project = load_project_fn
    if store_path is not None:
        _sugg.STORE_PATH = store_path


def _scheduled_tasks_for(project_id: str) -> list:
    """Task text of every schedule already pointing at this project — enabled
    or not. A disabled row still means the human has seen the idea and made a
    call about it, so re-proposing it would be nagging."""
    try:
        return [s.get('task') or '' for s in _sched._load_schedules()
                if s.get('project_id') == project_id]
    except Exception as e:
        _log(f'[automation] could not read schedules for {project_id}: {e}')
        return []


def _refresh(project_id: str) -> None:
    """Mine one project's agent log into the store. Best-effort: a project with
    an unreadable log contributes nothing rather than failing the request."""
    try:
        entries = _load_agent_log(project_id) or []
    except Exception as e:
        _log(f'[automation] could not read agent log for {project_id}: {e}')
        return
    scheduled = _scheduled_tasks_for(project_id)
    live = _sugg.mine_recurring_manual_tasks(project_id, entries, scheduled)
    # Prune first: a pattern the human has since scheduled by hand, or that has
    # aged out of the log, should stop being offered. Decisions are untouched.
    _sugg.prune_project(project_id, [c['id'] for c in live])
    _sugg.refresh_project(project_id, entries, scheduled)


@bp.route('/api/automation/suggestions')
def list_automation_suggestions():
    """Pending suggestions. Refreshes from the agent log first (cheap: one
    already-on-disk JSON per project) unless ?refresh=0."""
    project_id = (request.args.get('project_id') or '').strip()
    if request.args.get('refresh', '1') != '0':
        if project_id:
            _refresh(project_id)
        else:
            try:
                pids = [p.get('id') for p in (load_projects() or []) if p.get('id')]
            except Exception as e:
                _log(f'[automation] project list unavailable: {e}')
                pids = []
            for pid in pids:
                _refresh(pid)

    rows = _sugg.list_pending(project_id or None)
    # Decorate with the project's display name so the card can say where a
    # suggestion came from without a second round trip.
    names: dict[str, str] = {}
    try:
        for p in (load_projects() or []):
            if p.get('id'):
                names[p['id']] = p.get('name') or p['id']
    except Exception:
        pass
    for r in rows:
        r['project_name'] = names.get(r.get('project_id', ''), r.get('project_id', ''))
    return jsonify(rows)


@bp.route('/api/automation/suggestions/<suggestion_id>/accept', methods=['POST'])
def accept_automation_suggestion(suggestion_id):
    """THE consent point. A human clicked Accept; create the real schedule.

    The stored spec is used as-is. The request body may override the cadence
    fields a human is entitled to change before saying yes (time, type, days,
    interval, cron) — but NOT project_id or task, because those are what the
    fingerprint and therefore the latch are computed over. Letting them move
    here would let an accepted row create a job for a different project than
    the one whose evidence justified it.
    """
    sugg = _sugg.get_pending(suggestion_id)
    if not sugg:
        decided = _sugg.get_decision(suggestion_id)
        if decided:
            return jsonify({'error': 'already decided',
                            'decision': decided.get('decision')}), 409
        return jsonify({'error': 'not found'}), 404

    spec = dict(sugg.get('spec') or {})
    body = request.get_json(silent=True) or {}
    for k in ('schedule_type', 'time', 'days', 'interval_minutes', 'run_at',
              'cron_expr', 'character', 'continue_session', 'description'):
        if k in body:
            spec[k] = body[k]

    sched, err = _sched.create_schedule_from_spec(spec)
    if err:
        # Leave the suggestion PENDING and record nothing. A failed create must
        # not consume the human's consent — they would have no way to retry.
        return err

    _sugg.record_decision(suggestion_id, 'accepted', source='ui_accept',
                          schedule_id=(sched or {}).get('id', ''),
                          project_id=sugg.get('project_id', ''))
    _log(f"[automation] suggestion {suggestion_id} accepted -> schedule "
         f"{(sched or {}).get('id', '')} ({sugg.get('project_id')})")
    return jsonify({'ok': True, 'schedule': sched}), 201


@bp.route('/api/automation/suggestions/<suggestion_id>/dismiss', methods=['POST'])
def dismiss_automation_suggestion(suggestion_id):
    """THE latch. Durable, permanent, and consulted at generation time, so this
    pattern is never mined into the store again — including after a restart."""
    sugg = _sugg.get_pending(suggestion_id)
    if not sugg:
        decided = _sugg.get_decision(suggestion_id)
        if decided:
            return jsonify({'ok': True, 'already': decided.get('decision')})
        return jsonify({'error': 'not found'}), 404
    _sugg.record_decision(suggestion_id, 'dismissed', source='ui_dismiss',
                          project_id=sugg.get('project_id', ''),
                          title=sugg.get('title', ''))
    _log(f"[automation] suggestion {suggestion_id} dismissed (latched) "
         f"({sugg.get('project_id')})")
    return jsonify({'ok': True})


__all__ = ['bp', 'wire']
