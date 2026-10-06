"""Higgsfield: sign in with your plan (OAuth over its MCP server) or, advanced, a
developer API key. Both feed the Desk engines that already exist; this provider
only connects and checks them.

  oauth    no pasted value. The one Save starts the sign-in (`desk_oauth.start`, the
           same human-started flow Connections uses); the token reaches the vault
           only through that flow's callback. Checked by `tools/list` on the MCP
           server with the access token: read-only, free, proves the server accepts
           the sign-in.
  api_key  the key ID and secret, stored as `higgsfield` (username = key ID).
           Checked by the engine's own price-quote endpoint, which is free and spends
           nothing.
"""
from __future__ import annotations

from mc import desk_engines as _engines
from mc import desk_oauth as _oauth
from mc import secrets_store as _vault
from mc.desk_connect.providers import base
from mc.desk_connect.providers.key_paste import KeyPaste

_OAUTH_ENTRY = 'oauth.higgsfield'
_QUOTE_MODEL = 'higgsfield-ai/soul/standard'
_QUOTE_BODY = {'prompt': 'connection check', 'num_images': 1, 'resolution': '2K', 'aspect_ratio': '1:1'}


class HiggsfieldProvider(base.Provider):
    service_id = 'higgsfield'
    summaries = {
        'oauth': 'Sign in to Higgsfield in the browser pane on the next step. Nothing is saved until you press Save on the '
                 'Review step, which keeps the sign-in in Secrets as "oauth.higgsfield".',
        'api_key': 'Stores your Higgsfield key ID and secret in Secrets as "higgsfield" and checks them with one '
                   'free price-quote call. Billed in dollars.',
    }
    signs_in = frozenset({'oauth'})

    def __init__(self) -> None:
        self._key = KeyPaste(vault='higgsfield', secret_label='API key secret', entry_type=_vault.ENTRY_API_KEY_PAIR,
                             user_label='API key ID', hint='Create an API key in the Higgsfield console.')

    def fields(self, method, vault_names=None):
        return [] if method == 'oauth' else self._key.fields(vault_names)

    def clean(self, method, fields, vault_names=None):
        if method == 'oauth':
            base.take_fields(fields, set())
            return {}
        return self._key.clean(fields, vault_names)

    def apply(self, method, clean, undo):
        if method == 'oauth':
            if _oauth.status('higgsfield')['state'] == 'connected':
                raise base.ProviderError('Higgsfield is already signed in. Disconnect it in Connections first '
                                         'to sign in again.', 409, 'already_signed_in')
            return base.Applied()
        self._key.apply(clean, undo, {s['name'] for s in _vault.list_secrets()})
        return base.Applied(extra={'stored': [self._key.vault]})

    def after_commit(self, method, clean, applied):
        if method != 'oauth':
            return {}
        try:
            return {'signin': _oauth.start('higgsfield')}
        except _oauth.OAuthError as e:
            return {'setup': {'state': 'failed', 'message': str(e)}}

    def credential_state(self, method, account_id=None):
        if method == 'api_key':
            return self._key.state()
        st = _oauth.status('higgsfield')['state']
        return {'state': {'connected': 'signed_in'}.get(st, st), 'entry': _OAUTH_ENTRY if st != 'not_connected' else None}

    def verify(self, method, account_id=None):
        return self._verify_oauth() if method == 'oauth' else self._verify_key()

    def _verify_oauth(self) -> base.Probe:
        try:
            token = _oauth.access_token('higgsfield', consumer='desk_connect:verify')
            _engines._mcp_post(token, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': _engines._MCP_PROTOCOL, 'capabilities': {},
                'clientInfo': {'name': 'Clayrune', 'version': '1'}}}, expect_id=1)
            _engines._mcp_post(token, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, expect_id=None)
            _engines._mcp_post(token, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, expect_id=2)
        except _oauth.OAuthError as e:
            kind = 'rejected' if e.code in ('needs_signin', 'not_connected') else 'unreachable'
            return base.Probe(False, str(e), kind=kind)
        except _engines.EngineError as e:
            return base.Probe(False, f'Higgsfield did not accept the sign-in: {e}',
                              kind='rejected' if e.kind == 'auth' else 'unreachable')
        except _vault.SecretsError as e:
            return base.Probe(False, f'the saved sign-in could not be read: {_oauth._safe(e)}', kind='unavailable')
        return base.Probe(True, 'Higgsfield accepted the sign-in.', identity=_OAUTH_ENTRY,
                          capability='list tools (read-only)')

    def _verify_key(self) -> base.Probe:
        try:
            secret = _vault.get_secret_value('higgsfield', consumer='desk_connect:verify')
            user = _vault.get_username('higgsfield')
        except _vault.SecretNotFound:
            return base.Probe(False, 'No Higgsfield key is saved yet.', kind='unavailable')
        except _vault.SecretsError as e:
            return base.Probe(False, f'the saved key could not be read: {_oauth._safe(e)}', kind='unavailable')
        try:
            _engines._json_call('POST', f'{_engines._HIGGS_BASE}/estimate/{_QUOTE_MODEL}',
                                {'Authorization': f'Key {user}:{secret}', 'Accept': 'application/json'}, _QUOTE_BODY)
        except _engines.EngineError as e:
            if e.kind == 'auth':
                return base.Probe(False, 'Higgsfield did not accept the key ID and secret. Check both were copied '
                                         'whole, then save them again.', kind='rejected')
            return base.Probe(False, f'Higgsfield could not check the key: {e}',
                              kind='unreachable' if not e.definitive else 'rejected')
        return base.Probe(True, 'Higgsfield accepted the key.', identity='higgsfield',
                          capability='price quote (read-only, free)')
