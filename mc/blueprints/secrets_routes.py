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
    POST   /api/secrets/vault-lock/retire-legacy retry the legacy-key quarantine
                                               (human-only)
    POST   /api/secrets/exec                   run a command with secrets injected,
                                               entirely server-side (loopback+token only)
    POST   /api/secrets/notify-vault-locked    relay a 'vault locked' push for an
                                               out-of-process caller (loopback+token only)

The TOTP probe returns a boolean, never our own code — a route that minted live
second factors would be the plaintext hole this design otherwise refuses.

``/api/secrets/exec`` is the one deliberate exception to "no route returns a
plaintext value" — see its docstring below for why that's still safe (MC-979).
"""

import ctypes
import hmac
import os
import re
import signal
import subprocess
import time

from flask import Blueprint, jsonify, request

from mc import proc_kill as _proc_kill
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
    sharing the same machine, is the one asking.

    Shares local_auth's ONE per-IP guessing budget (MC-994 re-review finding
    N3, second pass — Fenn, 2026-09-28), not a separate ``_VAULT_LOCK_FAILS``
    counter. That separate dict let a caller already shut out of /login (or
    /set, or the attend-once grant — every one of them gates through this
    same function) keep an unthrottled run at the SAME passcode against the
    vault-lock routes instead; merged, a wrong guess anywhere against this
    passcode counts against every route that checks it.

    The guess goes through ``local_auth._local_auth_try_passcode`` (MC-994
    re-review finding N3, review #3 — Fenn): it reserves a slot before PBKDF2
    and always releases it, so concurrent guesses cannot overrun the shared
    cap and a malformed body cannot leak a slot. ``passcode_required`` is
    checked first because it spends no guess."""
    if not local_auth._local_auth_is_configured():
        return jsonify({'error': 'passcode_required',
                        'message': 'set a local dashboard passcode in '
                                   'Settings > Connectivity > Network access '
                                   'before changing the vault lock'}), 403
    passcode = data.get('passcode') if isinstance(data, dict) else None
    ok = local_auth._local_auth_try_passcode(
        passcode.strip() if isinstance(passcode, str) else '')
    if ok is None:
        return jsonify({'error': 'too_many_attempts',
                        'message': 'too many attempts — wait a few minutes '
                                   'and try again'}), 429
    if not ok:
        return jsonify({'error': 'bad_passcode'}), 403
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


def _entry_type_arg(data: dict):
    """``entry_type`` from a save body, or None when the client did not send
    one. Anything present but not a string is passed on as-is so the store's
    allowlist rejects it rather than it being quietly ignored."""
    et = data.get('entry_type')
    if et is None or et == '':
        return None
    return et.strip() if isinstance(et, str) else str(et)


