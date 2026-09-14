"""Clayrune secrets vault — encrypted credential storage for agent actions.

Lets an agent perform real authenticated work (account logins, social posting,
API calls) without a credential ever entering a transcript, a memory file, a
distilled skill, or the git repo.

## Where it lives — outside the checkout, by construction

Everything is under ``~/.clayrune/`` (override with ``CLAYRUNE_HOME`` for
tests). There is deliberately NO path under the repo that can hold a secret:

    ~/.clayrune/secrets.json         ciphertext + metadata            (0600)
    ~/.clayrune/secrets.key          fallback master key, plaintext   (0600)
    ~/.clayrune/secrets.key.dpapi    DPAPI-sealed mirror, Windows-only (0600)
    ~/.clayrune/secrets_audit.jsonl  append-only access log           (0600)

That is stronger than gitignoring. This project has already been bitten once by
"gitignored but bundled anyway" — ``build-macos.spec`` packaged
``data/SHARED_RULES.md`` *because it was still on disk* after being untracked
(CLAUDE.md, 2026-07-12). A file that never exists inside the repo cannot be
swept in by a future ``git add -f``, build spec, or installer glob.

## Crypto

Master key: 32 random bytes held in the OS keyring (Windows Credential Manager
/ macOS Keychain / SecretService). A keystore wipe — see ``load_master_key()``
for the 2026-09-14 incident this defends against — needs a second copy to
self-heal from instead of silently minting a replacement over undecryptable
ciphertext, but that second copy's own at-rest protection depends on the OS:

- **Windows**: mirrored into ``secrets.key.dpapi``, sealed with DPAPI
  (user-scope ``CryptProtectData``) on every successful keyring read. DPAPI
  ties the seal to this Windows user account, so the file is not a bare
  plaintext copy of the master key even though nothing else guards it.
- **macOS / Linux**: there is no equivalent OS primitive for sealing a file to
  "this user" the way Keychain/SecretService already do for the keyring
  entry itself, so **no mirror is written** while the keyring is healthy. A
  wipe with nothing to self-heal from fails closed
  (:class:`SecretsUnavailable`) rather than degrading at-rest security to
  cover for it — see ``load_master_key()``.
- **No keyring backend usable at all** (headless Linux, typically, or a
  keyring forced off via ``CLAYRUNE_SECRETS_KEY_BACKEND=file``): the
  plaintext 0600 ``secrets.key`` file is the sole copy, exactly as before
  this module grew a Windows mirror. That degradation is recorded in the
  store so the UI can warn.

Each value is sealed with AES-256-GCM under a fresh 12-byte nonce, with the
secret's own name as additional authenticated data, so a ciphertext cannot be
moved between entries.

## Access model, and its honest limit

The agent refers to a secret by name (``{{secret:reddit.password}}``); the
*server* resolves it at the moment of use, and
:func:`tools/with-secret.py <with-secret>` injects it into a child process's
environment so the plaintext never crosses the agent's stdout.

This keeps credentials out of the durable, exfiltrating surfaces — transcripts,
MEMORY.md, distilled artifacts, logs — and makes every access auditable. It is
**not** a sandbox: an agent with a shell can read anything this process can.
The real gate is the per-task agent rules plus the per-secret ``scope`` and
``allow_unattended`` flags enforced here.

An agent may *use* a credential; only a human may create one. That mirrors the
learning-system authority guard (CLAUDE.md): machinery must never be able to
expand the agent's own capability set.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from mc import totp as _totp
from mc.core import _harden_secret_perms, _log, now_iso


# ── Errors ───────────────────────────────────────────────────────────────────

class SecretsError(Exception):
    """Base for vault failures."""


class SecretsUnavailable(SecretsError):
    """No usable crypto backend (``cryptography`` missing)."""


class SecretNotFound(SecretsError):
    """No secret by that name."""


class SecretDenied(SecretsError):
    """The secret exists but this caller may not read it."""


# ── Paths ────────────────────────────────────────────────────────────────────

KEYRING_SERVICE = 'clayrune'
KEYRING_ACCOUNT = 'secrets-master-key'

STORE_VERSION = 1

# Values shorter than this are not registered for output redaction — scrubbing
# a 3-character string out of agent output would corrupt unrelated text far
# more often than it would protect anything.
MIN_REDACTABLE_LEN = 6


def clayrune_home() -> Path:
    """``~/.clayrune`` — the operator-state dir, deliberately outside the repo."""
    override = os.environ.get('CLAYRUNE_HOME')
    if override:
        return Path(override)
    home = (os.environ.get('USERPROFILE')
            or os.environ.get('HOME')
            or str(Path.home()))
    return Path(home) / '.clayrune'


def store_path() -> Path:
    return clayrune_home() / 'secrets.json'


def key_file_path() -> Path:
    return clayrune_home() / 'secrets.key'


def dpapi_mirror_path() -> Path:
    """Windows-only DPAPI-sealed mirror of the master key. Never written on
    other platforms — see the module docstring."""
    return clayrune_home() / 'secrets.key.dpapi'


def audit_path() -> Path:
    return clayrune_home() / 'secrets_audit.jsonl'


# Serializes read-modify-write of the store and appends to the audit log.
_lock = threading.RLock()


# ── Name validation ──────────────────────────────────────────────────────────

# Lowercase, dot-namespaced: `reddit.password`, `openai.api-key`. Rejects
# whitespace, slashes, and uppercase so a name is unambiguous inside a
# `{{secret:...}}` placeholder and safe as a JSON key.
_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')

_PLACEHOLDER_RE = re.compile(r'\{\{\s*secret:\s*([a-z0-9][a-z0-9._-]{0,63})\s*\}\}')

# `{{totp:name}}` yields a freshly generated 6-digit code rather than the stored
# seed. Deliberately a different keyword: the seed itself must never be
# substitutable into a login form, and a caller that types the wrong one should
# get an error, not a silently useless value.
_TOTP_PLACEHOLDER_RE = re.compile(r'\{\{\s*totp:\s*([a-z0-9][a-z0-9._-]{0,63})\s*\}\}')

# `{{user:name}}` yields the username stored alongside the password. A login
# needs both halves, and splitting them across two entries makes the pairing
# implicit — nothing stops `site.user` and `site.password` drifting apart. The
# username is metadata, not ciphertext: it is an identifier the site shows back
# to you, it is what makes two entries distinguishable in the list, and treating
# it as a secret would mean no route could display it. Same call as TOTP's
# `account`, which is already returned in the clear.
_USER_PLACEHOLDER_RE = re.compile(r'\{\{\s*user:\s*([a-z0-9][a-z0-9._-]{0,63})\s*\}\}')

KIND_PASSWORD = 'password'
KIND_TOTP = 'totp'


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ''))


# ── Master key ───────────────────────────────────────────────────────────────

def _keyring_disabled() -> bool:
    """Force the file backend — set by tests, and by operators on boxes where
    the keyring prompts interactively (which would hang a headless server)."""
    return str(os.environ.get('CLAYRUNE_SECRETS_KEY_BACKEND', '')).lower() == 'file'


def _keyring_get() -> str | None:
    if _keyring_disabled():
        return None
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception as e:
        _log(f"[secrets] keyring read failed, falling back to key file: {e}")
        return None


def _keyring_set(value: str) -> bool:
    if _keyring_disabled():
        return False
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)
        return True
    except Exception as e:
        _log(f"[secrets] keyring write failed, falling back to key file: {e}")
        return False


def _read_key_file() -> str | None:
    p = key_file_path()
    try:
        if p.is_file():
            return p.read_text(encoding='utf-8').strip() or None
    except OSError as e:
        _log(f"[secrets] key file read failed: {e}")
    return None


def _write_key_file(value: str) -> None:
    p = key_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_private_text(p, value)


def _remove_key_file(reason: str) -> None:
    """Delete the plaintext key-file mirror, best-effort, and log why. Called
    only once a stronger copy is in place (DPAPI mirror, verified) or once
    none is needed at all (a healthy keyring on an OS with no sealed-mirror
    option) — never speculatively."""
    p = key_file_path()
    try:
        if p.is_file():
            p.unlink()
            _log(f"[secrets] removed plaintext master-key mirror ({reason})")
    except OSError as e:
        _log(f"[secrets] could not remove stale plaintext master-key mirror: {e}")


# ── DPAPI-sealed mirror (Windows only) ──────────────────────────────────────
#
# Windows Data Protection API seals arbitrary bytes to "this OS user, this
# machine" without needing a key of our own to manage — the exact primitive
# identity_mirror.py's docstring notes doesn't exist for its own key file
# either. No new dependency: `ctypes.windll.crypt32` is stdlib, same pattern
# already used in mc/process_ledger.py and mc/blueprints/agent_routes.py.
# `pywin32` is not in requirements.txt and this doesn't need it.

def _dpapi_available() -> bool:
    """True only on Windows. A plain platform check, not a capability probe —
    if DPAPI is somehow broken on a Windows box, `_dpapi_protect`/`_unprotect`
    raise and callers treat that as mirror-unavailable, same as any other
    OSError from this section."""
    return os.name == 'nt'


class _DpapiBlob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_uint32), ('pbData', ctypes.c_void_p)]


def _dpapi_crypt32_and_kernel32():
    """`crypt32`/`kernel32` handles with explicit argtypes/restype.

    Without these, ctypes falls back to guessing a plain ``c_int`` for
    ``LocalFree``'s pointer argument, which overflows on 64-bit addresses
    (measured: ``ArgumentError: int too long to convert``). Declared once
    per call rather than at import time so importing this module on a
    non-Windows OS never touches ``ctypes.windll`` (which doesn't exist
    there) — callers already gate on `_dpapi_available()` first.
    """
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    blob_p = ctypes.POINTER(_DpapiBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_p, ctypes.c_wchar_p, blob_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, blob_p]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [
        blob_p, ctypes.POINTER(ctypes.c_wchar_p), blob_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_uint32, blob_p]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _dpapi_protect(data: bytes) -> bytes:
    """Seal ``data`` with user-scope DPAPI. Windows-only; callers must check
    `_dpapi_available()` first."""
    crypt32, kernel32 = _dpapi_crypt32_and_kernel32()
    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _DpapiBlob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    out_blob = _DpapiBlob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), 'clayrune-secrets-master-key', None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise OSError(f'CryptProtectData failed (error {ctypes.get_last_error()})')
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    """Reverse of `_dpapi_protect`. Windows-only; callers must check
    `_dpapi_available()` first."""
    crypt32, kernel32 = _dpapi_crypt32_and_kernel32()
    buf = ctypes.create_string_buffer(blob, len(blob))
    in_blob = _DpapiBlob(len(blob), ctypes.cast(buf, ctypes.c_void_p))
    out_blob = _DpapiBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError(f'CryptUnprotectData failed (error {ctypes.get_last_error()})')
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _read_dpapi_mirror() -> str | None:
    """The base64-encoded key from the DPAPI-sealed mirror, or ``None`` if
    absent, unreadable, or the seal can no longer be opened (wrong user
    profile, corrupted file) — never raises, so callers can treat it exactly
    like a missing mirror rather than a hard failure."""
    p = dpapi_mirror_path()
    try:
        if not p.is_file():
            return None
        sealed = p.read_bytes()
    except OSError as e:
        _log(f"[secrets] DPAPI mirror read failed: {e}")
        return None
    try:
        return _dpapi_unprotect(sealed).decode('ascii')
    except Exception as e:
        _log(f"[secrets] DPAPI mirror failed to unseal: {e}")
        return None


def _write_dpapi_mirror(encoded: str) -> None:
    sealed = _dpapi_protect(encoded.encode('ascii'))
    _write_private_bytes(dpapi_mirror_path(), sealed)


def _maintain_key_mirror(encoded: str) -> None:
    """Best-effort self-heal-mirror upkeep after a successful keyring read.
    Must never fail the caller — a sync failure just means the next read
    tries again, whereas an exception here would break every legitimate
    credential use whenever the mirror happens to be stale.

    Windows: reseal the DPAPI mirror if it doesn't already unseal to this
    key, then remove any pre-existing PLAINTEXT ``secrets.key`` — but only
    once the DPAPI mirror has been read back and confirmed to hold the same
    key. A verification failure leaves the plaintext copy in place rather
    than deleting the only working mirror on a guess.

    macOS/Linux: there is no OS primitive equivalent to DPAPI here (Keychain
    and SecretService already *are* the keyring backend in use), so no
    mirror is written at all. Per the module docstring, a keyring wipe on
    these platforms fails closed instead of self-healing — that is the
    deliberate protection, not a gap. If a plaintext mirror exists from
    before this fix, it is removed immediately: the keyring we just read
    from is proven live, so the plaintext copy is pure exposure with no
    self-heal benefit left to justify it.
    """
    try:
        if _dpapi_available():
            if _read_dpapi_mirror() != encoded:
                _write_dpapi_mirror(encoded)
            if _read_dpapi_mirror() != encoded:
                return
            reason = 'replaced by a DPAPI-sealed mirror, verified round-trip'
        else:
            reason = ('OS keyring is healthy and this OS has no sealed local '
                      'mirror; the fail-closed check is the protection instead')
        # Only ever delete a plaintext file that holds THIS key. A file with a
        # different key may be the last copy of whatever sealed older entries
        # (the 2026-09-14 incident orphaned 8 of them) -- keep it and say so.
        existing = _read_key_file()
        if existing is None:
            return
        if existing != encoded:
            _log("[secrets] secrets.key holds a different key than the keyring; "
                 "left in place (may be the only copy of an older key)")
            return
        _remove_key_file(reason)
    except OSError as e:
        _log(f"[secrets] key mirror sync failed: {e}")


def _read_self_heal_mirror() -> str | None:
    """The best available second copy of the master key for when the keyring
    itself came back empty, tried strongest-at-rest first: the DPAPI-sealed
    mirror (Windows) before the plaintext file (the no-keyring-backend
    fallback, unchanged since before this module grew a Windows mirror)."""
    if _dpapi_available():
        encoded = _read_dpapi_mirror()
        if encoded:
            return encoded
    return _read_key_file()


def load_master_key() -> tuple[bytes, str]:
    """Return ``(key_bytes, backend)``, creating the key ONLY when the store
    has nothing sealed under it yet.

    ``backend`` is ``'keyring'`` or ``'file'`` — surfaced so the UI can warn
    when the OS keyring wasn't usable and the key is sitting on disk.

    ## The incident this guards against

    2026-09-14: a Windows keystore wipe (see ``mc_remote/identity_mirror.py``
    for the 2026-09-08 sibling incident) emptied Credential Manager.
    ``keyring.get_password()`` on a wiped vault returns ``None`` — the same
    shape as "never had a key" — so the old version of this function treated
    the wipe as first-use and minted a replacement, silently orphaning every
    secret already sealed under the old key. 8 of 10 saved logins became
    permanently undecryptable before anyone noticed; nothing logged the mint
    because the keyring branch had no log line at all.

    ## The fix: a fail-closed check before minting, plus an at-rest-honest mirror

    Before minting, we check whether the store already holds sealed secrets.
    A wipe with no mirror and existing ciphertext raises
    :class:`SecretsUnavailable` instead of quietly minting a key nothing can
    be read with — the caller (or the human at the Secrets panel) finds out
    immediately instead of losing data silently. Minting stays automatic only
    when the store is genuinely empty (a fresh install), and is now always
    logged.

    A prior version of this fix (2026-09-14, since revised) mirrored the
    master key into a plaintext 0600 ``secrets.key`` on *every* successful
    keyring read, on every OS — durable, but a silent at-rest downgrade on a
    healthy box: the key would sit in plaintext next to the ciphertext it
    protects, forever, even though nothing was wrong. This version keeps the
    self-heal *behavior* but scopes the mirror's strength to what the OS
    actually offers (see :func:`_maintain_key_mirror` and the module
    docstring): DPAPI-sealed on Windows, no mirror at all on macOS/Linux
    (fail-closed is the protection there instead), plaintext only on the
    pre-existing no-keyring-backend fallback path.
    """
    with _lock:
        encoded = _keyring_get()
        if encoded:
            _maintain_key_mirror(encoded)
            return base64.b64decode(encoded), 'keyring'

        encoded = _read_self_heal_mirror()
        if encoded:
            # Keyring came back empty (wiped, or merely locked and swallowed
            # by _keyring_get's fallback) but a mirror has the key — this is
            # the self-heal path. Reseed the keyring so future reads don't
            # depend on the mirror forever; best-effort, the mirror read
            # already succeeded.
            if _keyring_set(encoded):
                _log('[secrets] keyring had no master key; restored from '
                     'local mirror and reseeded the keyring')
            return base64.b64decode(encoded), 'file'

        # Neither the keyring nor any mirror has a key. If the store already
        # holds sealed secrets, this is a wipe with no surviving copy of the
        # key — NOT a fresh install — so minting would silently orphan every
        # one of them (the 2026-09-14 incident). Fail closed.
        store = _load_store()
        n = len(store['secrets'])
        if n:
            raise SecretsUnavailable(
                f"master key missing: {n} stored secret(s) cannot be read "
                f"(no key in the OS keyring or a local mirror) — "
                f"re-enter them or restore the key")

        # First use, store genuinely empty: mint one.
        raw = os.urandom(32)
        encoded = base64.b64encode(raw).decode('ascii')
        backend = 'keyring' if _keyring_set(encoded) else 'file'
        if backend == 'keyring':
            _maintain_key_mirror(encoded)
        else:
            # No keyring backend at all (headless Linux, or forced off via
            # CLAYRUNE_SECRETS_KEY_BACKEND=file) — the plaintext file is the
            # sole copy, exactly as before this module grew a Windows mirror.
            _write_key_file(encoded)
        _log(f"[secrets] minted new master key (backend={backend}, "
             f"store was empty)")
        return raw, backend


# ── Sealed values ────────────────────────────────────────────────────────────

def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as e:  # pragma: no cover - dependency is in requirements
        raise SecretsUnavailable(
            f"the 'cryptography' package is required for the secrets vault: {e}")
    return AESGCM(key)


def _scrypt_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES-256 key from a user passphrase (backup §4.2).
    Deliberately separate from the master-key path above: this key is never
    stored anywhere, it exists only for the lifetime of one export/import call."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    kdf = Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1)
    return kdf.derive(passphrase.encode('utf-8'))


_EXPORT_AAD = b'clayrune-vault-export-v1'


def _seal(name: str, value: str) -> dict[str, Any]:
    key = load_master_key()[0]
    nonce = os.urandom(12)
    ct = _aesgcm(key).encrypt(nonce, value.encode('utf-8'), name.encode('utf-8'))
    return {
        'nonce': base64.b64encode(nonce).decode('ascii'),
        'ciphertext': base64.b64encode(ct).decode('ascii'),
    }


def _open(name: str, rec: dict[str, Any]) -> str:
    key = load_master_key()[0]
    try:
        nonce = base64.b64decode(rec['nonce'])
        ct = base64.b64decode(rec['ciphertext'])
        return _aesgcm(key).decrypt(nonce, ct, name.encode('utf-8')).decode('utf-8')
    except SecretsUnavailable:
        raise
    except Exception as e:
        # Wrong key (keyring wiped / restored from another machine) or tampered
        # blob. Say which, without leaking anything.
        raise SecretsError(
            f"could not decrypt secret '{name}' — the master key may have "
            f"changed or the store was modified ({type(e).__name__})") from e


# ── Store I/O ────────────────────────────────────────────────────────────────

def _write_private_bytes(path: Path, data: bytes) -> None:
    """Atomic write where the *temp file* is private from the moment it exists.

    ``mc.core._atomic_write_text`` creates its temp with default permissions;
    for a key or a ciphertext store that is a (brief) exposure window, so we
    open with 0600 up front and harden both files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp{os.getpid()}')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        _harden_secret_perms(tmp)
        os.replace(tmp, path)
        _harden_secret_perms(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _write_private_text(path: Path, text: str) -> None:
    _write_private_bytes(path, text.encode('utf-8'))


def _empty_store() -> dict[str, Any]:
    return {'version': STORE_VERSION, 'secrets': {}}


def _load_store() -> dict[str, Any]:
    p = store_path()
    try:
        if not p.is_file():
            return _empty_store()
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        _log(f"[secrets] store read failed: {e}")
        raise SecretsError(f"secrets store at {p} is unreadable: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get('secrets'), dict):
        raise SecretsError(f"secrets store at {p} is malformed")
    return data


def _save_store(data: dict[str, Any]) -> None:
    _write_private_text(store_path(), json.dumps(data, indent=2))


# ── Audit ────────────────────────────────────────────────────────────────────

def _audit(event: str, **fields: Any) -> None:
    """Append one JSONL line. Never contains a secret value.

    Best-effort by design: an audit-write failure must not break a legitimate
    credential use mid-action, but it IS logged (exception-swallowing policy,
    CLAUDE.md) so a silently unwritable audit log gets noticed.
    """
    rec = {'ts': now_iso(), 'event': event}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False)
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        with _lock:
            with open(p, 'a', encoding='utf-8') as fh:
                fh.write(line + '\n')
        if not existed:
            _harden_secret_perms(p)
    except Exception as e:
        _log(f"[secrets] audit append failed ({event}): {e}")


def audit_tail(limit: int = 100) -> list[dict[str, Any]]:
    """Most-recent-first audit records."""
    p = audit_path()
    try:
        if not p.is_file():
            return []
        lines = p.read_text(encoding='utf-8').splitlines()
    except OSError as e:
        _log(f"[secrets] audit read failed: {e}")
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= max(1, limit):
            break
    return out


# ── Dispensed-value redaction ────────────────────────────────────────────────
#
# Only values this process has actually handed out are scanned for. That keeps
# redaction cheap and bounded — we never decrypt the whole store to scrub a
# chunk of output — and it means a secret that was never used cannot be
# fingerprinted through the redactor.

_dispensed: dict[str, str] = {}  # value -> secret name
_dispensed_lock = threading.Lock()


def register_dispensed(name: str, value: str) -> None:
    if not value or len(value) < MIN_REDACTABLE_LEN:
        return
    with _dispensed_lock:
        _dispensed[value] = name


def redact(text: str) -> str:
    """Replace any dispensed secret value with a named marker.

    Call this on anything headed for a transcript, a log, or MEMORY.md.
    """
    if not text:
        return text
    with _dispensed_lock:
        items = sorted(_dispensed.items(), key=lambda kv: len(kv[0]), reverse=True)
    for value, name in items:
        if value in text:
            text = text.replace(value, f'[redacted:{name}]')
    return text


def _forget_dispensed(value: str) -> None:
    with _dispensed_lock:
        _dispensed.pop(value, None)


# ── Public API ───────────────────────────────────────────────────────────────

def _public(name: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Metadata view. Never carries the value, the ciphertext, or a last-4
    preview — a partial reveal is still a reveal."""
    return {
        'name': name,
        'username': rec.get('username', ''),
        'description': rec.get('description', ''),
        'hint': rec.get('hint', ''),
        'scope': rec.get('scope', 'global'),
        'allow_unattended': bool(rec.get('allow_unattended', True)),
        'created_at': rec.get('created_at'),
        'updated_at': rec.get('updated_at'),
        'last_used_at': rec.get('last_used_at'),
        'use_count': int(rec.get('use_count', 0) or 0),
        'kind': rec.get('kind', KIND_PASSWORD),
        # A TOTP entry's params are not sensitive (they're printed next to the
        # QR code on every enrolment page) and the UI needs them to label it.
        'issuer': rec.get('issuer', ''),
        'account': rec.get('account', ''),
        'digits': int(rec.get('digits', 6) or 6),
        'period': int(rec.get('period', 30) or 30),
        'placeholder': ('{{totp:%s}}' if rec.get('kind') == KIND_TOTP
                        else '{{secret:%s}}') % name,
    }


def list_secrets(project_id: str | None = None,
                 *, check_readable: bool = False) -> list[dict[str, Any]]:
    """Metadata for every secret visible to ``project_id`` (global + that
    project's own). Pass ``None`` for the full inventory.

    ``check_readable=True`` adds a ``'readable'`` bool to each entry (see
    :func:`is_readable`) — still metadata-only, never the value itself.
    """
    with _lock:
        store = _load_store()
    out = []
    for name, rec in sorted(store['secrets'].items()):
        scope = rec.get('scope', 'global')
        if project_id is not None and scope != 'global' and scope != project_id:
            continue
        pub = _public(name, rec)
        if check_readable:
            pub['readable'] = is_readable(name)
        out.append(pub)
    return out


def is_readable(name: str) -> bool:
    """Can ``name``'s ciphertext actually be decrypted with the current
    master key? Never dispenses, audits, or bumps use-count — this is a
    health probe, not a use. Returns ``False`` (never raises) for a missing
    entry, a corrupted blob, or a master key that can no longer open it
    (the exact failure mode the 2026-09-14 silent-remint incident produced:
    the old ``/api/secrets/check`` dry-run only confirmed an entry existed
    and reported an undecryptable entry as fine).
    """
    with _lock:
        store = _load_store()
        rec = store['secrets'].get(name)
    if rec is None:
        return False
    try:
        _open(name, rec)
        return True
    except SecretsError:
        return False


def key_backend() -> str:
    """``'keyring'`` or ``'file'`` — for the UI's at-rest warning."""
    return load_master_key()[1]


def set_secret(name: str,
               value: str,
               *,
               username: str = '',
               description: str = '',
               hint: str = '',
               scope: str = 'global',
               allow_unattended: bool = True,
               kind: str = KIND_PASSWORD) -> dict[str, Any]:
    """Create or replace a secret. Human-initiated only — no agent path calls
    this (see the module docstring's authority note).

    An ``otpauth://totp/...`` URI pasted as the value is detected and unpacked
    into a TOTP entry automatically — that string is what the enrolment QR code
    encodes, so pasting it is the path of least resistance and it would
    otherwise be stored as a useless literal password.
    """
    if not valid_name(name):
        raise SecretsError(
            f"invalid secret name '{name}' — use lowercase letters, digits, "
            f"'.', '-', '_' (e.g. reddit.password)")
    if not isinstance(value, str) or value == '':
        raise SecretsError('secret value must be a non-empty string')

    extra: dict[str, Any] = {}
    if _totp.looks_like_otpauth(value):
        try:
            parsed = _totp.parse_otpauth_uri(value)
        except _totp.TotpError as e:
            raise SecretsError(str(e)) from e
        value, kind = parsed['secret'], KIND_TOTP
        extra = {'issuer': parsed['issuer'], 'account': parsed['account'],
                 'digits': parsed['digits'], 'period': parsed['period'],
                 'algorithm': parsed['algorithm']}
    elif kind == KIND_TOTP:
        # A bare base32 seed — validate now rather than at first login attempt,
        # when a bad seed looks like "the site rejected our code".
        try:
            value = _totp.normalize_secret(value)
        except _totp.TotpError as e:
            raise SecretsError(str(e)) from e

    if kind not in (KIND_PASSWORD, KIND_TOTP):
        raise SecretsError(f"unknown secret kind '{kind}'")

    with _lock:
        store = _load_store()
        existing = store['secrets'].get(name) or {}
        # A rotated value invalidates the old one for redaction purposes.
        if existing:
            try:
                _forget_dispensed(_open(name, existing))
            except SecretsError:
                pass
        rec = _seal(name, value)
        rec.update({
            'username': str(username or ''),
            'description': str(description or ''),
            'hint': str(hint or ''),
            'scope': str(scope or 'global'),
            'allow_unattended': bool(allow_unattended),
            'created_at': existing.get('created_at') or now_iso(),
            'updated_at': now_iso(),
            'last_used_at': existing.get('last_used_at'),
            'use_count': int(existing.get('use_count', 0) or 0),
            'kind': kind,
        })
        # Carry forward TOTP params on a metadata-only edit that re-seals the
        # same seed, so re-saving a SHA256/8-digit entry doesn't silently reset
        # it to the defaults and start producing wrong codes.
        for key in ('issuer', 'account', 'digits', 'period', 'algorithm'):
            if key in extra:
                rec[key] = extra[key]
            elif key in existing:
                rec[key] = existing[key]
        store['secrets'][name] = rec
        store['key_backend'] = key_backend()
        _save_store(store)

    _audit('set', name=name, scope=scope, allow_unattended=bool(allow_unattended),
           kind=kind, replaced=bool(existing))
    return _public(name, rec)


def delete_secret(name: str) -> bool:
    with _lock:
        store = _load_store()
        rec = store['secrets'].pop(name, None)
        if rec is None:
            return False
        try:
            _forget_dispensed(_open(name, rec))
        except SecretsError:
            pass
        _save_store(store)
    _audit('delete', name=name)
    return True


# ── Unattended-context auto-detection (MC-923) ──────────────────────────────
#
# tools/with-secret.py's `--unattended` flag used to be the ONLY signal
# `get_secret_value` ever saw — the calling AGENT decided whether its own
# cycle counted as unattended, and simply omitting the flag silently dodged
# `allow_unattended=False`, indistinguishable from an honestly-attended call.
#
# This section gives CLI-spawned consumers (with-secret.py today) a way to
# derive that flag from something the calling agent does not control, instead
# of trusting what it typed. `get_secret_value` itself is deliberately left
# alone: it keeps trusting whatever `unattended` value a caller passes, the
# way it always has, because not every caller runs as a Claude Code CLI
# subprocess — mc.blueprints.secrets_routes calls it directly from inside the
# Flask server process for the human-facing Secrets panel (metadata edits,
# TOTP verification), where there is no CLAUDE_CODE_SESSION_ID to find and
# auto-detecting "no session id -> unattended" would wrongly refuse a human
# clicking in the browser. Baking detection into `get_secret_value` itself
# would have fixed with-secret.py's gap by breaking that one instead.
#
# Fix shape (per steward/fence.py's STEWARD_MARKER precedent, reused rather
# than reinvented): derive unattended-ness from something the calling agent
# does not control. `CLAUDE_CODE_SESSION_ID` is set by the Claude Code CLI
# itself in every tool subprocess it spawns (confirmed empirically — distinct
# from anything a typed command line can set), the same class of "the harness
# told us, the agent didn't" signal fence.py gets from its hook payload's
# `transcript_path`. That session id is looked up against `trigger_type`,
# which MC recorded server-side at dispatch time (mc.blueprints.agent_routes:
# GET /api/session/trigger-type) — ground truth the agent process cannot
# rewrite.
#
# Fails CLOSED at every step (no session id, server unreachable, session
# unknown) — an inability to prove "this is attended" is treated as
# unattended, never the reverse. `detect_effective_unattended` ORs this with
# the caller-supplied flag, so the flag can only ADD strictness (an explicit
# opt-in, which steward code should still pass on purpose) and can never
# remove strictness that detection found on its own.

_TRIGGER_TYPE_URL = 'http://127.0.0.1:5199/api/session/trigger-type'


def _session_id_from_env() -> str:
    return (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()


def _lookup_trigger_type(claude_session_id: str) -> str | None:
    """The trigger_type MC recorded for this session, or None if the running
    server can't be reached or doesn't know the session.

    Split out from `detect_unattended_context` so tests can monkeypatch this
    one function instead of standing up a live server.
    """
    try:
        url = f'{_TRIGGER_TYPE_URL}?claude_session_id={urllib.parse.quote(claude_session_id)}'
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        _log(f"[secrets] unattended-context lookup failed: {e}")
        return None
    if not data.get('found'):
        return None
    return str(data.get('trigger_type') or 'manual')


def detect_unattended_context() -> tuple[bool, str]:
    """(is_unattended, reason) purely from server-side signals — no caller
    input. See module comment above for the fail-closed rationale. Public:
    meant to be called by CLI-spawned consumers (with-secret.py) to compute
    what they should pass as `unattended=`, not by `get_secret_value` itself."""
    sid = _session_id_from_env()
    if not sid:
        return True, 'no CLAUDE_CODE_SESSION_ID (fail-closed)'
    trigger_type = _lookup_trigger_type(sid)
    if trigger_type is None:
        return True, 'session unknown to MC (fail-closed)'
    if trigger_type == 'manual':
        return False, 'trigger_type=manual'
    return True, f'trigger_type={trigger_type}'


def detect_effective_unattended(unattended: bool) -> tuple[bool, str]:
    """OR a caller-supplied flag with auto-detection. The flag can only ADD
    strictness (a caller opting into unattended treatment on purpose); it can
    never remove strictness that detection found on its own."""
    if unattended:
        return True, 'flag'
    return detect_unattended_context()


def get_secret_value(name: str,
                     *,
                     consumer: str,
                     project_id: str | None = None,
                     unattended: bool = False) -> str:
    """Decrypt and dispense a value. Every call is audited.

    ``consumer`` is a short free-text label for the audit trail
    (``'browser-login'``, ``'with-secret'``, ``'send_mail'``). ``unattended``
    must be True for steward / scheduled cycles so ``allow_unattended`` can be
    enforced. This function trusts the value it's given as-is — CLI-spawned
    callers should compute it via `detect_effective_unattended` first (see the
    MC-923 module comment above for why that OR-with-detection step happens
    at the call site instead of in here).
    """
    with _lock:
        store = _load_store()
        rec = store['secrets'].get(name)
        if rec is None:
            _audit('denied', name=name, consumer=consumer, project=project_id,
                   unattended=unattended, reason='not_found')
            raise SecretNotFound(f"no secret named '{name}'")

        scope = rec.get('scope', 'global')
        if scope != 'global' and scope != project_id:
            _audit('denied', name=name, consumer=consumer, project=project_id,
                   unattended=unattended, reason='out_of_scope')
            raise SecretDenied(
                f"secret '{name}' is scoped to project '{scope}' and is not "
                f"available to '{project_id}'")

        if unattended and not rec.get('allow_unattended', True):
            _audit('denied', name=name, consumer=consumer, project=project_id,
                   unattended=True, reason='unattended_blocked')
            raise SecretDenied(
                f"secret '{name}' is marked attended-only and cannot be used "
                f"by an unattended cycle")

        value = _open(name, rec)
        rec['last_used_at'] = now_iso()
        rec['use_count'] = int(rec.get('use_count', 0) or 0) + 1
        _save_store(store)

    register_dispensed(name, value)
    _audit('read', name=name, consumer=consumer, project=project_id,
           unattended=unattended)
    return value


def generate_totp_code(name: str,
                       *,
                       consumer: str,
                       project_id: str | None = None,
                       unattended: bool = False) -> tuple[str, int]:
    """A live one-time code for a TOTP secret, plus its seconds of validity.

    The seed itself is decrypted, used, and dropped — it is deliberately NOT
    registered for output redaction, because the seed must never be dispensed
    anywhere that could echo it. The *code* is not registered either: it is
    public-by-design for 30 seconds and scrubbing 6 digits out of agent output
    would mangle unrelated numbers.
    """
    with _lock:
        store = _load_store()
        rec = store['secrets'].get(name)
        if rec is not None and rec.get('kind') != KIND_TOTP:
            raise SecretsError(
                f"'{name}' is not a TOTP secret — use {{{{secret:{name}}}}}")

    seed = get_secret_value(name, consumer=consumer, project_id=project_id,
                            unattended=unattended)
    # get_secret_value registers everything it hands out; a TOTP seed must not
    # stay in the redaction table, since it is never expected in output and
    # keeping it there is a needless copy of the crown jewels.
    _forget_dispensed(seed)

    period = int((rec or {}).get('period', 30) or 30)
    try:
        code = _totp.generate(seed,
                              digits=int((rec or {}).get('digits', 6) or 6),
                              period=period,
                              algorithm=(rec or {}).get('algorithm', 'SHA1'))
    except _totp.TotpError as e:
        raise SecretsError(str(e)) from e
    return code, _totp.seconds_remaining(period)


def get_username(name: str, *, project_id: str | None = None) -> str:
    """The username stored with ``name``.

    Not audited and not registered for redaction: it is metadata (see
    ``_USER_PLACEHOLDER_RE``), and scrubbing a username out of agent output
    would mangle ordinary text. Scope is still enforced, so a project-scoped
    login is not enumerable from elsewhere. ``allow_unattended`` deliberately is
    not — the password half of the same login carries that gate, so an
    unattended run that gets the username still cannot log in.
    """
    with _lock:
        store = _load_store()
        rec = store['secrets'].get(name)
    if rec is None:
        raise SecretNotFound(f"no secret named '{name}'")
    scope = rec.get('scope', 'global')
    if scope != 'global' and scope != project_id:
        raise SecretDenied(
            f"secret '{name}' is scoped to project '{scope}' and is not "
            f"available to '{project_id}'")
    username = str(rec.get('username', '') or '')
    if not username:
        raise SecretsError(
            f"secret '{name}' has no username stored — add one in the Secrets "
            f"panel, or drop the {{{{user:{name}}}}} reference")
    return username


def referenced_names(text: str) -> list[str]:
    """Secret names a piece of text refers to, in first-appearance order.

    Covers `{{secret:…}}`, `{{totp:…}}` and `{{user:…}}`.
    """
    seen: list[str] = []
    for pattern in (_PLACEHOLDER_RE, _TOTP_PLACEHOLDER_RE, _USER_PLACEHOLDER_RE):
        for m in pattern.finditer(text or ''):
            if m.group(1) not in seen:
                seen.append(m.group(1))
    return seen


def referenced_usernames(text: str) -> list[str]:
    """Names referenced specifically as ``{{user:…}}``.

    Separate from :func:`referenced_names` so a dry-run can tell "this secret
    exists" from "this secret exists *and* has a username" — the two fail in
    different places and only one of them is fixable in the Secrets panel.
    """
    seen: list[str] = []
    for m in _USER_PLACEHOLDER_RE.finditer(text or ''):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def resolve_placeholders(text: str,
                         *,
                         consumer: str,
                         project_id: str | None = None,
                         unattended: bool = False) -> tuple[str, list[str]]:
    """Substitute every ``{{secret:name}}``, ``{{totp:name}}`` and
    ``{{user:name}}`` in ``text``.

    Returns ``(resolved_text, names_used)``. Raises on the first unknown or
    denied name rather than leaving a live placeholder in a command line —
    a silently-unsubstituted ``{{secret:...}}`` would otherwise get sent
    somewhere as a literal password.
    """
    names = referenced_names(text)
    if not names:
        return text, []
    # Each name is resolved exactly once per call, so repeating a placeholder
    # doesn't inflate its use count — and, for TOTP, so every occurrence in one
    # command carries the SAME code even if the 30s window turns over mid-parse.
    values = {n: get_secret_value(n, consumer=consumer, project_id=project_id,
                                  unattended=unattended)
              for n in set(_PLACEHOLDER_RE.findall(text))}
    codes = {n: generate_totp_code(n, consumer=consumer, project_id=project_id,
                                   unattended=unattended)[0]
             for n in set(_TOTP_PLACEHOLDER_RE.findall(text))}
    users = {n: get_username(n, project_id=project_id)
             for n in set(_USER_PLACEHOLDER_RE.findall(text))}
    out = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)
    out = _TOTP_PLACEHOLDER_RE.sub(lambda m: codes[m.group(1)], out)
    out = _USER_PLACEHOLDER_RE.sub(lambda m: users[m.group(1)], out)
    return out, names


def env_for(mapping: Iterable[tuple[str, str]],
            *,
            consumer: str,
            project_id: str | None = None,
            unattended: bool = False) -> dict[str, str]:
    """Build an env dict from ``(ENV_VAR, secret_name)`` pairs."""
    return {var: get_secret_value(name, consumer=consumer,
                                  project_id=project_id, unattended=unattended)
            for var, name in mapping}


# ── Backup/export re-encrypt (docs/BACKUP_EXPORT_SPEC.md §4.2, Phase 2) ─────
#
# A backup archive must never carry a plaintext secret NOR this machine's
# master key (the destination has its own keyring entry). The answer is the
# same shape §4.2 specifies: dispense every value server-side, seal the lot
# under a user-supplied passphrase (scrypt KDF + AES-256-GCM, never stored),
# and on import write each entry back through the normal sealed-record path
# so it lands under the *destination's* own master key. No route here ever
# returns plaintext to a caller — mc/backup.py only ever sees ciphertext
# bytes, matching vault rule 2 (CLAUDE.md, "no route returns a plaintext
# value").

def export_all_for_backup(passphrase: str, *, consumer: str,
                          scope_filter: str | None = None) -> bytes:
    """Dispense every secret (optionally narrowed to one project's ``scope``)
    and return a passphrase-sealed ciphertext blob. ``scope_filter`` exists so
    a per-project export (mc/backup.py's ``export_project``) carries only that
    project's own secrets rather than the whole global vault — nothing is
    widened by transit (spec §4.2 open note)."""
    if not passphrase:
        raise SecretsError('a passphrase is required to export the vault')
    with _lock:
        store = _load_store()
        entries = []
        for name, rec in store['secrets'].items():
            if scope_filter is not None and rec.get('scope', 'global') != scope_filter:
                continue
            try:
                value = _open(name, rec)
            except SecretsError as e:
                _log(f"[secrets] export skipped '{name}': {e}")
                continue
            entries.append({
                'name': name, 'value': value,
                'username': rec.get('username', ''),
                'description': rec.get('description', ''),
                'hint': rec.get('hint', ''),
                'scope': rec.get('scope', 'global'),
                'allow_unattended': bool(rec.get('allow_unattended', True)),
                'kind': rec.get('kind', KIND_PASSWORD),
                'issuer': rec.get('issuer', ''), 'account': rec.get('account', ''),
                'digits': rec.get('digits', 6), 'period': rec.get('period', 30),
                'algorithm': rec.get('algorithm', 'SHA1'),
            })
    payload = json.dumps({'version': STORE_VERSION, 'entries': entries},
                         ensure_ascii=False).encode('utf-8')
    salt = os.urandom(16)
    key = _scrypt_key(passphrase, salt)
    nonce = os.urandom(12)
    ct = _aesgcm(key).encrypt(nonce, payload, _EXPORT_AAD)
    _audit('export', consumer=consumer, count=len(entries), scope_filter=scope_filter)
    return json.dumps({
        'salt': base64.b64encode(salt).decode('ascii'),
        'nonce': base64.b64encode(nonce).decode('ascii'),
        'ciphertext': base64.b64encode(ct).decode('ascii'),
    }).encode('utf-8')


def _import_entry_raw(entry: dict[str, Any]) -> None:
    """Write one exported entry straight into the store under THIS machine's
    master key. Deliberately bypasses ``set_secret``'s otpauth-URI sniffing —
    the value here is already the normalized secret ``_open`` returned, not
    something a human just pasted, so re-detecting it would be redundant and
    would drop the TOTP issuer/account/digits/period fields that only travel
    via this function's explicit copy below."""
    name = entry['name']
    if not valid_name(name):
        raise SecretsError(f"invalid secret name in import: {name!r}")
    with _lock:
        store = _load_store()
        rec = _seal(name, entry['value'])
        rec.update({
            'username': str(entry.get('username') or ''),
            'description': str(entry.get('description') or ''),
            'hint': str(entry.get('hint') or ''),
            'scope': str(entry.get('scope') or 'global'),
            'allow_unattended': bool(entry.get('allow_unattended', True)),
            'created_at': now_iso(), 'updated_at': now_iso(),
            'last_used_at': None, 'use_count': 0,
            'kind': entry.get('kind', KIND_PASSWORD),
        })
        for key in ('issuer', 'account', 'digits', 'period', 'algorithm'):
            if entry.get(key) is not None:
                rec[key] = entry[key]
        store['secrets'][name] = rec
        store['key_backend'] = key_backend()
        _save_store(store)
    _audit('set', name=name, scope=rec['scope'], allow_unattended=rec['allow_unattended'],
           kind=rec['kind'], replaced=False, via='import')


def import_all_from_backup(blob: bytes, passphrase: str, *, consumer: str,
                           on_collision: str = 'skip',
                           rescope: tuple[str, str] | None = None) -> dict[str, list[str]]:
    """Decrypt an ``export_all_for_backup`` blob and write every entry through
    the sealed-record path (never through set_secret's plaintext argument
    order — see ``_import_entry_raw``). ``on_collision``: ``'skip'`` (default)
    leaves an existing same-named secret untouched; ``'replace'`` overwrites it.
    ``rescope=(old_id, new_id)``: an entry scoped to ``old_id`` is rewritten to
    ``new_id`` before it's written — needed when the caller is
    mc.backup.import_project's ``import-as-copy`` path, which mints a new
    project id; without this a project-scoped secret would import under a
    scope no local project actually has, silently unusable (the same "state
    present but invisible" failure class the memory-vault remap exists to
    avoid). Returns ``{'imported': [names], 'skipped': [names]}``."""
    try:
        outer = json.loads(blob.decode('utf-8'))
        salt = base64.b64decode(outer['salt'])
        nonce = base64.b64decode(outer['nonce'])
        ct = base64.b64decode(outer['ciphertext'])
        key = _scrypt_key(passphrase, salt)
        payload = _aesgcm(key).decrypt(nonce, ct, _EXPORT_AAD)
        data = json.loads(payload.decode('utf-8'))
    except Exception as e:
        raise SecretsError(
            f'could not decrypt vault archive — wrong passphrase or corrupted archive: {e}') from e

    existing_names = {r['name'] for r in list_secrets()}
    imported: list[str] = []
    skipped: list[str] = []
    for entry in data.get('entries', []):
        if rescope and entry.get('scope') == rescope[0]:
            entry = dict(entry, scope=rescope[1])
        name = entry['name']
        if name in existing_names and on_collision == 'skip':
            skipped.append(name)
            continue
        _import_entry_raw(entry)
        imported.append(name)
    _audit('import', consumer=consumer, imported=len(imported), skipped=len(skipped))
    return {'imported': imported, 'skipped': skipped}
