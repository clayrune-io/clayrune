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
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
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


class VaultLocked(SecretsUnavailable):
    """The vault is passphrase-locked (``secrets.key.wrapped`` exists) and no
    human has unlocked it yet this process lifetime. A subclass of
    SecretsUnavailable so existing ``except SecretsUnavailable`` call sites
    keep working unchanged, but callers that want to distinguish "unlock it"
    from "something's broken" (the dashboard banner, ``tools/with-secret.py``)
    can catch this specifically."""


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


def wrapped_key_path() -> Path:
    """The passphrase-lock file (MC backlog 503edfe4). Its mere presence is
    what puts the vault in locked-by-default mode — see ``lock_state()``."""
    return clayrune_home() / 'secrets.key.wrapped'


def legacy_key_quarantine_dir() -> Path:
    """Where pre-passphrase-lock master-key copies go once retired by
    ``_quarantine_legacy_key_material`` — outside every path this module (or
    a bare ``keyring.get_password``/``open()`` one-liner using the well-known
    names) would ever read from again."""
    return clayrune_home() / 'legacy_key_quarantine'


def audit_path() -> Path:
    return clayrune_home() / 'secrets_audit.jsonl'


def exec_token_path() -> Path:
    """Per-boot random token gating ``POST /api/secrets/exec`` (MC-979,
    ``mc/blueprints/secrets_routes.py``). ``tools/with-secret.py`` falls back
    to that route when the vault is passphrase-locked in this process but was
    unlocked in the server's — the unwrapped master key lives only in the
    unlocking process's memory (see the passphrase-lock section below), so a
    separate CLI invocation can never read it directly and must ask the
    server to run the command instead. The token file is how that CLI proves
    its request is a legitimate loopback call and not a forged browser POST;
    never under the repo, never DATA_DIR, same as every other path here."""
    return clayrune_home() / 'secrets_exec_token'


# The per-boot exec token, cached in memory once minted — see
# `ensure_exec_token()`. `None` until first requested by this process.
_exec_token: str | None = None


def ensure_exec_token() -> str:
    """The per-boot random token gating ``POST /api/secrets/exec`` and
    ``POST /api/secrets/notify-vault-locked`` (MC-979).

    Minted fresh every time this process starts — never reused across a
    restart, so a token leaked from a previous boot (a stale log line, a
    crashed process's leftover file permissions) stops working the moment
    the server restarts. Persisted to `exec_token_path()` (0600) so a
    same-box CLI process started AFTER this one (``tools/with-secret.py``'s
    fallback) can read the CURRENT server's token off disk; cached in
    `_exec_token` so repeated calls within THIS process (every request to
    the gated routes) don't re-read the file.

    Call once from server.py's boot(), right after registering
    `secrets_routes.bp`, so the token exists before any request can reach
    the routes it gates. Idempotent within a process lifetime either way.
    """
    global _exec_token
    if _exec_token is not None:
        return _exec_token
    _exec_token = os.urandom(32).hex()
    _write_private_text(exec_token_path(), _exec_token)
    return _exec_token


def _read_exec_token_file() -> str:
    """Best-effort read of whatever token the CURRENTLY RUNNING server last
    wrote — used only by `_notify_vault_locked`'s out-of-process relay
    below. Never raises: a missing/unreadable file just means the relay
    can't authenticate, which is treated as "server unreachable", not an
    error of its own."""
    try:
        return exec_token_path().read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def exec_route_port() -> int:
    """Best-effort guess at the port the server's Flask app is bound to, for
    the handful of loopback-only calls made FROM a separate process
    (``tools/with-secret.py``'s exec fallback, `_notify_vault_locked`'s
    out-of-process relay below). Mirrors server.py's own ``PORT``
    computation (``MC_PORT`` env, else config.json's ``port``, else 5199)
    for the common case without importing server.py itself — a standalone
    script must not trigger the whole server's import-time side effects
    just to find a port number. Deliberately does not also read
    config.json: an operator who changed only the config file's `port`
    without also setting `MC_PORT` is already outside the common case these
    two callers exist to serve, same tradeoff `_TRIGGER_TYPE_URL` above
    already makes by hardcoding 5199.
    """
    return int(os.environ.get('MC_PORT', 5199))


# Serializes read-modify-write of the store and appends to the audit log.
_lock = threading.RLock()

# Set by load_master_key() whenever the OS keyring holds a key that decrypts
# none of the store's records — see key_mismatch() below.
_key_mismatch = False

# The master key, plaintext, held ONLY in process memory once a human unlocks
# a passphrase-locked vault (never written to disk unwrapped) — see the
# "Passphrase lock" section below. None whenever the vault is locked or
# passphrase-lock isn't configured at all.
_unlocked_key: bytes | None = None

# True once _notify_vault_locked() has already fired for the CURRENT lock
# period (since the last successful unlock) — so a burst of jobs hitting the
# lock sends one notification, not one per job. Reset to False by every
# successful unlock.
_lock_notified = False

