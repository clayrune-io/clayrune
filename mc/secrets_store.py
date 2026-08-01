"""Clayrune secrets vault — encrypted credential storage for agent actions.

Lets an agent perform real authenticated work (account logins, social posting,
API calls) without a credential ever entering a transcript, a memory file, a
distilled skill, or the git repo.

## Where it lives — outside the checkout, by construction

Everything is under ``~/.clayrune/`` (override with ``CLAYRUNE_HOME`` for
tests). There is deliberately NO path under the repo that can hold a secret:

    ~/.clayrune/secrets.json         ciphertext + metadata      (0600)
    ~/.clayrune/secrets.key          fallback master key         (0600)
    ~/.clayrune/secrets_audit.jsonl  append-only access log      (0600)

That is stronger than gitignoring. This project has already been bitten once by
"gitignored but bundled anyway" — ``build-macos.spec`` packaged
``data/SHARED_RULES.md`` *because it was still on disk* after being untracked
(CLAUDE.md, 2026-07-12). A file that never exists inside the repo cannot be
swept in by a future ``git add -f``, build spec, or installer glob.

## Crypto

Master key: 32 random bytes held in the OS keyring (Windows Credential Manager
/ macOS Keychain / SecretService). If no keyring backend is usable — headless
Linux, typically — we degrade to a 0600 key file and record the degradation in
the store so the UI can warn.

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
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

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


def load_master_key() -> tuple[bytes, str]:
    """Return ``(key_bytes, backend)``, creating the key on first use.

    ``backend`` is ``'keyring'`` or ``'file'`` — surfaced so the UI can warn
    when the OS keyring wasn't usable and the key is sitting on disk.
    """
    with _lock:
        encoded = _keyring_get()
        if encoded:
            return base64.b64decode(encoded), 'keyring'

        encoded = _read_key_file()
        if encoded:
            return base64.b64decode(encoded), 'file'

        # First use: mint one.
        raw = os.urandom(32)
        encoded = base64.b64encode(raw).decode('ascii')
        if _keyring_set(encoded):
            return raw, 'keyring'
        _write_key_file(encoded)
        _log('[secrets] master key stored in a 0600 key file '
             '(no usable OS keyring backend)')
        return raw, 'file'


# ── Sealed values ────────────────────────────────────────────────────────────

def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as e:  # pragma: no cover - dependency is in requirements
        raise SecretsUnavailable(
            f"the 'cryptography' package is required for the secrets vault: {e}")
    return AESGCM(key)


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

def _write_private_text(path: Path, text: str) -> None:
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
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
        _harden_secret_perms(tmp)
        os.replace(tmp, path)
        _harden_secret_perms(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


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
        'description': rec.get('description', ''),
        'hint': rec.get('hint', ''),
        'scope': rec.get('scope', 'global'),
        'allow_unattended': bool(rec.get('allow_unattended', True)),
        'created_at': rec.get('created_at'),
        'updated_at': rec.get('updated_at'),
        'last_used_at': rec.get('last_used_at'),
        'use_count': int(rec.get('use_count', 0) or 0),
        'placeholder': '{{secret:%s}}' % name,
    }


def list_secrets(project_id: str | None = None) -> list[dict[str, Any]]:
    """Metadata for every secret visible to ``project_id`` (global + that
    project's own). Pass ``None`` for the full inventory."""
    with _lock:
        store = _load_store()
    out = []
    for name, rec in sorted(store['secrets'].items()):
        scope = rec.get('scope', 'global')
        if project_id is not None and scope != 'global' and scope != project_id:
            continue
        out.append(_public(name, rec))
    return out


def key_backend() -> str:
    """``'keyring'`` or ``'file'`` — for the UI's at-rest warning."""
    return load_master_key()[1]


def set_secret(name: str,
               value: str,
               *,
               description: str = '',
               hint: str = '',
               scope: str = 'global',
               allow_unattended: bool = True) -> dict[str, Any]:
    """Create or replace a secret. Human-initiated only — no agent path calls
    this (see the module docstring's authority note)."""
    if not valid_name(name):
        raise SecretsError(
            f"invalid secret name '{name}' — use lowercase letters, digits, "
            f"'.', '-', '_' (e.g. reddit.password)")
    if not isinstance(value, str) or value == '':
        raise SecretsError('secret value must be a non-empty string')

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
            'description': str(description or ''),
            'hint': str(hint or ''),
            'scope': str(scope or 'global'),
            'allow_unattended': bool(allow_unattended),
            'created_at': existing.get('created_at') or now_iso(),
            'updated_at': now_iso(),
            'last_used_at': existing.get('last_used_at'),
            'use_count': int(existing.get('use_count', 0) or 0),
        })
        store['secrets'][name] = rec
        store['key_backend'] = key_backend()
        _save_store(store)

    _audit('set', name=name, scope=scope, allow_unattended=bool(allow_unattended),
           replaced=bool(existing))
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


def get_secret_value(name: str,
                     *,
                     consumer: str,
                     project_id: str | None = None,
                     unattended: bool = False) -> str:
    """Decrypt and dispense a value. Every call is audited.

    ``consumer`` is a short free-text label for the audit trail
    (``'browser-login'``, ``'with-secret'``, ``'send_mail'``). ``unattended``
    must be True for steward / scheduled cycles so ``allow_unattended`` can be
    enforced.
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


def referenced_names(text: str) -> list[str]:
    """Secret names a piece of text refers to, in first-appearance order."""
    seen: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(text or ''):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def resolve_placeholders(text: str,
                         *,
                         consumer: str,
                         project_id: str | None = None,
                         unattended: bool = False) -> tuple[str, list[str]]:
    """Substitute every ``{{secret:name}}`` in ``text``.

    Returns ``(resolved_text, names_used)``. Raises on the first unknown or
    denied name rather than leaving a live placeholder in a command line —
    a silently-unsubstituted ``{{secret:...}}`` would otherwise get sent
    somewhere as a literal password.
    """
    names = referenced_names(text)
    if not names:
        return text, []
    values = {n: get_secret_value(n, consumer=consumer, project_id=project_id,
                                  unattended=unattended) for n in names}
    out = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)
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
