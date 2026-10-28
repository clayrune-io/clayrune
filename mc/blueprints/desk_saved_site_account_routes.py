"""Human/passcode Save for explicit old saved-site attachment."""
from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_accounts import AccountError
from mc.desk_connect.browser_setup import SetupError
from mc.desk_connect import saved_site_account as sites
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_saved_site_account', __name__)


@bp.route('/api/desk/connect/saved-site/<service_id>/account', methods=['POST'])
def attach(service_id):
    if is_unattended_caller():
        return jsonify(error='this action needs a human', code='human_required'), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {'account', 'source', 'passcode'} or not isinstance(body.get('source'), dict):
        return jsonify(error='choose an account', code='invalid'), 400
    try:
        sites.validate(service_id, body.get('account'), body['source'])
    except (SetupError, AccountError) as e:
        return jsonify(error=str(e), code=getattr(e, 'code', 'invalid')), e.status
    refused = _require_human_passcode(body)
    if refused is not None:
        return refused
    try:
        return jsonify(ok=True, **sites.attach(service_id, body.get('account'), body['source']))
    except (SetupError, AccountError) as e:
        return jsonify(error=str(e), code=getattr(e, 'code', 'invalid')), e.status
    except Exception as e:
        _log(f'[desk-connect] saved site attachment failed: {type(e).__name__}', flush=True)
        return jsonify(error='could not attach the account; see the server log', code='failed'), 500