# Monotonic timestamp (time.monotonic() — immune to wall-clock/NTP/DST jumps)
# of the last successful key use while unlocked, or of the unlock itself.
# None whenever nothing has unlocked yet this process lifetime. Drives the
# idle auto-lock below; reset by every unlock and cleared to irrelevance by
# every relock (the key is gone, so idle tracking restarts at the next
# unlock).
_last_key_use: float | None = None


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
    the keyring prompts interactively (which would hang a headless server).

    Also forced whenever ``CLAYRUNE_HOME`` is overridden (a temp home used by
    a test or a one-off probe script), unless the caller explicitly opts back
    into the real keyring with ``CLAYRUNE_SECRETS_KEY_BACKEND=keyring``. A temp
    home means a throwaway store, but the keyring is a single GLOBAL entry
    shared by every process on the box regardless of which store it's paired
    with — a probe against a temp store that finds it empty still mints into
    that real, persistent, global keyring entry and orphans whatever it was
    protecting. That is exactly what happened 2026-09-15: a redaction probe
    used ``CLAYRUNE_HOME=<mkdtemp>`` expecting isolation, minted a fresh master
    key into the real `clayrune/secrets-master-key` Credential Manager entry
    because its temp store was (correctly) empty, and the real server picked
    that key up next and could decrypt none of its 10 real secrets.
    """
    if str(os.environ.get('CLAYRUNE_SECRETS_KEY_BACKEND', '')).lower() == 'file':
        return True
    if (os.environ.get('CLAYRUNE_HOME')
            and str(os.environ.get('CLAYRUNE_SECRETS_KEY_BACKEND', '')).lower() != 'keyring'):
        return True
    return False


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


def _maintain_key_mirror(encoded: str, store: dict[str, Any]) -> None:
    """Best-effort self-heal-mirror upkeep after a successful keyring read.
    Must never fail the caller — a sync failure just means the next read
    tries again, whereas an exception here would break every legitimate
    credential use whenever the mirror happens to be stale.

    Windows: reseal the DPAPI mirror only if it doesn't already unseal to
    this key, AND EITHER the current mirror opens nothing in ``store`` OR the
    new key does open something in it (or ``store`` is empty). Resealing
    unconditionally — the pre-2026-09-15 behavior — meant any process that
    successfully reads a key from the keyring, however that key got there,
    overwrites the last surviving mirror of whatever an *older* key was
    protecting. That's exactly what a 2026-09-15 test probe would have done
    on a live server: it minted a fresh key into the real keyring because its
    own temp store was empty, and had the DPAPI mirror been writable in that
    session (S4U's DPAPI failure is the only reason it wasn't), it would have
    overwritten the one surviving copy of the key protecting 10 real secrets.
    Once resealed (or already matching), remove any pre-existing PLAINTEXT
    ``secrets.key`` — but only once the DPAPI mirror has been read back and
    confirmed to hold the same key. A verification failure leaves the
    plaintext copy in place rather than deleting the only working mirror on
    a guess.

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
            current = _read_dpapi_mirror()
            if current != encoded:
                new_opens = (not store['secrets']
                             or _key_opens_any(base64.b64decode(encoded), store))
                current_opens = bool(current) and _key_opens_any(
                    base64.b64decode(current), store)
                if new_opens and not current_opens:
                    _write_dpapi_mirror(encoded)
                else:
                    _log("[secrets] not resealing the DPAPI mirror: the new "
                         "key doesn't prove itself against the store, or the "
                         "existing mirror already does — leaving it in place")
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


def _key_opens_any(key_bytes: bytes, store: dict[str, Any]) -> bool:
    """True as soon as ``key_bytes`` decrypts ANY record in ``store`` — a
    single successful trial decrypt is enough to prove the key is live for
    this store, so this short-circuits rather than proving every record.
    Never raises: a per-record decrypt failure (wrong key, tampered blob) just
    means try the next one. Deliberately bypasses ``_open``/``load_master_key``
    (which would recurse) — this operates on a key that hasn't been decided
    on yet."""
    for name, rec in store['secrets'].items():
        try:
            nonce = base64.b64decode(rec['nonce'])
            ct = base64.b64decode(rec['ciphertext'])
            _aesgcm(key_bytes).decrypt(nonce, ct, name.encode('utf-8'))
            return True
        except Exception:
            continue
    return False


