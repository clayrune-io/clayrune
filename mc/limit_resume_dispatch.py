"""MC-1163 scheduler/follow-up and cancel integration."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from mc import limit_resume as policy
from mc.core import _log

bp = Blueprint('limit_resume', __name__)


def restore_waits(ar) -> None:
    """Rehydrate paused sessions without starting provider processes."""
    store = policy.store()
    if not store:
        return
    rows = store.records()
    for row in rows:
        if row['phase'] not in ('waiting', 'submitting'):
            continue
        sid, pid = row['session_id'], row['project_id']
        with ar.get_manager(pid).lock:
            if sid in ar.agent_sessions:
                continue
            if not ar.load_project(pid):
                continue  # a deleted project is never recreated
            session = dict(row['snapshot'])
            session.update(status='waiting_for_reset', proc=None, process_alive=False,
                           stdin_lock=threading.Lock(), pending_followups=[],
                           last_output_time=time.time(), last_status_change_time=time.time())
            session['log_lines'] = list(session.get('log_lines', []))
            if row['phase'] == 'submitting':
                session['log_lines'].append(
                    '[Reset continuation submission was interrupted; continue manually to avoid a duplicate.]')
            ar.agent_sessions[sid] = session
            ar.get_manager(pid).session_ids.add(sid)


def tick(now: datetime | None = None) -> None:
    from mc.blueprints import agent_routes as ar
    store = policy.store()
    if not store:
        return
    restore_waits(ar)
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
                store.submitted(sid)
                ar.get_manager(pid).ensure_guardian()
            except Exception as exc:
                # Keep the durable submitting claim. Retrying an unknown write
                # after a crash could spend twice on the same task.
                _log(f'[limit-resume] {sid} submission needs manual review: {exc}', flush=True)
                if session.get('status') != 'running':
                    session['status'] = 'waiting_for_reset'


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
