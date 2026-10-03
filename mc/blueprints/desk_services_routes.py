"""Desk "Something else" services — routes (`mc/desk_services.py`).

A service is a name, an optional link and an optional vault credential NAME that
Clayrune only remembers so agents can see it. Reading is open (an agent lists
the services it may use); saving, changing and removing one are a human's, like
adding or removing an account: an unattended agent session gets a 403. No route
here takes, stores or returns a credential value.
"""
from flask import Blueprint, jsonify, request

from mc import desk_services as _services
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_services_routes', __name__)


def _call(fn, *args, status=200, **kw):
    try:
        out = fn(*args, **kw)
    except _services.ServiceError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(out), status


def _human_only(action: str):
    if is_unattended_caller():
        return jsonify({'error': f'this action needs a human: an unattended agent session '
                                 f'cannot {action} a saved service'}), 403
    return None


@bp.route('/api/desk/services', methods=['GET'])
def list_services():
    return jsonify(_services.list_services())


@bp.route('/api/desk/services', methods=['POST'])
def create_service():
    refused = _human_only('save')
    if refused:
        return refused
    d = request.get_json(silent=True) or {}
    return _call(_services.create_service, d.get('name'), link=d.get('link'),
                 credential=d.get('credential'), service_id=d.get('id'), status=201)


@bp.route('/api/desk/services/<service_id>', methods=['PATCH'])
def update_service(service_id):
    refused = _human_only('change')
    if refused:
        return refused
    return _call(_services.update_service, service_id, request.get_json(silent=True) or {})


@bp.route('/api/desk/services/<service_id>', methods=['DELETE'])
def delete_service(service_id):
    refused = _human_only('remove')
    if refused:
        return refused
    if not _services.delete_service(service_id):
        return jsonify({'error': 'service not found'}), 404
    return jsonify({'ok': True})
