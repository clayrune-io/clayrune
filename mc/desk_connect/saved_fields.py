"""Shared provider-field validation/storage for saved-entry selections."""
from mc import secret_field_refs as refs
from mc.desk_connect.providers.base import ProviderError


def reference(raw):
    try:
        return refs.clean(raw)
    except refs.ReferenceError as e:
        raise ProviderError(str(e), 400, e.code) from None


def create(name, value, *, username: str | dict = '', allow_unattended=True, entry_type=None, undo=None):
    try:
        return refs.create(name, value, username=username,
                           allow_unattended=allow_unattended, entry_type=entry_type, undo=undo)
    except refs.ReferenceError as e:
        raise ProviderError(str(e), 409, e.code) from None
