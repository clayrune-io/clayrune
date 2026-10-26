"""Shared provider-field validation/storage for saved-entry selections."""
from mc import secret_field_refs as refs
from mc.desk_connect.providers.base import ProviderError


def reference(raw):
    try:
        return refs.clean(raw)
    except refs.FieldRefError as e:
        raise ProviderError(str(e), 400, e.code) from None


def create(name, value, *, username: str | dict = '', allow_unattended=True, entry_type=None,
           undo=None, confirm_replace=False):
    try:
        return refs.create(name, value, username=username,
                           allow_unattended=allow_unattended, entry_type=entry_type, undo=undo,
                           confirm_replace=confirm_replace)
    except refs.vault.SecretExists:
        raise ProviderError('Save replaces the saved key. Confirm replacement on Review first.',
                            409, 'replace_confirmation_required') from None
    except refs.FieldRefError as e:
        raise ProviderError(str(e), 409, e.code) from None
