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

An existing vault name for a typed credential is refused, never replaced: the compensating delete is only
safe for an entry this call created, and replacing someone's credential is the
explicit-consent step the spec reserves for a later slice.

MC-1062/12b adds existing-name references and bounded, labelled reference-only
API/PyPI metadata. reference_draft validates them; reference_save owns the local
writes. Referenced entries are never resolved, changed or rolled back.

Duplicate protection is per process and in memory: `request_id` plus a keyed hash
of the validated draft. The same id with the same draft answers the first
result without writing again; the same id with a different draft is a 409. A
FAILED commit is not remembered, so the form can retry it. The hash is keyed with
a per-process random key and kept only in memory, so it is not a way to test a
guess at the value.

A known-host method (slice 2: X sign-in, Higgsfield sign-in or key, a Gemini or
OpenAI key) goes through `provider_commit` instead: the adapter's own writes, undone
newest-first if a later one fails, then the sign-in (which cannot be undone, and is
reported as "setup failed" rather than rolled back). The service is taken from the
address's HOST via the registry, never from anything the browser names.

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
from mc.desk_connect import methods as _methods
from mc.desk_connect import provider_commit, providers, registry, url_check
from mc.desk_connect import signin_login_store as _logins
from mc.desk_connect import account_attach, x_account_attach, reference_draft
from mc.desk_connect.providers.base import ProviderError
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


def _clean_provider_draft(draft: dict, checked: dict, vault_names) -> dict:
    """The draft of a known-host method: the service from the address's host, the
    method one the registry offers as available AND the service's provider supports,
    the typed fields as the provider validates them. No generic credential, no name."""
    svc = registry.lookup(checked['host'])
    method = draft.get('method')
    prov = providers.for_service(svc['id']) if svc else None
    if svc is None or prov is None or not isinstance(method, str) or not _methods.is_connectable(svc, method):
        raise CommitError('that method cannot be set up from here: it is information only for this service',
                          400, 'method_not_available')
    unknown = sorted(set(draft) - {'url', 'method', 'fields', 'held', 'new_login', 'account_id'})
    if unknown:
        raise CommitError(f'unknown draft field(s) for this method: {", ".join(unknown)}')
    raw_fields = draft.get('fields')
    attached = None
    if 'account_id' in draft:
        try:
            identity = raw_fields.get('identity') if isinstance(raw_fields, dict) else None
            if identity is not None and not isinstance(identity, str):
                raise CommitError('X handle must be text')
            attached = x_account_attach.target(svc['id'], draft['account_id'], identity=identity)
        except (account_attach.AttachError, LookupError) as e:
            raise CommitError(str(e), getattr(e, 'status', 400), getattr(e, 'code', 'account_refused')) from e
        if not isinstance(raw_fields, dict):
            raise CommitError('fields must be an object')
        raw_fields = {**raw_fields, 'identity': attached['identity']}
    try:
        fields = prov.clean(method, raw_fields, vault_names)
    except ProviderError as e:
        raise CommitError(str(e), e.status, e.code) from e
    out = {'url': checked['url'], 'method': method, 'service': svc['id'], 'label': svc['label'], 'fields': fields}
    if attached:
        out['account_id'] = attached['account_id']
    if draft.get('held') is not None:
        out['held'] = _clean_held(draft['held'], prov, method)
    if draft.get('new_login') is not None:
        out['new_login'] = _clean_new_login(draft['new_login'], prov, svc['id'], method, vault_names)
    return out


def _clean_new_login(raw, prov, service: str, method: str, vault_names) -> dict:
    """A login typed on the Details step of a sign-in method, stored by this same Save (one passcode).
    The result carries the password: it reaches `provider_commit.apply` and nothing else."""
    if method not in prov.signs_in:
        raise CommitError('that method has no sign-in to type a login into', 400, 'method_not_available')
    try:
        return _logins.clean_for_save(raw, service, method, vault_names)
    except _logins.LoginError as e:
        raise CommitError(str(e), e.status, e.code) from e


