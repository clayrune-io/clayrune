"""Human write boundary and persistence adapters for steward assignments.

Route hooks live here rather than growing the scheduler/project monoliths.
SR-1 deliberately blocks role execution until SR-2/3 supply the guarded runner.
"""
from __future__ import annotations

from functools import wraps
import json
from threading import RLock

from flask import jsonify, request

from mc import caller_attribution as ca, characters, state
from mc import steward_role_model as model
from mc.atomic_json import read_text_with_retry
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log, now_iso
from mc.project_safety_settings import is_agent_caller, refuse_agent_safety_write

# Only role-owned persistence holds WRITE_LOCK -> project lock across writes.
# Validation adapters release both before calling a legacy handler: handlers
# may take a session manager lock before save_project takes the project lock.
WRITE_LOCK = RLock()
EXECUTION_ERROR = 'steward role execution unavailable: SR-2/SR-3 guarded runner required'


def serialized_write(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapped


def require_human(data):
    if is_agent_caller():
        return jsonify({'error': 'Steward role writes are human-only. Use the Clayrune UI.'}), 403
    return _require_human_passcode(data)


def _error(exc, code=400):
    return jsonify({'error': str(exc)}), code


def _schedules():
    # Unlike the legacy scheduler reader, a damaged role store must fail closed.
    from mc.blueprints import scheduler_routes as sr
    if sr.SCHEDULES_PATH is None or not sr.SCHEDULES_PATH.exists():
        return []
    try:
        rows = json.loads(read_text_with_retry(sr.SCHEDULES_PATH))
    except (ValueError, UnicodeError) as exc:
        raise OSError('steward role schedules store is corrupt') from exc
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise OSError('invalid schedules store')
    return rows


@serialized_write
def save_runtime_schedules(rows):
    """An old timer/Run Now snapshot must never undo a human binding save."""
    from mc.blueprints import scheduler_routes as sr
    current = _schedules()
    protected = {r.get('id') for r in [*rows, *current] if model.is_role(r)}
    sr._save_schedules([r for r in rows if r.get('id') not in protected]
                       + [r for r in current if r.get('id') in protected])


def _own_suspension(row, data):
    if set(data) != {'enabled'} or data.get('enabled') is not False:
        return False
    env = request.environ
    caller = ca.attribute_caller(request.remote_addr or '', env.get('REMOTE_PORT'),
                                 env.get('SERVER_PORT'),
                                 ca.managed_roots(state.agent_sessions, state.tracked_processes))
    if caller.status != ca.ATTRIBUTED:
        return False
    session = state.agent_sessions.get(caller.session_id, {})
    return (session.get('project_id') == row.get('project_id')
            and session.get('trigger_type') == 'schedule'
            and session.get('trigger_id') == row.get('id'))


def _persist(row, rows, old=None):
    from mc.blueprints import project_routes as pr, scheduler_routes as sr
    pid = row.get('project_id')
    if old and old.get('project_id') != pid:
        raise model.RoleError('detach the role before moving its schedule to another project')
    with pr._project_write_lock(pid):
        project = sr.load_project(pid)
        if project is None:
            raise model.RoleError('role project not found')
        row = model.validate_schedule(row, project, rows, characters.read_character)
        project = dict(project)
        pointer = project.get('steward_assignment')
        if row['enabled'] or not pointer or pointer == {'schedule_id': row['id']}:
            project['steward_assignment'] = {'schedule_id': row['id']}
        # Save the binding first. A failed pointer save leaves a non-runnable
        # orphan, never a pointer granting authority to an absent schedule.
        rows = [r for r in rows if r.get('id') != row['id']] + [row]
        sr._save_schedules(rows)
        sr.save_project(pid, project)
        return row


def create(data, build):
    refusal = require_human(data)
    if refusal is not None:
        return None, refusal
    try:
        row, err = build(data)
        if err:
            return None, err
        if row is None:
            raise model.RoleError('schedule validation returned no row')
        if data.get('steward'):
            raise model.RoleError('legacy steward and steward_role cannot share a schedule')
        return _persist(row, _schedules()), None
    except (model.RoleError, TypeError, ValueError) as exc:
        return None, _error(exc)
    except OSError as exc:
        _log(f'[steward-role] create failed: {exc}', flush=True)
        return None, _error('steward role persistence failed', 500)


def append_ordinary(row):
    """Ordinary creates must not reset a corrupt store containing role rows."""
    from mc.blueprints import scheduler_routes as sr
    try:
        rows = _schedules()
        rows.append(row)
        sr._save_schedules(rows)
        return row, None
    except OSError as exc:
        _log(f'[steward-role] schedule create persistence failed: {exc}', flush=True)
        return None, _error('schedules store unavailable', 500)


def schedule_mutation(kind):
    """Intercept only role rows; ordinary update/delete keep their old handler."""
    def decorate(fn):
        @wraps(fn)
        @serialized_write
        def wrapped(schedule_id, *args, **kwargs):
            from mc.blueprints import scheduler_routes as sr, project_routes as pr
            data = request.get_json(silent=True) or {}
            try:
                rows = _schedules()
                old = next((s for s in rows if s.get('id') == schedule_id), None)
                if not (old and model.is_role(old)) and 'steward_role' not in data:
                    return fn(schedule_id, *args, **kwargs)
                if not old:
                    return _error('schedule not found', 404)
                own_suspend = kind == 'update' and model.is_role(old) and _own_suspension(old, data)
                if not own_suspend:
                    refusal = require_human(data)
                    if refusal is not None:
                        return refusal
                suspend = (kind == 'update' and model.is_role(old)
                           and set(data) - {'passcode'} == {'enabled'} and data.get('enabled') is False)
                if suspend:
                    # Even a broken/deleted charter must not prevent stand-down.
                    row = dict(old, enabled=False, next_run=None)
                    sr._save_schedules([row if r.get('id') == schedule_id else r for r in rows])
                    return jsonify(row)
                if kind == 'delete' or data.get('steward_role', old.get('steward_role')) is None:
                    # Explicit human detachment; null is not an agent bypass.
                    with pr._project_write_lock(old['project_id']):
                        p = sr.load_project(old['project_id'])
                        if kind == 'delete':
                            replacement = None
                        else:
                            merged = dict(old, **data)
                            merged.pop('steward_role', None)
                            replacement, err = sr._build_schedule_from_spec(merged)
                            if err:
                                return err
                            if replacement is None:
                                raise model.RoleError('schedule validation returned no row')
                            replacement.update({k: v for k, v in old.items()
                                                if k in ('id', 'created_at', 'last_run')})
                            replacement['enabled'] = merged.get('enabled', True)
                        kept = [r for r in rows if r.get('id') != schedule_id]
                        if replacement:
                            kept.append(replacement)
                        sr._save_schedules(kept)
                        if p and p.get('steward_assignment') == {'schedule_id': schedule_id}:
                            p.pop('steward_assignment')
                            sr.save_project(old['project_id'], p)
                        return jsonify(replacement if replacement else {'ok': True})
                merged = dict(old, **{k: v for k, v in data.items() if k != 'passcode'})
                if merged.get('steward'):
                    raise model.RoleError('legacy steward and steward_role cannot share a schedule')
                # Identity is not editable through a generic PUT body.
                merged['id'] = old['id']
                row, err = sr._build_schedule_from_spec(merged)
                if err:
                    return err
                if row is None:
                    raise model.RoleError('schedule validation returned no row')
                row.update({k: v for k, v in old.items()
                            if k in ('id', 'created_at', 'last_run')})
                return jsonify(_persist(row, rows, old))
            except (model.RoleError, TypeError, ValueError) as exc:
                return _error(exc)
            except OSError as exc:
                _log(f'[steward-role] {kind} failed: {exc}', flush=True)
                return _error('steward role persistence failed', 500)
        return wrapped
    return decorate


def project_write(fn):
    """Cover whole-project/backlog replacement as well as the narrow item API."""
    @wraps(fn)
    def wrapped(project_id, *args, **kwargs):
        from mc.blueprints import project_routes as pr
        with WRITE_LOCK:
            data = request.get_json(silent=True) or {}
            refusal = refuse_agent_safety_write(data)
            if refusal is not None:
                return refusal
            with pr._project_write_lock(project_id):
                p = pr.load_project(project_id)
            try:
                rows = _schedules()
                bound = [s for s in rows if s.get('project_id') == project_id and model.is_role(s)]
                protected = {'steward_assignment'} & data.keys()
                if bound:
                    protected |= {'steward_mode', 'steward_objective', 'steward_cadence_minutes',
                                  'project_path', 'id', 'roster', 'backlog'} & data.keys()
                if protected:
                    refusal = require_human(data)
                    if refusal is not None:
                        return refusal
                    merged = dict(p or {'id': project_id}, **data)
                    if bound and merged.get('steward_assignment') is None:
                        raise model.RoleError('detach the assignment through its schedule first')
                    model.assignment(merged, rows)
                    for row in bound:
                        model.validate_schedule(row, merged, rows, characters.read_character)
                    if bound and 'steward_objective' in data:
                        raise model.RoleError('edit the assigned backlog charter, not steward_objective')
                    if bound and 'steward_cadence_minutes' in data:
                        raise model.RoleError('edit cadence on the assigned schedule')
            except (model.RoleError, TypeError, ValueError) as exc:
                return _error(exc)
            except OSError as exc:
                _log(f'[steward-role] project write validation failed: {exc}', flush=True)
                return _error('steward role store unavailable', 500)
        return fn(project_id, *args, **kwargs)
    return wrapped


def legacy_enable_error(project_id):
    """Also covers the standalone/internal bootstrap entry point."""
    try:
        from mc.blueprints import steward_routes as st
        project = st.load_project(project_id) or {}
        if (project.get('steward_assignment') is not None
                or any(r.get('project_id') == project_id and model.is_role(r) for r in _schedules())):
            return {'error': 'project has a steward role; use its assigned schedule'}, 409
    except (OSError, ValueError) as exc:
        _log(f'[steward-role] legacy enable validation failed: {exc}', flush=True)
        return {'error': 'steward role store unavailable'}, 500
    return None


def status(project, rows):
    """Read-only compatibility fields for the existing steward surface."""
    bound = [r for r in rows if r.get('project_id') == project.get('id') and model.is_role(r)]
    if not bound and project.get('steward_assignment') is None:
        return None
    try:
        row = model.assignment(project, rows)
        if not row:
            raise model.RoleError('missing steward_assignment pointer')
        row = model.validate_schedule(row, project, rows, characters.read_character)
        return {'enabled': project.get('steward_mode') == 'on' and row['enabled'],
                'objective': model.objective(project, rows),
                'cadence_minutes': row.get('interval_minutes'),
                'charter_item_id': row['steward_role']['charter_item_id'],
                'schedule_id': row['id'], 'next_run': row.get('next_run'),
                'schedule': row, 'execution_error': EXECUTION_ERROR}
    except (model.RoleError, TypeError, ValueError) as exc:
        return {'enabled': False, 'role_error': str(exc), 'execution_error': EXECUTION_ERROR}


def charter_write(fn):
    @wraps(fn)
    def wrapped(project_id, item_id, *args, **kwargs):
        with WRITE_LOCK:
            try:
                protected = model.protected_charters(project_id, _schedules())
                data = request.get_json(silent=True) or {}
                if item_id in protected and (request.method == 'DELETE' or {'text', 'status'} & data.keys()):
                    refusal = require_human(data)
                    if refusal is not None:
                        return refusal
                    if 'text' in data and (not isinstance(data['text'], str) or not data['text'].strip()):
                        raise model.RoleError('charter text must not be empty')
                    if 'status' in data and not isinstance(data['status'], str):
                        raise model.RoleError('charter status must be a string')
                # Charter reopening would restore a stopped mandate. Keep its
                # lifecycle human-only too; ordinary item state remains writable.
            except (model.RoleError, TypeError, ValueError) as exc:
                return _error(exc)
            except OSError as exc:
                _log(f'[steward-role] charter write validation failed: {exc}', flush=True)
                return _error('steward role store unavailable', 500)
        return fn(project_id, item_id, *args, **kwargs)
    return wrapped


def project_delete(fn):
    @wraps(fn)
    def wrapped(project_id, *args, **kwargs):
        from mc.blueprints import project_routes as pr
        with WRITE_LOCK:
            try:
                p = pr.load_project(project_id) or {}
                if (p.get('steward_assignment') is not None
                        or any(r.get('project_id') == project_id and model.is_role(r) for r in _schedules())):
                    refusal = require_human(request.get_json(silent=True) or {})
                    if refusal is not None:
                        return refusal
            except OSError as exc:
                _log(f'[steward-role] project delete validation failed: {exc}', flush=True)
                return _error('steward role store unavailable', 500)
        return fn(project_id, *args, **kwargs)
    return wrapped


def steward_control(fn):
    @wraps(fn)
    def wrapped(project_id, *args, **kwargs):
        from mc.blueprints import steward_routes as st
        from mc.blueprints import project_routes as pr
        with WRITE_LOCK, pr._project_write_lock(project_id):
            try:
                rows = _schedules()
                bound = [s for s in rows if s.get('project_id') == project_id and model.is_role(s)]
                p = st.load_project(project_id)
                if bound or (p or {}).get('steward_assignment'):
                    return _control_role(project_id, p, rows)
            except (model.RoleError, TypeError, ValueError) as exc:
                return _error(exc)
            except OSError as exc:
                _log(f'[steward-role] steward control failed: {exc}', flush=True)
                return _error('steward role store unavailable', 500)
        return fn(project_id, *args, **kwargs)
    return wrapped


def _control_role(project_id, p, rows):
    """Mutate a role under the caller-held WRITE_LOCK and project lock."""
    from mc.blueprints import steward_routes as st
    data = request.get_json(silent=True) or {}
    refusal = require_human(data)
    if refusal is not None:
        return refusal
    if not p:
        return _error('project not found', 404)
    enable = request.path.endswith('/enable')
    # Even an orphan pointer must not prevent a human kill switch.
    row = model.assignment(p, rows) if enable else None
    if enable:
        if not row:
            raise model.RoleError('select a steward_assignment schedule first')
        if set(data) - {'passcode'}:
            raise model.RoleError('edit role owner, charter and cadence on the schedule')
        row = _persist(dict(row, enabled=True), rows, row)
        p = st.load_project(project_id)
        if p is None:
            raise model.RoleError('role project disappeared during enable')
    p['steward_mode'] = 'on' if enable else 'off'
    p['last_updated'] = now_iso()
    st.save_project(project_id, p)
    # A role kill switch never removes shared guards or assignments.
    return jsonify({'ok': True, 'enabled': enable,
                    'schedule_id': (row or {}).get('id'),
                    'execution_error': EXECUTION_ERROR})
