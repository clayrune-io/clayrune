"""Public campaign destinations, projected from Connect's registry."""
from flask import Blueprint, jsonify

from mc import desk_accounts
from mc.desk_connect import registry

bp = Blueprint('desk_connect_catalog', __name__)


@bp.get('/api/desk/connect/services')
def services():
    # Reuse the account store's channel definition: YouTube / Instagram are
    # modeled accounts even though their profiles have no publish purpose yet.
    # New publishing profiles qualify without adding another vendor list.
    return jsonify({'services': [
        registry.brief(s) for s in registry.registry()['services']
        if s['id'] in desk_accounts.ACCOUNT_PLATFORMS
        or any(p['id'] == 'publish' for p in s.get('profile', {}).get('purposes', []))
    ]})
