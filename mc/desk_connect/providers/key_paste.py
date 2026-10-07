"""The pasted-key half every key-based provider shares: one vault entry, typed
here, stored under the name the Desk engines already read (`gemini-api`,
`openai-api`, `higgsfield`). Composition, not inheritance: a provider holds one of
these per key method and keeps its own verify.

An entry that already exists is never replaced (`secret_exists`, 409): replacing a
stored credential is the explicit-consent step reserved for a later slice, and the
undo of a write is only safe for an entry this call created.
"""
from __future__ import annotations

from mc import desk_vault_lock as _vault_lock
from mc import secrets_store as _vault
from mc.desk_connect.providers import base

MAX_VALUE = 8192
MAX_USER = 200
DESCRIPTION = 'Saved from Connect by address'


class KeyPaste:
    def __init__(self, *, vault: str, secret_label: str, hint: str, entry_type: str = _vault.ENTRY_API_KEY,
                 user_label: str | None = None, user_hint: str = ''):
        self.vault = vault
        self.secret_label = secret_label
        self.hint = hint
        self.entry_type = entry_type
        self.user_label = user_label
        self.user_hint = user_hint

    def fields(self, names) -> list[dict]:
        out = []
        if self.user_label:
            out.append(base.text_field('key_id', self.user_label, hint=self.user_hint))
        out.append(base.secret_field('secret', self.secret_label, self.vault, names=names, hint=self.hint))
        out.append(base.checkbox_field('allow_unattended', 'Scheduled and unattended runs may use it', default=True))
        return out

    def clean(self, raw, names=None) -> dict:
        allowed = {'secret', 'allow_unattended'} | ({'key_id'} if self.user_label else set())
        raw = base.take_fields(raw, allowed)
        secret = raw.get('secret')
        if not isinstance(secret, str) or not secret:
            raise base.ProviderError(f'{self.secret_label} is required')
        if len(secret) > MAX_VALUE:
            raise base.ProviderError(f'{self.secret_label} is limited to {MAX_VALUE} characters')
        user = base.text_value(raw, 'key_id', self.user_label or '', MAX_USER, required=True) if self.user_label else ''
        if names is not None and self.vault in names:
            raise self.exists()
        return {'secret': secret, 'username': user,
                'allow_unattended': base.bool_value(raw, 'allow_unattended', 'Unattended use', True)}

    def exists(self) -> base.ProviderError:
        return base.ProviderError(f'a secret named "{self.vault}" already exists: it is already connected. '
                                  'Clayrune does not replace a stored credential from here; remove it in the Vault first.',
                                  409, 'secret_exists')

    def apply(self, clean: dict, undo, names) -> None:
        if self.vault in names:
            raise self.exists()
        try:
            _vault.set_secret(self.vault, clean['secret'], username=clean['username'], description=DESCRIPTION,
                              scope='global', allow_unattended=clean['allow_unattended'],
                              entry_type=self.entry_type)
        except _vault.SecretsError as e:
            raise base.ProviderError(f'the credential could not be stored: {e}', 400, 'vault_refused') from e
        undo.push(f'vault entry {self.vault}', lambda: _vault.delete_secret(self.vault))

    def state(self) -> dict:
        try:
            have = {s['name'] for s in _vault.list_secrets()}
        except _vault.SecretsError:
            return {'state': 'not_connected', 'entry': None}
        if self.vault not in have:
            return {'state': 'not_connected', 'entry': None}
        opened = _vault_lock.probe(self.vault)
        if opened == _vault_lock.LOCKED:         # the key is saved; the vault needs unlocking, not the key replacing
            return {'state': _vault_lock.VAULT_LOCKED, 'entry': self.vault}
        return {'state': 'key_stored' if opened == _vault_lock.OK else 'key_unreadable', 'entry': self.vault}
