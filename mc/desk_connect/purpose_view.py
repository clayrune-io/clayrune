"""The Routes screen's data for one known service: every route grouped by PURPOSE, and what
one account has chosen (docs/DESK_SERVICE_PROFILES_SPEC.md sections 3.3 and 4; slice P2).

Read-only and free: it reads the profile, the Desk accounts and vault METADATA (names and
states), opens nothing and never returns a secret value. Nothing here chooses for the
person: a route's `status` says what Clayrune can run and whether a choice can be recorded,
and the order within a purpose is the display order only (decided 2026-10-05: what Clayrune
can run, then the browser, then manual, then the rest; reversing it is `_RANK`).

Per account it reports each bound route's SETUP state (`route_readiness`) and each purpose's
VERIFICATION state (`purpose_verification`) side by side, because the two are different
questions: a route can be set up and unchecked, or saved and not runnable yet.
"""
from __future__ import annotations

from mc import desk as _desk
from mc import secrets_store as _vault
from mc.desk_connect import profile_loader as _loader
from mc.desk_connect import purpose_bindings as _bindings
from mc.desk_connect import purpose_verification as _verification
from mc.desk_connect import registry as _registry
from mc.desk_connect import route_readiness as _ready

PURPOSE_LABELS = {'publish': 'Publish', 'read_own': 'Read your own account', 'listen_broad': 'Listen broadly',
                  'generate_image': 'Generate images', 'generate_video': 'Generate video'}
CAPABILITY_LABELS = {'post': 'Post', 'reply': 'Reply', 'media': 'Media', 'article': 'Article', 'own_posts': 'Your posts',
                     'mentions': 'Mentions', 'replies': 'Replies', 'post_metrics': 'Post metrics', 'search': 'Search'}


def _rank(route: dict, status: dict) -> int:
    if status['executes']:
        return 0
    if status['bindable']:
        return 1
    if route['support'] == 'manual':
        return 3
    if route['transport'] == 'browser':
        return 2
    return 4


def _cost(route: dict) -> dict:
    c = route['cost']
    lines = []
    for i in c['items']:
        who = {'account_owner': 'paid by the account owner', 'clayrune': 'paid by Clayrune'}.get(i['payer'], '')
        lines.append(f'{i["currency"]} {i["amount"]} per {i["unit"]}' + (f' ({who})' if who else ''))
    return {'basis': c['basis'], 'lines': lines}


def _evidence(profile: dict, route: dict) -> dict:
    by_id = {e['id']: e for e in profile['evidence']}
    v = route['verification']
    return {'status': v['status'], 'last_verified': v['last_verified'], 'age_days': _loader.age_days(v['last_verified']),
            'links': [{'label': by_id[i]['label'], 'url': by_id[i]['url'], 'retrieved': by_id[i]['retrieved']}
                      for i in route['evidence'] if by_id[i]['url']]}


def _route_row(profile: dict, route: dict, purpose: dict) -> dict:
    st = _ready.route_status(profile['service_id'], route, purpose['id'])
    coverage: dict[str, dict] = {}
    for c in route['coverage']:
        if c['purpose'] == purpose['id'] and c['capability'] in purpose['capabilities']:
            coverage.setdefault(c['account_kind'], {})[c['capability']] = c['status']
    return {'id': route['id'], 'title': route['title'], 'guidance': route['guidance'], 'transport': route['transport'],
            'support': route['support'], 'coverage': coverage, 'status': st, 'cost': _cost(route),
            'requirements': [{'kind': r['kind'], 'text': r['text']} for r in route['requirements']],
            'limits': [{'name': x['name'], 'source': x['source']} for x in route['limits']],
            'auth': [{'type': a['type'], 'issuer': a['issuer'], 'scopes': a['scopes'], 'registration': a['registration']}
                     for a in route['auth']],
            'signin': ({'url': route['signin']['url'], 'hosts': route['signin']['hosts']} if route.get('signin') else None),
            'evidence': _evidence(profile, route), '_rank': _rank(route, st)}


