"""Secrets vault endpoints — the human-facing management surface.

Deliberate omission: there is **no route that returns a plaintext value**.
The UI can create, describe, re-scope, rotate, and delete a secret, and it can
read the audit log — but a value only ever leaves this process into a child
process's environment (tools/with-secret.py) or into a resolved command, never
back over HTTP into a browser tab. That removes the whole class of "the vault
page was left open / was screenshotted / was proxied" exposure.

Routes:
    GET    /api/secrets                        list metadata (+ key backend)
    POST   /api/secrets                        create or rotate
    PATCH  /api/secrets/<name>                 edit metadata / policy
    DELETE /api/secrets/<name>                 delete
    GET    /api/secrets/audit                  recent access records
    POST   /api/secrets/check                  which names does this text use,
                                               and are they all resolvable?
    POST   /api/secrets/import-authenticator   Google Authenticator export QR
    POST   /api/secrets/totp/<name>            does this code match? (yes/no)
    GET    /api/secrets/vault-lock             passphrase-lock state (any caller)
    POST   /api/secrets/vault-lock/set         first-time passphrase setup (human-only)
    POST   /api/secrets/vault-lock/change      rotate the passphrase (human-only)
    POST   /api/secrets/vault-lock/unlock      unlock with passphrase or recovery key (human-only)
    POST   /api/secrets/vault-lock/lock        lock now, immediately (human-only)
    POST   /api/secrets/exec                   run a command with secrets injected,
                                               entirely server-side (loopback+token only)
    POST   /api/secrets/notify-vault-locked    relay a 'vault locked' push for an
                                               out-of-process caller (loopback+token only)

The TOTP probe returns a boolean, never our own code — a route that minted live
second factors would be the plaintext hole this design otherwise refuses.

``/api/secrets/exec`` is the one deliberate exception to "no route returns a
plaintext value" — see its docstring below for why that's still safe (MC-979).
"""

import hmac
import os
import re
import subprocess
import time

from flask import Blueprint, jsonify, request

from mc import secrets_store as vault
from mc import totp as _totp
from mc.blueprints import local_auth
from mc.core import _is_loopback_request, _log
from mc.unattended import is_unattended_caller

bp = Blueprint('secrets_routes', __name__)

# ── /api/secrets/exec gates (MC-979) ────────────────────────────────────────
#
# This route lets the SERVER run a command with real secret values injected
# into its environment, on an agent's behalf, so the caller never needs the
# unwrapped master key itself (see mc/secrets_store.py's passphrase-lock
# section: that key lives only in whichever process's memory unlocked it,
# which after a human unlock is the server's, never a separately-spawned CLI
# invocation's). That makes this the one place in the vault surface that DOES
# run with a plaintext secret in scope — so unlike every other route here, it
# is gated hard and fails closed, on three independent axes, all of which
# must hold:

_EXEC_DEFAULT_TIMEOUT = 600
_EXEC_MAX_TIMEOUT = 1800
_EXEC_MAX_OUTPUT_BYTES = 2_000_000


def _cf_header_present() -> bool:
    """True if this request carries ANY Cloudflare header — ``Cf-Ray``,
    ``Cf-Connecting-Ip``, or any ``Cf-Access-*``. Tunnel traffic terminates
    at ``cloudflared`` on THIS host and is forwarded to the origin over
    loopback (see ``mc/blueprints/local_auth.py``'s docstring and
    ``remote_routes.py``'s ``_cf_tunneled_and_verified``), so it satisfies
    `_is_loopback_request()` too — loopback alone cannot tell a genuine local
    CLI call from a phone reaching in over the tunnel. Checked broadly
    (any header name starting with ``cf-``, case-insensitive) rather than
    naming only the three examples, since a legitimate local caller
    (``with-secret.py``'s fallback) never sends ANY Cloudflare header at all.
    """
    return any(name.lower().startswith('cf-') for name in request.headers.keys())


def _exec_token_ok() -> bool:
    supplied = request.headers.get('X-Clayrune-Exec-Token', '')
    if not supplied:
        return False
    return hmac.compare_digest(supplied, vault.ensure_exec_token())


