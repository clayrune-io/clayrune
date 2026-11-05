"""Channel-impact routes (MC-1152). P2 adds only the tracked-link seam, DARK.

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 10 (a new route group lives
here) and section 12 (P2). Every route answers 404 until the operator sets the
config key `desk_impact_links_enabled` (`mc.desk_impact_link_insert.CONFIG_KEY`),
so registering this blueprint exposes nothing by default. `status` is the one
exception: it reports the flag so the page can decide, and says nothing else.

  GET  /api/desk/impact/links/status    {enabled}
  POST /api/desk/impact/links/preview   {piece_id, version_id, destination}
  POST /api/desk/impact/links/insert    {piece_id, version_id, destination}

The project, campaign and platform come from the stored piece, never from the
request. Insert edits a draft body the way PATCH already may; it approves,
schedules and sends nothing, and the approval gates are untouched.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from mc import desk as _desk
from mc import desk_impact_link_insert as _ins
from mc import desk_pieces as _pieces
from mc.core import _log
from mc.desk_impact_links import TrackedLinks
from mc.desk_impact_store import (
    ImpactError, ImpactStore, LinkConflict, NotFound, StoreCorrupt, StoreIOError,
)

bp = Blueprint('desk_impact_routes', __name__)

_links: TrackedLinks | None = None


def wire(*, links: TrackedLinks | None = None) -> None:
    """Replace the service (tests). Production builds it lazily."""
    global _links
    _links = links


def _service() -> TrackedLinks:
    global _links
    if _links is None:
        # Beside data/desk.json, outside data/projects (spec section 5).
        if _desk.STORE_PATH is None:
            raise StoreIOError('the Desk store is not wired')
        data = Path(_desk.STORE_PATH).parent
        _links = TrackedLinks(ImpactStore(data / 'desk_impact', data_dir=data / 'projects'))
    return _links


def _body() -> tuple[str, str, object]:
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    return str(data.get('piece_id') or ''), str(data.get('version_id') or ''), data.get('destination')


def _run(fn):
    if not _ins.enabled():
        return jsonify({'error': 'not available'}), 404
    piece_id, version_id, destination = _body()
    try:
        return jsonify(fn(_service(), piece_id, version_id, destination))
    except _ins.InsertRefused as e:
        return jsonify({'error': str(e), 'code': e.code, 'keys': list(e.keys)}), e.status
    except _pieces.PieceError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except NotFound:
        return jsonify({'error': 'not found'}), 404
    except LinkConflict:
        return jsonify({'error': 'that link identity is already issued differently',
                        'code': 'link_conflict'}), 409
    except (StoreCorrupt, StoreIOError, ImpactError) as e:
        _log(f'[desk_impact] tracked-link request failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'the link record could not be read or saved',
                        'code': 'store'}), 500


@bp.route('/api/desk/impact/links/status', methods=['GET'])
def links_status():
    return jsonify({'enabled': _ins.enabled()})


@bp.route('/api/desk/impact/links/preview', methods=['POST'])
def links_preview():
    return _run(_ins.preview)


@bp.route('/api/desk/impact/links/insert', methods=['POST'])
def links_insert():
    return _run(_ins.insert)
