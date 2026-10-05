"""Unknown-service discovery routes (`mc/desk_connect/discovery.py`, slice 3 of
docs/DESK_CONNECT_BY_URL_SPEC.md). Its own blueprint: a new concern does not grow
`desk_connect_routes.py`.

    POST /api/desk/connect/discover          {url | input, request_id}: Tier 2 for an
                                             address the registry does not know. Reads the
                                             public page in a signed-out pane and looks the
                                             service up in the official MCP registry, then
                                             sorts what it found through the isolated
                                             toolless transform. Takes no credential, writes
                                             nothing, and is bounded to 60 seconds.
    POST /api/desk/connect/discover/cancel   {request_id}: stop a running lookup.

Humans only (403 for an unattended agent session): a lookup starts a browser and a model
call, and nothing an agent needs goes through here. A NAME the registry does not know is
refused (`name_needs_address`): turning a name into an address would be a guess, and the
Service step asks for the address instead. A known host is refused too (`known_service`):
Tier 1 already answers it, with no outside call.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.core import _log
from mc.desk_connect import commit as _commit
from mc.desk_connect import discovery as _discovery
from mc.desk_connect import registry as _registry
from mc.desk_connect import resolve as _resolve
from mc.desk_connect.url_check import UrlError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_discover_routes', __name__)

_NO_AGENTS = {'error': 'this action needs a human: an unattended agent session cannot look a service up'}


def _own_hosts() -> tuple:
    host = (request.host or '').split(':')[0].lower()
    return (host,) if host else ()


@bp.route('/api/desk/connect/discover', methods=['POST'])
def discover_service():
    if is_unattended_caller():
        return jsonify(_NO_AGENTS), 403
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id = _commit.clean_request_id(d.get('request_id'))
    except _commit.CommitError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    try:
        info = _resolve.resolve(d['input'] if 'input' in d else d.get('url'), own_hosts=_own_hosts())
    except _resolve.UnknownNameError as e:
        return jsonify({'error': str(e), 'hint': e.hint, 'code': 'name_needs_address'}), 400
    except UrlError as e:
        return jsonify({'error': str(e), 'hint': e.hint}), 400
    if info['input_kind'] != 'url':
        return jsonify({'error': 'Clayrune already knows that service by name.', 'code': 'known_service'}), 400
    if info.get('service') or _registry.lookup(info['host']):
        return jsonify({'error': 'Clayrune already knows that service, so there is nothing to look up.',
                        'code': 'known_service'}), 400
    cancel_ev = _discovery.begin(request_id)
    if cancel_ev is None:
        return jsonify({'error': 'Another lookup is still running. Wait for it, or cancel it.', 'code': 'busy'}), 429
    try:
        answer = _discovery.discover(info, own_hosts=_own_hosts(), cancel_ev=cancel_ev)
    except _discovery.Cancelled:
        return jsonify({'ok': False, 'cancelled': True, 'code': 'cancelled', 'error': 'The lookup was cancelled.'}), 200
    except Exception as e:
        _log(f'[desk_connect] discovery failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The lookup failed; see the server log.', 'code': 'failed'}), 500
    finally:
        _discovery.finish(request_id)
    if not answer.get('ok'):                 # an address that may not be opened
        return jsonify({'error': answer['error'], 'hint': answer.get('hint', ''), 'code': answer['code']}), 400
    return jsonify(answer)


@bp.route('/api/desk/connect/discover/cancel', methods=['POST'])
def cancel_discovery():
    if is_unattended_caller():
        return jsonify(_NO_AGENTS), 403
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id = _commit.clean_request_id(d.get('request_id'))
    except _commit.CommitError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    return jsonify({'ok': True, 'cancelled': _discovery.cancel(request_id)})