def _exec_gate_refusal():
    """The fail-closed gates shared by ``/api/secrets/exec`` and
    ``/api/secrets/notify-vault-locked`` — both are "make the server do a
    privileged thing on my behalf" calls reachable only by a same-box CLI
    process that already knows the per-boot token. Returns a Flask response
    tuple to return immediately on refusal, or ``None`` to proceed. Order:
    cheapest and most decisive first.
    """
    if not _is_loopback_request():
        return jsonify({'error': 'loopback_required',
                        'message': 'this endpoint only answers to this machine'}), 403
    if _cf_header_present():
        return jsonify({'error': 'tunnel_refused',
                        'message': 'tunneled/CF-Access requests may not use this '
                                   'endpoint even though they also look like loopback'}), 403
    if not _exec_token_ok():
        return jsonify({'error': 'bad_exec_token'}), 403
    return None

# Per-source-IP throttle on vault-lock passcode attempts (set/change/unlock) —
# same shape as local_auth's own login throttle and f6a8159's recovery-key
# throttle, kept as a separate dict because a wrong passcode here is an
# attempt to own or relock the key that opens every secret, not just the
# dashboard session. Best-effort; resets on restart.
_VAULT_LOCK_FAIL_CAP = 5
_VAULT_LOCK_FAIL_WINDOW = 300  # seconds
_VAULT_LOCK_FAILS: dict[str, list[float]] = {}


def _vault_lock_throttled(ip: str) -> bool:
    rec = _VAULT_LOCK_FAILS.get(ip)
    if not rec:
        return False
    if time.time() - rec[1] > _VAULT_LOCK_FAIL_WINDOW:
        _VAULT_LOCK_FAILS.pop(ip, None)
        return False
    return rec[0] >= _VAULT_LOCK_FAIL_CAP


def _vault_lock_note_fail(ip: str) -> None:
    now = time.time()
    rec = _VAULT_LOCK_FAILS.get(ip)
    if not rec or now - rec[1] > _VAULT_LOCK_FAIL_WINDOW:
        _VAULT_LOCK_FAILS[ip] = [1, now]
    else:
        rec[0] += 1


def _err(e: Exception, code: int = 400):
    return jsonify({'error': str(e)}), code


def _unattended_refusal():
    """CLAUDE.md vault rule 3: 'Agents use credentials; only humans create
    them. There is no agent-facing write path.' Was true only by convention —
    see docs/_review/2026-09-10_security.md F3/F5. project_id=None: a secret
    isn't necessarily project-scoped (global secrets exist), so any running
    non-manual session anywhere refuses this call."""
    return jsonify({'error': 'this action needs a human — an unattended agent '
                             'session cannot create, edit, or delete a secret; '
                             'ask the user to do it from the Settings UI'}), 403


def _require_human_passcode(data: dict):
    """Gate on the LOCAL DASHBOARD PASSCODE, re-entered in the request body
    and verified server-side — NOT on ``is_unattended_caller()`` alone.

    Dave's review of d3516a2 (MC 503edfe4): ``is_unattended_caller()``
    returns False (i.e. "looks human") for ANY request carrying a browser
    ``Origin`` header — forgeable by a plain agent ``curl`` — and for every
    manual-chat agent session (``trigger_type == 'manual'`` is exempt by
    design). Exactly the forged-Origin gap Wren's review closed for the
    recovery-key display in f6a8159; the vault-lock set/change/unlock routes
    shipped in d3516a2 never got the same fix. Worst case before this:
    an agent calls ``/set`` on an unconfigured vault first and owns both the
    passphrase and the recovery key.

    Returns ``None`` on success (caller proceeds), or a Flask response tuple
    to return immediately on refusal. If no passcode is configured yet, the
    action is refused outright — nothing else on this local,
    unauthenticated API surface proves a human, as opposed to an agent
    sharing the same machine, is the one asking."""
    ip = request.remote_addr or '?'
    if _vault_lock_throttled(ip):
        return jsonify({'error': 'too_many_attempts',
                        'message': 'too many attempts — wait a few minutes '
                                   'and try again'}), 429
    if not local_auth._local_auth_is_configured():
        return jsonify({'error': 'passcode_required',
                        'message': 'set a local dashboard passcode in '
                                   'Settings > Connectivity > Network access '
                                   'before changing the vault lock'}), 403
    passcode = (data.get('passcode') or '').strip()
    if not passcode or not local_auth._local_auth_verify_passcode(passcode):
        _vault_lock_note_fail(ip)
        return jsonify({'error': 'bad_passcode'}), 403
    _VAULT_LOCK_FAILS.pop(ip, None)
    return None


