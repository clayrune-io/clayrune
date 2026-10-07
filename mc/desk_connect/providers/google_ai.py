"""Google AI Studio (Gemini API key): the key Veo and the Gemini image models use.

Stored as `gemini-api`, the entry the Desk engine reads. Verified by the one free
read call Connections' "Test connection" already makes (list one model); see
`desk_oauth.test_key`.
"""
from __future__ import annotations

from mc import desk_oauth as _oauth
from mc import secrets_store as _vault
from mc.desk_connect.providers import base
from mc.desk_connect.providers.key_paste import KeyPaste


class GoogleAiProvider(base.Provider):
    service_id = 'google_ai'
    summaries = {'api_key': 'Stores your Gemini API key in the Vault as "gemini-api" and checks it with one free read '
                            'call. Clayrune does not generate anything yet.'}

    def __init__(self) -> None:
        self._key = KeyPaste(vault='gemini-api', secret_label='Gemini API key',
                             hint='Create it in Google AI Studio; billing must be on for video.')

    def fields(self, method, vault_names=None):
        return self._key.fields(vault_names)

    def clean(self, method, fields, vault_names=None):
        return self._key.clean(fields, vault_names)

    def apply(self, method, clean, undo):
        self._key.apply(clean, undo, {s['name'] for s in _vault.list_secrets()})
        return base.Applied(extra={'stored': [self._key.vault]})

    def credential_state(self, method, account_id=None):
        return self._key.state()

    def verify(self, method, account_id=None):
        out = _oauth.test_key('gemini')
        if out.get('ok'):
            return base.Probe(True, out.get('message') or 'Google accepted the key.', identity=self._key.vault,
                              capability='list models (read-only)')
        return base.Probe(False, out.get('message') or 'Google did not accept the key.', kind='rejected')