def key_mismatch() -> bool:
    """True if the last ``load_master_key()`` call found a value in the OS
    keyring that decrypted none of the store's records — the exact shape of
    the 2026-09-15 incident (a test probe minted a fresh key into the real,
    global keyring entry because its own throwaway store was empty; the real
    server then loaded that key next and could decrypt 0 of its 10 secrets).
    Metadata only — surfaced by ``GET /api/secrets`` so the UI can warn
    without ever touching a value."""
    return _key_mismatch


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

    ## The 2026-09-15 sibling incident: a WRONG key in the keyring, not a MISSING one

    The 2026-09-14 fix above only covers the keyring coming back *empty*. On
    2026-09-15 a test probe ran with ``CLAYRUNE_HOME`` pointed at a throwaway
    temp directory, expecting isolation — but the OS keyring is one GLOBAL
    entry (``clayrune/secrets-master-key``) shared by every process on the
    box regardless of which store it's paired with. The probe's temp store
    was (correctly) empty, so the fail-closed check above did not fire, and
    it minted a fresh key straight into the real keyring entry. The real
    server then read that key next and could decrypt 0 of its 10 real
    secrets — a keyring that answers, but with someone else's key. (Also
    fixed the same day: ``_keyring_disabled()`` now refuses to touch the
    keyring at all under an overridden ``CLAYRUNE_HOME`` unless
    ``CLAYRUNE_SECRETS_KEY_BACKEND=keyring`` is explicit, which closes the
    class this incident belongs to. This check is the second layer, for a
    keyring that is already wrong for some other reason.)

    So a key returned by the keyring is now trial-decrypted against one of
    the store's own records before being trusted (:func:`_key_opens_any`,
    short-circuits at the first success). If the store is non-empty and the
    key opens nothing in it, the keyring's answer is treated as untrustworthy,
    not authoritative: fall back to the local mirror, and only use it if IT
    opens something. If neither does, raise rather than hand back a key that
    silently can't read anything — the same "found out immediately instead of
    losing data silently" posture as the empty-keyring case above.

    ## Passphrase lock (MC backlog 503edfe4)

    If ``wrapped_key_path()`` exists, the vault is in passphrase-lock mode and
    NONE of the keyring/DPAPI/plaintext-file logic below runs at all — see the
    "Passphrase lock" section further down this module. The key lives only in
    ``_unlocked_key``, set by a human unlock call, and this function either
    returns it or raises :class:`VaultLocked`. That file's mere presence is
    the switch: before a human ever sets a passphrase it doesn't exist, so
    every box that hasn't opted in keeps the exact behavior above, unchanged.
    """
    global _key_mismatch
    with _lock:
        if wrapped_key_path().is_file():
            # Idle auto-lock (MC-949 follow-up): clear the key BEFORE
            # deciding, so a read that lands exactly at the idle boundary
            # never gets handed back a key that should already be gone.
            _idle_relock_if_due()
            if _unlocked_key is not None:
                _mark_key_used()
                return _unlocked_key, 'passphrase'
            _notify_vault_locked()
            raise VaultLocked(
                "vault is locked — unlock it from the dashboard "
                "(Settings > Vault) before this can be read")
        store = _load_store()
        n = len(store['secrets'])

        encoded = _keyring_get()
        if encoded:
            key_bytes = base64.b64decode(encoded)
            if n and not _key_opens_any(key_bytes, store):
                _key_mismatch = True
                _log(f"[secrets] keyring key mismatch: decrypts none of "
                     f"{n} stored secret(s) — not trusting it, trying the "
                     f"local mirror instead")
                mirror_encoded = _read_self_heal_mirror()
                if mirror_encoded:
                    mirror_key = base64.b64decode(mirror_encoded)
                    if _key_opens_any(mirror_key, store):
                        if _keyring_set(mirror_encoded):
                            _log('[secrets] keyring key mismatch; restored '
                                 'from local mirror and reseeded the keyring')
                        return mirror_key, 'file'
                raise SecretsUnavailable(
                    f"key mismatch: the OS keyring's master key decrypts "
                    f"none of {n} stored secret(s), and no local mirror "
                    f"opens one either — re-enter them or restore the "
                    f"correct key")
            _key_mismatch = False
            _maintain_key_mirror(encoded, store)
            return key_bytes, 'keyring'

        encoded = _read_self_heal_mirror()
        if encoded:
            # Keyring came back empty (wiped, or merely locked and swallowed
            # by _keyring_get's fallback) but a mirror has the key — this is
            # the self-heal path. Reseed the keyring so future reads don't
            # depend on the mirror forever; best-effort, the mirror read
            # already succeeded.
            _key_mismatch = False
            if _keyring_set(encoded):
                _log('[secrets] keyring had no master key; restored from '
                     'local mirror and reseeded the keyring')
            return base64.b64decode(encoded), 'file'

        # Neither the keyring nor any mirror has a key. If the store already
        # holds sealed secrets, this is a wipe with no surviving copy of the
        # key — NOT a fresh install — so minting would silently orphan every
        # one of them (the 2026-09-14 incident). Fail closed.
        if n:
            raise SecretsUnavailable(
                f"master key missing: {n} stored secret(s) cannot be read "
                f"(no key in the OS keyring or a local mirror) — "
                f"re-enter them or restore the key")

        # First use, store genuinely empty: mint one.
        _key_mismatch = False
        raw = os.urandom(32)
        encoded = base64.b64encode(raw).decode('ascii')
        backend = 'keyring' if _keyring_set(encoded) else 'file'
        if backend == 'keyring':
            _maintain_key_mirror(encoded, store)
        else:
            # No keyring backend at all (headless Linux, or forced off via
            # CLAYRUNE_SECRETS_KEY_BACKEND=file) — the plaintext file is the
            # sole copy, exactly as before this module grew a Windows mirror.
            _write_key_file(encoded)
        _log(f"[secrets] minted new master key (backend={backend}, "
             f"store was empty)")
        return raw, backend


# ── Passphrase lock (MC backlog 503edfe4, Ron's 2026-09-23 decision) ────────
#
# Threat model: agents run as the same OS user as the server, so a key the
# server can read unattended, an agent can read too. So the master key K is
# never written to disk unwrapped — it exists in plaintext ONLY in
# `_unlocked_key`, set by a human-only unlock call, for the life of this
# process. At rest, `secrets.key.wrapped` holds K twice: once wrapped by a KEK
# derived from a human passphrase (scrypt), once wrapped by a KEK derived from
# a recovery key Ron holds offline. Either unwrap is verified against the same
# fingerprint scheme `_key_opens_any` uses for trial decryption, so a wrong
# passphrase/recovery key is DETECTED, never silently "accepted" as a new key.
#
# This file's mere presence is the switch (see load_master_key() above): a
# box that has never had a passphrase set behaves exactly as before this
# section existed. Migration happens once, at first `set_passphrase()` call,
# by reusing load_master_key()'s own (still fully intact) keyring/file-backend
# logic to find whatever key already protects the store — never destroying
# that copy.

_WRAP_KDF_N, _WRAP_KDF_R, _WRAP_KDF_P = 2 ** 15, 8, 1
_WRAP_AAD = b'clayrune-vault-wrap-v1'
_KEY_FINGERPRINT_CONST = b'clayrune-secrets-vault-fingerprint-v1'


def _key_fingerprint(key_bytes: bytes) -> str:
    """Proves two keys are the same key without a single AES-GCM trial-decrypt
    against real ciphertext — the store may hold zero secrets (nothing to
    trial-decrypt against) and this is cheap enough to check on every unlock
    attempt. An HMAC (not a bare hash) because the input is a fixed, public
    constant: a bare SHA256 of a constant plus the key bytes invites a
    length-extension-style shortcut, whereas HMAC's key-then-hash
    construction is the standard way to key a MAC by something secret without
    leaking structure about it."""
    return hmac.new(key_bytes, _KEY_FINGERPRINT_CONST, hashlib.sha256).hexdigest()[:16]


def _wrap_leg(secret_text: str, key_bytes: bytes) -> dict[str, Any]:
    """Wrap ``key_bytes`` (the master key) under a KEK derived from
    ``secret_text`` (a human passphrase, or a normalized recovery-key
    string) — a fresh per-leg salt, so the passphrase leg and the recovery
    leg of the same wrapped file never share a KEK even if the two secrets
    happened to coincide."""
    salt = os.urandom(16)
    kek = _scrypt_key_with_params(secret_text, salt, _WRAP_KDF_N, _WRAP_KDF_R, _WRAP_KDF_P)
    nonce = os.urandom(12)
    ct = _aesgcm(kek).encrypt(nonce, key_bytes, _WRAP_AAD)
    return {
        'salt': base64.b64encode(salt).decode('ascii'),
        'n': _WRAP_KDF_N, 'r': _WRAP_KDF_R, 'p': _WRAP_KDF_P,
        'nonce': base64.b64encode(nonce).decode('ascii'),
        'ciphertext': base64.b64encode(ct).decode('ascii'),
    }


def _unwrap_leg(secret_text: str, leg: dict[str, Any]) -> bytes:
    """Reverse of :func:`_wrap_leg`. Raises ``SecretsError`` — never a bare
    crypto exception — on a wrong passphrase/recovery key or a corrupted
    file, so callers get one exception type to catch."""
    try:
        salt = base64.b64decode(leg['salt'])
        kek = _scrypt_key_with_params(
            secret_text, salt, leg.get('n', _WRAP_KDF_N),
            leg.get('r', _WRAP_KDF_R), leg.get('p', _WRAP_KDF_P))
        nonce = base64.b64decode(leg['nonce'])
        ct = base64.b64decode(leg['ciphertext'])
        return _aesgcm(kek).decrypt(nonce, ct, _WRAP_AAD)
    except SecretsUnavailable:
        raise
    except Exception as e:
        raise SecretsError(f"could not unwrap the master key: {type(e).__name__}") from e


def _scrypt_key_with_params(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode('utf-8'))


def _generate_recovery_key() -> str:
    """160 bits, Base32 (RFC 4648, no padding), grouped in 4s for readability
    — shown to the human exactly once, at `set_passphrase()` time, and never
    stored anywhere in this format (only its wrapped leg is persisted)."""
    raw = os.urandom(20)
    b32 = base64.b32encode(raw).decode('ascii').rstrip('=')
    return '-'.join(b32[i:i + 4] for i in range(0, len(b32), 4))


def _normalize_recovery_key(value: str) -> str:
    """Strip everything but the Base32 alphabet and uppercase — so a human
    can paste the key back in with or without the dashes, in any case,
    with stray whitespace, and it still matches."""
    return re.sub(r'[^A-Z2-7]', '', (value or '').upper())


def lock_state() -> str:
    """``'unconfigured'`` (no passphrase ever set — behaves exactly like the
    pre-503edfe4 vault), ``'locked'`` (configured, not yet unlocked this
    process lifetime), or ``'unlocked'``."""
    if _unlocked_key is not None:
        return 'unlocked'
    if wrapped_key_path().is_file():
        return 'locked'
    return 'unconfigured'


def is_locked() -> bool:
    return lock_state() == 'locked'


# ── Idle auto-lock + manual "Lock now" (MC-949 follow-up, Ron 2026-09-24) ───
#
# `_unlocked_key` otherwise lives for the rest of the process's life once a
# human unlocks it — the passphrase lock only ever protected a restart. Two
# ways the key now leaves memory sooner: (1) lazily, the next time anything
# calls load_master_key() after the configured idle window has elapsed
# (below, wired into load_master_key() above); (2) a background sweeper
# (start_idle_lock_sweeper) so the key is cleared even if nothing reads it in
# the meantime. `vault_idle_lock_minutes` (config.json, default 120) is the
# threshold; 0 disables auto-lock entirely, same convention as every other
# 0-disables minutes/bytes knob in this codebase.

def _monotonic() -> float:
    """Indirection so tests can inject a fake clock without real sleeps."""
    return time.monotonic()


def _idle_lock_minutes() -> float:
    """Live config read, never cached — a Settings change applies to the
    very next check, not the next restart. <= 0 disables auto-lock. Falls
    back to the config.json default if ``mc.state`` hasn't been wired (a
    script importing this module standalone, same posture as every other
    ``state.CONFIG.get(..., default)`` call site)."""
    try:
        from mc import state
        return float(state.CONFIG.get('vault_idle_lock_minutes', 120) or 0)
    except Exception:
        return 120.0


def _mark_key_used() -> None:
    """Record that the key was just read or just unlocked — the idle clock
    restarts from here."""
    global _last_key_use
    _last_key_use = _monotonic()


def _do_relock(reason: str, *, caller_addr: str = '') -> None:
    """Clear the unlocked key (no-op if already locked) and reset the
    once-per-period lock notification so the NEXT blocked job fires exactly
    one push alert for this NEW lock period — same throttle
    ``_notify_vault_locked`` already relies on. Caller must hold ``_lock``.
    ``reason`` is ``'idle'`` or ``'manual'``, recorded in both the audit log
    and the log line so a relock is traceable to which path caused it."""
    global _unlocked_key, _lock_notified
    if _unlocked_key is None:
        return
    _unlocked_key = None
    _lock_notified = False
    _audit('vault_relocked', reason=reason, caller_addr=caller_addr)
    _log(f"[secrets] vault relocked ({reason})"
         + (f" — {caller_addr}" if caller_addr else ""))


def _idle_relock_if_due() -> bool:
    """Caller must hold ``_lock``. Returns True if this call just relocked."""
    if _unlocked_key is None:
        return False
    minutes = _idle_lock_minutes()
    if minutes <= 0 or _last_key_use is None:
        return False
    if _monotonic() - _last_key_use >= minutes * 60:
        _do_relock('idle')
        return True
    return False


def check_idle_lock() -> bool:
    """Relock if the configured idle window has elapsed since the last key
    use. Safe to call anytime (locked, unconfigured, or unlocked) — a no-op
    unless currently unlocked with a positive idle threshold configured.
    Shared by the lazy check in load_master_key() and the background
    sweeper below."""
    with _lock:
        return _idle_relock_if_due()


def lock_now(*, caller_addr: str = '') -> None:
    """Human-triggered immediate lock — the dashboard's "Lock now" control
    (``POST /api/secrets/vault-lock/lock``). No-op if already locked or
    unconfigured, so a caller doesn't need to check state first."""
    with _lock:
        _do_relock('manual', caller_addr=caller_addr)


