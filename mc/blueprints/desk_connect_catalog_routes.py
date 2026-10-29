"""Public service names for Desk surfaces, projected from Connect's registry."""
from flask import Blueprint, jsonify

from mc.desk_connect import registry

bp = Blueprint('desk_connect_catalog', __name__)


@bp.get('/api/desk/connect/services')
def services():
    return jsonify({'services': [registry.brief(s) for s in registry.registry()['services']]})
