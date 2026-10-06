"""Desk browser connection setup (`mc/desk_connect/browser_setup.py`, MC-1062 ticket 03). Its own
blueprint: a new concern does not grow `desk_connect_routes.py`.

    POST /api/desk/connect/browser-setup/commit   NEW endpoint. {request_id, draft, passcode}: the one
                                                  Save of a browser connection: an X or LinkedIn
                                                  account (new or existing), its named browser profile
                                                  and, optionally, the NAME of a stored login. It
                                                  grants no permission, starts no agent read or post,
                                                  creates no browser profile and mints no token.

Commit is for humans only and passcode-gated, in the fixed order of `desk_connect_purpose_routes`:
refuse an unattended caller, check the draft's shape (a bad draft must not cost a passcode guess),
check the passcode once, then write. The password of a typed login (`draft.new_login`) goes to the
vault and nowhere else; no response carries it.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import browser_setup as _setup
from mc.desk_connect import commit as _commit
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_browser_setup_routes', __name__)


def _refuse_agent(what: str):
    return jsonify({'error': f'this action needs a human: an unattended agent session cannot {what}', 'code': 'human_required'}), 403


@bp.route('/api/desk/connect/browser-setup/commit', methods=['POST'])
def commit_browser_setup():
    if is_unattended_caller():
        return _refuse_agent('save a browser connection')
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id = _setup.clean_request_id(d.get('request_id'))
        # A replay of a saved request answers from memory: validating it again would trip over the
        # account and login that request itself wrote. It still needs the passcode.
        saved = _setup.replayed(request_id, d.get('draft'))
        clean = None if saved is not None else _setup.clean_draft(d.get('draft'))
    except (_setup.SetupError, _commit.CommitError) as e:
        return jsonify({'error': str(e), 'code': getattr(e, 'code', 'invalid')}), getattr(e, 'status', 400)
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        if saved is not None:
            return jsonify({'ok': True, 'duplicate': True, **saved}), 200
        assert clean is not None
        result, duplicate = _setup.commit(request_id, d.get('draft'), clean)
    except _setup.SetupError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk_connect] browser setup failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not save; see the server log', 'code': 'failed'}), 500
    return jsonify({'ok': True, 'duplicate': duplicate, **result}), (200 if duplicate or result['unchanged'] else 201)
