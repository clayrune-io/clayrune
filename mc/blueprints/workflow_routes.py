"""Workflow builder — routes. Spec: `docs/WORKFLOW_BUILDER_SPEC.md`. MC-871
Phase 1 (runner + stores; no UI, no scheduler wiring — those are later passes
in the spec's own build order).

  DEFINITION CRUD  GET/POST /api/workflows, PUT/DELETE /api/workflows/<id>
  RUNS             POST /api/workflows/<id>/run, GET /api/workflows/<id>/runs,
                    GET /api/workflow-runs/<run_id>
  DECISION         POST /api/workflow-runs/<run_id>/decision
  CANCEL           POST /api/workflow-runs/<run_id>/cancel

THE AUTHORITY GUARD, enforced HERE not in a docstring (settled, see
`position_whetheranagentsessionmaycreateoreditworkflowdefi` in the project
memory dir + spec "Scope"): definition CRUD and approval decisions refuse an
agent caller. A workflow definition dispatches tooled agents, with characters,
on a schedule — an agent authoring or approving one is self-expansion in a new
costume, exactly what CLAUDE.md's authority guard exists to pre-empt. See
`_refuse_if_agent_caller` for the structural signal and its limits.
"""
from __future__ import annotations

from typing import Callable, Optional

from flask import Blueprint, jsonify, request

from mc import workflows as _wf

bp = Blueprint('workflow_routes', __name__)

# -- wired by server.py (see wire()) ------------------------------------------
_load_agent_log: Optional[Callable[[str], list]] = None  # kept for parity/tests


def wire(*, workflows_path=None, workflow_runs_dir=None,
         dispatch_agent_internal_fn=None, load_agent_log_fn=None):
    global _load_agent_log
    _load_agent_log = load_agent_log_fn
    _wf.wire(workflows_path=workflows_path, workflow_runs_dir=workflow_runs_dir,
             dispatch_agent_internal_fn=dispatch_agent_internal_fn,
             load_agent_log_fn=load_agent_log_fn)


# Inbound shim (startup boot phase) — server.py calls this after wire(), the
# same pattern as _bp_hivemind._hm_reconcile_stale_on_startup.
adopt_on_startup = _wf.adopt_on_startup


def _is_agent_caller() -> bool:
    """Same structural signal `agent_dispatch` (agent_routes.py) uses to route
    a dispatch to the 'agent' side flow: a real browser fetch from the SPA
    always carries an Origin header; a bare HTTP call from an agent's Bash/
    curl tool does not. This app has no session/CSRF layer to check instead —
    the whole local API surface is localhost-trust (see MC-914's /api/config
    precedent) — so this is the only structural signal available.

    Unlike `agent_dispatch`'s `source`/`client` override fields (a ROUTING
    convenience, not a security gate — a caller may name itself 'ui' to land
    in the right chat flow), nothing in the request body can flip this check.
    A caller cannot self-report its way past it.
    """
    return not request.headers.get('Origin')


def _refuse_if_agent_caller():
    if _is_agent_caller():
        return jsonify({
            'error': ('workflow definitions and approval decisions are human-only '
                     '-- no capability-expanding artifact may be authored or '
                     'approved by an agent session (CLAUDE.md authority guard, '
                     'MC-871). Use the Clayrune UI.'),
        }), 403
    return None


# ── Definition CRUD (human-only) ─────────────────────────────────────────────

@bp.route('/api/workflows', methods=['GET'])
def list_workflows():
    return jsonify(_wf.list_workflows())


@bp.route('/api/workflows', methods=['POST'])
def create_workflow():
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    doc = request.get_json(silent=True) or {}
    try:
        record = _wf.create_workflow(doc)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'workflow': record})


@bp.route('/api/workflows/<workflow_id>', methods=['PUT'])
def update_workflow(workflow_id):
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    doc = request.get_json(silent=True) or {}
    try:
        record = _wf.update_workflow(workflow_id, doc)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except KeyError:
        return jsonify({'error': 'workflow not found'}), 404
    return jsonify({'ok': True, 'workflow': record})


@bp.route('/api/workflows/<workflow_id>', methods=['DELETE'])
def delete_workflow(workflow_id):
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    if not _wf.delete_workflow(workflow_id):
        return jsonify({'error': 'workflow not found'}), 404
    return jsonify({'ok': True})


# ── Runs ──────────────────────────────────────────────────────────────────────

@bp.route('/api/workflows/<workflow_id>/run', methods=['POST'])
def run_workflow(workflow_id):
    """Manual trigger + 'Run Now' (later phases). Not gated: running an
    already-authored, human-approved workflow does not expand any agent's
    capability set — the same posture the scheduler's existing run-now
    endpoint already takes."""
    try:
        run = _wf.start_run(workflow_id, trigger_type='manual')
    except KeyError:
        return jsonify({'error': 'workflow not found'}), 404
    except RuntimeError as e:
        return jsonify({'error': str(e), 'busy': True}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'run': run})


@bp.route('/api/workflows/<workflow_id>/runs', methods=['GET'])
def get_workflow_runs(workflow_id):
    try:
        limit = max(1, min(500, int(request.args.get('limit', 50))))
    except (TypeError, ValueError):
        limit = 50
    return jsonify(_wf.list_runs(workflow_id, limit=limit))


@bp.route('/api/workflow-runs/<run_id>', methods=['GET'])
def get_run(run_id):
    run = _wf.get_run(run_id)
    if run is None:
        return jsonify({'error': 'run not found'}), 404
    return jsonify(run)


@bp.route('/api/workflow-runs/<run_id>/decision', methods=['POST'])
def decide_run(run_id):
    """Resolve a parked approval gate. Human-only for the same reason
    definition CRUD is: satisfying a gate on an agent's own behalf is exactly
    the auto-answer the spec's Scope section forbids ('An approval gate
    cannot be removed, satisfied, or auto-answered by the runner or by any
    agent step')."""
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    data = request.get_json(silent=True) or {}
    choice = (data.get('choice') or '').strip()
    if not choice:
        return jsonify({'error': 'choice required'}), 400
    try:
        run = _wf.resolve_decision(run_id, choice)
    except KeyError:
        return jsonify({'error': 'run not found'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'run': run})


@bp.route('/api/workflow-runs/<run_id>/cancel', methods=['POST'])
def cancel_run(run_id):
    """Cancel a live run. Human-only: stopping a pipeline is an operator
    decision, and an agent that could cancel runs could also clear the
    one-live-run guard to re-fire a workflow at will. Leaves any in-flight
    agent session running -- see `mc.workflows.cancel_run`; the response's
    `run.left_running` names them."""
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    try:
        run = _wf.cancel_run(run_id)
    except KeyError:
        return jsonify({'error': 'run not found'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    return jsonify({'ok': True, 'run': run})
