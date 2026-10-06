"""Desk Read/Post consent (MC-1062/05), independent of connection setup.

Stored on the account in desk.json, outside DATA_DIR. Missing on a legacy
account means legacy behavior, not Allow or Deny. New setup must call
initialize_new_account: the separate version marker makes a lost explicit
policy fail closed. Only the human/passcode blueprint calls commit.

Scope entries name exact purpose/capability/route triples. They never choose
routes, edit detailed bindings, grant browser-site access, alter vault policy,
or approve arbitrary MCP tools. Execution enforcement belongs to ticket 06;
decision is its tri-state seam (None = retain legacy behavior).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections import OrderedDict

from mc import desk as _desk
from mc.core import now_iso
from mc.desk_connect import registry as _registry
from mc.desk_connect import route_readiness as _ready

VERSION = 1
POLICY_KEY = 'permission_policy'
VERSION_KEY = 'permission_policy_version'
MAX_SCOPES = 32
_ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
_REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{8,80}$')
_FIELDS = {'account_id', 'service', 'account_kind', 'read', 'post', 'scopes'}
_lock = threading.Lock()
_done: OrderedDict[str, tuple[str, dict]] = OrderedDict()


class PolicyError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status, self.code = status, code


def _need(condition, message: str, status: int = 400, code: str = 'invalid') -> None:
    if not condition:
        raise PolicyError(message, status, code)


def _account(store: dict, account_id: str) -> dict:
    rec = (store.get('accounts') or {}).get(account_id)
    if not isinstance(rec, dict):
        raise PolicyError('account not found', 404, 'account_not_found')
    return rec


def _check_identity(rec: dict, draft: dict) -> dict:
    aid, service, kind = draft.get('account_id'), draft.get('service'), draft.get('account_kind')
    _need(isinstance(aid, str) and _ID.fullmatch(aid), 'that account id is not valid')
    _need(rec.get('id') == aid, 'policy belongs to a different account', 409, 'wrong_account')
    _need(isinstance(service, str) and rec.get('platform') == service,
          'policy belongs to a different service', 409, 'wrong_service')
    profile = _registry.profile(service)
    if profile is None:
        raise PolicyError('that service has no Desk permission profile')
    _need(isinstance(kind, str) and kind in {k['id'] for k in profile['account_kinds']},
          'that account kind is not supported')
    kinds = {rec['account_kind']} if rec.get('account_kind') else set()
    previous = rec.get(POLICY_KEY)
    if isinstance(previous, dict) and isinstance(previous.get('account_kind'), str):
        kinds.add(previous['account_kind'])
    if service == 'x':
        kinds.add('account')
    if service == 'linkedin' and rec.get('organization_id'):
        kinds.add('organization')
    conns = rec.get('connections')
    if isinstance(conns, dict):
        for caps in conns.values():
            if isinstance(caps, dict):
                kinds.update(b['account_kind'] for b in caps.values()
                             if isinstance(b, dict) and b.get('account_kind'))
    _need(not kinds or kinds == {kind}, 'account already has a different kind', 409, 'wrong_kind')
    return profile


def _clean(rec: dict, draft) -> dict:
    _need(isinstance(draft, dict) and set(draft) == _FIELDS,
          'policy needs only account_id, service, account_kind, read, post and scopes')
    profile = _check_identity(rec, draft)
    _need(type(draft['read']) is bool and type(draft['post']) is bool, 'Read and Post must be explicit booleans')
    raw = draft['scopes']
    _need(isinstance(raw, list) and len(raw) <= MAX_SCOPES, f'scopes must be a list of at most {MAX_SCOPES}')
    routes = {r['id']: r for r in profile['routes']}
    scopes: list[dict] = []
    seen: set[tuple] = set()
    for scope in raw:
        _need(isinstance(scope, dict) and set(scope) == {'purpose', 'capability', 'route_id'},
              'each scope names only purpose, capability and route_id')
        purpose, cap, rid = scope['purpose'], scope['capability'], scope['route_id']
        _need(all(isinstance(v, str) for v in (purpose, cap, rid)), 'scope identifiers must be text')
        # Post means post only. Reply/media/article and broad listening require
        # their own later consent, even if a profile lists them as documented.
        operation = 'read' if purpose == 'read_own' else 'post' if purpose == 'publish' and cap == 'post' else None
        _need(operation is not None, 'that capability is not covered by Read/Post', 400, 'unsupported_scope')
        _need(draft[operation], 'a denied operation cannot carry allowed scopes')
        route = routes.get(rid)
        if (route is None or route.get('transport') == 'mcp'
                or _ready.executor(draft['service'], rid, purpose) is None):
            raise PolicyError('this route has no Desk executor; MCP approval is whole-server', 400, 'unsupported_scope')
        _need(any(c['purpose'] == purpose and c['capability'] == cap
                  and c['account_kind'] == draft['account_kind'] and c['status'] in _ready.COVERED
                  for c in route['coverage']), 'that route does not cover this exact capability', 400, 'unsupported_scope')
        key = (purpose, cap, rid)
        _need(key not in seen, 'duplicate permission scope')
        seen.add(key)
        scopes.append(dict(scope))
    for operation, purpose in (('read', 'read_own'), ('post', 'publish')):
        _need(not draft[operation] or any(s['purpose'] == purpose for s in scopes),
              f'{operation} needs at least one explicit supported scope')
    return {**draft, 'scopes': sorted(scopes, key=lambda s: (s['purpose'], s['capability'], s['route_id']))}


def fingerprint(draft: dict) -> str:
    """Version, identity, operations and every exact scope; no caller approval token."""
    return hashlib.sha256(json.dumps({'version': VERSION, **draft}, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _record(clean: dict) -> dict:
    return {'version': VERSION, **copy.deepcopy(clean), 'fingerprint': fingerprint(clean), 'updated_at': now_iso()}


def initialize_new_account(rec: dict, account_kind: str) -> None:
    """Setup's default-deny initializer; call only on a newly created account.

    Does not write the store or authorize any operation. Existing policy cannot
    be reset through this helper. The setup Save owns its own human gate.
    """
    _need(POLICY_KEY not in rec and VERSION_KEY not in rec, 'account already has a permission policy', 409)
    clean = _clean(rec, {'account_id': rec.get('id'), 'service': rec.get('platform'),
                         'account_kind': account_kind, 'read': False, 'post': False, 'scopes': []})
    rec[POLICY_KEY], rec[VERSION_KEY] = _record(clean), VERSION


def read_policy(rec, *, account_id: str | None = None, account_kind: str | None = None) -> dict:
    """Metadata only. Malformed/missing explicit consent is always fail closed."""
    denied = {'state': 'invalid', 'read': False, 'post': False, 'scopes': [], 'fingerprint': None}
    if (not isinstance(rec, dict) or not isinstance(rec.get('id'), str)
            or not isinstance(rec.get('platform'), str)
            or (account_id is not None and rec.get('id') != account_id)):
        return denied
    if account_kind is not None:
        try:
            _check_identity(rec, {'account_id': rec['id'], 'service': rec['platform'], 'account_kind': account_kind})
        except (PolicyError, TypeError, KeyError):
            return denied
    if POLICY_KEY not in rec and VERSION_KEY not in rec:
        return {'state': 'legacy', 'read': None, 'post': None, 'scopes': [], 'fingerprint': None}
    policy = rec.get(POLICY_KEY)
    if (type(rec.get(VERSION_KEY)) is not int or rec[VERSION_KEY] != VERSION
            or not isinstance(policy, dict) or set(policy) != _FIELDS | {'version', 'fingerprint', 'updated_at'}
            or type(policy.get('version')) is not int or policy['version'] != VERSION
            or not isinstance(policy.get('updated_at'), str) or not policy['updated_at']):
        return denied
    try:
        clean = _clean(rec, {k: policy[k] for k in _FIELDS})
    except (PolicyError, TypeError, KeyError):
        return denied
    if (policy['fingerprint'] != fingerprint(clean)
            or (account_kind is not None and clean['account_kind'] != account_kind)):
        return denied
    return {'state': 'explicit', **copy.deepcopy(policy)}


def decision(rec, operation: str, *, purpose: str, capability: str, route_id: str,
             account_id: str | None = None, account_kind: str | None = None) -> bool | None:
    """None retains legacy behavior; explicit consent permits only the named scope.

    An Allow is an additional restriction, never proof that campaign, provider,
    budget, vault or browser policy permits execution.
    """
    if operation not in ('read', 'post') or purpose != {'read': 'read_own', 'post': 'publish'}[operation]:
        return False
    state = read_policy(rec, account_id=account_id, account_kind=account_kind)
    if state['state'] == 'legacy':
        return None
    return bool(state[operation] and {'purpose': purpose, 'capability': capability, 'route_id': route_id} in state['scopes'])


def clean_submission(body) -> tuple[str, dict]:
    """Validate before spending a passcode guess. No approved/token fields accepted."""
    if not isinstance(body, dict) or set(body) - {'request_id', 'draft', 'passcode'}:
        raise PolicyError('Save accepts only request_id, draft and passcode')
    rid = body.get('request_id')
    if not isinstance(rid, str) or not _REQUEST_ID.fullmatch(rid):
        raise PolicyError('request_id must be 8-80 letters, digits, - or _')
    draft = body.get('draft')
    if not isinstance(draft, dict):
        raise PolicyError('draft must be an object')
    aid = draft.get('account_id')
    if not isinstance(aid, str) or not _ID.fullmatch(aid):
        raise PolicyError('that account id is not valid')
    with _desk._store_lock:
        clean = _clean(_account(_desk._read_store(), aid), draft)
    return rid, clean


def commit(request_id: str, clean: dict) -> tuple[dict, bool]:
    """Called only AFTER the blueprint checks the human passcode, even on replay."""
    fp = fingerprint(clean)
    with _lock:
        seen = _done.get(request_id)
        if seen is not None:
            _need(seen[0] == fp, 'request_id already used for a different policy', 409, 'request_id_reused')
            return copy.deepcopy(seen[1]), True
        with _desk._store_lock:
            store = _desk._read_store()
            rec = _account(store, clean['account_id'])
            clean = _clean(rec, clean)  # recheck identity/coverage under the write lock
            rec[POLICY_KEY], rec[VERSION_KEY] = _record(clean), VERSION
            _desk._write_store(store)
            result = {'account_id': clean['account_id'], 'policy': read_policy(rec)}
        _done[request_id] = (fp, copy.deepcopy(result))
        while len(_done) > 200:
            _done.popitem(last=False)
    return result, False


def view(account_id: str) -> dict:
    _need(isinstance(account_id, str) and _ID.fullmatch(account_id), 'that account id is not valid')
    with _desk._store_lock:
        return {'account_id': account_id, 'policy': read_policy(_account(_desk._read_store(), account_id))}


def _forget_all_for_tests() -> None:
    with _lock:
        _done.clear()