def _clean_held(raw, prov, method: str) -> dict:
    """{flow_id, claim} of a sign-in the Details step made and the server holds in memory.
    Only a method that signs in has one; the two values are opaque text the server checks."""
    if method not in prov.signs_in:
        raise CommitError('that method has no sign-in to claim', 400, 'method_not_available')
    if not isinstance(raw, dict) or set(raw) != {'flow_id', 'claim'}:
        raise CommitError('held must be {flow_id, claim}')
    for key in ('flow_id', 'claim'):
        if not isinstance(raw[key], str) or not 1 <= len(raw[key]) <= 200:
            raise CommitError(f'held.{key} must be text')
    return {'flow_id': raw['flow_id'], 'claim': raw['claim']}


def clean_draft(draft, own_hosts=(), vault_names=None) -> dict:
    """Validate a draft into the exact shape that is saved. Raises CommitError.
    Writes nothing; an explicit X attachment reads the saved-account contract. `vault_names` (names only,
    from the route) lets a known-host method refuse an already-stored entry before
    the passcode is asked; None skips that, `commit` checks again."""
    if not isinstance(draft, dict):
        raise CommitError('draft must be an object')
    unknown = sorted(set(draft) - {'url', 'method', 'name', 'credential', 'fields', 'held', 'new_login', 'account_id', 'reference_draft'})
    if unknown:
        raise CommitError(f'unknown draft field(s): {", ".join(unknown)}')
    try:
        checked = url_check.check_url(draft.get('url'), own_hosts=own_hosts)
    except UrlError as e:
        raise CommitError(f'{e} {e.hint}'.strip(), 400, 'bad_url') from e
    if draft.get('method') != 'save_for_agents':
        return _clean_provider_draft(draft, checked, vault_names)
    if 'account_id' in draft:
        raise CommitError('"Save for agents" does not attach an account')
    if 'fields' in draft:
        raise CommitError('"Save for agents" takes a credential, not fields')
    if 'new_login' in draft:
        raise CommitError('"Save for agents" takes a credential, not a sign-in login')
    try:
        name = _services._clean_name(draft.get('name'))
    except _services.ServiceError as e:
        raise CommitError(str(e)) from e
    out = {'url': checked['url'], 'method': 'save_for_agents', 'name': name, 'credential': None}

    if 'reference_draft' in draft:
        try:
            out['reference_draft'] = reference_draft.clean(draft['reference_draft'], own_hosts=own_hosts)
        except reference_draft.ReferenceError as e:
            raise CommitError(str(e)) from e

    cred = draft.get('credential')
    if cred is None:
        return out
    if not isinstance(cred, dict):
        raise CommitError('credential must be an object')
    if 'existing' in cred:
        try:
            out['credential'] = reference_draft.credential_reference(cred, vault_names)
        except reference_draft.ReferenceError as e:
            raise CommitError(str(e)) from e
        return out
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
    if clean.get('new_login'):             # a typed login counts by its name and type only: its password is never hashed
        clean = {**clean, 'new_login': _logins.public(clean['new_login'])}
    blob = json.dumps(clean, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.new(_key, blob, hashlib.sha256).hexdigest()


def is_known_request(request_id: str) -> bool:
    """True when this request id has already been saved. The route then skips its
    "that entry already exists" pre-check, which would otherwise see the entries the
    first request itself wrote and refuse the replay of a lost response; `commit`
    compares the draft and answers with the remembered result."""
    with _lock:
        return request_id in _done


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
        if clean['method'] == 'save_for_agents':
            result = _save_for_agents(clean)
            applied = None
        else:
            try:
                applied, result = provider_commit.apply(clean)
            except ProviderError as e:
                raise CommitError(str(e), e.status, e.code) from e
        _done[request_id] = (fp, result)
        while len(_done) > _REMEMBER:
            _done.popitem(last=False)
    if applied is None:
        return result, False
    # Outside the lock: starting a sign-in is network. The remembered result above
    # has no sign-in in it: a replayed request must not reopen a stale one.
    return {**result, **provider_commit.follow(clean, applied)}, False


def _save_for_agents(clean: dict) -> dict:
    from mc.desk_connect.reference_save import save
    return save(clean)
