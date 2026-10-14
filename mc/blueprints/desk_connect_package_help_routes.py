"""Human-requested, read-only package setup guidance; no approval or save seam."""
from flask import Blueprint, jsonify, request
from mc.desk_connect.mcp_errors import ActivationError
from mc.desk_connect.package_setup_helper import help_setup
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_package_help', __name__)


@bp.route('/api/desk/connect/custom/setup-help', methods=['POST'])
def package_setup_help():
    if is_unattended_caller():
        return jsonify({'error':'Setup help needs a human request.'}), 403
    try:
        return jsonify(help_setup(request.get_json(silent=True)))
    except ActivationError as error:
        return jsonify({'error':str(error), 'code':error.code}), error.status
