"""Human-only gate for SCHEDULING and RUNNING a workflow (MC-1188 / DCW-W2).

Standing rule (Ron, 2026-09-18): an agent may draft a workflow; only a human may
enable, schedule or run one unattended. Definition CRUD, draft, decision and
cancel keep the stronger Origin-plus-retyped-passcode gate in
`workflow_routes._refuse_if_agent_caller`. The routes here are clicked from the
dashboard on every Run, so they use the lighter gate the steward enable routes
use: `project_safety_settings.is_agent_caller()` -- a browser Origin, plus the OS
attribution of the request to a managed agent process. No passcode, and nothing
in the request body can grant a bypass.

`project_safety_settings` imports `workflow_routes`, so it is imported lazily
inside the call, never at module top, to keep the import graph acyclic.
"""
from __future__ import annotations

from flask import jsonify


def refuse_if_agent_caller():
    """None for a human caller; else a 403 response tuple to return as-is."""
    from mc.project_safety_settings import is_agent_caller
    if is_agent_caller():
        return jsonify({
            'error': ('workflows are scheduled and run from the Clayrune UI -- '
                      'an agent session may not schedule or run one '
                      '(standing rule 2026-09-18, MC-1188).'),
        }), 403
    return None


def is_pause_only(data: dict, row: dict) -> bool:
    """True when a schedule PUT body's only effective change is `enabled` ->
    false. Pausing is the safe direction, so any caller may do it; enabling,
    or changing anything else, is not."""
    if not isinstance(data, dict) or data.get('enabled', True) is not False:
        return False
    return all(data[k] == row.get(k) for k in data if k != 'enabled')