@bp.route('/api/secrets')
def api_secrets_list():
    project_id = request.args.get('project_id') or None
    if vault.is_locked():
        # Deliberately short-circuit before list_secrets(check_readable=True):
        # that path decrypts nothing when locked (is_readable() swallows
        # VaultLocked and reports False), which would render as EVERY secret
        # looking permanently broken instead of the vault being locked.
        # Metadata (names/scope) is still safe to show — only values are gated.
        items = vault.list_secrets(project_id, check_readable=False)
        return jsonify({
            'secrets': items,
            'key_backend': 'locked',
            'locked': True,
            'key_at_rest_warning': '',
            'unreadable_count': 0,
            'key_mismatch': False,
        })
    try:
        # check_readable actually decrypts each entry to prove it, rather
        # than just confirming it exists — the 2026-09-14 silent-remint
        # incident orphaned 8 entries that a plain existence check kept
        # reporting as fine. Still metadata-only: the booleans go out, the
        # values never do.
        items = vault.list_secrets(project_id, check_readable=True)
    except vault.SecretsError as e:
        return _err(e, 500)
    try:
        backend = vault.key_backend()
        warning = ('Master key is in a 0600 file — no usable OS keyring '
                   'backend was found.' if backend == 'file' else '')
    except vault.SecretsUnavailable:
        # No key anywhere (keyring and file mirror both gone) but the store
        # itself is readable — still return the list so the UI can show
        # which entries need re-entry, rather than 500ing the whole panel.
        backend = 'unavailable'
        warning = 'Master key is missing — stored secrets cannot be decrypted.'
    unreadable = sum(1 for s in items if not s.get('readable', True))
    return jsonify({
        'secrets': items,
        'key_backend': backend,
        'locked': False,
        # The UI badges this: a file-backed key is readable by anything running
        # as this user, whereas the OS keyring is at least gated by the login
        # session. Worth telling the operator which one they're on.
        'key_at_rest_warning': warning,
        'unreadable_count': unreadable,
        # True if the OS keyring answered with a key that decrypts none of the
        # store's records (2026-09-15: a test probe minted a fresh key into
        # the real keyring entry). Metadata only, never a value — see
        # vault.key_mismatch().
        'key_mismatch': vault.key_mismatch(),
    })


@bp.route('/api/secrets', methods=['POST'])
def api_secrets_set():
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    value = data.get('value')
    if not isinstance(value, str) or not value:
        return jsonify({'error': 'value is required'}), 400
    try:
        rec = vault.set_secret(
            name,
            value,
            username=data.get('username') or '',
            description=data.get('description') or '',
            hint=data.get('hint') or '',
            scope=(data.get('scope') or 'global').strip() or 'global',
            allow_unattended=bool(data.get('allow_unattended', True)),
            kind=(data.get('kind') or vault.KIND_PASSWORD).strip(),
        )
    except vault.SecretsError as e:
        return _err(e)
    _log(f"[secrets] stored '{name}' (scope={rec['scope']}, "
         f"unattended={rec['allow_unattended']})")
    return jsonify(rec)


@bp.route('/api/secrets/<name>', methods=['PATCH'])
def api_secrets_patch(name: str):
    """Edit metadata/policy without re-typing the value.

    Implemented as decrypt-and-reseal so there is exactly one write path into
    the store; the value never leaves this function.
    """
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    try:
        current = {s['name']: s for s in vault.list_secrets()}.get(name)
        if current is None:
            return jsonify({'error': f"no secret named '{name}'"}), 404
        value = data.get('value')
        if not isinstance(value, str) or not value:
            value = vault.get_secret_value(name, consumer='api:patch')
        rec = vault.set_secret(
            name,
            value,
            username=data.get('username', current.get('username', '')),
            description=data.get('description', current['description']),
            hint=data.get('hint', current['hint']),
            scope=data.get('scope', current['scope']),
            allow_unattended=bool(
                data.get('allow_unattended', current['allow_unattended'])),
            # Carry the kind forward. Defaulting here would silently turn a TOTP
            # entry into a password on a description edit, and the breakage
            # would only surface as a failed login much later.
            kind=data.get('kind', current.get('kind', vault.KIND_PASSWORD)),
        )
    except vault.SecretNotFound as e:
        return _err(e, 404)
    except vault.SecretsError as e:
        return _err(e)
    return jsonify(rec)