_idle_lock_stop = threading.Event()


def _idle_lock_sweep_loop() -> None:
    while not _idle_lock_stop.wait(60):
        try:
            check_idle_lock()
        except Exception as e:
            _log(f"[secrets] idle-lock sweep failed: {e}")


def start_idle_lock_sweeper() -> threading.Thread:
    """Start the background idle-lock sweeper — clears the unwrapped master
    key from memory after the configured idle window even if nothing reads
    it in the meantime (the lazy check inside load_master_key() only fires
    on a read). Call ONCE, from server.py's boot() only: nothing else in
    this module calls it, so a test or one-off script that merely imports
    mc.secrets_store never gets a background thread as a side effect — the
    same server-process-only posture as browser_routes.SWEEP_ENABLED."""
    t = threading.Thread(target=_idle_lock_sweep_loop, daemon=True,
                          name='vault-idle-lock-sweep')
    t.start()
    return t


def _notify_vault_locked() -> None:
    """Best-effort, ONE notification per lock period (see ``_lock_notified``)
    — a burst of jobs hitting a locked vault must not spam. Lazy-imports the
    push blueprint (mirrors how server.py itself reaches `_notify_push`) so
    this module never depends on the Flask app being wired up, and a
    notification failure can never turn into a *worse* error than the
    VaultLocked the caller is already about to raise.

    MC-979: this fires from `load_master_key()`, which runs in EVERY process
    that imports this module — including `tools/with-secret.py` run as a
    bare CLI, standalone scripts, and tests. Only the actual server process
    ever calls `push_mobile.wire()`; everywhere else, `PUSH_VAPID_PATH` (and
    every other path constant `_notify_push` reads) is still `None`, and
    calling it directly used to crash deep inside `open(None, ...)`
    (``expected str, bytes or os.PathLike object, not NoneType``) — silently
    swallowed by the `except Exception` below, so Ron never got the push at
    all. Detect which side of that we're on via `PUSH_VAPID_PATH` itself
    (set only by `wire()`) and, when we're not the server, ask the server to
    relay the push instead — over the same loopback+token gate
    `/api/secrets/exec` uses, since this is the same shape of "make the
    server do a privileged thing on my behalf" call.
    """
    global _lock_notified
    if _lock_notified:
        return
    _lock_notified = True
    try:
        from mc.blueprints import push_mobile as _bp_push_mobile
    except Exception as e:
        _log(f"[secrets] vault-locked notification failed: {e}")
        return
    if _bp_push_mobile.PUSH_VAPID_PATH is not None:
        # wire() has already run in THIS process — we ARE the server.
        try:
            _bp_push_mobile._notify_push(
                'Vault locked',
                'A job needs the secrets vault unlocked — open the dashboard to '
                'unlock it.')
        except Exception as e:
            _log(f"[secrets] vault-locked notification failed: {e}")
        return
    # Not the server process. Relay via loopback instead of calling
    # _notify_push directly (see docstring above) — best-effort, and this
    # must never raise or block past this point: a notification is strictly
    # secondary to the VaultLocked the caller is already about to see.
    try:
        token = _read_exec_token_file()
        if not token:
            return
        req = urllib.request.Request(
            f'http://127.0.0.1:{exec_route_port()}/api/secrets/notify-vault-locked',
            data=b'{}', method='POST',
            headers={'Content-Type': 'application/json',
                     'X-Clayrune-Exec-Token': token})
        urllib.request.urlopen(req, timeout=3).close()
    except Exception as e:
        _log(f"[secrets] vault-locked notification (out-of-process relay) failed: {e}")


