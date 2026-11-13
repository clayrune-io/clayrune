"""Local route an agent calls to mail one note to the install owner (MC-1166 thin slice, U3).

``POST /api/project/<pid>/agent/mail/note {session_id, request_id, subject, body}``.
There is deliberately no recipient field: ``Outbound`` and the Worker only ever send to
the verified owner. The response is ``Outbound``'s metadata dict (intent id, state,
reason, message id), never the body, reply token or any remote error text.

Not registered anywhere by this module; unit W3 adds it beside the other child
blueprints in ``agent_mail_routes``. Dark unless ``agent_mail_outbound_enabled``.
"""
from __future__ import annotations

from typing import Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_ownership import OwnershipHeld
from mc.agent_mail_store import MailStoreError
from mc.core import _log

FLAG = 'agent_mail_outbound_enabled'
ROUTE = '/api/project/<pid>/agent/mail/note'
FIELDS = frozenset({'session_id', 'request_id', 'subject', 'body'})

# Refusal reasons are a closed, code-owned vocabulary in mc.agent_mail_outbound, so
# they are safe to return. Anything not listed is a held state: 409.
_STATUS = {'invalid_send': 400, 'session_unavailable': 404, 'project_unavailable': 404,
           'vault_locked': 503, 'outbound_full': 503, 'ownership_capacity': 503}


def _default_factory() -> Outbound:
    from mc import agent_mail_factory
    return agent_mail_factory.get().outbound()


def create_blueprint(*, factory: Callable[[], Outbound] | None = None) -> Blueprint:
    bp = Blueprint('agent_mail_note', __name__)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(ROUTE, methods=['POST'])
    def note(pid: str):
        if state.CONFIG.get(FLAG) is not True:
            return jsonify({'error': 'not_found'}), 404
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != FIELDS
                or any(not isinstance(body[key], str) for key in FIELDS)):
            return jsonify({'error': 'invalid_request'}), 400
        try:
            outbound = (factory or _default_factory)()
            prepared = outbound.prepare(pid, body['session_id'], dict(
                request_id=body['request_id'], subject=body['subject'], body=body['body'], kind='note'))
            result = outbound.send(prepared['intent_id'])
        except EnrollmentRefused as exc:
            reason = str(exc)
            return jsonify({'error': reason}), _STATUS.get(reason, 409)
        except OwnershipHeld:
            return jsonify({'error': 'ownership_capacity'}), 503
        except MailStoreError:
            return jsonify({'error': 'store_unavailable'}), 503
        except Exception:
            _log('[agent-mail] note route held: outbound_unavailable', flush=True)
            return jsonify({'error': 'outbound_unavailable'}), 503
        return jsonify(result)

    return bp
