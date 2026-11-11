"""Public mail deployment readback and human-passcode-gated configuration (MC-1166).

Available before enrollment is enabled. Never fetches a manifest, creates an identity,
resets a rollback floor or changes a mail flag. Public keys are not vault secrets.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from mc import provider_preferences, state
from mc.agent_mail_factory import ORIGIN_KEY, ROOT_KEY
from mc.agent_mail_manifest_fetch import valid_service_origin
from mc.agent_mail_service_manifest import ManifestRefused, _decode
from mc.atomic_json import write_json_atomic
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.unattended import is_unattended_caller

ROUTE = '/api/agent-mail/deployment'
KEYS = frozenset({ROOT_KEY, ORIGIN_KEY})


def validate_values(body: dict[str, Any]) -> dict[str, str]:
    """Both public values are required; no normalization of a trust anchor or origin."""
    if set(body) - KEYS - {'passcode'} or not KEYS <= body.keys():
        raise ValueError('invalid_request')
    root, origin = body[ROOT_KEY], body[ORIGIN_KEY]
    if not isinstance(root, str):
        raise ValueError('invalid_root_public_key')
    try:
        _decode(root, 32)  # Same canonical, unpadded base64url Ed25519 bytes as the verifier.
    except (ManifestRefused, ValueError):
        raise ValueError('invalid_root_public_key') from None
    if not valid_service_origin(origin):
        raise ValueError('invalid_service_origin')
    return {ROOT_KEY: root, ORIGIN_KEY: origin}


def _snapshot() -> dict[str, Any]:
    values = {key: state.CONFIG.get(key, '') for key in KEYS}
    return {**values, 'set': {key: isinstance(value, str) and bool(value)
                              for key, value in values.items()}}


def create_blueprint(*, config_path: Path | None = None) -> Blueprint:
    bp = Blueprint('agent_mail_deployment', __name__)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(ROUTE, methods=['GET'])
    @provider_preferences.serialized_write
    def read():
        return jsonify(_snapshot())

    @bp.route(ROUTE, methods=['PUT'])
    @provider_preferences.serialized_write
    def update():
        if is_unattended_caller():
            return jsonify({'error': 'human_required'}), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({'error': 'invalid_request'}), 400
        # A forged Origin or a manual-session label cannot replace this proof.
        refusal = _require_human_passcode(body)
        if refusal is not None:
            return refusal
        try:
            values = validate_values(body)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        from mc.blueprints import settings_routes
        try:
            write_json_atomic(config_path if config_path is not None else settings_routes.CONFIG_PATH,
                              {**state.CONFIG, **values}, indent=2, ensure_ascii=False)
        except OSError as exc:
            _log(f'[agent-mail-deployment] config save failed: {exc}', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503
        # Commit in-memory state only after the durable atomic write succeeds.
        state.CONFIG.update(values)
        return jsonify({'ok': True, **_snapshot()})

    return bp