def _notify_vault_tamper(action: str, caller_addr: str) -> None:
    """Fires on every ``set``/``change``/recovery-key ``unlock`` — never on a
    routine passphrase unlock, which is normal daily use and would just
    become noise. These three are the ones a hijacker of the bootstrap
    window (Wren's review of MC 503edfe4: local_auth's passcode could be
    overwritten blind, then used to reach this endpoint) would call, so each
    one must be VISIBLE to Ron even if he never opens the dashboard again —
    always-on, not best-effort-silent like ``_notify_vault_locked``'s
    once-per-period throttle. Same lazy-import, same never-worse-than-the-
    caller's-own-error posture."""
    when = now_iso()
    addr = caller_addr or 'unknown address'
    try:
        from mc.blueprints import push_mobile as _bp_push_mobile
        _bp_push_mobile._notify_push(
            'Vault passphrase changed',
            f'The vault passphrase was {action} from {addr} at {when}. '
            f'If this was not you, treat every stored secret as compromised.')
    except Exception as e:
        _log(f"[secrets] vault-tamper notification failed: {e}")


def _quarantine_legacy_key_material() -> None:
    """Retire every pre-passphrase-lock copy of the master key, once the
    wrapped key is durably written AND both its legs have been verified to
    open (called only from :func:`set_passphrase`, after that check).

    Dave's review of d3516a2 (MC 503edfe4): ``set_passphrase`` used to leave
    the OS keyring entry, the Windows DPAPI mirror, and the plaintext
    ``secrets.key`` fallback file all live — so the lock was cosmetic.
    Anyone running as this OS user (a same-user agent, not only the
    dashboard) could still fetch K straight from any of those three
    well-known locations, ignoring the lock entirely.

    Never an outright delete: file-based copies are MOVED into a
    timestamped, owner-only-permissioned quarantine directory outside every
    lookup path this module (or a bare ``keyring.get_password(...)``/
    ``open(...)`` one-liner against the well-known names) would ever
    consult again — so a bug discovered later still has something to
    recover from. The OS keyring has no per-entry "move" primitive, so its
    value is written into the same quarantine directory FIRST, and only
    then is the live entry deleted via ``keyring.delete_password`` — the
    value survives in quarantine, only the well-known-name copy is gone.

    Best-effort per copy, deliberately: one legacy location failing to
    quarantine must not unwind the wrapped-key write that already
    succeeded, and a half-retired legacy set is still strictly safer than
    the pre-fix all-of-them-live state. Every step is logged so a partial
    failure is visible, not silent."""
    ts = now_iso().replace(':', '').replace('+00:00', 'Z')
    qdir = legacy_key_quarantine_dir() / ts
    try:
        qdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log(f"[secrets] could not create legacy-key quarantine dir "
             f"({qdir}) — leaving legacy key copies in place: {e}")
        return
    if os.name == 'nt':
        # Fail CLOSED, SID-based (Wren's review of MC 503edfe4): this dir is
        # about to receive retired copies of the master key, so hardening it
        # with the bare-%USERNAME% _harden_secret_perms helper (best-effort,
        # swallows its own failure, names the grantee by a string that fails
        # open on a domain-joined box or resolves the wrong account — the
        # exact blocker D shape _write_wrapped_key already fixed for the
        # wrapped key file itself) would risk moving key material into a
        # directory left on a default/inherited ACL with no signal that it
        # happened. If the ACL can't be resolved and verified, abort the
        # quarantine and leave the legacy copies where they already are —
        # a known-working location — rather than "protect" them by moving
        # them into an unverified one.
        try:
            sid, account = _current_user_sid_and_name()
        except Exception as e:
            _log(f"[secrets] could not resolve current user SID to harden "
                 f"the legacy-key quarantine dir {qdir} — leaving legacy "
                 f"key copies in place: {e}")
            return
        ok, detail = _icacls_grant_and_verify(qdir, sid, account)
        if not ok:
            _log(f"[secrets] could not secure the legacy-key quarantine "
                 f"dir {qdir}'s ACL — leaving legacy key copies in place: "
                 f"{detail}")
            return
    else:
        _harden_secret_perms(qdir)

    kp = key_file_path()
    try:
        if kp.is_file():
            dest = qdir / kp.name
            os.replace(kp, dest)
            _harden_secret_perms(dest)
            _log(f"[secrets] quarantined plaintext key file to {dest}")
    except OSError as e:
        _log(f"[secrets] could not quarantine plaintext key file {kp}: {e}")

    dp = dpapi_mirror_path()
    try:
        if dp.is_file():
            dest = qdir / dp.name
            os.replace(dp, dest)
            _harden_secret_perms(dest)
            _log(f"[secrets] quarantined DPAPI key mirror to {dest}")
    except OSError as e:
        _log(f"[secrets] could not quarantine DPAPI key mirror {dp}: {e}")

    if not _keyring_disabled():
        try:
            import keyring
            encoded = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
            if encoded:
                dest = qdir / 'keyring_secrets-master-key.b64'
                _write_private_text(dest, encoded)
                keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
                _log(f"[secrets] quarantined OS keyring master-key entry to "
                     f"{dest} and removed the live keyring entry")
        except Exception as e:
            _log(f"[secrets] could not quarantine/remove the OS keyring "
                 f"master-key entry: {e}")