@bp.route('/api/secrets/<name>', methods=['DELETE'])
def api_secrets_delete(name: str):
    if is_unattended_caller():
        return _unattended_refusal()
    try:
        ok = vault.delete_secret(name)
    except vault.SecretsError as e:
        return _err(e, 500)
    if not ok:
        return jsonify({'error': f"no secret named '{name}'"}), 404
    return jsonify({'ok': True, 'deleted': name})


@bp.route('/api/secrets/import-authenticator', methods=['POST'])
def api_secrets_import_authenticator():
    """Import from a Google Authenticator *Transfer accounts → Export* QR.

    Two-step, both driven by the same ``otpauth-migration://`` URI the user
    pasted: without ``commit`` we return a preview (issuer/account/suggested
    name — **no seeds**), and with it we store the selected entries. The URI is
    re-posted rather than parked in server-side state because the browser
    already holds it by construction, so re-sending leaks nothing new, and the
    decoded seeds never travel back out.
    """
    data = request.get_json(silent=True) or {}
    uri = (data.get('uri') or '').strip()
    if not uri:
        return jsonify({'error': 'uri is required'}), 400
    try:
        entries = _totp.parse_migration_uri(uri)
    except _totp.TotpError as e:
        return _err(e)

    chosen = data.get('names') or {}          # suggested_name -> final name ('' = skip)
    preview = []
    for entry in entries:
        suggested = _totp.suggested_name(entry)
        preview.append({
            'suggested_name': suggested,
            'issuer': entry.get('issuer', ''),
            'account': entry.get('account', ''),
            'digits': entry.get('digits', 6),
            'period': entry.get('period', 30),
        })

    if not data.get('commit'):
        return jsonify({'accounts': preview, 'count': len(preview)})

    # THE COMMIT PATH IS A VAULT WRITE, so it is gated like every other one.
    # Gated HERE rather than at the top of the route because the preview half
    # (no `commit`) returns issuer/account only, never a seed, and decodes a URI
    # the caller already holds — refusing that would break the human's own
    # two-step import for nothing. `set_secret` below defaults
    # allow_unattended=True, so an ungated commit was the F3 hole wearing a
    # different route: plant a credential AND mark it usable unattended, in one
    # call. Missed by the F3/F5 pass because it sits outside the cited range.
    if is_unattended_caller():
        return _unattended_refusal()

    scope = (data.get('scope') or 'global').strip() or 'global'
    allow_unattended = bool(data.get('allow_unattended', True))
    imported, skipped = [], []
    for entry, shown in zip(entries, preview):
        target = (chosen.get(shown['suggested_name'], shown['suggested_name']) or '').strip()
        if not target:
            skipped.append(shown['suggested_name'])
            continue
        try:
            rec = vault.set_secret(
                target, entry['secret'], kind=vault.KIND_TOTP, scope=scope,
                allow_unattended=allow_unattended,
                description=' — '.join(x for x in (entry.get('issuer'),
                                                   entry.get('account')) if x))
            imported.append(rec['name'])
        except vault.SecretsError as e:
            _log(f"[secrets] authenticator import of '{target}' failed: {e}")
            skipped.append(target)
    return jsonify({'imported': imported, 'skipped': skipped})


@bp.route('/api/secrets/totp/<name>', methods=['POST'])
def api_secrets_totp_probe(name: str):
    """Confirm a stored TOTP seed is right, by checking a code the user reads
    off their phone.

    Returns only whether it matched — never our own generated code. Otherwise
    this would be the plaintext-returning route the design deliberately lacks:
    anyone who could reach the vault page could mint live second factors.
    """
    data = request.get_json(silent=True) or {}
    expect = re.sub(r'\s', '', str(data.get('code') or ''))
    if not expect:
        return jsonify({'error': 'code is required'}), 400
    try:
        code, remaining = vault.generate_totp_code(name, consumer='api:verify')
    except vault.SecretNotFound as e:
        return _err(e, 404)
    except vault.SecretsError as e:
        return _err(e)
    return jsonify({'match': hmac.compare_digest(code, expect),
                    'seconds_remaining': remaining})