def purposes(profile: dict) -> list[dict]:
    """The purposes of a profile, each with its routes in display order."""
    routes = {r['id']: r for r in profile['routes']}
    out = []
    for p in profile['purposes']:
        rows = [_route_row(profile, routes[rid], p) for rid in p['routes'] if rid in routes]
        rows.sort(key=lambda r: r['_rank'])          # stable: the profile's own order breaks ties
        for r in rows:
            r.pop('_rank')
        out.append({'id': p['id'], 'label': PURPOSE_LABELS.get(p['id'], p['id']), 'account_kinds': p['account_kinds'],
                    'capabilities': [{'id': c, 'label': CAPABILITY_LABELS.get(c, c)} for c in p['capabilities']],
                    'standing_policy': p['standing_policy'], 'bindable': p['id'] in _ready.BINDABLE_PURPOSES,
                    'routes': rows})
    return out


def account_kind(rec: dict) -> str | None:
    """The kind of account a record is, when it is settled: from what was already bound,
    else from the record (a LinkedIn Company Page carries its organization id), else None."""
    conns = rec.get('connections')
    kinds = {b.get('account_kind') for caps in (conns.values() if isinstance(conns, dict) else [])
             if isinstance(caps, dict) for b in caps.values() if isinstance(b, dict)} - {None}
    if len(kinds) == 1:
        return next(iter(kinds))
    if rec.get('platform') == 'x':
        return 'account'
    if rec.get('platform') == 'linkedin' and rec.get('organization_id'):
        return 'organization'
    return None


def _account_view(rec: dict, profile: dict) -> dict:
    routes = {r['id']: r for r in profile['routes']}
    bound = []
    for g in _bindings.groups(rec):
        route = routes.get(g['route_id'])
        setup = _ready.setup_state(profile['service_id'], g['route_id'], g['purpose'], rec, g)
        bound.append({**{k: g[k] for k in ('purpose', 'route_id', 'capabilities', 'refs', 'approved_at', 'account_kind', 'revision')},
                      'route_title': route['title'] if route else g['route_id'], 'gone_from_profile': route is None, **setup})
    return {'id': rec['id'], 'label': rec.get('label') or rec.get('identity') or rec['id'], 'identity': rec.get('identity'),
            'account_kind': account_kind(rec), 'organization_id': rec.get('organization_id') or None,
            'read_via': _desk.account_read_via(rec), 'bound': bound,
            'verification': {p: _verification.purpose_state(rec, p) for p in _ready.BINDABLE_PURPOSES}}


def vault_choices(service_id: str) -> list[dict]:
    """Names and kinds of what is stored, for the credential pickers. Metadata only.
    `matches` marks the entries named for this service (`linkedin`, `linkedin.login`): the
    screen offers those first and still lets the person pick any other."""
    try:
        rows = _vault.list_secrets()
    except _vault.SecretsError:
        return []
    return [{'name': s['name'], 'entry_type': s.get('entry_type'), 'has_username': bool(s.get('username')),
             'scope': s.get('scope', 'global'), 'matches': s['name'].split('.')[0].lower() == service_id}
            for s in rows if not _vault.is_server_internal(s['name'])]


def view(service_id: str) -> dict | None:
    """The whole Routes screen for one service, or None when it has no profile."""
    profile = _registry.profile(service_id)
    if profile is None:
        return None
    with _desk._store_lock:
        recs = [r for r in (_desk._read_store().get('accounts') or {}).values() if r.get('platform') == service_id]
    return {'service': {'id': profile['service_id'], 'label': profile['label']}, 'revision': profile['revision'],
            'account_kinds': profile['account_kinds'], 'change_summary': profile['change_summary'],
            'new_account_allowed': service_id in _bindings._NEW_ACCOUNT_SERVICES,
            'purposes': purposes(profile), 'accounts': [_account_view(r, profile) for r in recs],
            'vault': vault_choices(service_id)}