def set_passphrase(passphrase: str, *, caller_addr: str = '') -> str:
    """First-time setup only (refuses if already configured — use
    :func:`change_passphrase` instead). Migrates whatever key currently
    protects the store (via the untouched keyring/file-backend logic in
    :func:`load_master_key`, called BEFORE the wrapped file exists) into the
    new wrapped format, generates a fresh recovery key, and returns it —
    the ONLY time it is ever available in this format; the caller must show
    it to the human now.

    Once the wrapped key is written and both legs verified to open, retires
    every legacy copy of the key (OS keyring entry, DPAPI mirror, plaintext
    key file) via :func:`_quarantine_legacy_key_material` — MOVED to a
    quarantine directory, never destroyed outright (MC 503edfe4, Dave's
    review: leaving them live made the lock cosmetic). Auto-unlocks: the
    human who just typed the passphrase should not have to immediately
    retype it.
    """
    passphrase = (passphrase or '').strip()
    if not passphrase:
        raise SecretsError("passphrase is required")
    if len(passphrase) < 8:
        raise SecretsError("passphrase must be at least 8 characters")
    global _unlocked_key, _lock_notified
    with _lock:
        if wrapped_key_path().is_file():
            raise SecretsError(
                "a passphrase is already set — use change-passphrase instead")
        # Reuses the pre-lock code path in full: mints a key if the store is
        # genuinely empty, or finds/migrates whatever already protects it.
        key_bytes, _backend = load_master_key()
        recovery_key = _generate_recovery_key()
        data = {
            'version': 1,
            'fingerprint': _key_fingerprint(key_bytes),
            'passphrase': _wrap_leg(passphrase, key_bytes),
            'recovery': _wrap_leg(_normalize_recovery_key(recovery_key), key_bytes),
            'created': now_iso(),
        }
        # Verify both legs open before this becomes the vault's only story —
        # never write a wrapped file that turns out to be unopenable.
        if _unwrap_leg(passphrase, data['passphrase']) != key_bytes:
            raise SecretsError("passphrase wrap verification failed")
        if _unwrap_leg(_normalize_recovery_key(recovery_key), data['recovery']) != key_bytes:
            raise SecretsError("recovery-key wrap verification failed")
        _write_wrapped_key(data)
        _unlocked_key = key_bytes
        _lock_notified = False
        _mark_key_used()
        # Only now: both legs verified in-memory against key_bytes (the same
        # key load_master_key() just proved protects the store), and the
        # wrapped file is durably on disk. Safe to retire the legacy copies.
        _quarantine_legacy_key_material()
    _audit('vault_passphrase_set', caller_addr=caller_addr)
    _notify_vault_tamper('set', caller_addr)
    _log("[secrets] passphrase lock configured; vault auto-unlocked "
         "for this process")
    return recovery_key


def change_passphrase(old_passphrase: str, new_passphrase: str, *,
                      caller_addr: str = '') -> None:
    """Requires the vault to be configured; verifies ``old_passphrase``
    against the passphrase leg regardless of current unlock state (so a
    human can rotate it without a separate unlock step), then rewraps ONLY
    the passphrase leg — the recovery leg, and the key itself, are
    unchanged."""
    new_passphrase = (new_passphrase or '').strip()
    if not new_passphrase:
        raise SecretsError("new passphrase is required")
    if len(new_passphrase) < 8:
        raise SecretsError("new passphrase must be at least 8 characters")
    global _unlocked_key
    with _lock:
        if not wrapped_key_path().is_file():
            raise SecretsError("no passphrase is set yet — use set-passphrase")
        data = _read_wrapped_key()
        try:
            key_bytes = _unwrap_leg((old_passphrase or '').strip(), data['passphrase'])
        except SecretsError:
            raise SecretDenied("wrong passphrase")
        if _key_fingerprint(key_bytes) != data['fingerprint']:
            raise SecretDenied("wrong passphrase")
        data['passphrase'] = _wrap_leg(new_passphrase, key_bytes)
        if _unwrap_leg(new_passphrase, data['passphrase']) != key_bytes:
            raise SecretsError("passphrase wrap verification failed")
        _write_wrapped_key(data)
        _unlocked_key = key_bytes
        _mark_key_used()
    _audit('vault_passphrase_changed', caller_addr=caller_addr)
    _notify_vault_tamper('changed', caller_addr)
    _log("[secrets] passphrase changed")


def unlock_with_passphrase(passphrase: str) -> None:
    global _unlocked_key, _lock_notified, _key_mismatch
    with _lock:
        if not wrapped_key_path().is_file():
            raise SecretsError("no passphrase is set yet — use set-passphrase")
        data = _read_wrapped_key()
        try:
            key_bytes = _unwrap_leg((passphrase or '').strip(), data['passphrase'])
        except SecretsError:
            _audit('vault_unlock_denied', reason='wrong_passphrase')
            raise SecretDenied("wrong passphrase")
        # Belt-and-suspenders: a passphrase that decrypts SOMETHING under
        # AES-GCM's own tag check but isn't actually this vault's key would
        # be a coincidence GCM's authentication already rules out — but the
        # fingerprint compare is what every other mismatch path in this
        # module uses, so unlock stays consistent with load/migrate/restore.
        if _key_fingerprint(key_bytes) != data['fingerprint']:
            _audit('vault_unlock_denied', reason='wrong_passphrase')
            raise SecretDenied("wrong passphrase")
        _unlocked_key = key_bytes
        _lock_notified = False
        _key_mismatch = False
        _mark_key_used()
    _audit('vault_unlocked', method='passphrase')
    _log("[secrets] vault unlocked (passphrase)")


