"""How a LATER connection attaches to an account a browser setup already saved (MC-1062 ticket 03,
"Boundary"; consumed by ticket 09's API screen).

The contract, one stable account id:

  * the account is named by its ID. A connection that arrives later (an X developer app's OAuth
    sign-in) belongs to THAT account: it adds its own token and profile references to it and never
    calls `desk_accounts.create_account`, so no duplicate account appears;
  * the account already owns its OAuth references (`account['credentials']`: `oauth_vault`,
    `oauth_profile`, bound by `desk_account_refs` when the account was made). An attachment uses those,
    not the browser setup's profile or login, which are a different sign-in;
  * an attachment never writes `browser_setup`, `read_via`, the legacy `browser_profile` or
    `connections`: attaching an API connection does not rewrite how the account reads through the
    browser, and a browser setup does not touch the OAuth references.

`target` is the read that states this: it validates the account against the service (and, when the
caller holds the handle the person typed, against that identity) and returns the references an
attachment may use, names only. It writes nothing.

The provider side is built in MC-1062/03b (`x_account_attach`): `start-held` and the provider
Save accept an explicit existing account id and validate through this contract. Calls without
an account id retain the original create path. Both human passcode boundaries stay in place.
"""
from __future__ import annotations

from mc import desk as _desk
from mc.desk_connect import browser_setup_record as _record


class AttachError(ValueError):
    """A refusal with the HTTP status and a short machine `code`."""

    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


def target(service: object, account_id: object, *, identity: str | None = None) -> dict:
    """The existing account a later connection attaches to, as names only:

        {'account_id', 'platform', 'identity', 'label', 'account_kind',
         'oauth': {'oauth_vault', 'oauth_profile'} | None,       # the account's own OAuth references
         'browser': {'route_id', 'account_kind', 'refs'} | None}  # the saved browser setup, for display

    Raises AttachError: 400 for a bad id, 404 when there is no such account, 400 `wrong_platform` when it
    is on another service, 409 `identity_mismatch` when `identity` (a handle the person typed, with or
    without a leading @) is not this account's."""
    if not (isinstance(service, str) and isinstance(account_id, str) and account_id):
        raise AttachError('name the service and the account to attach to', 400, 'bad_account')
    with _desk._store_lock:
        rec = (_desk._read_store().get('accounts') or {}).get(account_id)
    if not isinstance(rec, dict):
        raise AttachError('account not found', 404, 'account_not_found')
    if rec.get('platform') != service:
        raise AttachError(f'that account is on {rec.get("platform")}, not {service}', 400, 'wrong_platform')
    if identity is not None and (rec.get('identity') or '').lower() != identity.lstrip('@').strip().lower():
        raise AttachError('that is not the account you picked: its identity differs from the one entered', 409, 'identity_mismatch')
    creds = rec.get('credentials')
    creds = creds if isinstance(creds, dict) else {}
    oauth = {k: creds[k] for k in ('oauth_vault', 'oauth_profile') if isinstance(creds.get(k), str) and creds[k]}
    setup = _record.of(rec)
    browser = None
    if setup is not None:
        browser = {'route_id': setup['route_id'], 'account_kind': setup.get('account_kind'),
                   'refs': _record.saved_refs(rec, setup['route_id'])}
    return {'account_id': account_id, 'platform': service, 'identity': rec.get('identity') or account_id,
            'label': rec.get('label') or rec.get('identity') or account_id, 'account_kind': _record.kind_of(rec),
            'oauth': oauth or None, 'browser': browser}
