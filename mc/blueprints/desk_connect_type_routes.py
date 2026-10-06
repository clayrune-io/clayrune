"""Desk connection types (`mc/desk_connect/type_view.py`, MC-1062 ticket 01). Its own blueprint:
a new concern does not grow `desk_connect_routes.py`.

    POST /api/desk/connect/types   read-only: {input} a service NAME or an address (`url` is
                                   accepted too, as `inspect` does); returns the resolved
                                   address plus the Sign in / API / MCP projection: `picker`
                                   (types with a variant Clayrune can set up), `details`
                                   (what it cannot, and why) and `reference`. Takes no
                                   credential, opens no vault, makes no lookup.

`inspect` is untouched; this answers the same input in the connection-type shape.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.desk_connect import registry as _registry
from mc.desk_connect import resolve as _resolve
from mc.desk_connect import type_view as _types
from mc.desk_connect.url_check import UrlError

bp = Blueprint('desk_connect_type_routes', __name__)


def _own_hosts() -> tuple:
    host = (request.host or '').split(':')[0].lower()
    return (host,) if host else ()


@bp.route('/api/desk/connect/types', methods=['POST'])
def connection_types():
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        got = _resolve.resolve(d['input'] if 'input' in d else d.get('url'), own_hosts=_own_hosts())
    except _resolve.UnknownNameError as e:
        return jsonify({'error': str(e), 'hint': e.hint, 'code': e.code, 'suggestions': e.suggestions}), 400
    except UrlError as e:
        return jsonify({'error': str(e), 'hint': e.hint}), 400
    svc = got.pop('service') or _registry.lookup(got['host'])
    return jsonify({**got, **_types.project_service(svc['id'] if svc else None)})