def unlock_with_recovery_key(recovery_key: str, *, caller_addr: str = '') -> None:
    global _unlocked_key, _lock_notified, _key_mismatch
    normalized = _normalize_recovery_key(recovery_key)
    with _lock:
        if not wrapped_key_path().is_file():
            raise SecretsError("no passphrase is set yet — use set-passphrase")
        data = _read_wrapped_key()
        try:
            key_bytes = _unwrap_leg(normalized, data['recovery'])
        except SecretsError:
            _audit('vault_unlock_denied', reason='wrong_recovery_key')
            raise SecretDenied("wrong recovery key")
        if _key_fingerprint(key_bytes) != data['fingerprint']:
            _audit('vault_unlock_denied', reason='wrong_recovery_key')
            raise SecretDenied("wrong recovery key")
        _unlocked_key = key_bytes
        _lock_notified = False
        _key_mismatch = False
        _mark_key_used()
    _audit('vault_unlocked', method='recovery_key', caller_addr=caller_addr)
    _notify_vault_tamper('unlocked with the recovery key', caller_addr)
    _log("[secrets] vault unlocked (recovery key)")


def _read_wrapped_key() -> dict[str, Any]:
    try:
        return json.loads(wrapped_key_path().read_text(encoding='utf-8'))
    except Exception as e:
        raise SecretsError(f"could not read {wrapped_key_path()}: {e}") from e


def _write_wrapped_key(data: dict[str, Any]) -> None:
    """Write ``secrets.key.wrapped``. Windows takes the fail-closed,
    SID-based ACL path (ported from Tobin's f6a8159 vault-review-fixes,
    adapted from the master-key file to this one — same blocker D it fixed:
    an icacls call AFTER the bytes hit disk, naming the grantee by bare
    %USERNAME%, fails open on a domain-joined box or silently resolves the
    wrong account). POSIX keeps the plain 0600-from-creation write — it has
    none of those failure modes."""
    path = wrapped_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    if os.name == 'nt':
        _harden_clayrune_home_windows(path.parent)
        _write_private_text_fail_closed_windows(path, text)
    else:
        _write_private_text(path, text)


# ── Windows ACL: SID resolution + fail-closed write (ported from f6a8159) ───

def _current_user_sid_and_name() -> tuple[str, str]:
    """(sid_string, 'DOMAIN\\name') for the current process token.
    Windows-only; callers must be on Windows already."""
    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32)]
    advapi32.GetTokenInformation.restype = ctypes.c_int
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    advapi32.LookupAccountSidW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    advapi32.LookupAccountSidW.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1

    h_token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                     ctypes.byref(h_token)):
        raise OSError(f'OpenProcessToken failed (error {ctypes.get_last_error()})')
    try:
        size = ctypes.c_uint32(0)
        advapi32.GetTokenInformation(h_token, TOKEN_USER, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise OSError('GetTokenInformation size probe returned 0')
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(h_token, TOKEN_USER, buf, size.value,
                                            ctypes.byref(size)):
            raise OSError(f'GetTokenInformation failed (error {ctypes.get_last_error()})')
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]

        sid_ptr = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(sid_ptr)):
            raise OSError(f'ConvertSidToStringSidW failed (error {ctypes.get_last_error()})')
        try:
            sid_string = sid_ptr.value or ''
        finally:
            kernel32.LocalFree(sid_ptr)
        if not sid_string:
            raise OSError('ConvertSidToStringSidW returned an empty SID')

        account = sid_string
        name_len = ctypes.c_uint32(0)
        domain_len = ctypes.c_uint32(0)
        use = ctypes.c_uint32(0)
        advapi32.LookupAccountSidW(None, psid, None, ctypes.byref(name_len),
                                   None, ctypes.byref(domain_len), ctypes.byref(use))
        if name_len.value and domain_len.value:
            name_buf = ctypes.create_unicode_buffer(name_len.value)
            domain_buf = ctypes.create_unicode_buffer(domain_len.value)
            if advapi32.LookupAccountSidW(
                    None, psid, name_buf, ctypes.byref(name_len),
                    domain_buf, ctypes.byref(domain_len), ctypes.byref(use)):
                account = (f'{domain_buf.value}\\{name_buf.value}'
                          if domain_buf.value else name_buf.value)
        return sid_string, account
    finally:
        kernel32.CloseHandle(h_token)


