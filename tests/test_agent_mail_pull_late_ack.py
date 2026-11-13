"""Recreated W02 regression: real custody/client, signed synthetic proof, no transport.

Recreated from the recorded W02 failure after the original scratch file was
lost, rather than ported. All durable state and signing keys are test-private.
"""
from contextlib import contextmanager
from dataclasses import replace
import json

import pytest

from mc.agent_mail_enrollment_keys import IdentitySeal
from mc.agent_mail_pull_client import PullClient
from mc.agent_mail_pull_schema import PullSchemaCorrupt
from mc.agent_mail_signing import IngressRefused
from test_agent_mail_pull_receiver import Harness, INSTALL, FLOW, compact, encoded, event


@pytest.fixture
def late(tmp_path):
    h = Harness(tmp_path)
    ack = h.receiver.receive(h.wire())
    assert ack is not None

    def no_transport(*args, **kwargs):
        pytest.fail('late settlement must not use transport or a socket')

    client = PullClient(h.store, IdentitySeal(tmp_path / 'seals', key=lambda: None),
        no_transport, install_id=INSTALL, reviewed_service=no_transport,
        socket_factory=no_transport)
    with h.store._read() as db:
        client._row = client._account(db)
    client._epoch, client._connection = 1, '1' * 32
    client._receiver = h.receiver
    payload = {k: v for k, v in ack['payload'].items() if k != 'lease_id'}
    payload.update(request_id=ack['request_id'], receipt_id='9' * 32, committed_ms=h.now)
    return h, client, payload


def disable(h):
    with h.store._tx() as db:
        db.execute("UPDATE mail_pull_accounts SET local_enabled=0,state='disabled'")


def proof(h, payload, **updates):
    frame = dict(frame_id='3' * 32)
    frame.update(updates)
    return h.wire(kind='ack_committed', payload=payload,
                  **frame)


def snapshot(h):
    with h.store._read() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: [dict(r) for r in db.execute(f'SELECT * FROM "{name}"')]
                for name in tables}


def assert_settled(h, payload, before):
    receipt = h.rows('mail_pull_receipts')[0]
    assert (receipt['cloud_receipt_id'], receipt['cloud_committed_ms']) == (
        payload['receipt_id'], payload['committed_ms'])
    assert h.rows('mail_pull_ack_outbox') == []
    expected = dict(before)
    expected['mail_pull_receipts'] = [dict(before['mail_pull_receipts'][0],
        cloud_receipt_id=payload['receipt_id'], cloud_committed_ms=payload['committed_ms'])]
    expected['mail_pull_ack_outbox'] = []
    assert snapshot(h) == expected


def assert_refused(h, client, raw, reason, pin=None):
    before = snapshot(h)
    with pytest.raises(IngressRefused, match='^' + reason + '$'):
        client._handle(raw, h.pin if pin is None else pin)
    assert snapshot(h) == before


@pytest.mark.parametrize('off_first', [False, True], ids=['active', 'disabled'])
def test_recreated_late_ack_settles_existing_custody(late, off_first):
    h, client, payload = late
    assert len(h.rows('events')) == len(h.rows('mail_pull_receipts')) == 1
    assert len(h.rows('mail_pull_ack_outbox')) == 1
    raw = proof(h, payload)
    assert client._verified(raw, h.pin)[1] == payload
    if off_first:
        disable(h)
    before = snapshot(h)
    try:
        client._handle(raw, h.pin)
    except IngressRefused as exc:
        # Preserve the exact base failure evidence in reported pytest output.
        print(f"recreated base refusal={exc}; cloud_receipt_id="
              f"{h.rows('mail_pull_receipts')[0]['cloud_receipt_id']}; "
              f"outbox={len(h.rows('mail_pull_ack_outbox'))}")
        raise
    assert_settled(h, payload, before)
    if not off_first:
        disable(h)  # Ack-before-off ordering preserves the same positive proof.
    account = h.rows('mail_pull_accounts')[0]
    assert (account['local_enabled'], account['state']) == (0, 'disabled')
    assert h.rows('mail_pull_receipts')[0]['cloud_receipt_id'] == payload['receipt_id']


