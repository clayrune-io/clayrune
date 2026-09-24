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

The TOTP probe returns a boolean, never our own code — a route that minted live
second factors would be the plaintext hole this design otherwise refuses.
"""

import hmac
import re
import time

from flask import Blueprint, jsonify, request

from mc import secrets_store as vault
from mc import totp as _totp
from mc.blueprints import local_auth
from mc.core import _log
from mc.unattended import is_unattended_caller

bp = Blueprint('secrets_routes', __name__)

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