def _icacls_grant_and_verify_once(path: Path, sid: str, account: str) -> tuple[bool, str]:
    """One attempt at :func:`_icacls_grant_and_verify` — see that function for
    the retry wrapper callers actually use. Never raises; returns (ok, detail)."""
    p = str(path)
    try:
        grant = subprocess.run(
            ['icacls', p, '/inheritance:r', '/grant:r', f'*{sid}:F', '*S-1-5-18:F'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:
        return False, f"icacls grant raised: {e}"
    if grant.returncode != 0:
        return False, (f"icacls exited {grant.returncode}: "
                        f"{(grant.stdout or '').strip()} {(grant.stderr or '').strip()}".strip())
    try:
        verify = subprocess.run(
            ['icacls', p], capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:
        return False, f"icacls verify raised: {e}"
    low = (verify.stdout or '').lower()
    owner_present = sid.lower() in low or (bool(account) and account.lower() in low)
    system_present = ('s-1-5-18' in low) or ('system' in low)
    if not (owner_present and system_present):
        return False, f"unexpected ACL after icacls grant: {(verify.stdout or '').strip()!r}"
    return True, ''


def _icacls_grant_and_verify(path: Path, sid: str, account: str) -> tuple[bool, str]:
    """Strip inheritance and grant only (owner SID, SYSTEM) full control on
    `path`, then re-read the ACL to confirm both grants actually landed.

    Retries ONCE on failure (Dave's review, MC 503edfe4, blocker 4): a
    transient icacls hiccup (the CreateProcess-under-captured-stdin failure
    this same module hit under pytest is one concrete shape of "transient")
    would otherwise strand Ron mid set_passphrase/change_passphrase with no
    recourse but to start over. Still fails CLOSED if the retry also fails —
    this is a retry, not a fallback to a weaker guarantee; the caller's
    fail-closed behavior on a False return is unchanged.

    Never raises; returns (ok, detail)."""
    ok, detail = _icacls_grant_and_verify_once(path, sid, account)
    if ok:
        return True, detail
    retry_ok, retry_detail = _icacls_grant_and_verify_once(path, sid, account)
    if retry_ok:
        _log(f"[secrets] icacls grant on {path} failed once ({detail}) but "
             f"succeeded on retry")
        return True, retry_detail
    return False, f"failed twice (retried once): first={detail!r} retry={retry_detail!r}"


def _harden_clayrune_home_windows(home: Path) -> None:
    """Best-effort: strip ACL inheritance on ~/.clayrune itself and grant
    only (owner, SYSTEM), so a file created under it no longer inherits
    whatever the parent directory happens to grant. Logged, not raised: the
    wrapped-key FILE's own ACL is independently verified and fail-closed
    (see _write_private_text_fail_closed_windows below)."""
    if os.name != 'nt':
        return
    try:
        sid, account = _current_user_sid_and_name()
    except Exception as e:
        _log(f"[secrets] could not resolve current user SID to harden {home}: {e}")
        return
    ok, detail = _icacls_grant_and_verify(home, sid, account)
    if not ok:
        _log(f"[secrets] could not harden {home}'s ACL: {detail}")


def _write_private_text_fail_closed_windows(path: Path, text: str) -> None:
    """Create an EMPTY temp file, grant+verify its ACL by SID, and only THEN
    write the real content and atomically rename into place. Any step
    failing deletes the temp file and raises SecretsError — the wrapped key
    is never created with an unverified ACL."""
    tmp = path.with_name(f'.{path.name}.tmp{os.getpid()}')
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
    except OSError as e:
        raise SecretsError(f"could not create {path.name}: {e}") from e
    try:
        try:
            sid, account = _current_user_sid_and_name()
        except Exception as e:
            raise SecretsError(
                f"could not resolve the current Windows user's SID — "
                f"refusing to write {path.name} rather than leave it under "
                f"a default/inherited ACL: {e}") from e
        ok, detail = _icacls_grant_and_verify(tmp, sid, account)
        if not ok:
            raise SecretsError(
                f"could not secure {path.name}'s permissions — refusing to "
                f"write it: {detail}")
        try:
            tmp.write_text(text, encoding='utf-8')
        except OSError as e:
            raise SecretsError(f"could not write {path.name}: {e}") from e
        try:
            os.replace(tmp, path)
        except OSError as e:
            raise SecretsError(f"could not finalize {path.name}: {e}") from e
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


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

# Sentinel distinguishing "caller didn't pass claude_session_id at all" (the
# with-secret.py in-process shape, where falling back to THIS process's own
# CLAUDE_CODE_SESSION_ID is correct because this process IS the CLI) from
# "caller passed None/empty explicitly" (the /api/secrets/exec route, MC-979
# — where THIS process is the SERVER, and its own os.environ has nothing to
# do with the actual HTTP caller). `None` cannot serve as that "not given"
# marker: the route always passes an argument, and it is `None` whenever the
# JSON body simply omits `claude_session_id` — using `None` for both meanings
# let an exec-route caller who omits the field fall through to reading the
# SERVER's own environment instead of failing closed. If the server process
# happens to have been started from inside a Claude Code session (routine in
# dev — a Bash tool starting `python server.py` inherits the var), that
# lookup can resolve to trigger_type=manual and report unattended=False for
# ANY caller, regardless of what the real caller is — bypassing
# allow_unattended=False. Confirmed via a PoC 2026-09-26 (MC-979 audit).
class _NotGiven:
    pass


_NOT_GIVEN = _NotGiven()


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


def detect_unattended_context(
        claude_session_id: str | None | _NotGiven = _NOT_GIVEN) -> tuple[bool, str]:
    """(is_unattended, reason) purely from server-side signals — no caller
    input, UNLESS ``claude_session_id`` is passed explicitly.

    Not given at all (default): reads ``CLAUDE_CODE_SESSION_ID`` from THIS
    process's own environment — the shape ``with-secret.py``'s in-process
    path uses, since it inherits the CLI's env directly.

    Explicitly passed (even ``None`` or ``''``): for ``POST
    /api/secrets/exec`` (MC-979, ``mc/blueprints/secrets_routes.py``), which
    runs inside the SERVER process — the caller's env var lives in a
    different process entirely, so the server can't read it off its own
    ``os.environ`` and the caller must send it in the request body instead.
    An explicitly-passed-but-empty value fails closed directly and NEVER
    falls back to this process's own environment — that fallback is only
    correct for the "not given at all" case above, where this process really
    is the CLI. Collapsing the two (as an earlier version of this function
    did, using `None` as both "not given" and "given empty") let an exec-route
    caller who simply omits `claude_session_id` fall through to the SERVER's
    own os.environ, which may carry an unrelated attended session's id if the
    server itself was started from inside a Claude Code session — reporting
    unattended=False for a caller that never proved anything of the kind.

    See module comment above for the fail-closed rationale. Public: meant to
    be called by CLI-spawned consumers (with-secret.py) and the exec route to
    compute what they should pass as `unattended=`, not by `get_secret_value`
    itself."""
    if isinstance(claude_session_id, _NotGiven):
        sid = _session_id_from_env()
    else:
        sid = (claude_session_id or '').strip()
    if not sid:
        return True, 'no CLAUDE_CODE_SESSION_ID (fail-closed)'
    trigger_type = _lookup_trigger_type(sid)
    if trigger_type is None:
        return True, 'session unknown to MC (fail-closed)'
    if trigger_type == 'manual':
        return False, 'trigger_type=manual'
    return True, f'trigger_type={trigger_type}'


def detect_effective_unattended(
        unattended: bool,
        claude_session_id: str | None | _NotGiven = _NOT_GIVEN) -> tuple[bool, str]:
    """OR a caller-supplied flag with auto-detection. The flag can only ADD
    strictness (a caller opting into unattended treatment on purpose); it can
    never remove strictness that detection found on its own.

    ``claude_session_id``: see `detect_unattended_context` — pass it (even if
    it's ``None``/``''``) when the caller (the exec route) can't rely on its
    own process environment; leave it unset only for the in-process
    with-secret.py shape."""
    if unattended:
        return True, 'flag'
    return detect_unattended_context(claude_session_id)


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
