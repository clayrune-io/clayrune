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
"""

from flask import Blueprint, jsonify, request

from mc import secrets_store as vault
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
            description=data.get('description') or '',
            hint=data.get('hint') or '',
            scope=(data.get('scope') or 'global').strip() or 'global',
            allow_unattended=bool(data.get('allow_unattended', True)),
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
            description=data.get('description', current['description']),
            hint=data.get('hint', current['hint']),
            scope=data.get('scope', current['scope']),
            allow_unattended=bool(
                data.get('allow_unattended', current['allow_unattended'])),
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
        else:
            report.append({'name': n, 'ok': True})
    return jsonify({'referenced': report,
                    'resolvable': all(r['ok'] for r in report)})
