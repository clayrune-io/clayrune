"""The Save of a known-host method (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 2): the
adapter's local writes, undone newest-first if a later one fails, then the part that
cannot be undone.

    apply   inside commit's lock: the provider's local writes (vault entries, a Desk
            account), each pushing its undo. A failure unwinds them all and raises
            ProviderError; nothing is left behind except what an undo could not
            remove, and that is named in the message.
    follow  outside the lock, after the commit is durable: the sign-in to start. It
            is NOT a transaction (spec, "Explicit partial-success boundary"): if it
            fails the committed credential and account stay, reported as
            `setup.state == 'failed'`, never Verified.

A sign-in made earlier on the Details step is HELD in server memory (desk_oauth_hold);
clean['held'] names it. Then apply writes it to the vault as its last local write, in the
same undo stack, so a failure anywhere leaves no vault entry and no account, and follow
starts nothing (the person already signed in).

A login typed on the Details step (clean['new_login']) is one more local write in that same
stack: the one passcode that authorised the Save authorised it, and if the connection fails the
login is removed again, so neither is kept. Its password goes to the vault and nowhere else;
the result names it (`login`, value-free).

Neither returns a credential value. `commit.py` turns ProviderError into its own
CommitError (this module must not import it: commit imports this one).
"""
from __future__ import annotations

from mc import desk_account_refs as _refs
from mc import desk_oauth as _oauth
from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import providers, verification
from mc.desk_connect import signin_login_store as _logins
from mc.desk_connect.providers.base import Applied, ProviderError
from mc.desk_connect.undo import UndoStack


def apply(clean: dict) -> tuple[Applied, dict]:
    """Make the provider's local writes. Returns `(applied, result)`; `result` is
    safe to remember and to return (names and states only)."""
    prov = providers.for_service(clean['service'])
    if prov is None:
        raise ProviderError('that service has no connector yet', 400, 'method_not_available')
    method = clean['method']
    # A sign-in method stores nothing here, but its sign-in ends by writing the token to the vault: refuse
    # now, not after the person has finished signing in at the vendor.
    new_login = clean.get('new_login')
    if _vault.is_locked() and (method in prov.signs_in or new_login or _writes_secrets(prov, method, clean['fields'])):
        raise ProviderError('the vault is locked: unlock it in Secrets, then save again', 409, 'vault_locked')
    held = clean.get('held')
    fields = clean['fields']
    if clean.get('account_id'):
        fields = {**fields, '_attach_account_id': clean['account_id']}
    if held:
        if method not in prov.signs_in:
            raise ProviderError('that method has no sign-in to claim', 400, 'method_not_available')
        save_app = _oauth.save_client_id(clean['service'], clean['fields'])     # the app this Save stores; checked twice
        try:
            account = _oauth.held_account(clean['service'], held['flow_id'], held['claim'], client_id=save_app)
        except _oauth.OAuthError as e:
            raise ProviderError(str(e), e.status, e.code) from e
        if clean.get('account_id') and account != clean['account_id']:
            raise ProviderError('the sign-in was started for a different account; sign in again', 409, 'account_mismatch')
        if account:                 # the Desk account the sign-in was started for (new or explicitly attached)
            fields = {**fields, '_account_id': account}
    undo = UndoStack()
    try:
        applied = prov.apply(method, fields, undo)
        if new_login:
            _logins.write_with_undo(new_login, undo)
        if held:
            arg = applied.extra['oauth_arg'] if 'oauth_arg' in applied.extra else _refs.oauth_arg_for(applied.account_id)
            _oauth.commit_held(clean['service'], held['flow_id'], held['claim'],
                               arg, undo, client_id=save_app)
    except ProviderError as e:
        left = undo.unwind()
        raise ProviderError(_with_left(str(e), left), e.status, e.code) from e
    except (_oauth.OAuthError, _logins.LoginError) as e:
        left = undo.unwind()
        raise ProviderError(_with_left(str(e), left), e.status, e.code) from e
    except Exception as e:
        _log(f'[desk_connect] {clean["service"]}/{method} failed unexpectedly ({type(e).__name__}); undoing', flush=True)
        left = undo.unwind()
        raise ProviderError(_with_left('could not save; see the server log', left), 500, 'record_failed') from e
    undo.commit()
    if held:
        _oauth.consume_held(held['flow_id'])        # the saved one now: forget the held copy, no revoke
    try:
        status = verification.status(clean['service'], method, applied.account_id)
    except Exception as e:                  # the save has landed: a status that cannot be read must not undo that
        _log(f'[desk_connect] {clean["service"]}/{method} saved; its status could not be read ({type(e).__name__})', flush=True)
        status = {'state': 'unknown', 'label': 'Saved; its status could not be read', 'entry': None}
    result = {'service': {'id': clean['service'], 'label': clean['label']}, 'method': method,
              'credential': None, 'stored': list(applied.extra.get('stored') or []),
              'account': applied.extra.get('account'),
              'status': status,
              'account_id': applied.account_id}
    if new_login:
        result['login'] = _logins.public(new_login)
    return applied, result


def follow(clean: dict, applied: Applied) -> dict:
    """What happens after the durable commit: the sign-in. Never raises. Nothing starts for a
    Save that claimed a held sign-in: the person already signed in."""
    if clean.get('held'):
        return {}
    prov = providers.for_service(clean['service'])
    try:
        return prov.after_commit(clean['method'], clean['fields'], applied) if prov else {}
    except Exception as e:
        _log(f'[desk_connect] {clean["service"]} sign-in start raised {type(e).__name__}', flush=True)
        return {'setup': {'state': 'failed', 'message': 'the sign-in could not be started; see the server log'}}


def _writes_secrets(prov, method: str, fields: dict) -> bool:
    return any(f.get('kind') == 'secret' and fields.get(f['key']) for f in prov.fields(method, None))


def _with_left(msg: str, left: list) -> str:
    if left:
        msg += f'; this could not be removed and needs removing by hand in Secrets or Desk: {", ".join(left)}'
    return msg
