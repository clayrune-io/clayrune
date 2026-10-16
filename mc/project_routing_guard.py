"""Human-only writes of project routing fields (MC-1080)."""
from __future__ import annotations

from flask import Response, jsonify

from mc.project_safety_settings import is_agent_caller
from mc.blueprints.secrets_routes import _require_human_passcode


PROJECT_ROUTING_KEYS = frozenset({'id', 'project_path'})


def require_human_routing_write(data: dict, *, is_new: bool = False) -> tuple[Response, int] | None:
    """Refuse before side effects; presence includes null and unchanged values.

    Caller attribution runs before the passcode check, so an attributed agent
    cannot use a forged Origin or even a valid passcode to change routing.
    Human creation skips proof; existing records still require it.
    The route must discard the proof before persisting its body.
    """
    keys = sorted(PROJECT_ROUTING_KEYS.intersection(data))
    if not keys:
        return None
    if is_agent_caller():
        return jsonify({
            'error': ('Project routing fields are human-only; agent callers cannot '
                      f'write: {", ".join(keys)}. The whole write was refused. '
                      'Use the Clayrune UI.'),
            'protected_keys': keys,
        }), 403
    return None if is_new else _require_human_passcode(data)
