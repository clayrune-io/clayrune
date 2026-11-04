"""Native proposals and approved observation jobs; activation stays human-only."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.dev_harness_catalogue import load_catalogue
from mc.dev_harness_native_plan import inspect_plan, strict_loads
from mc.dev_harness_store import RevisionConflict, StoreError
from mc.landing_serialization import LandingBusy

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


def _run_request(project_id: str, kind: str):
    from mc.blueprints.dev_harness_routes import _error, _project
    from mc.dev_harness_dispatch import dispatch
    from mc.dev_harness_policy import require_digest
    try:
        data = strict_loads(request.get_data(cache=True))
        fields = {'session_id', 'expected_revision', 'native_plan_digest'} | (
            {'purpose'} if kind == 'preview' else {'target', 'incoming'})
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError('invalid_run_request')
        if (not isinstance(data['session_id'], str) or not data['session_id'] or len(data['session_id']) > 128
                or type(data['expected_revision']) is not int or data['expected_revision'] < 0):
            raise ValueError('invalid_run_request')
        if data['native_plan_digest'] is not None:
            require_digest(data['native_plan_digest'])
        if kind == 'preview':
            if data['purpose'] not in ('observation', 'activation_measurement'):
                raise ValueError('invalid_run_request')
        elif any(not isinstance(data[k], str) or not data[k] or len(data[k]) > 256
                 or data[k].startswith('-') or '\0' in data[k] for k in ('target', 'incoming')):
            raise ValueError('invalid_run_request')
        project, store = _project(project_id)
        return dispatch(store, project, data, kind=kind)
    except LandingBusy:
        return jsonify({'status': 'error', 'error': 'landing_busy'}), 409
    except (StoreError, OSError, LookupError, RevisionConflict) as exc:
        return _error(exc)
    except (ValueError, TypeError, KeyError):
        return jsonify({'status': 'error', 'error': 'invalid_run_request'}), 400


@bp.post('/api/project/<project_id>/dev-harness/preview')
def preview_run(project_id: str):
    return _run_request(project_id, 'preview')


@bp.post('/api/project/<project_id>/dev-harness/run')
def candidate_run(project_id: str):
    return _run_request(project_id, 'run')


@bp.get('/api/project/<project_id>/dev-harness/runs/<run_id>')
def run_read(project_id: str, run_id: str):
    from mc.blueprints.dev_harness_routes import _error, _project
    from mc.dev_harness_integration import read_run
    try:
        project, store = _project(project_id)
        return jsonify(read_run(store, project, run_id))
    except (StoreError, OSError, LookupError) as exc:
        return _error(exc)
    except (ValueError, KeyError, TypeError):
        return jsonify({'status': 'error', 'error': 'integration_record_unavailable'}), 500