@bp.route('/api/secrets', methods=['POST'])
def api_secrets_set():
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    # MC-995: is_unattended_caller() alone is forgeable (an agent curl can
    # carry an Origin header and read as "attended manual chat") — also
    # require the retyped dashboard passcode, same as the vault-lock routes.
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
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
            entry_type=_entry_type_arg(data),
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
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        current = {s['name']: s for s in vault.list_secrets()}.get(name)
        if current is None:
            return jsonify({'error': f"no secret named '{name}'"}), 404
        value = data.get('value')
        if not isinstance(value, str) or not value:
            # Re-seal, never return: human-only (passcode above). `internal` lets a
            # metadata edit of a server-kept sign-in entry (`oauth.*`) re-read it.
            value = vault.get_secret_value(name, consumer='api:patch', internal=True)
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
            # Not carried from `current`: that is the effective (possibly
            # inferred) type, and freezing a guess into the record on every
            # edit is not what an edit asked for. None keeps the stored one.
            entry_type=_entry_type_arg(data),
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
    data = request.get_json(silent=True) or {}
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
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
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal

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
    is in (see ``vault.lock_state()``), plus a metadata-only boolean for
    whether a pre-passphrase-lock copy of the master key is still live
    (MC 503edfe4 follow-up — no values, no key-material paths)."""
    state = vault.lock_state()
    return jsonify({
        'state': state,
        'configured': state != 'unconfigured',
        'legacy_key_copies_present': (
            vault.legacy_key_copies_present() if state != 'unconfigured' else False),
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


@bp.route('/api/secrets/vault-lock/retire-legacy', methods=['POST'])
def api_vault_lock_retire_legacy():
    """Manual retry of the legacy-key quarantine (MC 503edfe4 follow-up) —
    same human-only + passcode gate as set/change/unlock/lock. No-op (still
    200, ``had_legacy_copies: false``) if nothing legacy is left to
    retire — the button doesn't need to know the current state first."""
    if is_unattended_caller():
        return _unattended_refusal()
    data = request.get_json(silent=True) or {}
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        had_legacy = vault.retire_legacy_key_copies(caller_addr=request.remote_addr or '')
    except vault.SecretsError as e:
        return _err(e)
    return jsonify({
        'ok': True,
        'had_legacy_copies': had_legacy,
        'legacy_key_copies_present': vault.legacy_key_copies_present(),
    })


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
    own passthrough. Redact BEFORE truncating: ``subprocess.run`` already
    holds all of ``raw_bytes`` in memory (no streaming cap upstream), so
    truncating first buys nothing, and it used to leave a secret that
    straddled the cut point reduced to a prefix — ``vault.redact`` matches
    the full dispensed value, so a partial match wasn't scrubbed and the
    fragment reached the caller in cleartext (MC-979 audit finding,
    confirmed 2026-09-26: a 130-byte secret with a 110-byte cap leaked its
    first 110 bytes unredacted). Truncated by encoded byte length so the cap
    still means what it says once redaction markers have changed the
    string's length."""
    text = vault.redact(raw_bytes.decode('utf-8', errors='replace'))
    encoded = text.encode('utf-8')
    if len(encoded) > _EXEC_MAX_OUTPUT_BYTES:
        text = encoded[:_EXEC_MAX_OUTPUT_BYTES].decode('utf-8', errors='ignore')
        text += '\n...[truncated]'
    return text


_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _win_job_object_for(pid: int):
    """Windows only: create a job object, put ``pid`` in it, return the job
    HANDLE (an int) — or ``None`` on any failure, so the caller falls back to
    plain ``taskkill /T``.

    Why a job object and not just ``taskkill /T /PID <pid>`` (MC-981 review
    finding, reproduced with a probe script): ``/T`` walks the process tree
    from ``pid`` by PPID at the moment it's invoked. A command that forks a
    grandchild and then EXITS ITSELF (the ordinary daemon/fork-and-detach
    shape — confirmed with a script whose child's only job is to spawn a
    sleeper and return) leaves nothing for `/T` to walk from: `pid` is
    already gone by the time the timeout fires, so `taskkill` reports
    "not found" and the grandchild — which kept the stdout pipe open, which
    is *why* ``communicate()`` timed out in the first place — survives
    forever. A job object doesn't have this hole: once a process is a
    member, every process IT creates automatically joins too (as long as
    membership happens before that grandchild is spawned), and the
    membership persists independent of whether the original member is still
    alive. ``TerminateJobObject`` then kills everyone still in the job in one
    call, dead parent or not.

    Assigned as early as possible after ``Popen`` returns to keep the window
    where the child could fork before joining the job as small as possible —
    not zero (that needs ``CREATE_SUSPENDED`` plus manual STARTUPINFO/pipe
    wiring to replace ``subprocess.Popen``, which is a bigger rewrite than
    this fix's scope), but in practice a fresh child process take many
    milliseconds to reach the point of spawning anything, versus the
    microseconds this function takes to run — the probe's fork-and-exit
    shape reproduces the bug we're fixing but does not hit this residual
    window.
    """
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    # Declare HANDLE types: ctypes defaults to a 32-bit int, which can
    # truncate a 64-bit handle on the way out and back in.
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    proc_handle = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not proc_handle:
        kernel32.CloseHandle(job)
        return None
    try:
        ok = kernel32.AssignProcessToJobObject(job, proc_handle)
    finally:
        kernel32.CloseHandle(proc_handle)
    if not ok:
        kernel32.CloseHandle(job)
        return None
    return job


