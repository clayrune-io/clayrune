"""Held agent-mail events: a metadata list and the two human-only review actions.

A `blocked` event freezes every later event in the same chat (see
`MailStore.claim_for_submission`: a hold in front keeps everything behind it
queued "until it is reviewed"). `MailStore.release_hold` / `reject_event` are
that review, and until this blueprint nothing but a script could reach them.

* `GET  /api/agent-mail/holds`                 closed-field metadata, never mail text.
* `POST /api/agent-mail/holds/<id>/release`    human-only: back into the pipeline.
* `POST /api/agent-mail/holds/<id>/reject`     human-only: dropped for good, content purged.

The two POSTs are gated exactly as the other human-only agent-mail writes
(`agent_mail_deployment_routes`, `agent_mail_enrollment_routes`): an unattended
agent session is refused, and the dashboard passcode must be retyped in the body
and is verified server-side. A forged Origin header or a manual-chat session
label is not a proof of a human; only the passcode is. Neither action ever runs
on a timer or on anything read from a reply.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc.agent_mail_store import (BLOCKED, IllegalTransition, MailStore, MailStoreError,
                                 _UNSETTLED)
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.unattended import is_unattended_caller

BASE = '/api/agent-mail/holds'
REJECT_REASON = 'rejected_by_owner'
MAX_HOLDS = 200
# The only columns that leave this module. `body`, `laundered`, `last_error`,
# `message_id` and `submit_evidence` are mail content or free text: never listed.
FIELDS = ('event_id', 'project_id', 'session_id', 'reason', 'received_at', 'state_at',
          'expires_at', 'body_len')


def _view(event: dict[str, Any], behind: int) -> dict[str, Any]:
    out = {key: event.get(key) for key in FIELDS}
    out['queued_behind'] = behind
    return out


def _queued_behind(store: MailStore, event: dict[str, Any]) -> int:
    """Later events in the same chat this hold is keeping from delivery."""
    marks = ','.join('?' * len(_UNSETTLED))
    with store._read() as db:
        return int(db.execute(
            'SELECT COUNT(*) FROM events WHERE project_id=? AND session_id=? AND seq>? '
            f'AND state IN ({marks})',
            (event['project_id'], event['session_id'], event['seq'], *_UNSETTLED)).fetchone()[0])


def create_blueprint(*, store_factory: Callable[[], MailStore] | None = None) -> Blueprint:
    bp = Blueprint('agent_mail_holds', __name__)

    def store() -> MailStore:
        if store_factory is not None:
            return store_factory()
        from mc import agent_mail_factory
        return agent_mail_factory.get().require_store()

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route(BASE, methods=['GET'])
    def holds():
        try:
            mail = store()
            events = mail.list_events(states=(BLOCKED,), limit=MAX_HOLDS)
            return jsonify({'holds': [_view(e, _queued_behind(mail, e)) for e in events]})
        except (MailStoreError, OSError, sqlite3.Error) as exc:
            reason = getattr(exc, 'reason', '')
            _log(f'[agent-mail-holds] list unavailable: {reason or type(exc).__name__}', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503

    @bp.route(BASE + '/<event_id>/<action>', methods=['POST'])
    def review(event_id: str, action: str):
        if action not in ('release', 'reject'):
            return jsonify({'error': 'not_found'}), 404
        if is_unattended_caller():
            return jsonify({'error': 'human_required'}), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({'error': 'invalid_request'}), 400
        refusal = _require_human_passcode(body)
        if refusal is not None:
            return refusal
        try:
            mail = store()
            event = mail.get_event(event_id)
            if event is None:
                return jsonify({'error': 'unknown_event'}), 404
            # Only a hold is reviewable here. Rejecting a ready or uncertain event
            # is a different decision from the one this screen describes.
            if event['state'] != BLOCKED:
                return jsonify({'error': 'not_held', 'state': event['state']}), 409
            if action == 'release':
                result = {'ok': True, 'state': mail.release_hold(event_id)}
            else:
                mail.reject_event(event_id, REJECT_REASON)
                result = {'ok': True, 'state': 'rejected'}
            _log(f'[agent-mail-holds] {action} {event_id[:12]} by passcode holder', flush=True)
            return jsonify(result)
        except IllegalTransition:
            return jsonify({'error': 'not_held'}), 409
        except ValueError:   # release_hold: the content was never kept, so only reject is possible
            return jsonify({'error': 'nothing_to_release'}), 409
        except (MailStoreError, OSError, sqlite3.Error) as exc:
            reason = getattr(exc, 'reason', '')
            _log(f'[agent-mail-holds] {action} failed: {reason or type(exc).__name__}', flush=True)
            return jsonify({'error': 'storage_unavailable'}), 503

    return bp
