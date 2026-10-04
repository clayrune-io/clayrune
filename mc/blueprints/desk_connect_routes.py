"""Desk connect-by-URL routes (`mc/desk_connect/`, docs/DESK_CONNECT_BY_URL_SPEC.md).

    GET  /api/desk/connect/suggest   read-only: ?q=<typed so far> -> the registry's
                                     services whose name starts with it.
    POST /api/desk/connect/inspect   read-only: {input} a service NAME or an address
                                     (`url` is accepted too); returns its connection
                                     options. Takes no credential, makes no lookup.
    POST /api/desk/connect/commit    the one Save: {request_id, draft, passcode}.
    POST /api/desk/connect/verify    {service, method, account_id?}: ONE free read-only
                                     check with the stored credential; human-only, no
                                     passcode (it writes nothing). Answers the derived
                                     status, "verified" only when the check passed.
                                     `check: false` answers the status without checking.

Commit is human-passcode-gated and for humans only. Agents never write secrets
(CLAUDE.md vault rule 3): an unattended caller gets 403 before anything is read, and
`_require_human_passcode` is the second, forgery-proof gate (the first alone trusts
a browser Origin header). Order is fixed: refuse an unattended caller, check the
request's shape (a bad draft must not cost a passcode guess), check the passcode
exactly once, then write.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from mc import secrets_store as _vault

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import commit as _commit
from mc.desk_connect import methods as _methods
from mc.desk_connect import registry as _registry
from mc.desk_connect import resolve as _resolve
from mc.desk_connect import verification as _verification
from mc.desk_connect.url_check import UrlError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_routes', __name__)

_ACCOUNT_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def _own_hosts() -> tuple:
    host = (request.host or '').split(':')[0].lower()
    return (host,) if host else ()


@bp.route('/api/desk/connect/suggest', methods=['GET'])
def suggest_services():
    q = request.args.get('q', '')[:_resolve.MAX_NAME]
    return jsonify({'q': q, 'suggestions': _registry.suggest(q)})


@bp.route('/api/desk/connect/inspect', methods=['POST'])
def inspect_address():
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        return jsonify(_methods.inspect(d['input'] if 'input' in d else d.get('url'), own_hosts=_own_hosts()))
    except _resolve.UnknownNameError as e:
        return jsonify({'error': str(e), 'hint': e.hint, 'code': e.code, 'suggestions': e.suggestions}), 400
    except UrlError as e:
        return jsonify({'error': str(e), 'hint': e.hint}), 400


def _vault_names():
    """Names of what is stored (metadata only), so a known-host draft that would
    collide with a stored entry is refused before the passcode is asked. None when
    the vault cannot be listed: `commit` checks again."""
    try:
        return {s['name'] for s in _vault.list_secrets()}
    except _vault.SecretsError:
        return None


@bp.route('/api/desk/connect/commit', methods=['POST'])
def commit_connection():
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot save a service or a credential'}), 403
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id = _commit.clean_request_id(d.get('request_id'))
        names = None if _commit.is_known_request(request_id) else _vault_names()
        clean = _commit.clean_draft(d.get('draft'), own_hosts=_own_hosts(), vault_names=names)
    except _commit.CommitError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        result, duplicate = _commit.commit(request_id, clean)
    except _commit.CommitError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk_connect] commit failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not save; see the server log', 'code': 'failed'}), 500
    return jsonify({'ok': True, 'duplicate': duplicate, **result}), (200 if duplicate else 201)


@bp.route('/api/desk/connect/verify', methods=['POST'])
def verify_connection():
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot check a stored credential'}), 403
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    account_id = d.get('account_id')
    if account_id is not None and not (isinstance(account_id, str) and _ACCOUNT_ID.match(account_id)):
        return jsonify({'error': 'that account id is not valid', 'code': 'bad_account'}), 400
    try:
        if d.get('check') is False:        # the derived status only: no outside call
            return jsonify(_verification.status(d.get('service'), d.get('method'), account_id))
        return jsonify(_verification.verify(d.get('service'), d.get('method'), account_id))
    except _verification.VerifyError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
