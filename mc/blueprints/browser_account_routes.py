"""Metadata-only browser accounts for Vault and existing human attention UI."""
from flask import Blueprint, jsonify

from mc import desk_browser_accounts
from mc.core import _log

bp = Blueprint('browser_accounts', __name__)


@bp.route('/api/secrets/browser-accounts')
def browser_accounts():
    try:
        return jsonify(desk_browser_accounts.snapshot())
    except Exception as e:
        _log(f'[browser_accounts] metadata listing failed: {type(e).__name__}', flush=True)
        return jsonify(error='Browser accounts could not be read.'), 500
