"""Human-only gate for the two project routes that publish (MC-1179 pass 11, SF22).

`POST /api/project/<id>/github/setup` starts creating GitHub issues from the
backlog and its notes; `POST /api/project/<id>/code-sync/enable` pushes a branch
carrying local commits. Neither can be taken back by Clayrune, so neither may be
reached by an agent. Same layering as `mc.project_routing_guard`: caller
attribution first (cheap, and a valid passcode or a forged Origin cannot talk
past it), then the retyped dashboard passcode.

The route must call this before it validates the repo, saves anything or
starts a thread, and must not persist the `passcode` field of its body.
"""
from __future__ import annotations

from flask import Response, jsonify

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.project_safety_settings import is_agent_caller
from mc.unattended import is_unattended_caller


def require_human_publish(data: dict, action: str) -> tuple[Response, int] | None:
    """Return a refusal to send straight back, or None to proceed.

    `action` is a plain phrase for the error text, e.g. "connect a GitHub repo".
    """
    if is_unattended_caller() or is_agent_caller():
        return jsonify({
            'error': 'human_only',
            'message': (f'Only a person at the Clayrune dashboard can {action}: it '
                        'publishes project data and cannot be undone from here. '
                        'Agent and unattended callers are refused.'),
        }), 403
    return _require_human_passcode(data)
