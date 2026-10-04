"""OpenAI (API key): the key the image engine uses.

Stored as `openai-api`, the entry the Desk engine reads. Verified by the one free
read call Connections' "Test connection" already makes (list models); see
`desk_oauth.test_key`. A ChatGPT or Codex plan does not include API use.
"""
from __future__ import annotations

from mc import desk_oauth as _oauth
from mc import secrets_store as _vault
from mc.desk_connect.providers import base
from mc.desk_connect.providers.key_paste import KeyPaste


class OpenAiProvider(base.Provider):
    service_id = 'openai'
    summaries = {'api_key': 'Stores your OpenAI API key in Secrets as "openai-api" and checks it with one free read '
                            'call. A ChatGPT or Codex plan does not include API use.'}

    def __init__(self) -> None:
        self._key = KeyPaste(vault='openai-api', secret_label='OpenAI API key',
                             hint='Create it on platform.openai.com; API use is billed separately from ChatGPT.')

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
        out = _oauth.test_key('openai')
        if out.get('ok'):
            return base.Probe(True, out.get('message') or 'OpenAI accepted the key.', identity=self._key.vault,
                              capability='list models (read-only)')
        return base.Probe(False, out.get('message') or 'OpenAI did not accept the key.', kind='rejected')
