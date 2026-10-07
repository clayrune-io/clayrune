"""Add a UI hint to locked-vault API answers without changing their refusal or gates.

Older Desk refusals use `not_connected` with a vault reason. Keep that contract,
and mark nested status rows too so every consumer can offer the same unlock link.
This hook never reads the vault or handles an unlock request.
"""
import re

from flask import Blueprint, Response, request

bp = Blueprint('vault_lock_responses', __name__)
_LOCKED_TEXT = re.compile(r'\bvault (?:is )?locked\b', re.IGNORECASE)


def mark_locked(value: object) -> bool:
    """Annotate only the object describing the lock, not its enclosing inventory."""
    changed = False
    if isinstance(value, list):
        for row in value:
            changed = mark_locked(row) or changed
    elif isinstance(value, dict):
        locked = any(value.get(k) in ('vault_locked', 'passkey_vault_locked')
                     for k in ('code', 'error', 'state', 'reason') if isinstance(value.get(k), str))
        locked = locked or any(_LOCKED_TEXT.search(value[k]) for k in ('error', 'reason', 'message')
                               if isinstance(value.get(k), str))
        if locked and value.get('vault_locked') is not True:
            value['vault_locked'] = True
            changed = True
        for row in list(value.values()):
            if isinstance(row, (dict, list)):
                changed = mark_locked(row) or changed
    return changed


@bp.after_app_request
def flag_locked_vault(response: Response) -> Response:
    if (request.path.startswith(('/api/desk/', '/api/secrets', '/api/passkeys'))
            and response.is_json and not response.is_streamed):
        body = response.get_json()
        if mark_locked(body):
            from flask import current_app
            response.set_data(current_app.json.dumps(body))
    return response
