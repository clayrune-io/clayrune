"""Read-only model catalogue and free quotes using the server-held connection."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc import desk_engines as engines
from mc import desk_pieces
from mc.desk_model_discovery.catalogue import discover
from mc.desk_model_discovery.quotes import quote
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_model_discovery', __name__)


def _call(fn):
    try:
        return jsonify(fn())
    except engines.NotConnected as e:
        return jsonify({'error': e.reason, 'code': 'not_connected', 'engine_id': e.engine_id,
                        'vault_entry': e.vault_entry}), 409
    except engines.Refused as e:
        return jsonify({'error': str(e), 'code': e.code, **e.extra}), e.status
    except desk_pieces.PieceError as e:
        return jsonify({'error': str(e), 'code': 'storyboard_unavailable'}), e.status
    except engines.EngineError as e:
        return jsonify({'error': engines._safe(e), 'code': 'discovery_failed'}), 502


@bp.get('/api/desk/engines/<engine_id>/models')
def models(engine_id: str):
    return _call(lambda: discover(engine_id, kind=request.args.get('kind', 'video'),
                                 query=request.args.get('q', ''),
                                 project_id=request.args.get('project_id') or None,
                                 unattended=is_unattended_caller(),
                                 refresh=request.args.get('refresh') == '1'))


@bp.post('/api/desk/engines/<engine_id>/models/quote')
def model_quote(engine_id: str):
    return _call(lambda: quote(engine_id, request.get_json(silent=True),
                              unattended=is_unattended_caller()))
