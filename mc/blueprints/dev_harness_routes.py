"""DH-M1: catalogue, effective state/draft and human-only activation contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc.atomic_json import read_text_with_retry
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.dev_harness_catalogue import load_catalogue
from mc.dev_harness_policy import resolve_policy
from mc.dev_harness_store import HarnessStore, RevisionConflict, StoreError
from mc.project_safety_settings import is_agent_caller
from mc import dev_harness_sources

bp = Blueprint('dev_harness_routes', __name__)
_store: HarnessStore | None = None
_load_project: Callable[[str], Any] = lambda _: None


def wire(*, data_root: Path, load_project_fn: Callable[[str], Any]) -> None:
    global _store, _load_project
    _store = HarnessStore(data_root)
    _load_project = load_project_fn


def _project(project_id: str) -> tuple[dict[str, Any], HarnessStore]:
    if _store is None:
        raise StoreError('Harness store not wired')
    _store.project_dir(project_id)
    project = _load_project(project_id)
    if not project:
        raise LookupError('Project not found')
    return project, _store


def _error(exc: Exception):
    if isinstance(exc, LookupError):
        return jsonify({'error': 'Project not found'}), 404
    if isinstance(exc, RevisionConflict):
        return jsonify({'error': str(exc)}), 409
    if isinstance(exc, (StoreError, OSError)):
        _log(f'[dev-harness] record operation failed: {exc}')
        return jsonify({'status': 'error', 'error': 'Harness state unavailable or corrupt'}), 500
    # Do not echo JSON content, passcodes or native rule values in parse errors.
    return jsonify({'status': 'error', 'error': str(exc)}), 400


@bp.get('/api/project/<project_id>/dev-harness/catalogue')
def catalogue(project_id: str):
    try:
        _project(project_id)
        return jsonify(load_catalogue())
    except (ValueError, OSError, LookupError, StoreError) as exc:
        return _error(exc)


@bp.post('/api/project/<project_id>/dev-harness/install-plan')
def install_plan(project_id: str):
    try:
        project, store = _project(project_id)
        return dev_harness_sources.install_plan_request(project_id, store, project)
    except (ValueError, OSError, LookupError, StoreError) as exc:
        return _error(exc)


@bp.post('/api/project/<project_id>/dev-harness/install')
def install(project_id: str):
    # Refuse before even resolving a project/reading an approved plan.
    if is_agent_caller():
        return jsonify({'error': 'Guidance installation is human-only'}), 403
    try:
        project, store = _project(project_id)
        return dev_harness_sources.install_request(project_id, store, project)
    except (ValueError, OSError, LookupError, StoreError) as exc:
        return _error(exc)


@bp.get('/api/project/<project_id>/dev-harness')
def read(project_id: str):
    try:
        project, store = _project(project_id)
        active = store.read(project_id)
        catalogue_data = load_catalogue()
        # Repository drafts are editable proposals; they never select the mode
        # or replace an active policy. Bad drafts are visible even with an active
        # snapshot; the trusted snapshot remains effective.
        project_path = project.get('project_path')
        draft_path = (Path(project_path) / '.clayrune' / 'dev-harness' / 'policy.json'
                      if project_path else None)
        draft = None
        draft_error = None
        if draft_path is not None and draft_path.exists():
            try:
                draft = resolve_policy(json.loads(read_text_with_retry(draft_path)), catalogue_data)
            except (ValueError, OSError, UnicodeError):
                draft_error = 'Repository draft is unreadable or invalid'
        return jsonify({'project_id': project_id, 'configured': active is not None or draft is not None,
                        'mode': active['mode'] if active else 'preview',
                        'revision': active['revision'] if active else 0,
                        'status': 'error' if draft_error else 'active' if active else 'draft' if draft else 'not_configured',
                        'active': active, 'draft': draft, 'draft_error': draft_error,
                        'languages': catalogue_data['languages'],
                        'merge_enforcement_available': False})
    except (ValueError, OSError, LookupError, StoreError) as exc:
        return _error(exc)


@bp.post('/api/project/<project_id>/dev-harness/activate')
def activate(project_id: str):
    # Refuse before parsing policy or touching project/runtime files.
    if is_agent_caller():
        return jsonify({'error': 'Harness activation and mode changes are human-only'}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        if set(data) - {'passcode', 'expected_revision', 'draft', 'mode', 'evidence_id', 'acknowledged_losses'}:
            raise ValueError('Unknown activation fields')
        revision, draft, mode = data.get('expected_revision'), data.get('draft'), data.get('mode')
        if type(revision) is not int or not isinstance(draft, dict) or not isinstance(mode, str):
            raise ValueError('Expected active revision, draft object and explicit mode')
        project, store = _project(project_id)
        catalogue_data = load_catalogue()
        if mode == 'enforce':
            if not isinstance(data.get('evidence_id'), str):
                raise ValueError('Stored measured Preview evidence is required')
            current = store.read(project_id)
            if revision != (current['revision'] if current else 0):
                raise RevisionConflict('Active revision changed; reload before activating')
            dev_harness_sources.validate_activation(store, project,
                resolve_policy(draft, catalogue_data), catalogue_data)
        result = store.activate(project_id, expected_revision=revision,
                                draft=draft, mode=mode,
                                evidence_id=data.get('evidence_id'),
                                acknowledged_losses=data.get('acknowledged_losses'),
                                catalogue=catalogue_data)
        return jsonify(result)
    except (ValueError, OSError, LookupError, StoreError) as exc:
        return _error(exc)
