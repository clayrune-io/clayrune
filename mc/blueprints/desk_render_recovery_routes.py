"""A human can acknowledge an interrupted submission without spending."""
from flask import Blueprint, jsonify

from mc import desk_engines, desk_render_recovery
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_render_recovery_routes', __name__)


@bp.route('/api/desk/engines/renders/<render_id>/acknowledge', methods=['POST'])
def acknowledge_submission(render_id):
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot acknowledge a render submission'}), 403
    # No passcode: this only acknowledges the record. The next Render still
    # reaches the existing human/passcode gate; this route never submits.
    try:
        record = desk_render_recovery.acknowledge(render_id)
    except desk_engines.Refused as error:
        return jsonify({'error': str(error), 'code': error.code}), error.status
    return jsonify({'render': record})
