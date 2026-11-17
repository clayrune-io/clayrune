"""Attribution-only human gate for mutating routes that checked no caller
(MC-1179 pass 08 / T8-3 follow-up, gate batch 1).

An agent's Bash `curl` and an unattended session reach every route on the local
API. A route that records "a person clicked", drives the remote-access control
plane, pairs a phone, deletes a project or stops the server must not be
reachable that way. This is the lightest gate the codebase has: no passcode, no
prompt, no UI change. A person at the dashboard (or on the phone / tunnel, whose
requests carry the browser Origin of the page) is served exactly as before.

It composes the two checks `mc.project_publish_gate.require_human_publish` is
built from -- `is_unattended_caller()` and `project_safety_settings.is_agent_caller()`
(browser Origin, then OS attribution of the loopback peer to a managed agent
process) -- and nothing else. It does NOT add an attribution check of its own,
and it does NOT ask for the passcode (publish gate = this + passcode;
`_require_human_passcode` stays the stronger gate for the routes that have it).

`project_safety_settings` imports `workflow_routes`, so it is imported inside
the call, never at module top, to keep the import graph acyclic (same as
`mc.workflow_human_gate`).

Use `@human_only('<what the route does>')` directly under `@bp.route`, above any
other decorator of the view, so the refusal runs before any lookup, validation,
save, thread or outside call. The wrapped view keeps its name, so the Flask
endpoint does not change.
"""
from __future__ import annotations

from functools import wraps

from flask import Response, jsonify


def refuse_non_human(action: str) -> tuple[Response, int] | None:
    """None for a person at the dashboard (or phone/tunnel), else a 403 to return as-is.

    `action` is a plain phrase for the error text, e.g. "stop the Clayrune server".
    """
    from mc.project_safety_settings import is_agent_caller
    from mc.unattended import is_unattended_caller
    if is_unattended_caller() or is_agent_caller():
        return jsonify({
            'error': 'human_only',
            'message': (f'Only a person at the Clayrune dashboard can {action}. '
                        'Agent and unattended callers are refused.'),
        }), 403
    return None


def human_only(action: str):
    """Decorator form of `refuse_non_human` for a Flask view."""
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            refusal = refuse_non_human(action)
            if refusal is not None:
                return refusal
            return fn(*args, **kwargs)
        return wrapped
    return deco
