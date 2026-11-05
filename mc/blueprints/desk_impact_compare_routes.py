"""Channel-impact comparison routes (MC-1152 P4, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 10 (a new route group) and
section 12 (P4); D1 = A. Every route but `status` answers 404 until the operator
sets `desk_impact_compare_enabled` (`mc.desk_impact_view.CONFIG_KEY`), so
registering this blueprint exposes nothing by default. `status` reports the flag
and nothing else, so the page can decide whether to draw.

  GET  /api/desk/impact/compare/status                      {enabled}
  GET  /api/desk/impact/compare/<campaign_id>[?start&end]   cached comparison; LOCAL ONLY
  GET  /api/desk/impact/definition/<campaign_id>            goal + conversion definition
  PUT  /api/desk/impact/definition/<campaign_id>            set the definition   (human only)
  POST /api/desk/impact/refresh/<campaign_id>               read the source now  (human only)

The two writes need the retyped dashboard passcode, the same gate the Desk's
other human-only routes use (`desk_routes._human_only`): a definition binds a
credential reference to a campaign's measurement and a refresh spends a vault
read. Neither approves, schedules, sends or widens anything. The project is taken
from the stored campaign, never from the request.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc import desk_impact_schema as S
from mc import desk_impact_view as _view
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_impact_sources.base import DefinitionRefused
from mc.desk_impact_store import (
    ImpactError, NotFound, RevisionConflict, StoreCorrupt, StoreIOError,
)
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_impact_compare_routes', __name__)


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _human_only(action: str, data: dict):
    if is_unattended_caller():
        return jsonify({'error': f'this action needs a human: an unattended agent session '
                                 f'cannot {action}', 'code': 'human_only'}), 403
    return _require_human_passcode(data)


def _run(fn):
    if not _view.enabled():
        return jsonify({'error': 'not available'}), 404
    try:
        return jsonify(fn())
    except _view.NoConnection as e:
        return jsonify({'error': str(e), 'code': 'no_connection'}), 409
    except NotFound:
        return jsonify({'error': 'not found'}), 404
    except RevisionConflict as e:
        return jsonify({'error': str(e), 'code': 'revision_conflict'}), 409
    except (S.SchemaError, DefinitionRefused) as e:
        return jsonify({'error': str(e), 'code': 'invalid'}), 400
    except (StoreCorrupt, StoreIOError, ImpactError) as e:
        _log(f'[desk_impact] compare request failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'the impact record could not be read or saved',
                        'code': 'store'}), 500


@bp.route('/api/desk/impact/compare/status', methods=['GET'])
def compare_status():
    return jsonify({'enabled': _view.enabled()})


@bp.route('/api/desk/impact/compare/<campaign_id>', methods=['GET'])
def compare_read(campaign_id):
    return _run(lambda: _view.comparison_view(
        campaign_id, request.args.get('start'), request.args.get('end')))


@bp.route('/api/desk/impact/definition/<campaign_id>', methods=['GET'])
def definition_read(campaign_id):
    return _run(lambda: _view.definition_view(campaign_id))


@bp.route('/api/desk/impact/definition/<campaign_id>', methods=['PUT'])
def definition_write(campaign_id):
    if not _view.enabled():
        return jsonify({'error': 'not available'}), 404
    data = _json()
    refused = _human_only('change a campaign\'s conversion definition', data)
    if refused:
        return refused
    return _run(lambda: _view.save_definition(
        campaign_id, data.get('definition'), data.get('expected_revision')))


@bp.route('/api/desk/impact/refresh/<campaign_id>', methods=['POST'])
def refresh(campaign_id):
    if not _view.enabled():
        return jsonify({'error': 'not available'}), 404
    data = _json()
    refused = _human_only('refresh a campaign\'s conversion source', data)
    if refused:
        return refused
    return _run(lambda: _view.refresh(campaign_id, data.get('start'), data.get('end')))
