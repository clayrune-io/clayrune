"""A NEW stored login for a sign-in route (docs/DESK_SERVICE_PROFILES_SPEC.md, "P2b").

One vault entry holds the username AND the password (`entry_type` login), typed into the
shared secret form (`static/js/secret-form.js`). This module only validates that typed
entry and writes it; WHEN it is written is the caller's passcode-gated Save, never sooner:

    purposes Save   `purpose_bindings.commit` writes the entry first and removes it again if
                    the binding write that follows fails (compensating rollback)
    sign-in Save    `POST /api/desk/connect/signin/store-login`, for a route with no purposes
                    screen (Higgsfield's sign-in)

The value is held only in the request that carries it. It is never put in a draft
fingerprint, a remembered result, a log line or an error message (`public` is the
value-free view those use). A stored entry is never replaced from here.
"""
from __future__ import annotations

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import commit as _commit


class LoginError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


_FIELDS = {'name', 'username', 'value', 'description', 'allow_unattended'}


def clean(raw) -> dict:
    """Validate the typed login. Raises LoginError. The result carries the value; use
    `public` for anything that is stored, hashed, logged or returned."""
    if not isinstance(raw, dict):
        raise LoginError('new_login must be an object')
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        raise LoginError(f'unknown new_login field(s): {", ".join(unknown)}')
    name = raw.get('name')
    if not isinstance(name, str) or not _vault.valid_name(name.strip()):
        raise LoginError('the login name uses lowercase letters, digits, . - _ (for example linkedin.login)')
    name = name.strip()
    if _vault.is_server_internal(name):
        raise LoginError(f'names starting with "{_vault.SERVER_INTERNAL_PREFIX}" are kept by Clayrune for its own sign-ins')
    value = raw.get('value')
    if not isinstance(value, str) or not value:
        raise LoginError('the password is required')
    if len(value) > _commit.MAX_VALUE:
        raise LoginError(f'the password is limited to {_commit.MAX_VALUE} characters')
    try:
        username = _commit._text(raw.get('username'), 'username', _commit.MAX_USERNAME)
        description = _commit._text(raw.get('description'), 'description', _commit.MAX_DESCRIPTION)
    except _commit.CommitError as e:
        raise LoginError(str(e)) from e
    if not username:
        raise LoginError('a login needs its username: it is typed on the sign-in page with the password')
    au = raw.get('allow_unattended', True)
    if not isinstance(au, bool):
        raise LoginError('allow_unattended must be true or false')
    return {'name': name, 'username': username, 'value': value, 'description': description, 'allow_unattended': au}


def public(login: dict) -> dict:
    """The value-free view: safe to hash into a fingerprint or put in a response."""
    return {'name': login['name'], 'entry_type': _vault.ENTRY_LOGIN, 'allow_unattended': login['allow_unattended']}


def check_free(login: dict) -> None:
    """Refuse a name that is taken, and a locked vault, before anything is written."""
    if _vault.is_locked():
        raise LoginError('the vault is locked: unlock it in Secrets, then save again', 409, 'vault_locked')
    try:
        taken = {s['name'] for s in _vault.list_secrets()}
    except _vault.SecretsError as e:
        raise LoginError(f'the vault could not be read: {e}', 503, 'vault_unavailable') from e
    if login['name'] in taken:
        raise LoginError(f'a secret named "{login["name"]}" already exists; pick it from the list or choose another name '
                         f'(Clayrune does not replace a stored login from here)', 409, 'secret_exists')


def write(login: dict) -> None:
    """Store it (global scope, as the connect flow's credential is). Raises LoginError."""
    check_free(login)
    try:
        _vault.set_secret(login['name'], login['value'], username=login['username'], description=login['description'],
                          scope='global', allow_unattended=login['allow_unattended'], entry_type=_vault.ENTRY_LOGIN)
    except _vault.SecretsError as e:
        raise LoginError(f'the login could not be stored: {e}', 400, 'vault_refused') from e


def remove(name: str) -> bool:
    """Undo a `write` made by this request. False when it could not be removed."""
    try:
        return bool(_vault.delete_secret(name))
    except Exception as e:
        _log(f'[desk_connect] compensating delete of a new login failed: {type(e).__name__}', flush=True)
        return False
