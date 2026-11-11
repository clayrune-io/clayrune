"""Dedicated, default-disabled mail receiver. No dashboard-auth exemption."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wsgi import get_input_stream

from mc import state
from mc.agent_mail_delivery_proof import PROOF_DIRECTION, DeliveryProof
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_envelope import MAX_REQUEST_BYTES
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_signing import IngressRefused
from mc.agent_mail_store import MailStore, MailStoreError
from mc.core import _log

FLAG = 'agent_mail_ingress_enabled'
ROUTE = '/api/agent-mail/ingress'


def create_blueprint(data_dir: Path, *, store_factory: Callable[[], MailStore] | None = None,
                     proof_factory: Callable[[], DeliveryProof] | None = None) -> Blueprint:
    bp = Blueprint('agent_mail_ingress', __name__)
    # Flask's existing blueprint registry installs the separate lifecycle route
    # family without adding another concern to server.py. No operational factory.
    from mc.blueprints.agent_mail_enrollment_routes import create_blueprint as enrollment_blueprint
    bp.register_blueprint(enrollment_blueprint())
    from mc.blueprints.agent_mail_manifest_routes import create_blueprint as manifest_blueprint
    bp.register_blueprint(manifest_blueprint())  # 7e-2m: read-only verified-manifest GET

    def enrolled_store() -> MailStore:
        # The one shared, outside-repo store (7b location, external marker). It is
        # never created or recreated here, and the receiver's old data-dir default
        # (`data/agent_mail.sqlite3`) is never read or migrated. A held store
        # (missing/corrupt/version-mismatched) raises MailStoreError -> 503.
        from mc import agent_mail_factory
        return agent_mail_factory.get().require_store()

    factory = store_factory or enrolled_store

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    def proof_receipt(headers, raw):
        if proof_factory is None:
            return jsonify({'error': 'proof_unavailable'}), 503
        try:
            return jsonify(proof_factory().receive_proof(headers, raw, method=request.method, path=request.path)), 200
        except EnrollmentRefused:
            # Only the closed vault-locked refusal escapes receive_proof as this class; never auto-unlock.
            _log('[agent-mail] proof refused: vault locked', flush=True)
            return jsonify({'error': 'vault_locked'}), 503

    @bp.route(ROUTE, methods=['POST'])
    def ingress():
        if state.CONFIG.get(FLAG) is not True:
            return jsonify({'error': 'not_found'}), 404
        if request.query_string or request.mimetype != 'application/json':
            return jsonify({'error': 'invalid_request'}), 400
        # A decoded path must not conceal percent-encoding/alternate wire paths.
        original_uri = request.environ.get('RAW_URI') or request.environ.get('REQUEST_URI')
        if original_uri != ROUTE:
            return jsonify({'error': 'invalid_path'}), 400
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            return jsonify({'error': 'body_limit'}), 413
        try:
            # Works on supported Flask versions whose per-request limit is read-only.
            stream = get_input_stream(request.environ, max_content_length=MAX_REQUEST_BYTES + 1)
            raw = stream.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                return jsonify({'error': 'body_limit'}), 413
            headers = {key.lower(): value for key, value in request.headers.items()}
            if headers.get('x-mail-direction') == PROOF_DIRECTION:
                # Decided BEFORE any pin lookup: a proof can never be ingested as mail (7d-2c, section 4.1).
                return proof_receipt(headers, raw)
            receipt = MailIngress(factory()).receive(headers, raw,
                                                     method=request.method, path=request.path)
            return jsonify(receipt), 200
        except RequestEntityTooLarge:
            return jsonify({'error': 'body_limit'}), 413
        except IngressRefused as exc:
            reason = str(exc)  # only closed, code-owned reason strings
            _log(f'[agent-mail] ingress refused: {reason}', flush=True)
            return jsonify({'error': reason}), 503 if reason == 'queue_full' else 401
        except (MailStoreError, OSError):
            _log('[agent-mail] ingress storage unavailable; custody retained at Worker', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503

    return bp
