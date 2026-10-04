"""Desk connect-by-URL routes (`mc/desk_connect/`, docs/DESK_CONNECT_BY_URL_SPEC.md).

    GET  /api/desk/connect/suggest   read-only: ?q=<typed so far> -> the registry's
                                     services whose name starts with it.
    POST /api/desk/connect/inspect   read-only: {input} a service NAME or an address
                                     (`url` is accepted too); returns its connection
                                     options. Takes no credential, makes no lookup.
    POST /api/desk/connect/commit    the one Save: {request_id, draft, passcode}.

Commit is human-passcode-gated and for humans only. Agents never write secrets
(CLAUDE.md vault rule 3): an unattended caller gets 403 before anything is read, and
`_require_human_passcode` is the second, forgery-proof gate (the first alone trusts
a browser Origin header). Order is fixed: refuse an unattended caller, check the
request's shape (a bad draft must not cost a passcode guess), check the passcode
exactly once, then write.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import commit as _commit
from mc.desk_connect import methods as _methods
from mc.desk_connect import registry as _registry
from mc.desk_connect import resolve as _resolve
from mc.desk_connect.url_check import UrlError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_routes', __name__)


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


@bp.route('/api/desk/connect/commit', methods=['POST'])
def commit_connection():
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot save a service or a credential'}), 403
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id = _commit.clean_request_id(d.get('request_id'))
        clean = _commit.clean_draft(d.get('draft'), own_hosts=_own_hosts())
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
