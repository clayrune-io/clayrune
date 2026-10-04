"""The one Save of connect-by-URL (docs/DESK_CONNECT_BY_URL_SPEC.md, "One save, one
authorization" and Dave's 2026-10-03 decision that overrules its coordinator).

Slice 1 saves exactly one thing, the "Save for agents" record, plus an optional
vault credential typed in the same flow. Two local writes, in this order:

    1. the vault entry     (`secrets_store.set_secret`, the existing create path)
    2. the Desk record     (`desk_services.create_service`, `data/desk.json`)

No transaction coordinator, no recovery journal. If step 2 fails, step 1 is undone
in the same request by deleting the entry this call created (compensating
rollback). A crash between the two leaves at most one orphan vault entry, visible
and removable in Secrets, and no plaintext anywhere: the value goes into the vault
and nowhere else, never into a log line, a record, an exception message or the
duplicate-request memory below.

An existing vault name is refused, never replaced: the compensating delete is only
safe for an entry this call created, and replacing someone's credential is the
explicit-consent step the spec reserves for a later slice.

Duplicate protection is per process and in memory: `request_id` plus a keyed hash
of the validated draft. The same id with the same draft answers the first
result without writing again; the same id with a different draft is a 409. A
FAILED commit is not remembered, so the form can retry it. The hash is keyed with
a per-process random key and kept only in memory, so it is not a way to test a
guess at the value.

The caller (the route) has already checked the human and the passcode; this
module trusts nothing else it is given and validates the draft again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from collections import OrderedDict

from mc import desk_services as _services
from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import url_check
from mc.desk_connect.url_check import UrlError

MAX_VALUE = 8192
MAX_USERNAME = 200
MAX_DESCRIPTION = 200
_REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{8,80}$')
_REMEMBER = 200


class CommitError(ValueError):
    """A refusal with the HTTP status and a short machine `code` for the route."""

    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


_lock = threading.Lock()
_key = os.urandom(32)
_done: 'OrderedDict[str, tuple[str, dict]]' = OrderedDict()


def _text(v, what: str, limit: int) -> str:
    if v is None:
        return ''
    if not isinstance(v, str):
        raise CommitError(f'{what} must be text')
    v = v.strip()
    if len(v) > limit:
        raise CommitError(f'{what} is limited to {limit} characters')
    return v


def clean_request_id(raw) -> str:
    if not isinstance(raw, str) or not _REQUEST_ID.match(raw):
        raise CommitError('request_id must be 8-80 letters, digits, - or _')
    return raw


def clean_draft(draft, own_hosts=()) -> dict:
    """Validate a draft into the exact shape that is saved. Raises CommitError.
    Pure: touches neither the vault nor the Desk store."""
    if not isinstance(draft, dict):
        raise CommitError('draft must be an object')
    unknown = sorted(set(draft) - {'url', 'method', 'name', 'credential'})
    if unknown:
        raise CommitError(f'unknown draft field(s): {", ".join(unknown)}')
    try:
        checked = url_check.check_url(draft.get('url'), own_hosts=own_hosts)
    except UrlError as e:
        raise CommitError(f'{e} {e.hint}'.strip(), 400, 'bad_url') from e
    if draft.get('method') != 'save_for_agents':
        raise CommitError('only "Save for agents" can be saved yet; the other methods are information only',
                          400, 'method_not_available')
    try:
        name = _services._clean_name(draft.get('name'))
    except _services.ServiceError as e:
        raise CommitError(str(e)) from e
    out = {'url': checked['url'], 'method': 'save_for_agents', 'name': name, 'credential': None}

    cred = draft.get('credential')
    if cred is None:
        return out
    if not isinstance(cred, dict):
        raise CommitError('credential must be an object')
    unknown = sorted(set(cred) - {'name', 'entry_type', 'username', 'value', 'description', 'allow_unattended'})
    if unknown:
        raise CommitError(f'unknown credential field(s): {", ".join(unknown)}')
    cname = cred.get('name')
    if not isinstance(cname, str) or not _vault.valid_name(cname.strip()):
        raise CommitError('the credential name uses lowercase letters, digits, . - _ (for example plausible.api-key)')
    cname = cname.strip()
    if _vault.is_server_internal(cname):
        raise CommitError(f'names starting with "{_vault.SERVER_INTERNAL_PREFIX}" are kept by Clayrune for its own sign-ins')
    value = cred.get('value')
    if not isinstance(value, str) or not value:
        raise CommitError('the credential value is required')
    if len(value) > MAX_VALUE:
        raise CommitError(f'the credential value is limited to {MAX_VALUE} characters')
    etype = cred.get('entry_type')
    if etype not in _vault.ENTRY_TYPES:
        raise CommitError(f'entry_type must be one of: {", ".join(_vault.ENTRY_TYPES)}')
    username = _text(cred.get('username'), 'username', MAX_USERNAME)
    if etype == _vault.ENTRY_API_KEY_PAIR and not username:
        raise CommitError('an API key pair needs its Key ID')
    if etype in (_vault.ENTRY_API_KEY, _vault.ENTRY_TOKEN):
        username = ''
    au = cred.get('allow_unattended', True)
    if not isinstance(au, bool):
        raise CommitError('allow_unattended must be true or false')
    out['credential'] = {'name': cname, 'entry_type': etype, 'username': username, 'value': value,
                         'description': _text(cred.get('description'), 'description', MAX_DESCRIPTION),
                         'allow_unattended': au}
    return out


def _fingerprint(clean: dict) -> str:
    blob = json.dumps(clean, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.new(_key, blob, hashlib.sha256).hexdigest()


def _forget_all_for_tests() -> None:
    with _lock:
        _done.clear()


def commit(request_id: str, clean: dict) -> tuple[dict, bool]:
    """Write the draft. Returns `(result, duplicate)`; `result` never holds a value.
    Raises CommitError. `clean` is `clean_draft`'s output."""
    fp = _fingerprint(clean)
    with _lock:
        seen = _done.get(request_id)
        if seen is not None:
            if hmac.compare_digest(seen[0], fp):
                return seen[1], True
            raise CommitError('that request_id was already used for a different draft', 409, 'request_id_reused')

        cred = clean['credential']
        try:
            existing = {s['name'] for s in _vault.list_secrets()}
        except _vault.SecretsError as e:
            raise CommitError(f'the vault could not be read: {e}', 503, 'vault_unavailable') from e
        if cred:
            if _vault.is_locked():
                raise CommitError('the vault is locked: unlock it in Secrets, then save again', 409, 'vault_locked')
            if cred['name'] in existing:
                raise CommitError(f'a secret named "{cred["name"]}" already exists; pick another name '
                                  f'(Clayrune does not replace a stored credential from here)', 409, 'secret_exists')
        wanted = clean['name'].lower()
        if any((s.get('name') or '').lower() == wanted for s in _services.list_services()):
            raise CommitError(f'{clean["name"]} is already saved', 409, 'service_exists')

        wrote_secret = False
        if cred:
            try:
                _vault.set_secret(cred['name'], cred['value'], username=cred['username'],
                                  description=cred['description'], scope='global',
                                  allow_unattended=cred['allow_unattended'], entry_type=cred['entry_type'])
            except _vault.SecretsError as e:
                raise CommitError(f'the credential could not be stored: {e}', 400, 'vault_refused') from e
            wrote_secret = True
        try:
            svc = _services.create_service(clean['name'], link=clean['url'],
                                           credential=cred['name'] if cred else None)
        except Exception as e:
            _log(f'[desk_connect] record write failed ({type(e).__name__}); undoing the vault entry', flush=True)
            orphan = False
            if wrote_secret:
                try:
                    orphan = not _vault.delete_secret(cred['name'])
                except Exception as e2:
                    orphan = True
                    _log(f'[desk_connect] compensating delete of {cred["name"]!r} failed: {type(e2).__name__}', flush=True)
            if isinstance(e, _services.ServiceError):
                msg, status, code = str(e), e.status, 'service_refused'
            else:
                msg, status, code = 'the service record could not be written', 500, 'record_failed'
            if orphan:
                msg += f'; the credential "{cred["name"]}" was stored and could not be removed: delete it in Secrets'
            raise CommitError(msg, status, code) from e

        result = {'service': svc, 'credential': {'name': cred['name'], 'stored': True} if cred else None}
        _done[request_id] = (fp, result)
        while len(_done) > _REMEMBER:
            _done.popitem(last=False)
        return result, False
