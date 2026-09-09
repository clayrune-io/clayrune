"""
Identity mirror — mc_remote/identity_mirror.py + device_keys.py.

Guards the 2026-09-08 outage: a KB5124008 servicing reboot emptied the ENTIRE
Windows Credential Manager vault (both %APPDATA% and %LOCALAPPDATA%
Credentials dirs, confirmed by matching mtimes; `cmdkey /list` returned NONE).
`keyring.get_password()` on a wiped vault returns None — it does not raise —
so without a second copy of the identity, `device_keys.load_identity()` had
no way to tell "genuinely never enrolled" from "was enrolled, vault got wiped
out from under us." These tests pin the mirror as an actual second copy (not
just the same single point of failure with extra steps — see the module
docstring on why its encryption key is file-only, never OS keyring) and the
self-heal + explicit-disconnect behavior around it.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from keyring.errors import KeyringLocked  # noqa: E402

from mc_remote import device_keys, identity_mirror  # noqa: E402


class FakeKeyringBackend:
    """In-memory stand-in for the OS credential store. `wipe()` simulates the
    incident: every entry gone, no exception — exactly what
    `keyring.get_password()` does against an emptied vault."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.locked = False

    def get_password(self, service, key):
        if self.locked:
            raise KeyringLocked("locked")
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        if self.locked:
            raise KeyringLocked("locked")
        self.store[(service, key)] = value

    def delete_password(self, service, key):
        if self.locked:
            raise KeyringLocked("locked")
        self.store.pop((service, key), None)

    def wipe(self):
        self.store.clear()


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = FakeKeyringBackend()
    monkeypatch.setattr(device_keys.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(device_keys.keyring, "set_password", fake.set_password)
    monkeypatch.setattr(device_keys.keyring, "delete_password", fake.delete_password)
    return fake


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    """Mirror + its key live under a throwaway CLAYRUNE_HOME, never the
    developer's real ~/.clayrune."""
    monkeypatch.setenv("CLAYRUNE_HOME", str(tmp_path / ".clayrune"))


def _identity(n: int = 1) -> device_keys.DeviceIdentity:
    return device_keys.DeviceIdentity(
        device_id=f"dev-{n}",
        device_pub_b64="pub-b64-value",
        username="ron",
        hostname="ron.clayrune.io",
        enrollment_token="tok-abc",
    )


# ── Mirror module in isolation ───────────────────────────────────────────────

def test_mirror_absent_returns_none(isolated_vault):
    assert identity_mirror.read() is None


def test_mirror_write_read_round_trip(isolated_vault):
    fields = {
        "device_pub": "pub", "device_id": "id", "username": "ron",
        "hostname": "ron.clayrune.io", "enrollment_token": "tok",
        "device_priv": "priv-b64",
    }
    identity_mirror.write(fields)
    assert identity_mirror.read() == fields


def test_partial_mirror_is_treated_as_absent(isolated_vault):
    """A half-written or corrupted mirror must not resurrect a broken
    identity that would fail to sign anything."""
    identity_mirror.write({"device_pub": "pub", "device_id": "id"})  # incomplete
    assert identity_mirror.read() is None


# ── The actual incident: keystore wiped, identity survives via the mirror ──

def test_keystore_wipe_self_heals_from_mirror(fake_keyring, isolated_vault):
    """THE BUG this defends against: keyring.get_password() on a wiped vault
    returns None with no exception, so load_identity() would otherwise report
    'not enrolled' for a device that was enrolled seconds earlier."""
    identity = _identity()
    priv_b64 = base64.b64encode(b"1" * 32).decode("ascii")
    device_keys.store_identity(identity, priv_b64)
    assert device_keys.load_identity() == identity  # sanity before the wipe

    fake_keyring.wipe()  # THE INCIDENT

    restored = device_keys.load_identity()
    assert restored == identity, "vault mirror did not restore the wiped identity"
    assert device_keys.load_device_priv() == base64.b64decode(priv_b64)

    # Prove it actually SELF-HEALED the keystore (not just "the mirror
    # answers every time"): wipe the mirror too and read again. If the
    # keystore was really rewritten, this still succeeds.
    identity_mirror.clear()
    assert device_keys.load_identity() == identity, (
        "keystore was not actually restored by the read — it only worked "
        "because the mirror was still there to fall back on every time"
    )


def test_locked_keystore_is_not_treated_as_lost_enrollment(fake_keyring, isolated_vault, monkeypatch):
    """A READ FAILURE (locked / no backend) must propagate untouched, never
    fall through to the mirror — conflating it with a genuine loss would
    erase the distinction between error_code=tunnel_keystore_unavailable and
    enrolled=false/error_code=None that provider_impl.status() depends on."""
    identity = _identity()
    priv_b64 = base64.b64encode(b"2" * 32).decode("ascii")
    device_keys.store_identity(identity, priv_b64)

    consulted = []
    monkeypatch.setattr(identity_mirror, "read", lambda: consulted.append(1) or None)

    fake_keyring.locked = True
    with pytest.raises(device_keys.KeystoreUnavailable):
        device_keys.load_identity()
    assert consulted == [], "a locked keystore must not fall back to the mirror"


def test_disconnect_clears_the_mirror_so_it_does_not_resurrect(fake_keyring, isolated_vault):
    """Without clearing the mirror on disconnect, a later keystore hiccup (or
    another wipe) would silently bring a revoked device's identity back."""
    identity = _identity()
    priv_b64 = base64.b64encode(b"3" * 32).decode("ascii")
    device_keys.store_identity(identity, priv_b64)

    device_keys.clear_identity()  # user clicks "Disconnect this PC"

    assert identity_mirror.read() is None
    assert device_keys.load_identity() is None
