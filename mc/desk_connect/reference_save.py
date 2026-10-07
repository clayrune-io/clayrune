"""The local reference Save and its create-only credential rollback (MC-1062/12b).

Called by Connect commit after the existing human/passcode checks, under its
idempotency lock. Existing vault entries are only referenced, never read or undone.
"""
from mc import desk_services as _services
from mc import secrets_store as _vault
from mc.core import _log


def save(clean: dict) -> dict:
    from mc.desk_connect.commit import CommitError

    cred = clean['credential']
    try:
        existing = {s['name'] for s in _vault.list_secrets()}
    except _vault.SecretsError as e:
        raise CommitError(f'the vault could not be read: {e}', 503, 'vault_unavailable') from e
    if cred and cred.get('existing'):
        if cred['name'] not in existing:
            raise CommitError('that credential is not in the Vault', 409, 'secret_missing')
    if cred and not cred.get('existing'):
        if _vault.is_locked():
            raise CommitError('the Vault is locked: unlock it, then save again', 409, 'vault_locked')
        if cred['name'] in existing:
            raise CommitError(f'a secret named "{cred["name"]}" already exists; pick another name '
                              f'(Clayrune does not replace a stored credential from here)', 409, 'secret_exists')
    wanted = clean['name'].lower()
    if any((s.get('name') or '').lower() == wanted for s in _services.list_services()):
        raise CommitError(f'{clean["name"]} is already saved', 409, 'service_exists')

    wrote_secret = False
    if cred and not cred.get('existing'):
        try:
            _vault.set_secret(cred['name'], cred['value'], username=cred['username'],
                              description=cred['description'], scope='global',
                              allow_unattended=cred['allow_unattended'], entry_type=cred['entry_type'])
        except _vault.SecretsError as e:
            raise CommitError(f'the credential could not be stored: {e}', 400, 'vault_refused') from e
        wrote_secret = True
    try:
        extra = {'reference_draft': clean['reference_draft']} if 'reference_draft' in clean else {}
        svc = _services.create_service(clean['name'], link=clean['url'],
                                       credential=cred['name'] if cred else None, **extra)
    except Exception as e:
        _log(f'[desk_connect] record write failed ({type(e).__name__}); undoing the vault entry', flush=True)
        orphan = False
        if wrote_secret:
            try:
                orphan = not _vault.delete_secret(cred['name'])
            except Exception as e2:
                orphan = True
                _log(f'[desk_connect] compensating delete of {cred["name"]!r} failed: {type(e2).__name__}', flush=True)
        if isinstance(e, _services.ServiceError):
            msg, status, code = str(e), e.status, 'service_refused'
        else:
            msg, status, code = 'the service record could not be written', 500, 'record_failed'
        if orphan:
            msg += f'; the credential "{cred["name"]}" was stored and could not be removed: delete it in the Vault'
        raise CommitError(msg, status, code) from e

    public_credential = None
    if cred:
        public_credential = {'name': cred['name'], 'stored': True}
        if cred.get('existing'):
            public_credential = {'name': cred['name'], 'stored': False, 'referenced': True}
    return {'service': svc, 'credential': public_credential}
