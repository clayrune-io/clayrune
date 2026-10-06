"""Stand-in for the unlocked vault in the passkey unit tests.

`mc.passkeys.integrity.mac_key` reads the real vault; every passkey test points
it at a fixed key instead so it never touches (or depends on) the real vault, the
OS keyring or `~/.clayrune`. Tests that exercise the locked / unconfigured / real
vault paths do not use this and build their own (tests/test_passkeys_integrity.py).
"""
from __future__ import annotations

TEST_KEY = bytes(range(32))


def use_test_key(monkeypatch, key: bytes = TEST_KEY) -> None:
    from mc.passkeys import integrity
    monkeypatch.setattr(integrity, 'mac_key', lambda: key)
