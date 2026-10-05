"""Per-account, per-purpose route bindings (docs/DESK_SERVICE_PROFILES_SPEC.md section 3.3;
slice P2).

An account holds `connections[purpose][capability]` = the route chosen for that capability,
the profile revision the person was shown, the NON-SECRET references the route uses (an
existing sign-in's vault and profile names, a vault entry they picked, a named browser
profile), the scopes the route asks for, a fingerprint of that contract (per capability, so
changing one leaves the others' verification alone) and the time of approval. Different
purposes may use different routes, and one purpose may use several (`read_own.mentions` through the pane, `read_own.post_metrics` through the API). The binding
records which capabilities each route supplies and never infers the missing half.

The one write (`commit`) is the human-passcode Save of the route (`desk_connect_purpose_routes`):

    1. an optional new account (LinkedIn only: a member or a Company Page record), made first
       and removed again if the binding write fails (compensating rollback);
    2. ONE desk.json write under the store lock: the bindings, and the legacy read setting
       (`read_via`, `browser_profile`) when every own-read binding agrees on one way to read.

It creates no vault entry, no browser profile, starts no sign-in, installs nothing and never
touches `credentials`, `organization_id`, `voice` or an existing read setting that the draft
does not change. When own reads are split across the API and the pane, the legacy single
`read_via` is left alone and the split is reported as a gap: engagement reads one account one
way today, and a binding cannot make it do more.

A repeated `request_id` with the same draft answers the first result without writing; the same
id with a different draft is 409. A failed Save is not remembered.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict

from mc import desk as _desk
from mc import desk_accounts as _accounts
from mc import secrets_store as _vault
from mc.core import _log, now_iso
from mc.desk_connect import commit as _commit
from mc.desk_connect import registry as _registry
from mc.desk_connect import route_readiness as _ready

MAX_BINDINGS = 8
_PROFILE_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,40}$')
_ACCOUNT_ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
# What a person may point a route at, by the kind of credential the route uses.
_ROLE_FOR_AUTH = {'browser_signin': 'login', 'api_key': 'api_key', 'bearer': 'api_key'}
_REMEMBER = 200
_NEW_ACCOUNT_SERVICES = ('linkedin',)       # X accounts are made by the X sign-in flow, which owns its sign-in


class BindError(ValueError):
    """A refusal with the HTTP status and a short machine `code` for the route."""

    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


_lock = threading.Lock()
_done: 'OrderedDict[str, tuple[str, dict]]' = OrderedDict()


def _forget_all_for_tests() -> None:
    with _lock:
        _done.clear()


# -- reading ----------------------------------------------------------------------

def groups(rec: dict | None) -> list[dict]:
    """One entry per (purpose, route) bound on this account record, capabilities merged.
    Tolerates a hand-edited record: anything malformed is skipped."""
    out: dict[tuple, dict] = {}
    conns = rec.get('connections') if isinstance(rec, dict) else None
    for purpose, caps in sorted((conns or {}).items()) if isinstance(conns, dict) else []:
        if not isinstance(caps, dict):
            continue
        for cap, b in sorted(caps.items()):
            if not isinstance(b, dict) or not b.get('route_id'):
                continue
            g = out.setdefault((purpose, b['route_id']), {
                'purpose': purpose, 'route_id': b['route_id'], 'service': b.get('service'),
                'account_kind': b.get('account_kind'), 'revision': b.get('revision'),
                'refs': dict(b.get('refs') or {}), 'scopes': list(b.get('scopes') or []),
                'approved_at': b.get('approved_at'), 'fingerprint': b.get('fingerprint'), 'capabilities': []})
            g['capabilities'].append(cap)
    return list(out.values())


def fingerprint(service: str, purpose: str, route_id: str, account_kind: str, capabilities, refs: dict, scopes) -> str:
    """The contract of one bound route. The profile revision is left out on purpose: a newer
    profile changes what is SHOWN, never a local selection (spec section 9)."""
    blob = json.dumps({'service': service, 'purpose': purpose, 'route': route_id, 'kind': account_kind,
                       'capabilities': sorted(capabilities), 'refs': refs, 'scopes': sorted(scopes)},
                      sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


# -- the draft --------------------------------------------------------------------

def _need(cond, message: str, status: int = 400, code: str = 'invalid') -> None:
    if not cond:
        raise BindError(message, status, code)


def _stored_account(account_id: str) -> dict | None:
    with _desk._store_lock:
        return (_desk._read_store().get('accounts') or {}).get(account_id)


def _clean_account(raw, service: str) -> dict:
    _need(isinstance(raw, dict), 'account must be an object')
    unknown = sorted(set(raw) - {'id', 'new'})
    _need(not unknown, f'unknown account field(s): {", ".join(unknown)}')
    _need(('id' in raw) != ('new' in raw), 'name either an existing account (id) or a new one (new)')
    if 'id' in raw:
        aid = raw['id']
        _need(isinstance(aid, str) and _ACCOUNT_ID.match(aid), 'that account id is not valid', 400, 'bad_account')
        rec = _stored_account(aid)
        if rec is None:
            raise BindError('account not found', 404, 'account_not_found')
        _need(rec.get('platform') == service, f'that account is on {rec.get("platform")}, not {service}', 400, 'wrong_platform')
        return {'id': aid}
    _need(service in _NEW_ACCOUNT_SERVICES, f'an account for {service} is made by its own sign-in flow: create it there, then bind it here')
    new = raw['new']
    _need(isinstance(new, dict), 'new account must be an object')
    unknown = sorted(set(new) - {'identity', 'label'})
    _need(not unknown, f'unknown new-account field(s): {", ".join(unknown)}')
    try:
        identity = _accounts._clean_text(new.get('identity'), 'identity', required=True)
        label = _accounts._clean_text(new.get('label'), 'label')
    except _accounts.AccountError as e:
        raise BindError(str(e), e.status) from e
    with _desk._store_lock:
        others = list((_desk._read_store().get('accounts') or {}).values())
    _need(not any(o.get('platform') == service and (o.get('identity') or '').lower() == identity.lower() for o in others),
          f'{identity} on {service} is already an account: pick it instead', 409, 'account_exists')
    return {'new': {'identity': identity, 'label': label}}


def _vault_names() -> set:
    try:
        return {s['name'] for s in _vault.list_secrets()}
    except _vault.SecretsError as e:
        raise BindError(f'the vault could not be read: {e}', 503, 'vault_unavailable') from e


def _clean_binding(b, i: int, profile: dict, kind: str, seen: set, names_cache: list) -> dict:
    where = f'bindings[{i}]'
    _need(isinstance(b, dict), f'{where} must be an object')
    unknown = sorted(set(b) - {'purpose', 'route_id', 'capabilities', 'browser_profile', 'credentials'})
    _need(not unknown, f'{where}: unknown field(s): {", ".join(unknown)}')
    purpose = b.get('purpose')
    _need(isinstance(purpose, str) and purpose in _ready.BINDABLE_PURPOSES, f'{where}: purpose must be one of {_ready.BINDABLE_PURPOSES}')
    pur = next((p for p in profile['purposes'] if p['id'] == purpose and kind in p['account_kinds']), None)
    if pur is None:
        raise BindError(f'{where}: {profile["label"]} has no {purpose} for a {kind} account', 400, 'purpose_not_offered')
    caps = b.get('capabilities')
    _need(isinstance(caps, list) and caps and len(caps) <= 8 and all(isinstance(c, str) for c in caps),
          f'{where}: capabilities must be a short list of names')
    _need(len(set(caps)) == len(caps), f'{where}: a capability is named twice')
    for c in caps:
        _need(c in pur['capabilities'], f'{where}: {c} is not a {purpose} capability of {profile["label"]}')
        _need((purpose, c) not in seen, f'{where}: {purpose}.{c} is bound twice in this request')
        seen.add((purpose, c))
    route_id = b.get('route_id')
    if route_id is None:                     # unbind
        _need('browser_profile' not in b and 'credentials' not in b, f'{where}: removing a binding takes no profile or credential')
        return {'purpose': purpose, 'route_id': None, 'capabilities': sorted(caps)}
    _need(isinstance(route_id, str) and route_id in pur['routes'], f'{where}: that route is not offered for {purpose}', 400, 'route_not_offered')
    route = next(r for r in profile['routes'] if r['id'] == route_id)
    st = _ready.route_status(profile['service_id'], route, purpose)
    _need(st['bindable'], f'{where}: {route["title"]} cannot be chosen here. {st["why"]}', 400, 'route_not_bindable')
    cov = {(c['purpose'], c['account_kind'], c['capability']): c['status'] for c in route['coverage']}
    for c in caps:
        got = cov.get((purpose, kind, c))
        _need(got in _ready.COVERED, f'{where}: {route["title"]} is not documented to cover {purpose}.{c} for a {kind} account '
              f'({got or "no coverage stated"}); Clayrune does not assume it', 400, 'capability_not_covered')
    out = {'purpose': purpose, 'route_id': route_id, 'capabilities': sorted(caps), 'browser_profile': None, 'credentials': {}}
    prof = b.get('browser_profile')
    if prof is not None:
        _need(route['transport'] == 'browser', f'{where}: a browser profile belongs to a browser route')
        _need(isinstance(prof, str) and _PROFILE_NAME.match(prof.strip().lower()),
              f'{where}: a browser profile name is lowercase letters, digits, - or _')
        out['browser_profile'] = prof.strip().lower()
    creds = b.get('credentials')
    if creds is not None:
        _need(isinstance(creds, dict) and len(creds) <= 3, f'{where}: credentials must be an object of role to vault name')
        roles = {_ROLE_FOR_AUTH[a['type']] for a in route['auth'] if a['type'] in _ROLE_FOR_AUTH}
        for role, name in creds.items():
            _need(role in roles, f'{where}: this route takes no "{role}" credential')
            _need(isinstance(name, str) and _vault.valid_name(name.strip()), f'{where}: {role} must name a vault entry')
            name = name.strip()
            _need(not _vault.is_server_internal(name), f'{where}: {name} is kept by Clayrune for its own sign-ins')
            if not names_cache:
                names_cache.append(_vault_names())
            _need(name in names_cache[0], f'{where}: there is no vault entry named {name}. Create it in Secrets, or pick one that exists',
                  400, 'vault_entry_missing')
            out['credentials'][role] = name
    return out


def clean_draft(draft) -> dict:
    """Validate a draft into the exact shape that is saved. Pure apart from READING the
    profile, the Desk accounts and the vault's names. Raises BindError."""
    _need(isinstance(draft, dict), 'draft must be an object')
    unknown = sorted(set(draft) - {'service', 'revision', 'account', 'account_kind', 'bindings'})
    _need(not unknown, f'unknown draft field(s): {", ".join(unknown)}')
    service = draft.get('service')
    profile = _registry.profile(service)
    if profile is None:
        raise BindError('that service has no profile', 400, 'unknown_service')
    _need(draft.get('revision') == profile['revision'], 'The service profile changed since you opened it. Open it again and review.',
          409, 'profile_changed')
    kind = draft.get('account_kind')
    _need(isinstance(kind, str) and kind in {k['id'] for k in profile['account_kinds']},
          f'account_kind must be one of {[k["id"] for k in profile["account_kinds"]]}')
    account = _clean_account(draft.get('account'), service)
    if 'id' in account:
        rec = _stored_account(account['id'])
        if service == 'linkedin' and kind == 'member':
            _need(not (rec or {}).get('organization_id'), 'that account has a Company Page id, so it is not a member profile', 400, 'kind_mismatch')
    raw = draft.get('bindings')
    _need(isinstance(raw, list) and raw and len(raw) <= MAX_BINDINGS, f'bindings must be a list of 1 to {MAX_BINDINGS}')
    seen: set = set()
    names: list = []
    bindings = [_clean_binding(b, i, profile, kind, seen, names) for i, b in enumerate(raw)]
    return {'service': service, 'revision': profile['revision'], 'account': account, 'account_kind': kind, 'bindings': bindings}


