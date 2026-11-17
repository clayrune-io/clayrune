"""Human-only HTTP gate for changes to installed skills and import state."""

from flask import jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.unattended import is_unattended_caller


def require_skill_mutation():
    """Use the same ordered checks as Distiller promotion; never gate reads."""
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human; an unattended '
                        'agent session cannot change skills; use the Skills UI'}), 403
    return _require_human_passcode(request.get_json(silent=True) or {})
