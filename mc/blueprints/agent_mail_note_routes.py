"""Local route an agent calls to mail one note to the install owner (MC-1166 thin slice, U3).

``POST /api/project/<pid>/agent/mail/note {session_id, request_id, subject, body}``.
There is deliberately no recipient field: ``Outbound`` and the Worker only ever send to
the verified owner. The response is ``Outbound``'s metadata dict (intent id, state,
reason, message id), never the body, reply token or any remote error text.
A refusal is ``{error, hint}``: ``error`` is the closed reason, ``hint`` is a fixed
string from this module saying what the caller should do. (The flag-off 404 and the
malformed-request 400 carry ``error`` only.)

Not registered anywhere by this module; unit W3 adds it beside the other child
blueprints in ``agent_mail_routes``. Dark unless ``agent_mail_outbound_enabled``.
"""
from __future__ import annotations

import time
from typing import Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_ownership import OwnershipHeld
from mc.agent_mail_store import MailStoreError
from mc.agent_mail_target import MailHeld
from mc.core import _log

FLAG = 'agent_mail_outbound_enabled'
ROUTE = '/api/project/<pid>/agent/mail/note'
FIELDS = frozenset({'session_id', 'request_id', 'subject', 'body'})
# Keep below send_mail.py's agent-mail timeout: its agent_mail_route helper
# uses TIMEOUT_S = 20 before falling back to Gmail.
NOTE_SEND_DEADLINE_SECONDS = 15

# Refusal reasons are a closed, code-owned vocabulary in mc.agent_mail_outbound, so
# they are safe to return. Anything not listed is a held state: 409.
# service_unavailable is transient (a manifest fetch or floor write failed), not a
# held state, so it is 503.
_STATUS = {'invalid_send': 400, 'session_unavailable': 404, 'project_unavailable': 404,
           'vault_locked': 503, 'service_unavailable': 503, 'outbound_full': 503,
           'ownership_capacity': 503}

_FALLBACK = ' Fall back to Gmail meanwhile.'
# Fixed text keyed by reason. Never interpolate exception or remote text into a hint.
_HINTS = {
    'vault_locked': "Clayrune's secrets vault is locked; the install owner must unlock it with "
                    'the passcode in Settings.' + _FALLBACK,
    'service_unavailable': 'The mail service check did not complete. Usually temporary: retry '
                           'shortly, and check the server log if it persists.' + _FALLBACK,
    'service_key_changed': 'The mail service key or manifest no longer matches the reviewed one; '
                           'the install owner must check agent-mail in Settings.' + _FALLBACK,
    'consent_required': 'Agent-mail is not enabled for this install; the install owner must '
                        'enable it in Settings.' + _FALLBACK,
    'outbound_disabled': 'Outbound agent-mail is switched off for this install.' + _FALLBACK,
    'invalid_send': 'Agent-mail does not accept this subject or body.' + _FALLBACK,
    'session_unavailable': 'No live chat matches this session_id, so a reply could not be '
                           'routed back to it.' + _FALLBACK,
    'project_unavailable': 'No project matches this project id.' + _FALLBACK,
}
_DEFAULT_HINT = 'Agent-mail did not send this note; the reason is in `error`.' + _FALLBACK


def _refusal(reason: str, status: int):
    return jsonify({'error': reason, 'hint': _HINTS.get(reason, _DEFAULT_HINT)}), status


def _default_factory() -> Outbound:
    from mc import agent_mail_factory
    return agent_mail_factory.get().outbound()


def create_blueprint(*, factory: Callable[[], Outbound] | None = None,
                     clock: Callable[[], float] = time.monotonic) -> Blueprint:
    bp = Blueprint('agent_mail_note', __name__)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(ROUTE, methods=['POST'])
    def note(pid: str):
        deadline = clock() + NOTE_SEND_DEADLINE_SECONDS
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
            result = outbound.send(prepared['intent_id'], deadline=deadline, clock=clock)
        except EnrollmentRefused as exc:
            reason = str(exc)
            return _refusal(reason, _STATUS.get(reason, 409))
        except OwnershipHeld:
            return _refusal('ownership_capacity', 503)
        except MailStoreError:
            return _refusal('store_unavailable', 503)
        except MailHeld as exc:
            # MailHeld reasons are a closed, code-owned vocabulary (agent_mail_target),
            # so naming the reason is safe and is the only way to see why a send was held.
            reason = str(exc) or 'mail_held'
            _log(f'[agent-mail] note route held: {reason}', flush=True)
            return _refusal(reason, 409)
        except Exception as exc:
            _log(f'[agent-mail] note route held: outbound_unavailable '
                 f'({type(exc).__name__}: {str(exc)[:200]})', flush=True)
            return _refusal('outbound_unavailable', 503)
        return jsonify(result)

    return bp
