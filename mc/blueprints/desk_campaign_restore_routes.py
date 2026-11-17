"""Undo of a Desk campaign delete: POST /api/desk/campaigns/<id>/restore.

The Desk's Delete calls DELETE /api/desk/campaigns/<id>, which moves the
campaign and its pieces to the trash (mc/desk_campaign_trash.py); its Undo
calls this. A person's action: an unattended caller is refused (MC-1187 DK1), and the
restored posts come back for review, never re-armed.
"""
from flask import Blueprint, jsonify, request

from mc import desk as _desk
from mc.desk_campaign_trash import RestoreError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_campaign_restore_routes', __name__)


@bp.route('/api/desk/campaigns/<campaign_id>/restore', methods=['POST'])
def restore_campaign(campaign_id: str):
    # MC-1187 DK1: undoing a delete is a person's decision, not an agent's.
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot restore a deleted campaign'}), 403
    try:
        camp = _desk.restore_campaign(campaign_id)
    except RestoreError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(_desk.v1_campaign(camp) if request.args.get('shape') == 'v1' else camp)
