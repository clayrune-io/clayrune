"""MC-1213: Outbound must never call reviewed_service() inside a store transaction.

A cold-cache review CASes the manifest floor on the SAME SQLite store. Nested in
Outbound's BEGIN IMMEDIATE it waited out the busy timeout and came back as
``service_unavailable`` on every send. These tests wire the REAL
``agent_mail_reviewed_service`` callable (only the HTTPS GET is faked) to the real
pull store, so the nested write is the real one.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from test_agent_mail_outbound import out, kit, setup, env, prepare, request  # noqa: F401
from test_agent_mail_pull_receiver import INSTALL
from test_agent_mail_reviewed_service import Origin, enc, pub
from mc import agent_mail_factory as fac
from mc import agent_mail_reviewed_service as rs
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_manifest import manifest_digest
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_service_manifest import ManifestState, canonical_manifest_bytes
from mc.agent_mail_service_review_schema import initialize as install_floor


class Live:
    """The production review chain over the outbound fixture's store."""

    def __init__(self, out):
        self.out, kit = out, out.kit
        pin, root = kit.pin, Ed25519PrivateKey.generate()
        self.payload = ['clayrune-mail-service-manifest', '1', pin.origin, str(pin.sequence),
                        str(kit.now - 1000), str(pin.expires_at_ms), pin.key_id, pin.public_key,
                        str(kit.now - 1000), str(pin.expires_at_ms + 3_600_000), []]
        self.digest = manifest_digest(self.payload)
        install_floor(out.store)
        # The fixture's generation row carries a placeholder digest.
        with out.store._tx() as db:
            db.execute('UPDATE mail_pull_generations SET manifest_digest=? WHERE install_id=?',
                       (self.digest, INSTALL))
        self.origin = Origin()
        signature = enc(root.sign(canonical_manifest_bytes(self.payload)))
        self.origin.reply = (200, json.dumps({'manifest': self.payload, 'signature': signature}).encode())
        factory = fac.MailFactory(out.store.path.parent / 'unused', clock=lambda: kit.now / 1000,
                                  config={rs.ORIGIN_KEY: pin.origin, rs.ROOT_KEY: pub(root)})
        factory.require_pull_store = lambda: out.store
        self.factory = factory
        self.cold()
        # ServiceClient (the transport) shares the same reviewer, as in production.
        kit.review = lambda: self.review()

    def cold(self):
        """A fresh process / expired 10-minute cache: the next review must CAS."""
        self.review = rs.build(self.factory, transport=self.origin)
        self.out.outbound.reviewed_service = lambda: self.review()

    def floor(self):
        return ManifestCAS(self.out.store).load(self.out.kit.pin.origin)


@pytest.fixture
def live(out):
    return Live(out)


@pytest.fixture
def depth(out, monkeypatch):
    """How many store transactions this thread currently holds."""
    local = threading.local()
    original = out.store._tx

    @contextmanager
    def tracked():
        with original() as db:
            local.n = getattr(local, 'n', 0) + 1
            try:
                yield db
            finally:
                local.n -= 1

    monkeypatch.setattr(out.store, '_tx', tracked)
    return lambda: getattr(local, 'n', 0)


def test_cold_cache_prepare_and_send_do_not_deadlock_on_the_floor_write(out, live):
    first = prepare(out)  # was: EnrollmentRefused('service_unavailable') after the busy timeout
    assert first['state'] == 'ready'
    assert live.floor() == ManifestState(out.kit.pin.sequence, live.digest)
    live.cold()
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'
    assert len(out.recording.calls) == 1


def test_review_is_never_called_inside_a_transaction_even_when_never_cached(out, depth):
    """Every call is treated as cold: a pre-warmed cache that expires mid-tx must not matter."""
    seen = []
    inner = out.kit.review

    def review():
        seen.append(depth())
        return inner()

    out.kit.review = review
    first = prepare(out)
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'
    assert seen and set(seen) == {0}


def test_locked_vault_is_named_and_answers_without_any_manifest_fetch(out, live):
    out.kit.master = None
    with pytest.raises(EnrollmentRefused, match='^vault_locked$'):
        prepare(out)
    assert live.origin.urls == []
    assert out.kit.rows('mail_outbound_intents') == []


def test_inactive_account_is_refused_before_any_manifest_fetch(out, live):
    with out.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET local_enabled=0')
    with pytest.raises(EnrollmentRefused, match='^consent_required$'):
        prepare(out)
    assert live.origin.urls == []


@pytest.mark.parametrize('stage', ['prepare', 'send'])
def test_floor_that_advances_after_the_review_is_refused_inside_the_transaction(out, live, stage):
    """The rail the removed in-transaction re-review carried: another refresh moved the
    durable floor between our review and our BEGIN IMMEDIATE."""
    first = prepare(out) if stage == 'send' else None
    real = live.review

    def review_then_advance():
        pair = real()
        cas = ManifestCAS(out.store)
        current = cas.load(out.kit.pin.origin)
        newer = ManifestState(out.kit.pin.sequence + 1, 'e' * 64)
        if current != newer:
            assert cas.compare_and_set(out.kit.pin.origin, current, newer)
        return pair

    live.review = review_then_advance
    with pytest.raises(EnrollmentRefused, match='^service_key_changed$'):
        if first:
            out.outbound.send(first['intent_id'])
        else:
            prepare(out)
    assert out.recording.calls == []
