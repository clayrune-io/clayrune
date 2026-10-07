"""Human-only project safety controls (MC-1076, follow-up to MC-1075).

Use the existing Origin distinction and OS caller attribution. This is a
request-level refusal: the route must call it before any project side effects.
"""
from __future__ import annotations

from typing import Iterable

from flask import Response, jsonify, request

from mc import caller_attribution, state
from mc.blueprints.workflow_routes import _is_agent_caller


PROJECT_SAFETY_KEYS = frozenset({
    'merge_requires_review',
    'worktree_isolation',
    'worktree_base_branch',
    'worktree_shared_runtime',
    'code_sync_branch',
    'steward_mode',
    'steward_objective',
    'steward_cadence_minutes',
})


STEWARD_ENABLE_SAFETY_KEYS = (
    'steward_mode', 'steward_objective', 'steward_cadence_minutes',
)


def is_agent_caller() -> bool:
    """Reuse the browser Origin check and OS attribution for every safety gate."""
    if _is_agent_caller():
        return True
    env = request.environ
    attribution = caller_attribution.attribute_caller(
        request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
        caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
    return attribution.status == caller_attribution.ATTRIBUTED


def refuse_agent_safety_write(data: Iterable[str]) -> tuple[Response, int] | None:
    """Refuse the whole write when an agent submits any protected key.

    Presence is enough, including null or an unchanged value. A browser
    Origin preserves the human path unless the existing process-tree lookup
    attributes the request to a managed agent. No body field grants a bypass.
    """
    keys = sorted(PROJECT_SAFETY_KEYS.intersection(data))
    if not keys or not is_agent_caller():
        return None
    return jsonify({
        'error': ('Project safety settings are human-only; agent callers cannot '
                  f'write: {", ".join(keys)}. The whole write was refused. '
                  'Use the Clayrune UI.'),
        'protected_keys': keys,
    }), 403