def _draft_fingerprint(clean: dict) -> str:
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


# -- the write --------------------------------------------------------------------

def _refs_for(route: dict, rec: dict, b: dict) -> dict:
    refs: dict = {}
    if route['transport'] == 'api' and any(a['type'] == 'oauth' for a in route['auth']):
        refs.update({k: v for k, v in (rec.get('credentials') or {}).items() if k in ('oauth_vault', 'oauth_profile') and v})
    if route['transport'] == 'browser':
        name = b.get('browser_profile') or rec.get('browser_profile')
        if name:
            refs['browser_profile'] = name
    refs.update(b.get('credentials') or {})
    return refs


def _scopes_for(route: dict) -> list:
    return sorted({s for a in route['auth'] for s in a.get('scopes') or []})


def _apply(store: dict, account_id: str, clean: dict) -> dict:
    """Mutate `store` (the caller holds the lock and writes). Returns the legacy summary."""
    rec = (store.get('accounts') or {}).get(account_id)
    if rec is None:
        raise BindError('account not found', 404, 'account_not_found')
    if rec.get('platform') != clean['service']:
        raise BindError(f'that account is on {rec.get("platform")}, not {clean["service"]}', 400, 'wrong_platform')
    profile = _registry.profile(clean['service'])
    if profile is None:
        raise BindError('that service has no profile', 400, 'unknown_service')
    routes = {r['id']: r for r in profile['routes']}
    kind = clean['account_kind']
    if clean['service'] == 'linkedin' and kind == 'member' and rec.get('organization_id'):
        raise BindError('that account has a Company Page id, so it is not a member profile', 400, 'kind_mismatch')
    conns = json.loads(json.dumps(rec.get('connections') if isinstance(rec.get('connections'), dict) else {}))
    if any(r.get('account_kind') not in (None, kind) for caps in conns.values() for r in caps.values() if isinstance(r, dict)):
        raise BindError('this account already has routes chosen for a different kind of account. '
                        'Remove them first; one account is one kind.', 409, 'account_kind_changed')
    when = now_iso()
    new_profile = None
    touched_read = False
    for b in clean['bindings']:
        bucket = conns.setdefault(b['purpose'], {})
        touched_read = touched_read or b['purpose'] == 'read_own'
        for cap in b['capabilities']:
            if b['route_id'] is None:
                bucket.pop(cap, None)
                continue
            route = routes[b['route_id']]
            bucket[cap] = {'service': clean['service'], 'route_id': b['route_id'], 'account_kind': kind,
                           'revision': clean['revision'], 'refs': _refs_for(route, rec, b),
                           'scopes': _scopes_for(route), 'approved_at': when}
        if b.get('browser_profile'):
            new_profile = b['browser_profile']
    conns = {p: caps for p, caps in conns.items() if caps}
    for purpose, caps in conns.items():            # one fingerprint per bound capability: changing one leaves the rest as verified
        for cap, r in caps.items():
            r['fingerprint'] = fingerprint(clean['service'], purpose, r['route_id'], r['account_kind'], [cap], r['refs'], r['scopes'])
    if conns:
        rec['connections'] = conns
    else:
        rec.pop('connections', None)
    return _sync_legacy(store, rec, conns, routes, new_profile, touched_read)