def _kill_process_tree(proc: subprocess.Popen, job_handle=None) -> None:
    """Kill ``proc`` AND every process it spawned, on timeout (MC-981 follow-up
    to MC-979). ``proc.kill()`` — what a bare ``timeout=`` on ``subprocess.run``
    plumbs into — only signals the direct child; a resolved command that forks
    its own grandchild (a daemon, a long sleeper) outlived the timeout because
    nothing ever reaped it. Requires the child to have been started in its own
    group (Windows: ``CREATE_NEW_PROCESS_GROUP``; POSIX: ``start_new_session``)
    — see the ``Popen`` call below. Kills by the PID this process itself
    started, never by image name (see AGENT_RULES.md process hygiene).

    ``job_handle`` (Windows only, from ``_win_job_object_for``) is tried
    FIRST and is the real fix — see that function's docstring for why a bare
    ``taskkill /T`` misses a child that already exited by the time the
    timeout fires. ``taskkill /T`` still runs unconditionally afterward as a
    fallback belt for anything the job object didn't catch (job creation
    failed, or a process that escaped it some other way).

    POSIX's ``os.killpg`` does not have the Windows hole: a process group is
    addressed by its (persistent) group id, not by walking a live PPID chain
    from the original member, so it reaches a grandchild whether or not the
    child that spawned it is still alive. Not independently verified with a
    live POSIX repro in this change (this box is Windows-only) — confirmed
    by POSIX process-group semantics instead: group membership outlives the
    member that created it.
    """
    if os.name == 'nt':
        if job_handle:
            try:
                ctypes.windll.kernel32.TerminateJobObject(job_handle, 1)
            except Exception as e:
                _log(f"[secrets] TerminateJobObject for server-exec pid "
                     f"{proc.pid} failed: {e}")
        try:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        except Exception as e:
            _log(f"[secrets] taskkill on server-exec pid {proc.pid} failed: {e}")
    else:
        try:
            if not _proc_kill.kill_tree(proc.pid, signal.SIGKILL):
                _log(f"[secrets] tree-kill signalled nothing for server-exec pid {proc.pid}")
        except Exception as e:
            _log(f"[secrets] killpg on server-exec pid {proc.pid} failed: {e}")


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
    # process creation outright on Windows).
    #
    # Started in its own process group/session (never inherited from this
    # server) so a timeout can take out the whole tree, not just this direct
    # child — see `_kill_process_tree` above (MC-981: a grandchild the child
    # spawned used to survive `subprocess.run(..., timeout=)`, which only
    # kills the process it started).
    stdin_kw = subprocess.PIPE if stdin_value is not None else subprocess.DEVNULL
    try:
        if os.name == 'nt':
            proc = subprocess.Popen(
                command, env=env, cwd=cwd, stdin=stdin_kw,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            proc = subprocess.Popen(
                command, env=env, cwd=cwd, stdin=stdin_kw,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True)
    except OSError as e:
        return jsonify({'error': f'failed to start command: {e}'}), 400

    # Join the child to a job object as early as possible so any grandchild
    # it spawns inherits membership too, even if the child itself has already
    # exited by the time a timeout fires — see `_win_job_object_for` above
    # for why `taskkill /T` alone misses that shape. None on POSIX, or on any
    # Windows failure (falls back to `taskkill /T` only).
    job_handle = _win_job_object_for(proc.pid) if os.name == 'nt' else None
    try:
        stdout_bytes, stderr_bytes = proc.communicate(
            input=stdin_value.encode('utf-8') if stdin_value is not None else None,
            timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc, job_handle)
        # Drain whatever the tree had already written before it died —
        # best-effort, bounded so a wedged pipe can't hang the request.
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=10)
        except Exception:
            stdout_bytes, stderr_bytes = b'', b''
        exit_code = None
    finally:
        if job_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(job_handle)
            except Exception as e:
                _log(f"[secrets] CloseHandle for server-exec job object failed: {e}")

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
