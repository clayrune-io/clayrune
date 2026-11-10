"""Dedicated human-only lifecycle blueprint, no production adapters or factory."""
from __future__ import annotations

import sqlite3
from typing import Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.agent_mail_enrollment import Enrollment
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_service_manifest import ManifestRefused
from mc.agent_mail_store import MailStoreError
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.unattended import is_unattended_caller

FLAG = 'agent_mail_enrollment_enabled'
BASE = '/api/agent-mail/enrollment'


def create_blueprint(*, factory: Callable[[], Enrollment] | None = None) -> Blueprint:
    bp = Blueprint('agent_mail_enrollment', __name__)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(BASE, methods=['GET'])
    @bp.route(BASE + '/<action>', methods=['POST'])
    def enrollment(action: str = ''):
        if state.CONFIG.get(FLAG) is not True:
            return jsonify({'error': 'not_found'}), 404
        if request.method == 'POST':
            if is_unattended_caller():
                return jsonify({'error': 'human_required'}), 403
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify({'error': 'invalid_request'}), 400
            refusal = _require_human_passcode(body)
            if refusal is not None:
                return refusal
        else:
            body = {}
        if factory is None:
            return jsonify({'error': 'operational_adapter_unavailable'}), 503
        try:
            core = factory()
            if request.method == 'GET':
                result = core.status()
            elif action in {'start', 'owner', 'key', 'repin'}:
                owner, payload, signature = body.get('owner'), body.get('manifest'), body.get('signature')
                if not isinstance(owner, str) or not isinstance(payload, list) or not isinstance(signature, str):
                    return jsonify({'error': 'invalid_request'}), 400
                result = (core.start(owner, payload, signature) if action == 'start' else
                          core.change_identity(owner, payload, signature))
            elif action == 'confirm':
                confirmation = body.get('confirmation')
                if not isinstance(confirmation, dict):
                    return jsonify({'error': 'invalid_request'}), 400
                result = core.confirm(confirmation)
            elif action == 'verify':
                code = body.get('code')
                if not isinstance(code, str):
                    return jsonify({'error': 'invalid_request'}), 400
                result = core.verify(code)
            elif action in {'resume', 'review', 'enable', 'disable', 'retry-revocations'}:
                methods = {'resume': core.resume, 'review': core.review, 'enable': core.enable,
                           'disable': core.disable, 'retry-revocations': core.retry_revocations}
                result = methods[action]()
            else:
                return jsonify({'error': 'not_found'}), 404
            return jsonify(result)
        except (EnrollmentRefused, ManifestRefused) as exc:
            _log(f'[agent-mail-enrollment] refused: {exc}', flush=True)
            return jsonify({'error': str(exc)}), 409
        except (MailStoreError, OSError, sqlite3.Error):
            _log('[agent-mail-enrollment] storage unavailable; operation not acknowledged', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503

    return bp
