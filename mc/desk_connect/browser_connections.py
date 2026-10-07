"""Generic browser-profile references, with no Desk executor or permission.

Saved in desk.json's services section, outside DATA_DIR. X/LinkedIn keep their
existing account setup. A reviewed check is taken only from the shipped profile;
requests and discovery evidence cannot install checks or authorize execution.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from threading import Lock

from mc import desk, desk_services
from mc.core import now_iso
from mc.desk_connect import registry, url_check
from mc.desk_connect.browser_setup import SetupError, _profile_state
from mc.desk_connect.commit import clean_request_id

PROFILE_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,40}$')
_lock = Lock()
_done: OrderedDict[str, tuple[str, dict]] = OrderedDict()


def variant(service: str | None) -> dict:
    """Inert setup choice; no runtime capability is inferred."""
    return {'id': 'browser-profile', 'route_id': 'browser-profile', 'type': 'signin',
            'kind': 'browser_profile', 'title': 'Sign in on the website',
            'transport': 'browser', 'support': 'available', 'auth': ['browser_signin'],
            'account_kinds': [], 'coverage': [], 'requirements': [], 'evidence': [],
            'verification': {}, 'cost': {'basis': 'unknown'}, 'explanation': None,
            'setup': {'mode': 'full', 'via': 'browser_profile', 'service': service},
            'runtime': {'purposes': {}, 'desk_executes': False, 'adapter': None}}


def clean_draft(raw, own_hosts=()) -> dict:
    if not isinstance(raw, dict) or set(raw) != {'name', 'url', 'browser_profile'}:
        raise SetupError('name, url and browser_profile are required; no other settings are accepted')
    try:
        name = desk_services._clean_name(raw['name'])
        got = url_check.check_url(raw['url'], own_hosts=own_hosts)
    except (desk_services.ServiceError, url_check.UrlError) as e:
        raise SetupError(str(e)) from e
    profile = raw['browser_profile']
    if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile):
        raise SetupError('a browser profile name uses lowercase letters, digits, - or _ (at most 41 characters)')
    svc = registry.lookup(got['host'])
    if svc and svc['id'] in ('x', 'linkedin'):
        raise SetupError('choose the account for this service first')
    return {'name': name, 'url': got['url'], 'browser_profile': profile,
            'service': svc['id'] if svc else None}


def _fingerprint(clean: dict) -> str:
    return hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()


def view(rec: dict) -> dict:
    from mc.desk_connect.browser_check import status_for
    setup = rec['browser_connection']
    return {'connection_id': rec['id'], 'name': rec['name'], 'url': rec['link'],
            'service': setup['service'], 'browser_profile': setup['browser_profile'],
            'profile_state': _profile_state(setup['browser_profile']),
            'read': False, 'post': False, 'executes': False,
            'status': status_for(rec['link'], setup['browser_profile'])}


def commit(request_id: str, clean: dict) -> tuple[dict, bool]:
    clean_request_id(request_id)
    fp = _fingerprint(clean)
    with _lock:
        if request_id in _done:
            old_fp, result = _done[request_id]
            if old_fp != fp:
                raise SetupError('this request was already used for different details', 409, 'request_changed')
            from mc.desk_connect.browser_check import refresh_result
            return refresh_result(result), True
        with desk._store_lock:
            store = desk._read_store()
            services = store.setdefault('services', {})
            existing = next((s for s in services.values() if s.get('name', '').casefold() == clean['name'].casefold()), None)
            if existing:
                held = existing.get('browser_connection') or {}
                if existing.get('link') != clean['url'] or held.get('browser_profile') != clean['browser_profile'] or held.get('service') != clean['service']:
                    raise SetupError('a service with this name is already saved; its connection is not replaced', 409, 'already_set_up')
                result = {**view(existing), 'unchanged': True}
            else:
                if len(services) >= desk_services.MAX_SERVICES:
                    raise SetupError('the saved-service limit has been reached', 409)
                rec = {'id': desk._new_id('svc'), 'name': clean['name'], 'link': clean['url'],
                       'credential': '', 'created_at': now_iso(),
                       'browser_connection': {'service': clean['service'], 'browser_profile': clean['browser_profile'],
                                              'read': False, 'post': False}}
                services[rec['id']] = rec
                desk._write_store(store)
                result = {**view(rec), 'unchanged': False}
        _done[request_id] = (fp, result)
        while len(_done) > 200:
            _done.popitem(last=False)
        return dict(result), False
