"""Read-only Vault projection of Desk browser references; never reads secrets.

Explicit browser Check connection verdicts are the only writers of requests.
They live in desk.json, outside project records, and survive server restarts.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from mc import desk
from mc.core import _log, now_iso
from mc.desk_connect import registry, signin_method

REQUESTS = 'browser_signin_requests'


def service_key(url: str) -> str:
    host = urlsplit(url).hostname or ''
    service = registry.lookup(host)
    # Unknown sites retain their exact address, like BrowserProvider's evidence key.
    return service['id'] if service else url


def project(store: dict) -> list[dict]:
    """Allowlisted metadata only. A locked vault needs no special access here."""
    rows = []
    for kind, records in (('account', store.get('accounts') or {}), ('service', store.get('services') or {})):
        for rec in records.values():
            if not isinstance(rec, dict) or not isinstance(rec.get('id'), str) or not rec['id']:
                continue
            setup = (rec.get('browser_setup') if kind == 'account' else rec.get('browser_connection')) or {}
            if not isinstance(setup, dict):
                continue
            refs = setup.get('refs') or {}
            profile = refs.get('browser_profile') if isinstance(refs, dict) else None
            profile = profile or setup.get('browser_profile') or (rec.get('browser_profile') if kind == 'account' else None)
            url = rec.get('link') if kind == 'service' else None
            service = setup.get('service') or rec.get('platform')
            if not url and isinstance(service, str):
                spec = registry.profile(service)
                url = spec.get('home_url') if spec else None
            if not isinstance(profile, str) or not profile or not isinstance(url, str):
                continue
            try:
                address = urlsplit(url)
                if address.scheme != 'https' or not address.hostname:
                    continue
                key = service_key(url)
            except ValueError as e:
                _log(f'[browser_accounts] invalid saved address: {type(e).__name__}', flush=True)
                continue
            token = hashlib.sha256(f'{kind}:{rec.get("id", "")}:{profile}'.encode()).hexdigest()[:24]
            name = next((v for v in (rec.get('label'), rec.get('name'), rec.get('identity'), service)
                         if isinstance(v, str) and v), url)
            rows.append({'id': token, 'source': kind, 'source_id': rec.get('id'),
                         'name': name,
                         'service': key, 'url': url, 'browser_profile': profile.strip().lower(),
                         'signin_method': signin_method.stored(setup if setup else rec),
                         'read_only': True})
    return rows


def snapshot() -> dict:
    with desk._store_lock:
        store = desk._read_store()
        rows = project(store)
        pending = store.get(REQUESTS) or {}
    requests = {}
    for row in rows:
        profiles = pending.get(row['service']) or {}
        request = profiles.get(row['browser_profile']) or {}
        row['needs_signin'] = row['id'] in request.get('sources', [])
        if row['needs_signin']:
            requests.setdefault(row['service'], {'service': row['service'], 'name': row['name'],
                                                 'message': 'Sign in again', 'profiles': []})
            names = requests[row['service']]['profiles']
            if row['browser_profile'] not in names:
                names.append(row['browser_profile'])
    return {'browser_accounts': rows, 'signin_requests': list(requests.values())}


def observe(target: dict, verdict: dict) -> None:
    """No probe, no guessing from stale/unchecked status, no page content saved."""
    if not target.get('check'):
        return
    passed = verdict.get('state') == 'verified' or (
        target.get('confirmed') and verdict.get('state') == 'not_checked' and verdict.get('at'))
    expired = verdict.get('failure') == 'signed_out'
    # Missing profiles produce sign_in_required even for reviewed services. In
    # contrast an unreviewed existing profile's inconclusive probe has message
    # but no evidence of expiry. Check directory metadata only in that case.
    if verdict.get('state') == 'sign_in_required' and not verdict.get('failure'):
        from mc.blueprints.browser_routes import named_profile_exists
        expired = not named_profile_exists(target['browser_profile'])
    if not passed and not expired:
        return
    service = service_key(target['url'])
    profile = target['browser_profile']
    with desk._store_lock:
        store = desk._read_store()
        sources = [r['id'] for r in project(store) if r['service'] == service and r['browser_profile'] == profile]
        if not sources:
            return
        pending = store.setdefault(REQUESTS, {})
        profiles = pending.get(service) or {}
        if expired and (profile not in profiles or profiles[profile].get('sources') != sources):
            pending[service] = {**profiles, profile: {'detected_at': now_iso(), 'sources': sources}}
        elif passed and profile in profiles:
            del profiles[profile]
            if profiles:
                pending[service] = profiles
            else:
                pending.pop(service, None)
        else:
            return
        desk._write_store(store)
