"""Explicit edits of a provider's existing key; untouched slots stay encrypted.

Only the ordinary passcode-gated provider commit calls apply. A keep marker is
an instruction to retain a slot, never a self-reference or a plaintext read.
"""
from __future__ import annotations

import copy

from mc import secrets_store as vault, secret_field_refs as refs
from mc.core import now_iso
from mc.desk_connect.providers.base import ProviderError, bool_value


def clean(name: str, raw: dict, paired: bool) -> dict:
    current = next((s for s in vault.list_secrets() if s['name'] == name), None)
    if current is None:
        raise ProviderError('this saved key no longer exists; add the connection again', 404, 'not_found')
    out: dict = {'edit_saved': True,
                 'allow_unattended': bool_value(raw, 'allow_unattended', 'Unattended use', current['allow_unattended']),
                 'confirm_replace': bool_value(raw, 'confirm_replace', 'Replace saved key', False)}
    for field, key in [('secret', 'secret')] + ([('key_id', 'username')] if paired else []):
        value = raw.get(field)
        if isinstance(value, dict) and value == {'keep': True} and type(value['keep']) is bool:
            out[key] = {'keep': True}
        else:
            try:
                out[key] = refs.clean(value)
            except refs.FieldRefError as e:
                raise ProviderError(str(e), 400, e.code) from None
            if out[key]['name'] == name:
                raise ProviderError('use the saved details to keep this field', 400, 'invalid')
            if not out['confirm_replace']:
                raise ProviderError('Save replaces the saved key. Confirm replacement on Review first.',
                                    409, 'replace_confirmation_required')
    if not paired:
        out['username'] = {'keep': True}
    return out


def apply(name: str, fields: dict, undo) -> None:
    vault.load_master_key()
    with vault._lock:
        store = vault._load_store()
        old = store['secrets'].get(name)
        if old is None:
            raise ProviderError('this saved key no longer exists; add the connection again', 404, 'not_found')
        rec = copy.deepcopy(old)
        bindings = copy.deepcopy(rec.get('field_refs') or {})
        for field, part in [('secret', 'value'), ('username', 'username')]:
            value = fields[field]
            if value == {'keep': True}:
                continue
            if fields.get('confirm_replace') is not True:
                raise ProviderError('Confirm replacement on Review first.', 409, 'replace_confirmation_required')
            binding = refs.clean(value)
            if binding['name'] == name:
                raise refs.FieldRefError()
            refs.resolve(binding)  # Validate actual source policy before changing the canonical entry.
            bindings[part] = binding
            if part == 'username':
                rec['username'] = ''  # Match ordinary bindings; metadata must not show the old key ID.
        if bindings:
            refs.validate_chain(name, bindings, store['secrets'])
            rec['field_refs'] = bindings
        rec['allow_unattended'] = fields['allow_unattended']
        rec['updated_at'] = now_iso()
        # Source resolution may have updated usage counters; don't overwrite it.
        store = vault._load_store()
        store['secrets'][name] = rec
        vault._save_store(store)

        def restore():
            with vault._lock:
                current = vault._load_store()
                stable = lambda r: {k: v for k, v in (r or {}).items() if k not in ('last_used_at', 'use_count')}
                if stable(current['secrets'].get(name)) != stable(rec):
                    raise vault.SecretsError('Saved details changed during this Save; they were left in place.')
                current['secrets'][name] = old
                vault._save_store(current)
        undo.push(f'saved key {name}', restore)
    vault.audit_event('edit_saved_key', name=name)