@bp.route('/api/secrets/audit')
def api_secrets_audit():
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        limit = 100
    return jsonify({'records': vault.audit_tail(limit)})


@bp.route('/api/secrets/vault-lock')
def api_vault_lock_state():
    """Read-only status — safe for any caller, including agents: it never
    reveals the key or a secret, only which of the three states the vault
    is in (see ``vault.lock_state()``)."""
    return jsonify({
        'state': vault.lock_state(),
        'configured': vault.lock_state() != 'unconfigured',
    })


@bp.route('/api/secrets/vault-lock/set', methods=['POST'])
def api_vault_lock_set():
    """First-time passphrase setup. Human-only (MC 503edfe4): an agent that
    could set the passphrase could just as easily set one only it knows.
    Gated on the re-entered dashboard passcode, not just
    ``is_unattended_caller()`` — see ``_require_human_passcode``."""
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    passphrase: str = str(data.get('passphrase') or '')
    try:
        recovery_key = vault.set_passphrase(passphrase, caller_addr=request.remote_addr or '')
    except vault.SecretsError as e:
        return _err(e)
    return jsonify({'ok': True, 'recovery_key': recovery_key})


@bp.route('/api/secrets/vault-lock/change', methods=['POST'])
def api_vault_lock_change():
    """Gated on the re-entered dashboard passcode as well as the old
    passphrase — see ``_require_human_passcode``."""
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        vault.change_passphrase(
            str(data.get('old_passphrase') or ''),
            str(data.get('new_passphrase') or ''),
            caller_addr=request.remote_addr or '')
    except vault.SecretDenied as e:
        return _err(e, 403)
    except vault.SecretsError as e:
        return _err(e)
    return jsonify({'ok': True})


@bp.route('/api/secrets/vault-lock/unlock', methods=['POST'])
def api_vault_lock_unlock():
    """Passcode-gated unlock. Human-only: an agent calling this would defeat
    the whole point of a lock an agent can't read past on its own. Gated on
    the re-entered dashboard passcode as well as the passphrase/recovery key
    — see ``_require_human_passcode``."""
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    passphrase: str = str(data.get('passphrase') or '')
    recovery_key: str = str(data.get('recovery_key') or '')
    if not passphrase and not recovery_key:
        return jsonify({'error': 'passphrase or recovery_key is required'}), 400
    try:
        if recovery_key:
            vault.unlock_with_recovery_key(recovery_key, caller_addr=request.remote_addr or '')
        else:
            vault.unlock_with_passphrase(passphrase)
    except vault.SecretDenied as e:
        return _err(e, 403)
    except vault.SecretsError as e:
        return _err(e)
    return jsonify({'ok': True, 'state': vault.lock_state()})


@bp.route('/api/secrets/vault-lock/lock', methods=['POST'])
def api_vault_lock_lock():
    """Manual immediate lock — the dashboard's "Lock now" control. Human-only,
    same guard as unlock/set/change: an agent that could lock the vault on
    demand could also unlock it, since both paths depend on the same
    passcode gate to prove a human is asking. No-op (still 200) if the vault
    is already locked or was never configured — the button doesn't need to
    know the current state first."""
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    vault.lock_now(caller_addr=request.remote_addr or '')
    return jsonify({'ok': True, 'state': vault.lock_state()})


@bp.route('/api/secrets/check', methods=['POST'])
def api_secrets_check():
    """Dry-run a template: report which secrets it references and whether each
    would actually resolve for the given project/attendedness. Tries a real
    decrypt of each referenced entry (never returns the value) rather than
    just confirming it exists — an existence-only check reported an
    undecryptable entry as fine during the 2026-09-14 silent-remint
    incident. Lets an agent verify a command before running it."""
    data = request.get_json(silent=True) or {}
    text = data.get('text') or ''
    project_id = data.get('project_id') or None
    unattended = bool(data.get('unattended', False))
    names = vault.referenced_names(text)
    wants_user = set(vault.referenced_usernames(text))
    known = {s['name']: s for s in vault.list_secrets()}
    report = []
    for n in names:
        s = known.get(n)
        if s is None:
            report.append({'name': n, 'ok': False, 'reason': 'not_found'})
        elif s['scope'] != 'global' and s['scope'] != project_id:
            report.append({'name': n, 'ok': False, 'reason': 'out_of_scope'})
        elif unattended and not s['allow_unattended']:
            report.append({'name': n, 'ok': False, 'reason': 'unattended_blocked'})
        elif n in wants_user and not s.get('username'):
            report.append({'name': n, 'ok': False, 'reason': 'no_username'})
        elif not vault.is_readable(n):
            report.append({'name': n, 'ok': False, 'reason': 'undecryptable'})
        else:
            report.append({'name': n, 'ok': True})
    return jsonify({'referenced': report,
                    'resolvable': all(r['ok'] for r in report)})