@pytest.mark.parametrize('fence_state', ['pending', 'acked'])
@pytest.mark.parametrize('off_first', [False, True])
def test_revoke_fence_and_invalidated_consent_only_settle_evidence(late, fence_state, off_first):
    h, client, payload = late
    if off_first:
        disable(h)
    with h.store._tx() as db:
        db.execute('''INSERT INTO mail_pull_fences VALUES(
            ?,?,1,?,?,'revoke',?,?,?,?)''',
            ('4' * 32, INSTALL, FLOW, '5' * 64, fence_state,
             '6' * 32 if fence_state == 'acked' else None, h.now, h.now))
        if off_first:
            db.execute('UPDATE mail_pull_accounts SET consent_epoch=consent_epoch+1')
            db.execute("UPDATE mail_pull_generations SET state='fenced',fenced_ms=?", (h.now,))
            db.execute("UPDATE mail_pull_threads SET state='revoked'")
            db.execute("UPDATE mail_pull_receipts SET disposition='revoked'")
            db.execute("UPDATE mail_pull_ack_outbox SET state='held',reason='local_disabled'")
    client._stop.set()
    before = snapshot(h)
    client._handle(proof(h, payload), h.pin)
    assert_settled(h, payload, before)
    with h.store._read() as db:
        with pytest.raises(IngressRefused, match='local_disabled' if off_first else 'fenced'):
            client._current(db)


@pytest.mark.parametrize('mutation', ['forged', 'payload', 'expired_pin', 'stale_signature'])
def test_late_ack_still_requires_valid_signature_and_fresh_pin(late, mutation):
    h, client, payload = late
    disable(h)
    raw, pin = proof(h, payload), h.pin
    if mutation == 'expired_pin':
        pin = replace(pin, expires_at_ms=h.now)
    elif mutation == 'stale_signature':
        raw = proof(h, payload, issued_ms=h.now - 300001)
    else:
        frame = json.loads(raw)
        if mutation == 'forged':
            frame['signature'] = encoded(b'x' * 64)
        else:
            frame['payload'] = encoded(compact(dict(payload, receipt_id='8' * 32)))
        raw = compact(frame)
    assert_refused(h, client, raw, 'frame_unverified', pin)


@pytest.mark.parametrize('updates', [dict(generation=2), dict(owner_generation=2),
    dict(install_id='b' * 32), dict(epoch=2)])
def test_foreign_signed_frame_scope_refused_after_off(late, updates):
    h, client, payload = late
    disable(h)
    assert_refused(h, client, proof(h, payload, **updates), 'frame_scope')


@pytest.mark.parametrize('sql', [
    'UPDATE mail_pull_accounts SET generation=2',
    'UPDATE mail_pull_accounts SET owner_generation=2',
    "UPDATE mail_pull_accounts SET key_id='key2'",
    "UPDATE mail_pull_generations SET key_id='key2'",
    "UPDATE mail_pull_accounts SET seal_flow_id='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'",
    "UPDATE mail_pull_accounts SET service_origin='https://other.example.test'",
    'UPDATE mail_pull_generations SET manifest_sequence=2',
    "UPDATE mail_pull_generations SET manifest_digest='" + 'a' * 64 + "'",
    "UPDATE mail_pull_generations SET service_key_id='other'",
    "UPDATE mail_pull_generations SET service_public_key='" + 'a' * 43 + "'",
    'UPDATE mail_pull_connections SET epoch=2',
    "UPDATE mail_pull_connections SET connection_id='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'",
    'UPDATE mail_pull_connections SET generation=2',
    'UPDATE mail_pull_connections SET owner_generation=2',
    'UPDATE mail_pull_connections SET reauth_ms=0',
    'DELETE FROM mail_pull_connections',
])
def test_changed_durable_identity_or_connection_scope_refused(late, sql):
    h, client, payload = late
    disable(h)
    with h.store._tx() as db:
        db.execute(sql)
    assert_refused(h, client, proof(h, payload), 'stale_generation')


@pytest.mark.parametrize('updates,reason', [
    (dict(event_id='a' * 64), 'ack_conflict'),
    (dict(event_digest='a' * 64), 'ack_conflict'),
    (dict(body_digest='a' * 64), 'ack_conflict'),
    (dict(local_receipt_id='a' * 32, request_id='a' * 32), 'ack_conflict'),
    (dict(request_id='a' * 32), 'protocol_failure'),
    (dict(committed_ms=True), 'protocol_failure'),
    (dict(committed_ms=0), 'ack_conflict'),
    (dict(committed_ms=10**15), 'protocol_failure'),
])
def test_foreign_event_digests_or_local_receipt_cannot_settle(late, updates, reason):
    h, client, payload = late
    disable(h)
    assert_refused(h, client, proof(h, dict(payload, **updates)), reason)


