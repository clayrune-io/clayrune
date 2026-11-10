"""Offline-root manifest acceptance, rollback and storage races. Test-only keys."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from mc.agent_mail_service_manifest import ManifestRefused, ManifestState, verify_service_manifest

NOW = 1_794_249_600_000
ORIGIN = "https://mail.example.com"


class Store:
    def __init__(self): self.value = None
    def load(self, origin): return self.value
    def compare_and_set(self, origin, expected, value):
        if self.value != expected: return False
        self.value = value
        return True


@pytest.fixture
def fixture():
    root = Ed25519PrivateKey.generate()
    service = Ed25519PrivateKey.generate()
    enc = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    pub = lambda key: enc(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    payload = ["clayrune-mail-service-manifest", "1", ORIGIN, "1", str(NOW - 1000), str(NOW + 100_000),
               "service1", pub(service), str(NOW - 1000), str(NOW + 200_000), []]
    store = Store()
    def verify(p=None, key=root, **over):
        p = payload if p is None else p
        signature = enc(key.sign(json.dumps(p, separators=(",", ":"), ensure_ascii=True).encode("ascii")))
        return verify_service_manifest(p, signature, **{"root_public_key": pub(root), "service_origin": ORIGIN,
            "store": store, "now_ms": NOW, "enabled": True, **over})
    return payload, store, verify, service


def test_valid_manifest_candidate_only_and_exact_retry(fixture):
    payload, store, verify, _ = fixture
    candidate = verify()
    assert candidate.origin == ORIGIN and candidate.key_id == "service1" and candidate.sequence == 1
    raw = base64.urlsafe_b64decode(payload[7] + "=")
    assert candidate.fingerprint == hashlib.sha256(raw).hexdigest()
    assert store.value.sequence == 1
    assert verify() == candidate


def test_disabled_before_store_or_crypto(fixture):
    _, store, verify, _ = fixture
    with pytest.raises(ManifestRefused, match="manifest_disabled"): verify(enabled=False)
    assert store.value is None


def test_higher_sequence_persists_then_rejects_rollback_and_equal_conflict(fixture):
    payload, store, verify, _ = fixture
    verify()
    higher = [*payload]; higher[3] = "2"
    verify(higher)
    with pytest.raises(ManifestRefused, match="manifest_rollback"): verify()
    conflict = [*higher]; conflict[6] = "different"
    with pytest.raises(ManifestRefused, match="manifest_rollback"): verify(conflict)
    assert store.value.sequence == 2


def test_operational_key_cannot_sign_its_replacement_manifest(fixture):
    _, store, verify, service = fixture
    with pytest.raises(ManifestRefused, match="manifest_signature"): verify(key=service)
    assert store.value is None


@pytest.mark.parametrize("index,value", [
    (0, "dashboard"), (1, "2"), (2, "https://evil.example.com"), (3, "0"), (3, "01"),
    (3, 1), (4, str(NOW + 1)), (5, str(NOW)), (6, "bad/key"), (7, "bad"),
    (8, str(NOW + 1)), (9, str(NOW)), (10, ["service1"]), (10, ["z", "a"]), (10, ["a", "a"]),
])
def test_invalid_manifest_never_updates_floor(fixture, index, value):
    payload, store, verify, _ = fixture
    changed = [*payload]; changed[index] = value
    with pytest.raises((ManifestRefused, ValueError)): verify(changed)
    assert store.value is None


def test_storage_error_does_not_reset_floor_or_return_candidate(fixture):
    _, store, verify, _ = fixture
    def fail(*args): raise OSError("disk unavailable")
    store.load = fail
    with pytest.raises(OSError): verify()
    store.load = lambda origin: ManifestState(1, "a" * 64)
    store.compare_and_set = fail
    with pytest.raises((OSError, ManifestRefused)): verify()


def test_concurrent_higher_sequence_refuses_stale_candidate(fixture):
    _, store, verify, _ = fixture
    def race(origin, expected, state):
        store.value = ManifestState(2, "b" * 64)
        return False
    store.compare_and_set = race
    with pytest.raises(ManifestRefused, match="manifest_state_raced"): verify()
    assert store.value.sequence == 2


def test_corrupt_persisted_floor_refused(fixture):
    _, store, verify, _ = fixture
    store.value = ManifestState(0, "bad")
    with pytest.raises(ManifestRefused, match="manifest_state_corrupt"): verify()
