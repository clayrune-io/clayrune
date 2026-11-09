"""Settle a chat submission after its route's publication locks exit."""
from functools import wraps

from flask import jsonify, request

from mc.agent_runtime import PromptDeliveryError
from mc.pending_delivery import publication_boundary


def delivery_response(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        # Imported at call time: route revival defines this exception itself.
        from mc.blueprints.agent_routes import ReviveDeliveryError
        try:
            with publication_boundary():
                return fn(*args, **kwargs)
        except (PromptDeliveryError, ReviveDeliveryError) as exc:
            internal = kwargs.get('_internal')
            data = internal if internal is not None else request.get_json(silent=True) or {}
            body = dict(ok=False, delivered=False,
                        session_id=data.get('session_id', ''), error=str(exc))
            return (body if internal is not None else jsonify(body)), 502
    return run