@pytest.mark.parametrize('sql', [
    'DELETE FROM mail_pull_ack_outbox',
    "UPDATE mail_pull_ack_outbox SET event_digest='" + 'a' * 64 + "'",
    "UPDATE mail_pull_ack_outbox SET body_digest='" + 'a' * 64 + "'",
    "UPDATE mail_pull_ack_outbox SET local_receipt_id='" + 'a' * 32 + "'",
    'UPDATE mail_pull_receipts SET generation=2',
    'UPDATE mail_pull_receipts SET owner_generation=2',
    "UPDATE mail_pull_receipts SET cloud_receipt_id='" + '8' * 32 + "',cloud_committed_ms=1",
    'UPDATE mail_pull_receipts SET cloud_committed_ms=1',
])
def test_missing_or_conflicting_durable_custody_intent_refused(late, sql):
    h, client, payload = late
    disable(h)
    with h.store._tx() as db:
        db.execute(sql)
    reason = 'schema_corrupt' if 'SET generation=2' in sql else 'ack_conflict'
    if reason == 'schema_corrupt':
        # A receipt referencing a nonexistent generation/thread is invalid storage.
        before = snapshot(h)
        with pytest.raises(PullSchemaCorrupt):
            client._handle(proof(h, payload), h.pin)
        assert snapshot(h) == before
    else:
        assert_refused(h, client, proof(h, payload), reason)


def test_exact_duplicate_ack_after_off_is_idempotent_and_conflict_refused(late):
    h, client, payload = late
    raw = proof(h, payload)
    client._handle(raw, h.pin)
    disable(h)
    before = snapshot(h)
    client._handle(raw, h.pin)  # Same frame is still authenticated before dedup.
    client._handle(proof(h, payload, frame_id='4' * 32), h.pin)
    assert snapshot(h) == before
    forged = json.loads(raw)
    forged['signature'] = encoded(b'x' * 64)
    assert_refused(h, client, compact(forged), 'frame_unverified')
    assert_refused(h, client, proof(h, dict(payload, receipt_id='8' * 32),
        frame_id='5' * 32), 'ack_conflict')
    assert_refused(h, client, proof(h, dict(payload, committed_ms=h.now + 1),
        frame_id='6' * 32), 'ack_conflict')


def test_old_connection_cannot_settle_after_valid_generation_switch(late):
    h, client, payload = late
    disable(h)
    with h.store._tx() as db:
        db.execute("UPDATE mail_pull_generations SET state='fenced',fenced_ms=?", (h.now,))
        db.execute('''INSERT INTO mail_pull_generations
            SELECT install_id,2,owner_generation,key_id,seal_flow_id,manifest_sequence,
            manifest_digest,service_key_id,service_public_key,'current',created_ms,NULL
            FROM mail_pull_generations WHERE generation=1''')
        db.execute('UPDATE mail_pull_accounts SET generation=2')
        db.execute('UPDATE mail_pull_connections SET generation=2,epoch=2')
    assert_refused(h, client, proof(h, payload), 'stale_generation')


def test_proof_cannot_crosswire_two_existing_custody_receipts(late):
    h, client, payload = late
    second = h.receiver.receive(h.wire(e=event(incoming_message_id='second@example.test', seq=2)))
    assert second is not None
    disable(h)
    assert_refused(h, client, proof(h, dict(payload,
        event_id=second['payload']['event_id'], event_digest=second['payload']['event_digest'])), 'ack_conflict')


def test_settlement_cannot_accept_new_offers_send_or_restore_authority(late):
    h, client, payload = late
    disable(h)
    client._handle(proof(h, payload), h.pin)
    before = snapshot(h)
    assert_refused(h, client, h.wire(e=event(incoming_message_id='new@example.test', seq=2),
        frame_id='4' * 32), 'local_disabled')
    for action in (client._replay, client._authority,
                   lambda: client._send('resume', dict(after_seq=0))):
        with pytest.raises(IngressRefused, match='^local_disabled$'):
            action()
    assert snapshot(h) == before
    assert (h.rows('mail_pull_accounts')[0]['local_enabled'],
            h.rows('mail_pull_accounts')[0]['state']) == (0, 'disabled')


def test_proof_storage_and_outbox_retirement_roll_back_together(late, monkeypatch):
    h, client, payload = late
    disable(h)
    before, original = snapshot(h), h.store._tx

    @contextmanager
    def fail_commit():
        with original() as db:
            yield db
            assert db.execute('SELECT cloud_receipt_id FROM mail_pull_receipts').fetchone()[0] == payload['receipt_id']
            assert db.execute('SELECT COUNT(*) FROM mail_pull_ack_outbox').fetchone()[0] == 0
            raise RuntimeError('synthetic commit failure')

    monkeypatch.setattr(h.store, '_tx', fail_commit)
    with pytest.raises(RuntimeError, match='synthetic commit failure'):
        client._handle(proof(h, payload), h.pin)
    assert snapshot(h) == before
