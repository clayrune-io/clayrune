"""MC-1163 scheduler/follow-up and cancel integration."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from mc import limit_resume as policy
from mc.limit_resume_completion import finalize
from mc.core import _log

bp = Blueprint('limit_resume', __name__)


def confirm_submission(session: dict, attempt: int | None) -> None:
    """Acknowledge and release only this attempt's deferred success boundary."""
    from mc.blueprints import agent_routes as ar
    with ar.get_manager(session['project_id']).lock:
        if (attempt is None or session.get('_limit_resume_attempt') != attempt
                or not policy.unattended(session) or session.get('status') == 'stopped'):
            return
        store = policy.store()
        row = store.get(session['session_id']) if store else None
        if row and row.get('deferred_success') and not finalize(ar, session, attempt):
            return
        if policy.confirm_submission(session['session_id'], attempt):
            session.pop('_limit_resume_completion', None)


def restore_waits(ar, *, include_completed: bool = True) -> None:
    """Rehydrate paused sessions without starting provider processes."""
    store = policy.store()
    if not store:
        return
    rows = store.records()
    for row in rows:
        if row['phase'] not in ('waiting', 'submitting') and not (
                include_completed and row['phase'] == 'active' and row.get('completion_done')):
            continue
        sid, pid = row['session_id'], row['project_id']
        with ar.get_manager(pid).lock:
            existing = ar.agent_sessions.get(sid)
            # Re-read after taking the same lock as cancel/takeover/ack.
            row = store.get(sid)
            if not row or row['phase'] == 'cancelled':
                continue
            deferred = row.get('deferred_success') or {}
            recover_success = (not row.get('review_required')
                               and deferred.get('attempt') == row['attempts'])
            if existing:
                if (existing.get('_limit_resume_recovering') and recover_success
                        and existing.get('status') == 'waiting_for_reset'
                        and row['phase'] == 'submitting'):
                    if finalize(ar, existing, row['attempts'],
                                restoring=not existing.get('process_alive')):
                        policy.confirm_submission(sid, row['attempts'])
                continue
            if not ar.load_project(pid):
                continue  # a deleted project is never recreated
            session = dict(row['snapshot'])
            session.update(status='waiting_for_reset', proc=None, process_alive=False,
                           stdin_lock=threading.Lock(), pending_followups=[],
                           last_output_time=time.time(), last_status_change_time=time.time())
            session['log_lines'] = list(session.get('log_lines', []))
            if row['phase'] == 'submitting' and not recover_success:
                session['log_lines'].append(
                    '[Reset continuation submission was interrupted; continue manually to avoid a duplicate.]')
            ar.agent_sessions[sid] = session
            ar.get_manager(pid).session_ids.add(sid)
            if row.get('completion_done') and recover_success:
                session['status'] = 'completed'
                if row['phase'] == 'submitting':
                    policy.confirm_submission(sid, row['attempts'])
            elif recover_success:
                session['_limit_resume_attempt'] = row['attempts']
                session['_limit_resume_recovering'] = True
                if finalize(ar, session, row['attempts'], restoring=True):
                    policy.confirm_submission(sid, row['attempts'])


def tick(now: datetime | None = None) -> None:
    from mc.blueprints import agent_routes as ar
    store = policy.store()
    if not store:
        return
    restore_waits(ar, include_completed=False)
    if (not ar.state.CONFIG.get('limit_resume_enabled', True)
            or ar.state.CONFIG.get('scheduler_paused')):
        return
    now = now or datetime.now(timezone.utc)
    candidates = [row['session_id'] for row in store.records()]
    for sid in candidates:
        row = store.get(sid)
        if not row or row['phase'] != 'waiting':
            continue
        pid = row['project_id']
        # Same project lock as human sends/cancel: no claim can race a new turn.
        with ar.get_manager(pid).lock:
            session = ar.agent_sessions.get(sid)
            if not session:
                continue
            if not policy.unattended(session) or session.get('status') == 'stopped':
                store.cancel(sid, pid)
                continue
            if session.get('status') != 'waiting_for_reset':
                continue
            # Never submit into an unfinished turn (a late result may yet arrive).
            proc = session.get('proc')
            if proc and proc.poll() is None and session.get('mode') != 'B':
                continue
            claimed = store.claim(sid, now)
            if not claimed:
                continue
            session['_limit_resume_attempt'] = claimed['attempts']
            session.pop('_limit_resume_exhaustion', None)
            app = ar._delegation_app
            if app is None:
                _log(f'[limit-resume] no follow-up app for {sid}; manual continuation required')
                policy.submission_failed(session, claimed['attempts'])
                continue
            try:
                with app.test_request_context(json={
                        'session_id': sid, 'message': policy.CONTINUATION,
                        '_durable_delivery_ack': True},
                        environ_overrides={'clayrune.limit_resume': True}):
                    response = app.make_response(ar.agent_followup(pid))
                    body = response.get_json(silent=True) or {}
                if (response.status_code >= 400 or body.get('session_id') != sid
                        or body.get('queued') or body.get('ok') is False
                        or session.get('status') == 'error'
                        or body.get('stdin_write_ack') in ('unknown', 'failed')):
                    raise RuntimeError(f'follow-up rejected or uncertain (HTTP {response.status_code})')
                # Claude respawns return before their worker even calls Popen.
                # The worker confirms launch/delivery; a crash first must leave
                # a recoverable review hold, never an invisible active record.
                if not body.get('submission_pending'):
                    confirm_submission(session, claimed['attempts'])
                ar.get_manager(pid).ensure_guardian()
            except Exception as exc:
                # Keep the durable submitting claim. Retrying an unknown write
                # after a crash could spend twice on the same task.
                _log(f'[limit-resume] {sid} submission needs manual review: {exc}', flush=True)
                policy.submission_failed(session, claimed['attempts'])


@bp.route('/api/project/<project_id>/agent/<session_id>/limit-resume/cancel', methods=['POST'])
def cancel(project_id: str, session_id: str):
    from mc.blueprints import agent_routes as ar
    store = policy.store()
    if not store:
        return jsonify({'error': 'reset-resume state unavailable'}), 503
    with ar.get_manager(project_id).lock:
        session = ar.agent_sessions.get(session_id)
        if session and session.get('status') == 'running':
            return jsonify({'error': 'continuation already running; use Stop to end it'}), 409
        if not store.cancel(session_id, project_id):
            return jsonify({'error': 'pending reset wait not found'}), 404
        if session and session.get('project_id') == project_id:
            session['_limit_resume_cancelled'] = True
            session['status'] = 'stopped'
            session['last_status_change_time'] = time.time()
            session['log_lines'].append('[Automatic reset continuation cancelled.]')
            ar._log_agent_completion(session)
    return jsonify({'ok': True, 'session_id': session_id})