def _sync_legacy(store: dict, rec: dict, conns: dict, routes: dict, new_profile: str | None, touched_read: bool) -> dict:
    """Keep the one read setting engagement reads (`read_via`, `browser_profile`) in step with
    the bindings, only when they agree. Mixed own reads leave it as it is and say so."""
    ways = {'api' if routes[r['route_id']]['transport'] == 'api' else 'pane'
            for r in (conns.get('read_own') or {}).values() if r['route_id'] in routes}
    read_via = next(iter(ways)) if touched_read and len(ways) == 1 else None
    summary = {'read_via': _desk.account_read_via(rec), 'split': len(ways) > 1}
    if read_via is not None or new_profile is not None:
        _accounts.apply_read_to_store(store, rec, read_via, new_profile, None)
        summary['read_via'] = _desk.account_read_via(rec)
    return summary


def _result(account_id: str, created: bool, legacy: dict) -> dict:
    return {'account_id': account_id, 'account_created': created, 'legacy_read': legacy}


def commit(request_id: str, clean: dict) -> tuple[dict, bool]:
    """Write the draft. Returns `(result, duplicate)`. Raises BindError."""
    fp = _draft_fingerprint(clean)
    with _lock:
        seen = _done.get(request_id)
        if seen is not None:
            if seen[0] == fp:
                return seen[1], True
            raise BindError('that request_id was already used for a different draft', 409, 'request_id_reused')
        created = None
        account_id = clean['account'].get('id')
        if account_id is None:
            new = clean['account']['new']
            try:
                created = _accounts.create_account(clean['service'], new['identity'], label=new['label'] or None)
            except _accounts.AccountError as e:
                raise BindError(str(e), e.status, 'account_refused') from e
            account_id = created['id']
        try:
            with _desk._store_lock:
                store = _desk._read_store()
                legacy = _apply(store, account_id, clean)
                _desk._write_store(store)
        except Exception as e:
            orphan = False
            if created is not None:
                try:
                    orphan = not _accounts.delete_account(account_id)
                except Exception as e2:
                    orphan = True
                    _log(f'[desk_connect] could not remove the account made for a failed binding: {type(e2).__name__}', flush=True)
            if isinstance(e, BindError) and not orphan:
                raise
            _log(f'[desk_connect] binding write failed ({type(e).__name__})', flush=True)
            msg = str(e) if isinstance(e, BindError) else 'the routes could not be saved; see the server log'
            if orphan:
                msg += f'; the new account {account_id} was made and could not be removed: delete it in Connections'
            raise BindError(msg, e.status if isinstance(e, BindError) else 500, 'record_failed') from e
        result = _result(account_id, created is not None, legacy)
        _done[request_id] = (fp, result)
        while len(_done) > _REMEMBER:
            _done.popitem(last=False)
    return result, False


def is_known_request(request_id: str) -> bool:
    with _lock:
        return request_id in _done


clean_request_id = _commit.clean_request_id
