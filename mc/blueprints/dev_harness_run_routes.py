"""Nonexecuting native-plan proposals. A2b owns future job-producing routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.dev_harness_catalogue import load_catalogue
from mc.dev_harness_native_plan import inspect_plan, strict_loads
from mc.dev_harness_store import RevisionConflict, StoreError

bp = Blueprint('dev_harness_run_routes', __name__)


@bp.post('/api/project/<project_id>/dev-harness/native-plan')
def native_plan(project_id: str):
    from mc.blueprints.dev_harness_routes import _error, _project
    try:
        data = strict_loads(request.get_data(cache=True))
        if not isinstance(data, dict) or set(data) != {'expected_revision', 'draft', 'steps', 'groups'}:
            raise ValueError('Expected revision, draft, steps and groups')
        project, store = _project(project_id)
        with store._lock:
            current = store._read_snapshot(project_id)
            if type(data['expected_revision']) is not int or data['expected_revision'] < 0:
                raise ValueError('Invalid expected revision')
            if data['expected_revision'] != (current['revision'] if current else 0):
                raise RevisionConflict('Active revision changed; reload before inspection')
            return jsonify(inspect_plan(store=store, project=project, draft=data['draft'],
                                        catalogue=load_catalogue(), steps=data['steps'], groups=data['groups']))
    except (ValueError, OSError, LookupError, StoreError, TypeError) as exc:
        # Never echo untrusted values, command arguments or parser fragments.
        if isinstance(exc, (StoreError, OSError, LookupError, RevisionConflict)):
            return _error(exc)
        code = 'coverage_required_unmeasurable' if str(exc) == 'coverage_required_unmeasurable' else 'invalid_native_plan'
        return jsonify({'status': 'error', 'error': code}), 400
