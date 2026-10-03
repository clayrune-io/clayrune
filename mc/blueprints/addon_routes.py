"""Add-on routes: agents request, the human approves (MC-1022, spec §4-5).

Thin wiring over `mc.addons`. Who may do what:

    GET  /api/addons                          read-only view for Settings > Add-ons
    POST /api/addons/requests                 AGENT-CALLABLE, installs nothing
    GET  /api/addons/requests/<id>            one card (poll for install progress)
    POST /api/addons/requests/<id>/approve    passcode-gated, starts the install
    POST /api/addons/requests/<id>/decline    passcode-gated, durable 30-day no
    POST /api/addons/install                  passcode-gated, Settings > Available
    POST /api/addons/<id>/remove              passcode-gated

Every route that can start an install, an adoption or a removal goes through
`_require_human_passcode` -- the same gate the secrets routes and
install-launch use -- before it touches `mc.addons`. There is no config key and
no route that installs without it (`tests/test_addons_gate.py` enumerates the
app's routes to keep it that way). The one open write, filing a request, is
safe because it only records a card.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.addons import catalogue, service
from mc.addons.manifest import AddonError, AddonInUse, AddonMissing
from mc.blueprints import local_auth
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log

bp = Blueprint('addon_routes', __name__)


def _body() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _addon_ref(raw: object) -> str | None:
    ref = raw.strip() if isinstance(raw, str) else ''
    return ref if ref and len(ref) <= 64 else None


def _user_identity() -> dict:
    return {'kind': 'user', 'session_id': '', 'project_id': '', 'unattended': False}


@bp.route('/api/addons')
def api_addons_overview():
    try:
        cat_ok, cat_err = True, ''
        catalogue.load()
    except catalogue.CatalogueError as e:
        cat_ok, cat_err = False, str(e)
    return jsonify({
        'platform': catalogue.platform_key(),
        'passcode_configured': bool(local_auth._local_auth_is_configured()),
        'catalogue_ok': cat_ok, 'catalogue_error': cat_err,
        'pending': service.pending_cards(),
        'installed': service.installed_view(),
        'available': service.available_view(),
    })


@bp.route('/api/addons/requests', methods=['POST'])
def api_addons_request():
    """Agent-callable. `project_id`, `session_id` and `requested_by` are derived
    from the server's session records, never read from the body."""
    d = _body()
    ref = _addon_ref(d.get('addon_id'))
    if ref is None:
        return jsonify({'error': 'addon_id is required'}), 400
    try:
        card, outcome = service.file_request(ref, d.get('reason', ''), service.caller_identity())
    except AddonError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[addons] request for {ref!r} failed: {e}', flush=True)
        return jsonify({'error': 'could not file the request; see the server log'}), 500
    return jsonify({'outcome': outcome, 'request': card}), (201 if outcome == 'created' else 200)


@bp.route('/api/addons/requests/<request_id>')
def api_addons_request_get(request_id):
    from mc.addons import request_store as rs
    rec = rs.get(request_id)
    if rec is None:
        return jsonify({'error': 'no such request'}), 404
    try:
        return jsonify(service.card(rec))
    except AddonError as e:
        return jsonify({'error': str(e)}), 404


@bp.route('/api/addons/requests/<request_id>/approve', methods=['POST'])
def api_addons_approve(request_id):
    d = _body()
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        claimed = service.approve(request_id)
    except AddonError as e:
        return jsonify({'error': str(e)}), 404 if str(e) == 'no such request' else 409
    if claimed is None:
        return jsonify({'error': 'this request was already approved, declined or is installing'}), 409
    return jsonify({'ok': True, 'request': service.card(claimed)}), 202


@bp.route('/api/addons/requests/<request_id>/decline', methods=['POST'])
def api_addons_decline(request_id):
    refusal = _require_human_passcode(_body())
    if refusal is not None:
        return refusal
    rec = service.decline(request_id)
    if rec is None:
        return jsonify({'error': 'this request is not waiting for a decision'}), 409
    return jsonify({'ok': True, 'request': service.card(rec)})


@bp.route('/api/addons/install', methods=['POST'])
def api_addons_install():
    """Settings > Add-ons > Available > Install. Catalogue ids and
    `system:<id>` adoption only; the user is asking in person."""
    d = _body()
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    ref = _addon_ref(d.get('addon_id'))
    if ref is None:
        return jsonify({'error': 'addon_id is required'}), 400
    try:
        view, outcome = service.install_direct(ref, _user_identity())
    except AddonError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[addons] direct install of {ref!r} failed to start: {e}', flush=True)
        return jsonify({'error': 'could not start the install; see the server log'}), 500
    return jsonify({'outcome': outcome, 'request': view}), (200 if outcome == 'already_installed' else 202)


@bp.route('/api/addons/<addon_id>/remove', methods=['POST'])
def api_addons_remove(addon_id):
    refusal = _require_human_passcode(_body())
    if refusal is not None:
        return refusal
    try:
        return jsonify({'ok': True, **service.remove(addon_id)})
    except AddonInUse as e:
        return jsonify({'error': str(e)}), 409
    except AddonMissing:
        return jsonify({'error': f'{addon_id} is not installed'}), 404
    except AddonError as e:
        return jsonify({'error': str(e)}), 500

