"""Read-only support routes for the steward role editor (BUILTINS-3 SR-5).

Nothing here writes. Saving, activating, suspending and detaching a role use
the existing human-only schedule and steward endpoints (SR-1); this module only
answers three questions the editor needs a server for:

* which role assignments exist, and are they running or blocked;
* what could be chosen for a project (charter items, a journal name);
* what a draft assignment would validate to, without saving it.

Preview performs no model call, no activation, no fence mutation, no mail send
and creates no journal file or runtime sidecar.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

from flask import Blueprint, jsonify, request

from mc import characters, state
from mc import steward_role_model as model
from mc import steward_role_task as composer
from mc import steward_role_writes as writes
from mc.core import _log

bp = Blueprint('steward_role_routes', __name__)

CHARTER_PREVIEW_CHARS = 400
CHARTER_FULL_CHARS = 4000


def _scheduler() -> Any:
    from mc.blueprints import scheduler_routes
    return scheduler_routes


def execution_blockers() -> list[dict]:
    """Every reason a role cannot fire on this install, each stated separately.

    These are checkout observations, recomputed per request. Per-engine guard
    readiness is NOT asserted here: it is demonstrated at every fire.
    """
    out: list[dict] = []
    try:
        from steward import core
        if 'cycle_snapshot' not in inspect.signature(core.build_cycle_task).parameters:
            out.append({'code': 'core_adapter_unapplied',
                        'message': 'The SR-2 core adapter patch is not applied, so a role '
                                   'cycle cannot be composed. A person applies it.'})
    except Exception as exc:  # signature probe must never take the editor down
        _log(f'[steward-role-ui] core adapter probe failed: {exc}', flush=True)
        out.append({'code': 'core_adapter_unknown',
                    'message': f'The cycle composer could not be inspected: {exc}'})
    try:
        composer.load_directive()
    except model.RoleError as exc:
        out.append({'code': 'directive_missing', 'message': f'{exc}. A person applies the SR-2 directive patch.'})
    if not callable(getattr(_scheduler(), 'steward_role_lifecycle', None)):
        out.append({'code': 'lifecycle_adapter_unavailable',
                    'message': 'The decision-channel readiness adapter is not installed, '
                               'so role delivery is held.'})
    if not state.CONFIG.get('fence_unattended_enabled', True):
        out.append({'code': 'unattended_guard_off',
                    'message': 'The unattended guard is switched off; roles refuse to run '
                               'until it is on.'})
    return out


def _charter_title(text: str) -> str:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), '')
    return first[:80]


def _slug(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', _charter_title(text).lower()).strip('-')
    return slug[:40].strip('-') or 'steward'


def suggested_journal(item: dict) -> str:
    return f"docs/_journal/{item['id']}-{_slug(item.get('text') or '')}.md"


def _assignment_summary(project: dict, row: dict) -> dict:
    """One role row as the cards need it. Errors are data, not exceptions."""
    raw = row.get('steward_role')
    role: dict = raw if isinstance(raw, dict) else {}
    mode_on = project.get('steward_mode') == 'on'
    out: dict[str, Any] = {
        'schedule_id': row.get('id'), 'project_id': project.get('id'),
        'project_name': project.get('name') or project.get('id'),
        'character': row.get('character') or '', 'enabled': bool(row.get('enabled')),
        'steward_mode': project.get('steward_mode') or 'off',
        'active': bool(row.get('enabled')) and mode_on,
        'pointed': (project.get('steward_assignment') or {}).get('schedule_id') == row.get('id'),
        'charter_item_id': role.get('charter_item_id'),
        'journal_path': role.get('journal_path'),
        'next_run': row.get('next_run'), 'last_error': row.get('last_error'),
        'interval_minutes': row.get('interval_minutes'),
        'charter_text': '', 'charter_status': '', 'role_error': '',
    }
    try:
        item = model.charter(project, role)
        out['charter_text'] = item['text'].strip()[:CHARTER_PREVIEW_CHARS]
        out['charter_status'] = item.get('status') or ''
    except (model.RoleError, TypeError, ValueError) as exc:
        out['role_error'] = str(exc)
    return out


@bp.route('/api/steward-role/assignments')
def list_assignments():
    sr = _scheduler()
    try:
        rows = [r for r in writes._schedules() if model.is_role(r)]
    except OSError as exc:
        _log(f'[steward-role-ui] schedules unreadable: {exc}', flush=True)
        return jsonify({'error': 'steward role store unavailable', 'assignments': [],
                        'blockers': execution_blockers()}), 500
    by_id = {p.get('id'): p for p in (sr.load_projects() or [])}
    out = []
    for row in rows:
        project = by_id.get(row.get('project_id'))
        if project is None:
            out.append({'schedule_id': row.get('id'), 'project_id': row.get('project_id'),
                        'project_name': row.get('project_id'), 'enabled': bool(row.get('enabled')),
                        'active': False, 'role_error': 'role project no longer exists'})
            continue
        out.append(_assignment_summary(project, row))
    return jsonify({'assignments': out, 'blockers': execution_blockers()})


@bp.route('/api/steward-role/options')
def options():
    pid = request.args.get('project_id', '')
    try:
        model.identifier(pid, 'project_id')
    except model.RoleError as exc:
        return jsonify({'error': str(exc)}), 400
    project = _scheduler().load_project(pid)
    if project is None:
        return jsonify({'error': 'project not found'}), 404
    try:
        protected = {r['steward_role']['charter_item_id']: r.get('id')
                     for r in writes._schedules()
                     if r.get('project_id') == pid and isinstance(r.get('steward_role'), dict)
                     and isinstance(r['steward_role'].get('charter_item_id'), str)}
    except OSError as exc:
        _log(f'[steward-role-ui] schedules unreadable: {exc}', flush=True)
        return jsonify({'error': 'steward role store unavailable'}), 500
    charters = []
    for item in project.get('backlog', []):
        text = item.get('text')
        if (not isinstance(text, str) or not text.strip() or item.get('status') in model.CLOSED
                or not isinstance(item.get('id'), str)):
            continue
        charters.append({'id': item['id'], 'title': _charter_title(text),
                         'status': item.get('status') or '',
                         'bound_schedule_id': protected.get(item['id']),
                         'suggested_journal': suggested_journal(item)})
    return jsonify({'project_id': pid, 'charters': charters,
                    'default_character': project.get('default_character') or '',
                    'steward_mode': project.get('steward_mode') or 'off',
                    'blockers': execution_blockers()})


def _check(name: str, fn) -> dict:
    try:
        detail = fn()
        return {'name': name, 'ok': True, 'detail': detail or ''}
    except (model.RoleError, TypeError, ValueError, OSError) as exc:
        return {'name': name, 'ok': False, 'detail': str(exc)}


@bp.route('/api/steward-role/preview', methods=['POST'])
def preview():
    """Validate a DRAFT assignment against the merged records. Writes nothing."""
    data = request.get_json(silent=True) or {}
    sr = _scheduler()
    try:
        pid = model.identifier(data.get('project_id'), 'project_id')
    except model.RoleError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    project = sr.load_project(pid)
    if project is None:
        return jsonify({'ok': False, 'error': 'project not found'}), 404
    role = data.get('steward_role')
    if not isinstance(role, dict):
        return jsonify({'ok': False, 'error': 'steward_role is required'}), 400
    try:
        rows = writes._schedules()
    except OSError as exc:
        _log(f'[steward-role-ui] preview schedules unreadable: {exc}', flush=True)
        return jsonify({'ok': False, 'error': 'steward role store unavailable'}), 500
    existing = None
    sid = data.get('schedule_id')
    if sid:
        existing = next((r for r in rows if r.get('id') == sid), None)
        if existing is None:
            return jsonify({'ok': False, 'error': 'schedule not found'}), 404
        if existing.get('project_id') != pid:
            return jsonify({'ok': False, 'error': 'schedule belongs to another project'}), 400
    candidate = dict(existing or {})
    candidate.update(id=sid or 'preview-draft', project_id=pid, steward_role=role,
                     character=data.get('character') or '', task='', workflow_id='',
                     enabled=bool(data.get('enabled', False)))
    candidate.setdefault('steward', False)
    checks: list[dict] = []
    charter_item: dict = {}

    def charter_check():
        item = model.charter(project, role)
        charter_item.update(item)
        return f"{item.get('status') or 'open'}: {_charter_title(item['text'])}"

    checks.append(_check('charter', charter_check))

    def journal_check():
        target = model.journal_path(project, role.get('journal_path'))
        return ('existing journal will be appended to' if target.exists()
                else 'journal file is created by the first cycle')

    checks.append(_check('journal', journal_check))
    owner = {'ref': ''}

    def owner_check():
        owner['ref'] = model.owner(project, candidate.get('character') or project.get('default_character'),
                                   characters.read_character)
        return owner['ref'] + (' (inherited from the project default and pinned on save)'
                               if not candidate.get('character') else '')

    checks.append(_check('owner', owner_check))
    # Authoritative merged validation last: catches schema, exclusivity and the
    # one-active-assignment rule that the per-field checks above cannot see.
    # Enabled is probed True so a conflict is reported before activation.
    probe = dict(candidate, enabled=True)
    full = _check('assignment', lambda: model.validate_schedule(
        probe, project, rows, characters.read_character) and 'valid')
    if full['ok'] or all(c['ok'] for c in checks):
        checks.append(full)  # a failure the per-field checks already explained is not repeated
    ok = all(c['ok'] for c in checks)
    blockers = execution_blockers()
    return jsonify({
        'ok': ok, 'checks': checks, 'blockers': blockers,
        'owner': owner['ref'], 'journal_path': role.get('journal_path'),
        'charter': {'id': charter_item.get('id'),
                    'text': (charter_item.get('text') or '').strip()[:CHARTER_FULL_CHARS],
                    'status': charter_item.get('status') or ''} if charter_item else None,
        'runnable_now': ok and not blockers,
        'note': 'Preview only: nothing was saved, started, fenced or sent. '
                'Saving and starting are separate steps that ask for your passcode.',
    })
