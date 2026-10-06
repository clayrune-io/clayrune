"""X (Twitter): connect one X account with your own X developer app (OAuth 2.0, PKCE).

The one Save writes, in this order: the app's Client ID (and Client Secret, when the
app is confidential) into the vault under the names `desk_oauth` already reads
(`x.client-id`, `x.client-secret`), then the Desk account. Only after that is
durable does the sign-in start (`after_commit`), in the account's own named
browser profile; its token reaches the vault through the existing callback.

The app is the workspace's, the sign-in is the account's: a second account reuses
the stored Client ID and gets its own sign-in entry and profile
(`mc/desk_account_refs.py`), so it never posts as the first.

X has no free read check, so this provider never reports Verified: `verify` says
so rather than spend a billed read (`GET /2/users/me`) the spec's out-of-scope
list rules out. A completed sign-in is "Signed in", not "Verified".
"""
from __future__ import annotations

import re

from mc import desk_account_refs as _refs
from mc import desk_accounts as _accounts
from mc import desk_oauth as _oauth
from mc import secrets_store as _vault
from mc.desk_connect.providers import base
from mc.desk_connect.providers.key_paste import MAX_VALUE

_HANDLE = re.compile(r'^[A-Za-z0-9_]{1,15}$')
_CLIENT_ID = 'x.client-id'
_CLIENT_SECRET = 'x.client-secret'


class XProvider(base.Provider):
    service_id = 'x'
    summaries = {'oauth': 'Sign in to X in its own browser profile on the next step, with your X app\'s Client ID. Nothing is '
                          'saved until you press Save on the Review step, which stores the Client ID in Secrets (if it is '
                          'not there already), adds the X account and keeps the sign-in.'}
    signs_in = frozenset({'oauth'})

    def guide(self, method):
        return ['Create an app in the X developer portal and turn on OAuth 2.0 (user authentication).',
                f'Set its callback address to {_oauth.x_redirect_uri()}',
                'Copy its Client ID (and Client Secret if it has one) into the fields below.']

    def fields(self, method, vault_names=None):
        return [
            base.text_field('identity', 'X handle', hint='The account you will sign in as, for example @yourname.'),
            base.text_field('label', 'Name for this account', required=False, hint='Optional. Shown in Desk.'),
            base.secret_field('client_id', 'X app Client ID', _CLIENT_ID, names=vault_names),
            base.secret_field('client_secret', 'X app Client Secret', _CLIENT_SECRET, names=vault_names, required=False,
                              hint='Only if your app is confidential.'),
            base.checkbox_field('allow_unattended', 'Scheduled and unattended runs may use the app details', default=True),
        ]

    def clean(self, method, fields, vault_names=None):
        raw = base.take_fields(fields, {'identity', 'label', 'client_id', 'client_secret', 'allow_unattended'})
        identity = base.text_value(raw, 'identity', 'X handle', 40, required=True).lstrip('@')
        if not _HANDLE.match(identity):
            raise base.ProviderError('an X handle is 1-15 letters, digits or _ (for example @yourname)')
        out = {'identity': identity, 'label': base.text_value(raw, 'label', 'Name', 80, required=False),
               'allow_unattended': base.bool_value(raw, 'allow_unattended', 'Unattended use', True)}
        for key, label, vault in (('client_id', 'X app Client ID', _CLIENT_ID),
                                  ('client_secret', 'X app Client Secret', _CLIENT_SECRET)):
            v = raw.get(key)
            if v in (None, ''):
                out[key] = ''
                continue
            if not isinstance(v, str):
                raise base.ProviderError(f'{label} must be text')
            if len(v) > MAX_VALUE:
                raise base.ProviderError(f'{label} is limited to {MAX_VALUE} characters')
            out[key] = v
            if vault_names is not None and vault in vault_names:
                raise _exists(vault)
        if vault_names is not None and not out['client_id'] and _CLIENT_ID not in vault_names:
            raise base.ProviderError('X app Client ID is required: create your X app first and paste its Client ID')
        return out

    def apply(self, method, clean, undo):
        names = {s['name'] for s in _vault.list_secrets()}
        for key, vault in (('client_id', _CLIENT_ID), ('client_secret', _CLIENT_SECRET)):
            if clean[key] and vault in names:
                raise _exists(vault)
        if not clean['client_id'] and _CLIENT_ID not in names:
            raise base.ProviderError('X app Client ID is required: create your X app first and paste its Client ID')
        for key, vault, what in (('client_id', _CLIENT_ID, 'X app Client ID'),
                                 ('client_secret', _CLIENT_SECRET, 'X app Client Secret')):
            if not clean[key]:
                continue
            try:
                _vault.set_secret(vault, clean[key], description=f'{what} (from Connect by address)',
                                  scope='global', allow_unattended=clean['allow_unattended'],
                                  entry_type=_vault.ENTRY_API_KEY)
            except _vault.SecretsError as e:
                raise base.ProviderError(f'the credential could not be stored: {e}', 400, 'vault_refused') from e
            undo.push(f'vault entry {vault}', lambda v=vault: _vault.delete_secret(v))
        try:
            acc = _accounts.create_account('x', clean['identity'], label=clean['label'] or None,
                                           account_id=clean.get('_account_id'))
        except _accounts.AccountError as e:
            raise base.ProviderError(str(e), e.status, 'account_refused') from e
        undo.push(f'Desk account {acc["id"]}', lambda: _undo_account(acc['id']))
        stored = [v for k, v in (('client_id', _CLIENT_ID), ('client_secret', _CLIENT_SECRET)) if clean[k]]
        return base.Applied(extra={'stored': stored,
                                   'account': {'id': acc['id'], 'label': acc['label'], 'identity': acc['identity']}},
                            account_id=acc['id'])

    def after_commit(self, method, clean, applied):
        try:
            return {'signin': _oauth.start('x', _refs.oauth_arg_for(applied.account_id))}
        except _oauth.OAuthError as e:
            return {'setup': {'state': 'failed', 'message': str(e)}}

    def credential_state(self, method, account_id=None):
        arg = _refs.oauth_arg_for(account_id)
        st = _oauth.status('x', arg)['state']
        return {'state': {'connected': 'signed_in'}.get(st, st),
                'entry': _oauth.vault_name('x', arg) if st != 'not_connected' else None}

    def verify(self, method, account_id=None):
        return base.Probe(None, 'X has no free check. Clayrune does not spend a billed read to prove the sign-in: '
                                'it stays "Signed in, not verified" until the first post or read goes through.',
                          kind='unavailable')


def _undo_account(account_id: str) -> bool:
    """Take back the account this Save created (the sign-in claim failed after it), and the legacy
    sign-in names it may have been handed."""
    removed = _accounts.delete_account(account_id)
    _refs.release_legacy(account_id)
    return removed


def _exists(vault: str) -> base.ProviderError:
    return base.ProviderError(f'a secret named "{vault}" already exists, so it is used as it is: leave that field '
                              'empty. Clayrune does not replace a stored credential from here.', 409, 'secret_exists')
