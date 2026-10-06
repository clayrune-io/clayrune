"""What a bound route may call "Verified", per capability (docs/DESK_SERVICE_PROFILES_SPEC.md
section 3.3; slice P2).

Setup state (is it signed in, is the profile there) lives in `route_readiness`. This is the
other dimension: has a read-only check actually run against it. A record exists only after a
human asks for the check and the route's checker reports success for a capability, so a
route whose checker proves one capability of four reads `partial`, never `verified`. A route
with no checker (the paid X API reads, every publish route: a billed call is not a probe)
stays `not_checked`, and says why.

Status is DERIVED each time it is asked, like `verification.status`:

    a record counts only while the binding it checked is still the one on the account
    (same per-capability fingerprint) AND every vault entry the binding rests on still
    carries the created/updated stamps it had at the check.

So changing one credential, one browser profile or one route un-verifies exactly the
capabilities that rested on it, with no code that has to remember to; the others keep
theirs. Records are bounded and survive a restart (`purpose_verification_store`: the identity
the check proved, and when). A loaded record is only a claim, held to the same two tests on
every read, so a file that no longer matches reads `not_checked`, the safe direction.
`status` never touches the network or decrypts anything.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable

from mc import desk as _desk
from mc import desk_engagement as _engagement
from mc import secrets_store as _vault
from mc.core import _log, now_iso
from mc.desk_connect import permission_check as _consent
from mc.desk_connect import purpose_bindings as _bindings
from mc.desk_connect import purpose_verification_store as _store
from mc.desk_connect import route_readiness as _ready

VAULT_REF_KEYS = ('oauth_vault', 'login', 'api_key')   # the refs that name a vault entry
_REMEMBER = 400
_lock = threading.Lock()
_records: 'OrderedDict[tuple, dict]' = OrderedDict()
_NOT_LOADED = object()
_loaded_from: object = _NOT_LOADED     # the sidecar path `_records` was read from (None = unwired)


class CheckError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


def _ensure_loaded() -> None:
    """Read the sidecar once per path. Caller holds `_lock`."""
    global _loaded_from
    here = _store.path()
    if _loaded_from is not _NOT_LOADED and _loaded_from == here:
        return
    _records.clear()
    _records.update(_store.load(_REMEMBER))
    _loaded_from = here


def _forget_all_for_tests() -> None:
    """Empty memory and treat the sidecar as already read, so nothing on disk comes back."""
    global _loaded_from
    with _lock:
        _records.clear()
        _loaded_from = _store.path()


def _restart_for_tests() -> None:
    """What a server restart does: memory is gone, the sidecar is untouched."""
    global _loaded_from
    with _lock:
        _records.clear()
        _loaded_from = _NOT_LOADED


# -- the checkers -------------------------------------------------------------------
# (service, route, purpose) -> fn(account_rec, group) -> {capability: identity}. A checker
# is read-only and free, and returns ONLY the capabilities it actually proved; it raises
# (`desk_engagement.ReadError` or anything else) when the check fails.

def _pane_mentions(rec: dict, group: dict) -> dict:
    # Current persisted Read consent for exactly this probe, before a reader (browser) exists.
    # Denial raises PermissionDenied, which `check` reports as a failed check, never a record.
    _consent.require_permission(rec['id'], 'x', 'read', purpose='read_own', capability='mentions',
                                route_id='x-browser', account_kind='account')
    profile = (group.get('refs') or {}).get('browser_profile') or ''
    reader = _engagement.PaneXReader(rec.get('project_id') or 'mission_control', profile)
    try:
        reader.fetch_mentions(since_id=None, known_posts={})
    finally:
        reader.close()
    # The page proves a signed-in session that can read mentions: not who it is, and not the other three.
    return {'mentions': ''}


CHECKERS: dict[tuple[str, str, str], Callable[[dict, dict], dict]] = {
    ('x', 'x-browser', 'read_own'): _pane_mentions,
}
NO_CHECK = 'Clayrune has no free read-only check for this route, and does not spend a billed call to prove it.'


# -- stamps -------------------------------------------------------------------------

def _stamps(refs: dict | None) -> dict:
    names = [v for k, v in (refs or {}).items() if k in VAULT_REF_KEYS and v]
    if not names:
        return {}
    try:
        meta = {s['name']: (s.get('created_at'), s.get('updated_at')) for s in _vault.list_secrets()}
    except _vault.SecretsError:
        return {n: None for n in names}
    return {n: meta.get(n) for n in names}


def _bound(rec: dict, purpose: str, cap: str) -> dict | None:
    conns = rec.get('connections')
    b = ((conns or {}).get(purpose) or {}).get(cap) if isinstance(conns, dict) else None
    return b if isinstance(b, dict) and b.get('route_id') else None


# -- reading ------------------------------------------------------------------------

def capability_state(rec: dict, purpose: str, cap: str) -> dict:
    """`{state, at?, identity?}` for one bound capability: verified or not_checked."""
    b = _bound(rec, purpose, cap)
    if b is None:
        return {'state': 'not_checked'}
    with _lock:
        _ensure_loaded()
        got = _records.get((rec['id'], purpose, cap))
    if got and got['fingerprint'] == b.get('fingerprint') and got['stamps'] == _stamps(b.get('refs')):
        return {'state': 'verified', 'at': got['at'], 'identity': got['identity']}
    return {'state': 'not_checked'}


def purpose_state(rec: dict, purpose: str) -> dict:
    """`{state, verified, bound, capabilities}` for a purpose: `verified` when every bound
    capability is, `partial` when some are, `not_checked` when none are or none is bound."""
    conns = rec.get('connections')
    caps = sorted(((conns or {}).get(purpose) or {})) if isinstance(conns, dict) else []
    states = {c: capability_state(rec, purpose, c) for c in caps}
    ok = sorted(c for c, s in states.items() if s['state'] == 'verified')
    state = 'verified' if caps and len(ok) == len(caps) else 'partial' if ok else 'not_checked'
    return {'state': state, 'verified': ok, 'bound': caps, 'capabilities': states}


# -- the check ----------------------------------------------------------------------

def check(account_id: str, purpose: str) -> dict:
    """Run every bound route's checker for one purpose of one account and record what each
    proved. Human-triggered. A failed check is a result (`failed`), never an exception;
    a route with no checker is reported as `no_check` and nothing is recorded for it."""
    with _desk._store_lock:
        rec = (_desk._read_store().get('accounts') or {}).get(account_id)
    if rec is None:
        raise CheckError('account not found', 404, 'account_not_found')
    if purpose not in _ready.BINDABLE_PURPOSES:
        raise CheckError('that purpose is not checked per account', 400, 'bad_purpose')
    groups = [g for g in _bindings.groups(rec) if g['purpose'] == purpose]
    if not groups:
        raise CheckError('nothing is bound for that purpose on this account', 409, 'nothing_bound')
    routes = []
    for g in groups:
        fn = CHECKERS.get((g['service'], g['route_id'], purpose))
        if fn is None:
            routes.append({'route_id': g['route_id'], 'result': 'no_check', 'message': NO_CHECK})
            continue
        stamps = {c: _stamps((_bound(rec, purpose, c) or {}).get('refs')) for c in g['capabilities']}   # BEFORE the probe
        try:
            proved = fn(rec, g)
        except Exception as e:             # a checker bug reads as a failed check, never as verified
            _log(f'[desk_connect] purpose check {g["service"]}/{g["route_id"]} failed: {type(e).__name__}', flush=True)
            routes.append({'route_id': g['route_id'], 'result': 'failed', 'message': str(e)[:300] or type(e).__name__})
            continue
        with _lock:
            _ensure_loaded()
            for cap in g['capabilities']:
                _records.pop((rec['id'], purpose, cap), None)
            for cap, identity in (proved or {}).items():
                b = _bound(rec, purpose, cap)
                if b is None:
                    continue
                _records[(rec['id'], purpose, cap)] = {'fingerprint': b.get('fingerprint'), 'stamps': stamps.get(cap, {}),
                                                       'at': now_iso(), 'identity': identity}
                while len(_records) > _REMEMBER:
                    _records.popitem(last=False)
            _store.save(_records)
        routes.append({'route_id': g['route_id'], 'result': 'passed', 'proved': sorted(proved or {}),
                       'not_proved': sorted(set(g['capabilities']) - set(proved or {}))})
    with _desk._store_lock:
        fresh = (_desk._read_store().get('accounts') or {}).get(account_id) or rec
    return {'account_id': account_id, 'purpose': purpose, 'routes': routes, **purpose_state(fresh, purpose)}
