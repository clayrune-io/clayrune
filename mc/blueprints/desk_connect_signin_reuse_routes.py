"""MC-1114: may a saved X account's own sign-in be kept by the next Save? Metadata only."""
from flask import Blueprint, jsonify

from mc.desk_connect import signin_reuse as _reuse

bp = Blueprint('desk_connect_signin_reuse_routes', __name__)


@bp.route('/api/desk/connect/signin-reuse/<account_id>', methods=['GET'])
def signin_reuse(account_id: str):
    """`{reusable, reason}` for the Client ID already saved in the Vault. Opens no token, writes nothing."""
    return jsonify(_reuse.check('x', account_id))
