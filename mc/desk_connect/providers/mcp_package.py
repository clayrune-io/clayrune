"""The curated-MCP half every MCP-capable provider shares (docs/DESK_CONNECT_BY_URL_SPEC.md,
slice 4). Composition, not inheritance, like `key_paste.KeyPaste`: a provider holds one of
these for its `mcp` method and keeps the rest of its own methods.

What the method needs, from the reviewed catalogue (`mcp_catalogue.json`) and nothing
else: one vault token, the user's consent to the install card, and the pins they
consented to. The consent is the draft's `install` object:

    {approved: true, package, version, integrity}

`clean` refuses it unless `approved` is true AND the pins equal the catalogue's NOW
(409 `pins_changed`: the package was bumped since Review, so the human must look at
the new card). `apply` checks the pins again under the commit lock. Both run before
anything is written and `clean` runs before the passcode is asked.

Writes, in order: the vault token (undoable, same `KeyPaste` the key providers use).
Registration is NOT here: it is the post-commit provisioning (`after_commit`), because
it needs the network and a tool that may not be installed, and its failure must leave
the committed token in place, reported as "setup failed" (spec, partial-success
boundary). A token already in the vault is used as it is, so a retry after a failed
setup does not ask for it again.
"""
from __future__ import annotations

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import mcp_activation, mcp_catalogue
from mc.desk_connect.providers import base
from mc.desk_connect.providers.key_paste import KeyPaste

_INSTALL_KEYS = {'approved', 'package', 'version', 'integrity'}


class McpPackage:
    def __init__(self, service_id: str):
        self.service_id = service_id

    def entry(self) -> dict:
        e = mcp_catalogue.for_service(self.service_id)
        if e is None:
            raise base.ProviderError('that service has no reviewed MCP package', 400, 'method_not_available')
        return e

    def _key(self, entry: dict) -> KeyPaste:
        c = entry['credential']
        return KeyPaste(vault=c['vault'], secret_label=c['label'], hint=c['hint'], entry_type=_vault.ENTRY_TOKEN)

    def card(self) -> dict:
        """The reviewed catalogue card, plus `notice` when this machine cannot run the server
        today (a passphrase-backed vault, MC-1047). The notice is a plain line, not a block: the
        human may still approve and Save. It is not part of the pins."""
        card = mcp_catalogue.card(self.entry())
        if mcp_activation.passphrase_backed():
            card['notice'] = mcp_activation.PASSPHRASE_NOTICE
        return card

    def fields(self, vault_names) -> list[dict]:
        return self._key(self.entry()).fields(vault_names)

    def _check_install(self, install, entry: dict) -> dict:
        if not isinstance(install, dict) or set(install) != _INSTALL_KEYS:
            raise base.ProviderError('approve the install on the Review step first', 400, 'install_not_approved')
        if install['approved'] is not True:
            raise base.ProviderError('approve the install on the Review step first', 400, 'install_not_approved')
        if {k: install[k] for k in ('package', 'version', 'integrity')} != mcp_catalogue.pins(entry):
            raise base.ProviderError('the package details changed since you reviewed them. Go back to Review and '
                                     'read the new details before approving.', 409, 'pins_changed')
        return mcp_catalogue.pins(entry)

    def clean(self, raw, names=None) -> dict:
        entry = self.entry()
        raw = base.take_fields(raw, {'secret', 'allow_unattended', 'install'})
        pins = self._check_install(raw.get('install'), entry)
        key = self._key(entry)
        if not raw.get('secret') and names is not None and key.vault in names:
            # A retry after a failed setup: the token is already stored and is used as it is.
            out = {'secret': '', 'username': '',
                   'allow_unattended': base.bool_value(raw, 'allow_unattended', 'Unattended use', True)}
        else:
            out = key.clean({k: raw[k] for k in ('secret', 'allow_unattended') if k in raw}, names)
        clash = mcp_activation.conflict(entry)
        if clash:
            raise base.ProviderError(str(clash), clash.status, clash.code)
        return {**out, 'install': {**pins, 'approved': True}}

    def apply(self, clean: dict, undo, names) -> list[str]:
        """The token write. Returns the vault names it stored (empty when it was already there)."""
        entry = self.entry()
        self._check_install(clean.get('install'), entry)
        key = self._key(entry)
        if not clean['secret']:
            if key.vault not in names:
                raise base.ProviderError(f'{entry["credential"]["label"]} is required', 400, 'invalid')
            return []
        key.apply(clean, undo, names)
        return [key.vault]

    def after_commit(self) -> dict:
        """Provisioning, after the durable commit. Never raises."""
        entry = self.entry()
        _log(f'[desk_connect] MCP install approved: {entry["package"]}@{entry["version"]}', flush=True)
        out = mcp_activation.provision(entry)
        out['approval'] = mcp_catalogue.pins(entry)
        try:                                    # the Save's own status was read before provisioning
            from mc.desk_connect import verification
            out['status'] = verification.status(self.service_id, 'mcp')
        except Exception as e:
            _log(f'[desk_connect] MCP {entry["id"]} status could not be read ({type(e).__name__})', flush=True)
        return out

    def state(self) -> dict:
        entry = self.entry()
        key = self._key(entry).state()
        if key['state'] != 'key_stored':
            return key
        if mcp_activation.passphrase_backed():
            return {'state': 'waiting_mc1047', 'entry': key['entry']}
        return {'state': 'registered' if mcp_activation.is_registered(entry) else 'setup_failed', 'entry': key['entry']}
