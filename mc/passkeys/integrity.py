"""Registry integrity key (docs/PASSKEYS_SPEC.md, "Slice 2 prerequisites" item 1).

The registry and the enrollment marker are plain files a process running as the
same OS user can rewrite, so a passkey an attacker enrolled would verify. Each
file therefore carries an HMAC-SHA256 under a key derived from the vault's
master key. The key exists only while the passphrase-locked vault is unlocked,
so a shell that never saw the passphrase cannot produce a registry the server
accepts, and a server whose vault is locked cannot read or write one.

The derived key is never cached here: every use goes back to the vault, so a
manual or idle relock takes effect on the very next passkey read.

Only a vault in passphrase-lock mode counts. A vault with no passphrase keeps
its master key in the OS keyring / a DPAPI mirror / a key file, all readable by
the same user, so a MAC under that key would be exactly the unstated claim the
spec forbids. That state reports `vault_not_configured`, never "fine".

Known limits (also in the spec): a MAC does not stop replay of an OLDER registry
and marker pair, and cannot see deletion of both files. Replay can only bring
back credentials the owner once enrolled; deletion reads as a fresh install,
where the dashboard passcode applies as it does with no passkey at all.
"""
from __future__ import annotations

import hashlib
import hmac
import json

_KEY_INFO = b'clayrune-passkey-registry-mac-v1'


class IntegrityUnavailable(Exception):
    """The MAC key cannot be had right now. `reason` is `vault_locked` or
    `vault_not_configured`; callers report it, they never read around it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def mac_key() -> bytes:
    """32-byte subkey derived from the unlocked vault's master key, or
    IntegrityUnavailable. Never mints a vault key: the lock state is checked
    first, and the key is read with `peek_passphrase_key`, which neither mints
    nor counts as a vault use (only real secret use refreshes the idle clock)."""
    from mc import secrets_store as vault
    vault.check_idle_lock()                 # relock quietly rather than via a "job blocked" push
    state = vault.lock_state()
    if state == 'locked':
        raise IntegrityUnavailable('vault_locked')
    if state != 'unlocked':
        raise IntegrityUnavailable('vault_not_configured')
    master = vault.peek_passphrase_key()    # not load_master_key: reading must not restart the idle clock
    if master is None:                      # relocked between the check and the read
        raise IntegrityUnavailable(
            'vault_locked' if vault.wrapped_key_path().is_file() else 'vault_not_configured')
    return hmac.new(master, _KEY_INFO, hashlib.sha256).digest()


def _canonical(kind: str, obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != 'mac'}
    return kind.encode('ascii') + b'\n' + json.dumps(
        body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sign(kind: str, obj: dict, key: bytes) -> str:
    """Hex MAC over `obj` minus its own `mac` field, domain-separated by `kind`
    so a registry MAC can never validate as a marker or the reverse."""
    return hmac.new(key, _canonical(kind, obj), hashlib.sha256).hexdigest()


def verify(kind: str, obj: dict, key: bytes) -> bool:
    mac = obj.get('mac')
    return isinstance(mac, str) and hmac.compare_digest(mac, sign(kind, obj, key))
