"""What a known-host adapter is (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 2).

A provider turns "connect <service> by <method>" into the writes Clayrune already
knows how to make, with its fields, its sign-in and its one read-only check. One
provider per module in this package, registered in `__init__`; the registry
(`registry.json`) stays data and names no code.

A provider owns no network and no vault code of its own beyond calling the
existing modules (`secrets_store`, `desk_accounts`, `desk_oauth`, the engine
transport). It never returns a credential value: `apply` and `after_commit`
return metadata, `verify` returns a `Probe`.

Field specs are plain dicts the screen renders (never a value):

    {'key', 'label', 'kind': 'text'|'secret'|'checkbox', 'required', 'hint',
     'vault': <vault entry the value is stored under, secrets only>,
     'present': True when that entry already exists (then it is not asked again)}

Stored keys never imply Verified: only `verify` returning `ok=True` can, and
`mc.desk_connect.verification` binds that to the vault entry it checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class ProviderError(ValueError):
    """A refusal with the HTTP status and a short machine `code` for the route."""

    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class Probe:
    """The outcome of one read-only check. `ok` None = this method has no free
    check; the state is then never "verified"."""
    ok: bool | None
    message: str
    identity: str = ''
    capability: str = ''
    kind: str = ''          # 'rejected' | 'unreachable' | 'unavailable' on a failure


@dataclass
class Applied:
    """What `apply` wrote. `extra` goes to the client verbatim (metadata only);
    `account_id` names the Desk account made, for the sign-in that follows."""
    extra: dict = field(default_factory=dict)
    account_id: str | None = None


class Provider:
    service_id = ''
    # method -> one honest sentence for the Review step.
    summaries: dict[str, str] = {}
    # methods that end in a browser sign-in rather than a pasted key.
    signs_in: frozenset = frozenset()

    def supports(self, method: str) -> bool:
        return method in self.summaries

    def guide(self, method: str) -> list[str]:
        """Short setup steps to show above the fields (no values)."""
        return []

    def fields(self, method: str, vault_names=None) -> list[dict]:
        raise NotImplementedError

    def clean(self, method: str, fields, vault_names=None) -> dict:
        """Validate the typed fields into the exact shape `apply` stores. Pure.
        Raises ProviderError. `vault_names` (names only) lets it check what is
        already stored; None skips those checks (`apply` makes them again)."""
        raise NotImplementedError

    def apply(self, method: str, clean: dict, undo) -> Applied:
        """Make the local writes, pushing an undo per write. Raises ProviderError
        before the first write when something is already stored or missing."""
        raise NotImplementedError

    def after_commit(self, method: str, clean: dict, applied: Applied) -> dict:
        """What follows a durable commit (a sign-in to start). Never raises: a
        failure is `{'setup': {'state': 'failed', 'message': ...}}`."""
        return {}

    def credential_state(self, method: str, account_id: str | None = None) -> dict:
        """`{state, entry}`: `state` is `not_connected`, `key_stored`, `signed_in`
        `needs_signin` or `key_unreadable`; `entry` the vault entry it rests on (or None). From
        vault metadata only: nothing is decrypted."""
        raise NotImplementedError

    def verify(self, method: str, account_id: str | None = None) -> Probe:
        """ONE free, read-only call with the stored credential."""
        raise NotImplementedError


def text_field(key: str, label: str, *, required: bool = True, hint: str = '') -> dict:
    return {'key': key, 'label': label, 'kind': 'text', 'required': required, 'hint': hint}


def secret_field(key: str, label: str, vault: str, *, names=None, required: bool = True, hint: str = '') -> dict:
    present = vault in (names or ())
    return {'key': key, 'label': label, 'kind': 'secret', 'required': required and not present,
            'hint': hint, 'vault': vault, 'present': present}


def checkbox_field(key: str, label: str, *, default: bool = True, hint: str = '') -> dict:
    return {'key': key, 'label': label, 'kind': 'checkbox', 'required': False, 'hint': hint, 'default': default}


def take_fields(raw, allowed: set) -> dict:
    """The typed `fields` object, with unknown keys refused."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ProviderError('fields must be an object')
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ProviderError(f'unknown field(s): {", ".join(unknown)}')
    return raw


def text_value(raw: dict, key: str, label: str, limit: int, *, required: bool) -> str:
    v = raw.get(key)
    if v is None or v == '':
        if required:
            raise ProviderError(f'{label} is required')
        return ''
    if not isinstance(v, str):
        raise ProviderError(f'{label} must be text')
    v = v.strip()
    if not v and required:
        raise ProviderError(f'{label} is required')
    if len(v) > limit:
        raise ProviderError(f'{label} is limited to {limit} characters')
    return v


def bool_value(raw: dict, key: str, label: str, default: bool) -> bool:
    v = raw.get(key, default)
    if not isinstance(v, bool):
        raise ProviderError(f'{label} must be true or false')
    return v