def _parse_pairs(raw, field_name: str) -> list[tuple[str, str]]:
    """Parse a list of ``[VAR, secret.name]`` pairs (or ``{"var":…,"name":…}``
    objects) from the JSON body — the wire shape of ``tools/with-secret.py``'s
    ``--env``/``--user``/``--totp``, which parses the same pairs off argv."""
    out = []
    for item in (raw or []):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            var, name = item
        elif isinstance(item, dict):
            var, name = item.get('var'), item.get('name')
        else:
            raise ValueError(f"{field_name}: expected [VAR, secret.name] pairs")
        var, name = str(var or '').strip(), str(name or '').strip()
        if not var or not name:
            raise ValueError(f"{field_name}: expected [VAR, secret.name] pairs")
        out.append((var, name))
    return out


def _decode_and_scrub(raw_bytes: bytes) -> str:
    """Decode child output and redact every value this process has dispensed
    — the same ``vault.redact`` scrub ``tools/with-secret.py`` applies to its
    own passthrough. Truncated BEFORE decode so the cap is exact bytes, and
    before redaction so a secret value never gets left half-truncated (a
    partial match wouldn't scrub)."""
    truncated = len(raw_bytes) > _EXEC_MAX_OUTPUT_BYTES
    if truncated:
        raw_bytes = raw_bytes[:_EXEC_MAX_OUTPUT_BYTES]
    text = raw_bytes.decode('utf-8', errors='replace')
    if truncated:
        text += '\n...[truncated]'
    return vault.redact(text)


