"""Desk routes by purpose (`mc/desk_connect/purpose_*.py`, slice P2 of
docs/DESK_SERVICE_PROFILES_SPEC.md). Its own blueprint: a new concern does not grow
`desk_connect_routes.py`.

    POST /api/desk/connect/purposes        {service}: read-only. Every route of a known service
                                           grouped by purpose, with requirements, cost, evidence,
                                           what Clayrune can run, and the accounts of that
                                           service with what each has bound (setup and
                                           verification state). Metadata only; no secret value.
    POST /api/desk/connect/purpose/commit  {request_id, draft, passcode}: the one Save of the
                                           routes chosen for one account.
    POST /api/desk/connect/purpose/verify  {account_id, purpose}: ONE free read-only check per
                                           bound route that has one; human-only, no passcode
                                           (it writes nothing to the Desk).

Commit is for humans only and passcode-gated, in the fixed order of `desk_connect_routes`:
refuse an unattended caller, check the draft's shape (a bad draft must not cost a passcode
guess), check the passcode once, then write.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import purpose_bindings as _bindings
from mc.desk_connect import purpose_verification as _verification
from mc.desk_connect import purpose_view as _view
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_purpose_routes', __name__)


def _refuse_agent(what: str):
    return jsonify({'error': f'this action needs a human: an unattended agent session cannot {what}'}), 403


@bp.route('/api/desk/connect/purposes', methods=['POST'])
def purposes_view():
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    service = d.get('service')
    out = _view.view(service) if isinstance(service, str) else None
    if out is None:
        return jsonify({'error': 'that service has no profile', 'code': 'unknown_service'}), 404
    return jsonify(out)


@bp.route('/api/desk/connect/purpose/commit', methods=['POST'])
def commit_purpose_routes():
    if is_unattended_caller():
        return _refuse_agent('choose how a service is connected')
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id = _bindings.clean_request_id(d.get('request_id'))
        clean = _bindings.clean_draft(d.get('draft'))
    except (_bindings.BindError, _bindings._commit.CommitError) as e:
        return jsonify({'error': str(e), 'code': getattr(e, 'code', 'invalid')}), getattr(e, 'status', 400)
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        result, duplicate = _bindings.commit(request_id, clean)
    except _bindings.BindError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk_connect] purpose commit failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not save; see the server log', 'code': 'failed'}), 500
    return jsonify({'ok': True, 'duplicate': duplicate, **result}), (200 if duplicate else 201)


@bp.route('/api/desk/connect/purpose/verify', methods=['POST'])
def verify_purpose():
    if is_unattended_caller():
        return _refuse_agent('check a connection')
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    account_id, purpose = d.get('account_id'), d.get('purpose')
    if not (isinstance(account_id, str) and _bindings._ACCOUNT_ID.match(account_id)):
        return jsonify({'error': 'that account id is not valid', 'code': 'bad_account'}), 400
    if not isinstance(purpose, str):
        return jsonify({'error': 'purpose must be text', 'code': 'bad_purpose'}), 400
    try:
        return jsonify(_verification.check(account_id, purpose))
    except _verification.CheckError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
