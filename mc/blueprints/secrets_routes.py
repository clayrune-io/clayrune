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

The TOTP probe returns a boolean, never our own code — a route that minted live
second factors would be the plaintext hole this design otherwise refuses.
"""

import hmac
import re

from flask import Blueprint, jsonify, request

from mc import secrets_store as vault
from mc import totp as _totp
from mc.core import _log

bp = Blueprint('secrets_routes', __name__)


def _err(e: Exception, code: int = 400):
    return jsonify({'error': str(e)}), code


@bp.route('/api/secrets')
def api_secrets_list():
    project_id = request.args.get('project_id') or None
    try:
        items = vault.list_secrets(project_id)
        backend = vault.key_backend()
    except vault.SecretsError as e:
        return _err(e, 500)
    return jsonify({
        'secrets': items,
        'key_backend': backend,
        # The UI badges this: a file-backed key is readable by anything running
        # as this user, whereas the OS keyring is at least gated by the login
        # session. Worth telling the operator which one they're on.
        'key_at_rest_warning': (
            'Master key is in a 0600 file — no usable OS keyring backend was '
            'found.' if backend == 'file' else ''),
    })


@bp.route('/api/secrets', methods=['POST'])
def api_secrets_set():
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


@bp.route('/api/secrets/check', methods=['POST'])
def api_secrets_check():
    """Dry-run a template: report which secrets it references and whether each
    would resolve for the given project/attendedness — without decrypting
    anything. Lets an agent verify a command before running it."""
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
        else:
            report.append({'name': n, 'ok': True})
    return jsonify({'referenced': report,
                    'resolvable': all(r['ok'] for r in report)})
