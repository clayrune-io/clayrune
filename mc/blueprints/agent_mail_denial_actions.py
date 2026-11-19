"""Pull denial actions called after the enrollment route checks the passcode."""
from __future__ import annotations

from typing import Any, Callable

from flask import jsonify

from mc import agent_mail_supervisor
from mc.agent_mail_disable import Disable
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_factory import MailFactory
from mc.agent_mail_signup import I

DENIAL_ACTIONS = frozenset({'disable', 'retry-revocations'})


def _install_for_denial(factory: MailFactory) -> str:
    # The checked v1 store has a single identity. Local denial remains
    # available even when its service pin or vault cannot be used.
    with factory.require_pull_store()._read() as db:
        ids = db.execute('SELECT install_id FROM mail_pull_accounts LIMIT 2').fetchall()
    if len(ids) != 1:
        raise EnrollmentRefused('schema_corrupt' if ids else 'registration_inactive')
    return ids[0]['install_id']


def _denial(factory: MailFactory, authorize: Callable[[], bool]) -> Disable:
    def stop_owned() -> bool:
        supervisor = agent_mail_supervisor.get()
        return supervisor is None or supervisor.factory is not factory or supervisor.stop()

    return Disable(factory, install_id=_install_for_denial(factory), authorize=authorize, stop=stop_owned)


def denial_action(factory: MailFactory, action: str, body: dict[str, Any], authorize: Callable[[], bool]):
    if action == 'retry-revocations':
        if set(body) != {'passcode'}:
            return jsonify({'error': 'invalid_request'}), 400
        result = _denial(factory, authorize).retry_revocations()
    else:
        op_id = body.get('op_id')
        if set(body) != {'passcode', 'op_id'} or not isinstance(op_id, str) or not I.fullmatch(op_id):
            return jsonify({'error': 'invalid_request'}), 400
        result = _denial(factory, authorize).disable(op_id)
    return jsonify({'protocol': 'pull', **result})
