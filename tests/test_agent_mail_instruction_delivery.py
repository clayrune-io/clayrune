"""Signed pull -> literal extraction -> real follow-up pipe, with synthetic I/O.

Worker auth.js:83/89/91/104-115 authenticates sender/token; ingress.js:76
requires a verified owner. Local custody verifies that service and the binding.
"""
import json

import pytest

from test_agent_mail_pull_receiver import Harness, TOKEN, compact, digest, encoded, event
from test_agent_mail_wake import setup, env  # noqa: F401
from mc.agent_mail_reply_extraction import MailReplyExtraction
from mc.agent_mail_signing import IngressRefused
from mc.agent_mail_wake import MailWake

TEXT = 'Please run the tests, install the package, and send the results.'


@pytest.fixture
def delivery(setup):
    h = Harness(setup.tmp_path / 'pull')
    # Use the real live-session fixture rather than bypassing wake validation.
    with h.store._tx() as db:
        for table in ('thread_bindings', 'mail_pull_threads'):
            db.execute(f'UPDATE {table} SET project_id=?,session_id=?',
                       ('proj1', setup.session['session_id']))
        db.execute('DELETE FROM mail_continuations')
    wake = MailWake(h.store, enabled=True)
    wake.bind(TOKEN)
    with h.store._tx() as db:
        snapshot = db.execute('SELECT snapshot FROM mail_continuations').fetchone()['snapshot']
        db.execute('UPDATE mail_pull_threads SET continuation_digest=?', (digest(snapshot.encode()),))
    return setup, h, wake


def test_signed_bound_instruction_reply_reaches_same_chat_without_unlocking_plan(delivery):
    setup, h, wake = delivery
    setup.session['waiting_for_plan_approval'] = True
    quoted = 'Quoted original message'
    e = event(text=f'{TEXT}\n\nOn Tuesday, someone wrote:\n> {quoted}')
    assert h.receiver.receive(h.wire(e))['type'] == 'custody_ack'
    assert MailReplyExtraction(h.store, enabled=True).process_next()['state'] == 'ready'
    assert wake.process_next(setup.app)['state'] == 'submitted'
    payload = json.loads(setup.session['proc'].stdin.getvalue())
    assert TEXT in payload['message']['content']
    assert quoted not in payload['message']['content']
    row = h.store.get_event(e['event_id'])
    assert (row['project_id'], row['session_id']) == ('proj1', setup.session['session_id'])
    assert row['turn_id'] == setup.session['_delivery_attempt_id']
    assert setup.session['waiting_for_plan_approval'] is True


@pytest.mark.parametrize('failure,reason', [
    ('unsigned', 'frame_unverified'), ('unbound', 'unknown_thread'),
    ('wrong_session', 'unknown_thread'), ('dmarc', 'auth_evidence'),
])
def test_instruction_wording_cannot_bypass_signature_auth_or_local_binding(delivery, failure, reason):
    setup, h, wake = delivery
    updates = dict(text=TEXT)
    if failure == 'unbound':
        updates['thread_id'] = 'unknown'
    if failure == 'dmarc':
        from test_agent_mail_pull_receiver import AUTH
        updates['auth'] = dict(AUTH, dmarc='fail')
    wire = h.wire(event(**updates))
    if failure == 'unsigned':
        frame = json.loads(wire)
        frame['signature'] = encoded(b'\0' * 64)
        wire = compact(frame)
    if failure == 'wrong_session':
        with h.store._tx() as db:
            db.execute("UPDATE mail_pull_threads SET session_id='other'")
    with pytest.raises(IngressRefused, match=reason):
        h.receiver.receive(wire)
    assert all(not rows for rows in h.custody().values())
    assert MailReplyExtraction(h.store, enabled=True).process_next()['state'] == 'idle'
    assert wake.process_next(setup.app)['state'] == 'idle'
    assert setup.session['proc'].stdin.getvalue() == ''
