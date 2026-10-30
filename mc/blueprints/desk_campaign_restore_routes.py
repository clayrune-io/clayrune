"""Undo of a Desk campaign delete: POST /api/desk/campaigns/<id>/restore.

The Desk's Delete calls DELETE /api/desk/campaigns/<id>, which moves the
campaign and its pieces to the trash (mc/desk_campaign_trash.py); its Undo
calls this. Reversible data, so agent-callable like the DELETE it reverses.
"""
from flask import Blueprint, jsonify, request

from mc import desk as _desk
from mc.desk_campaign_trash import RestoreError

bp = Blueprint('desk_campaign_restore_routes', __name__)


@bp.route('/api/desk/campaigns/<campaign_id>/restore', methods=['POST'])
def restore_campaign(campaign_id: str):
    try:
        camp = _desk.restore_campaign(campaign_id)
    except RestoreError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(_desk.v1_campaign(camp) if request.args.get('shape') == 'v1' else camp)