@bp.route('/api/secrets/exec', methods=['POST'])
def api_secrets_exec():
    """Run a command with real secret values injected into its environment,
    entirely server-side. The ONE deliberate exception to "no route returns a
    plaintext value" (CLAUDE.md vault rule 2) — but it does not actually
    violate that rule: this route never returns a *secret*, only the CHILD's
    own (redacted) output, exactly what an in-process ``tools/with-secret.py``
    call already prints to the agent's own stdout today.

    MC-979: after a human unlocks the passphrase-locked vault from the
    dashboard, the unwrapped master key lives ONLY in the server process's
    memory (see the passphrase-lock section of ``mc/secrets_store.py``) — a
    separately-spawned ``with-secret.py`` invocation can never read it, so
    every agent-side secret use failed with "vault is locked" even though a
    human had just unlocked it. This route lets the process that DOES hold
    the key run the command instead; ``with-secret.py`` falls back to it only
    when its own in-process attempt raises ``VaultLocked``.

    Gated hard, fails closed — see ``_exec_gate_refusal()`` above: loopback
    only, no Cloudflare/tunnel header (tunnel traffic also looks like
    loopback), and the per-boot token (blocks a forged browser-pane POST).
    Unattended detection and the per-secret ``allow_unattended`` gate apply
    exactly as they do for an in-process ``with-secret.py`` call — this route
    does not loosen or bypass either."""
    refusal = _exec_gate_refusal()
    if refusal is not None:
        return refusal

    data = request.get_json(silent=True) or {}
    command = data.get('command')
    if not isinstance(command, list) or not command or not all(
            isinstance(a, str) for a in command):
        return jsonify({'error': 'command must be a non-empty list of strings'}), 400
    if data.get('raw'):
        return jsonify({'error': 'raw is not supported over this route — '
                                 'interactive commands must stay in-process'}), 400

    project_id = data.get('project_id') or None
    claude_session_id = data.get('claude_session_id') or None
    unattended, _reason = vault.detect_effective_unattended(
        bool(data.get('unattended', False)), claude_session_id)

    try:
        env_pairs = _parse_pairs(data.get('env'), 'env')
        user_pairs = _parse_pairs(data.get('user'), 'user')
        totp_pairs = _parse_pairs(data.get('totp'), 'totp')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    stdin_name = data.get('stdin') or None

    try:
        env = dict(os.environ)
        env.update(vault.env_for(env_pairs, consumer='server-exec',
                                 project_id=project_id, unattended=unattended))
        for var, sec in user_pairs:
            env[var] = vault.get_username(sec, project_id=project_id)
        for var, sec in totp_pairs:
            code, remaining = vault.generate_totp_code(
                sec, consumer='server-exec', project_id=project_id,
                unattended=unattended)
            # See tools/with-secret.py: don't hand out a code that will
            # expire before the child finishes using it.
            if remaining < 5:
                time.sleep(remaining + 1)
                code, remaining = vault.generate_totp_code(
                    sec, consumer='server-exec', project_id=project_id,
                    unattended=unattended)
            env[var] = code
        command = [vault.resolve_placeholders(a, consumer='server-exec',
                                              project_id=project_id,
                                              unattended=unattended)[0]
                  for a in command]
        stdin_value = (vault.get_secret_value(stdin_name, consumer='server-exec',
                                              project_id=project_id,
                                              unattended=unattended)
                      if stdin_name else None)
    except vault.VaultLocked:
        return jsonify({'error': 'vault_locked',
                        'message': 'the vault is locked — unlock it from '
                                   'Settings > Vault'}), 423
    except vault.SecretsError as e:
        return _err(e)

    try:
        timeout = int(data.get('timeout') or _EXEC_DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = _EXEC_DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, _EXEC_MAX_TIMEOUT))
    cwd = data.get('cwd') or None
    # Windows children writing to a pipe default to cp1252 without this,
    # which corrupts non-ASCII output before we ever get to decode it.
    env.setdefault('PYTHONIOENCODING', 'utf-8')

    # Never let the child inherit THIS process's stdin — `stdin=None` here
    # would mean "inherit", and the server's own stdin is not something an
    # exec'd command should ever see (and, under a test harness that
    # replaces stdin with a non-inheritable handle, inheriting it fails
    # process creation outright on Windows). `subprocess.run`'s `input=`
    # already implies `stdin=PIPE`; DEVNULL only when there's no stdin_value.
    try:
        if stdin_value is not None:
            proc = subprocess.run(
                command, env=env, cwd=cwd, input=stdin_value.encode('utf-8'),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        else:
            proc = subprocess.run(
                command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        exit_code = proc.returncode
        stdout_bytes, stderr_bytes = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        exit_code = None
        stdout_bytes, stderr_bytes = (e.stdout or b''), (e.stderr or b'')
    except OSError as e:
        return jsonify({'error': f'failed to start command: {e}'}), 400

    stdout_text = _decode_and_scrub(stdout_bytes)
    stderr_text = _decode_and_scrub(stderr_bytes)

    if exit_code is None:
        _log(f"[secrets] server-exec timed out after {timeout}s")
        return jsonify({'error': 'timeout', 'exit_code': None,
                        'stdout': stdout_text, 'stderr': stderr_text}), 504
    return jsonify({'exit_code': exit_code, 'stdout': stdout_text,
                    'stderr': stderr_text})


@bp.route('/api/secrets/notify-vault-locked', methods=['POST'])
def api_secrets_notify_vault_locked():
    """Loopback relay target for ``mc.secrets_store._notify_vault_locked()``
    when it fires OUTSIDE the server process (a bare ``with-secret.py`` run,
    a standalone script) — see that function's MC-979 docstring for why it
    can't call ``push_mobile._notify_push`` directly from there (its config
    paths are only wired by ``push_mobile.wire()``, which only the server
    process calls). Same gate as ``/api/secrets/exec``: this is the same
    shape of "make the server do a privileged thing on my behalf" call, just
    a push notification instead of a subprocess."""
    refusal = _exec_gate_refusal()
    if refusal is not None:
        return refusal
    try:
        from mc.blueprints import push_mobile as _bp_push_mobile
        _bp_push_mobile._notify_push(
            'Vault locked',
            'A job needs the secrets vault unlocked — open the dashboard to '
            'unlock it.')
    except Exception as e:
        _log(f"[secrets] vault-locked notification relay failed: {e}")
    return jsonify({'ok': True})
