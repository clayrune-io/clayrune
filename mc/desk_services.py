"""Desk "Something else" services: a name, an optional link, an optional vault
credential NAME, kept so agents can see and use a service Clayrune has no
integration for (Ron 2026-10-03; Connections' "Add service" tile).

NOT AN ACCOUNT. `mc/desk_accounts.py` holds the places the Desk can put a
message, each with a publisher and a derived `publish` state; Where's tray,
Launch's offline check and the engagement poll all read it. A service saved here
has none of that, so it lives in its own `store['services']` section of the same
workspace store (`data/desk.json`, same `_store_lock`, outside DATA_DIR, so
nothing to suffix-exclude) and no Desk surface that places or publishes can ever
pick it up. It is workspace-wide, like accounts (the retire-presence decision).

WHAT A RECORD CLAIMS. Exactly one thing: Clayrune remembers it. Every read
carries `kind: 'saved_for_agents'` and `publish: False`, and nothing here dials
the service, so a UI must not say "Connected". An agent reads the list
(`GET /api/desk/services`) to learn the service exists, where it lives, and which
vault entry holds its credential.

CREDENTIALS ARE NEVER HERE. `credential` is the vault entry's NAME. Like
`desk_accounts`, this module only asks the vault whether that name exists
(`secrets_store.list_secrets`, metadata): `credential.in_vault` is derived on
every read, never stored, and a name with nothing behind it reads as
`in_vault: False`. An agent uses the named entry through the vault's own
placeholders (`tools/with-secret.py`); nothing returns a value.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from mc import desk as _desk
from mc import secrets_store
from mc.core import _log, now_iso

MAX_NAME = 80
MAX_LINK = 300
MAX_SERVICES = 200
KIND = 'saved_for_agents'
_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


class ServiceError(ValueError):
    """A refusal with the HTTP status the route should answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _vault_names() -> set:
    """Names the vault holds (metadata only). `set()` plus a log line when it
    cannot be listed, which reads as "not in the vault", never as "found"."""
    try:
        return {s['name'] for s in secrets_store.list_secrets()}
    except Exception as e:
        _log(f'[desk_services] vault listing failed: {e}', flush=True)
        return set()


def v1_service(rec: dict, vault: set | None = None) -> dict:
    cred = rec.get('credential') or ''
    out = {
        'id': rec['id'], 'name': rec.get('name') or rec['id'], 'link': rec.get('link') or '',
        'credential': {'name': cred, 'in_vault': None},
        'kind': KIND, 'publish': False, 'created_at': rec.get('created_at'),
    }
    if 'reference_draft' in rec:
        from mc.desk_connect.reference_draft import stored
        out['reference_draft'] = stored(rec['reference_draft'])
    if cred:
        out['credential']['in_vault'] = cred in (_vault_names() if vault is None else vault)
    return out


def _clean_name(value) -> str:
    if not isinstance(value, str):
        raise ServiceError('name must be text')
    value = value.strip()
    if not value:
        raise ServiceError('name is required')
    if len(value) > MAX_NAME:
        raise ServiceError(f'name is limited to {MAX_NAME} characters')
    return value


def _clean_link(value) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ServiceError('link must be text')
    value = value.strip()
    if not value:
        return ''
    if len(value) > MAX_LINK:
        raise ServiceError(f'link is limited to {MAX_LINK} characters')
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ServiceError('link must be a web address starting with http:// or https://')
    return value


def _clean_credential(value) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ServiceError('credential must be the NAME of a vault entry')
    value = value.strip()
    if not value:
        return ''
    if not secrets_store.valid_name(value):
        raise ServiceError('credential is the NAME of a vault entry (lowercase letters, digits, . - _), '
                           'never the secret itself')
    return value


def _services(store: dict) -> dict:
    return store.setdefault('services', {})


def list_services() -> list[dict]:
    with _desk._store_lock:
        store = _desk._read_store()
    rows = sorted(_services(store).values(), key=lambda r: (r.get('created_at') or '', r['id']))
    vault = _vault_names() if any(r.get('credential') for r in rows) else set()
    return [v1_service(r, vault) for r in rows]


def create_service(name, *, link=None, credential=None, service_id: str | None = None, reference_draft=None) -> dict:
    name = _clean_name(name)
    link = _clean_link(link)
    credential = _clean_credential(credential)
    if reference_draft is not None:
        from mc.desk_connect.reference_draft import stored
        reference_draft = stored(reference_draft)
    if service_id is not None and not (isinstance(service_id, str) and _ID.match(service_id)):
        raise ServiceError('service id must be 1-80 letters, digits, - or _')
    with _desk._store_lock:
        store = _desk._read_store()
        services = _services(store)
        if len(services) >= MAX_SERVICES:
            raise ServiceError(f'at most {MAX_SERVICES} services can be saved', 409)
        if service_id and service_id in services:
            raise ServiceError('a service with that id already exists', 409)
        if any((r.get('name') or '').lower() == name.lower() for r in services.values()):
            raise ServiceError(f'{name} is already saved', 409)
        rec: dict = {'id': service_id or _desk._new_id('svc'), 'name': name, 'link': link,
               'credential': credential, 'created_at': now_iso()}
        if reference_draft is not None:
            rec['reference_draft'] = reference_draft
        services[rec['id']] = rec
        _desk._write_store(store)
    return v1_service(rec)


_PATCHABLE = ('name', 'link', 'credential')


def update_service(service_id: str, patch) -> dict:
    if not isinstance(patch, dict):
        raise ServiceError('body must be a JSON object')
    unknown = sorted(k for k in patch if k not in _PATCHABLE)
    if unknown:
        raise ServiceError(f'cannot change: {", ".join(unknown)}')
    clean = {}
    if 'name' in patch:
        clean['name'] = _clean_name(patch['name'])
    if 'link' in patch:
        clean['link'] = _clean_link(patch['link'])
    if 'credential' in patch:
        clean['credential'] = _clean_credential(patch['credential'])
    with _desk._store_lock:
        store = _desk._read_store()
        services = _services(store)
        rec = services.get(service_id)
        if rec is None:
            raise ServiceError('service not found', 404)
        if 'name' in clean and any(o['id'] != service_id and (o.get('name') or '').lower() == clean['name'].lower()
                                   for o in services.values()):
            raise ServiceError(f'{clean["name"]} is already saved', 409)
        rec.update(clean)
        _desk._write_store(store)
    return v1_service(rec)


def delete_service(service_id: str) -> bool:
    with _desk._store_lock:
        store = _desk._read_store()
        services = _services(store)
        if service_id not in services:
            return False
        del services[service_id]
        _desk._write_store(store)
    return True
