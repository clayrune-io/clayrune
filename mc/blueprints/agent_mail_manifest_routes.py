"""Read-only local route that hands the UI a root-verified mail-service manifest (MC-1166 7e-2m).

Behind the same flag as the lifecycle routes. GET only and no passcode: it mutates nothing, takes
no input, and only ever reaches the one reviewed ``agent_mail_service_origin``. The mutations that
consume the manifest (start/owner/key/repin) stay passcode-gated in agent_mail_enrollment_routes.
"""
from __future__ import annotations

from typing import Any, Callable

from flask import Blueprint, jsonify

from mc import state
from mc.agent_mail_manifest_fetch import (
    NOT_CONFIGURED, UNREACHABLE, ManifestFetchError, fetch_verified_manifest,
)
from mc.agent_mail_service_manifest import ManifestState
from mc.core import _log

FLAG = 'agent_mail_enrollment_enabled'
ROUTE = '/api/agent-mail/enrollment/manifest'
ROOT_KEY, ORIGIN_KEY = 'agent_mail_root_public_key', 'agent_mail_service_origin'


def _production_floor(origin: str) -> ManifestState | None:
    """Current persisted rollback floor, or None when no store has been created yet."""
    from mc import agent_mail_factory
    from mc.agent_mail_enrollment_state import ManifestCAS
    store = agent_mail_factory.get().open_store()
    return None if store is None else ManifestCAS(store).load(origin)


def create_blueprint(*, fetch: Callable[..., dict[str, Any]] = fetch_verified_manifest,
                     floor: Callable[[str], ManifestState | None] = _production_floor,
                     transport: Any = None) -> Blueprint:
    bp = Blueprint('agent_mail_manifest', __name__)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(ROUTE, methods=['GET'])
    def manifest():
        if state.CONFIG.get(FLAG) is not True:
            return jsonify({'error': 'not_found'}), 404
        try:
            return jsonify(fetch(origin=state.CONFIG.get(ORIGIN_KEY), root_public_key=state.CONFIG.get(ROOT_KEY),
                                 floor=floor, transport=transport))
        except ManifestFetchError as exc:
            _log(f'[agent-mail-manifest] refused: {exc.code} {exc.detail}'.rstrip(), flush=True)
            status = 502 if exc.code == UNREACHABLE else 409
            body = {'error': exc.code}
            if exc.detail and exc.code != NOT_CONFIGURED:
                body['detail'] = exc.detail
            return jsonify(body), status

    return bp
