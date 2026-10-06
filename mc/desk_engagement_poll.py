"""Engagement per-platform dispatch and read consent (MC-1062/06b).

Reader selection and existing budgets remain in desk_engagement. Consent uses
its original presence channel_id, never the OAuth singleton argument, and is
checked separately before mentions and each metrics batch. Legacy/unset keeps
the previous poll/error/budget behavior. No new provider or spend authority.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from mc import desk as _desk
from mc import desk_engagement as _eng
from mc.desk_connect import permission_check as _permissions
from mc.desk_connect import permission_policy as _policy
from mc.desk_connect import registry as _registry


def _require_read(account_id: str | None, platform: str, reader: _eng.Reader,
                  capability: str) -> None:
    if platform == 'x':
        route = {'api': 'x-oauth', 'pane': 'x-browser'}.get(reader.via, '')
    else:
        # Identify the route we actually run, not a scope supplied by consent.
        # A missing/ambiguous route cannot grant an explicit Allow. Unset policy
        # still returns None from the shared seam, retaining generic legacy reads.
        with _desk._store_lock:
            rec = (_desk._read_store().get('accounts') or {}).get(account_id)
        explicit = isinstance(rec, dict) and (_policy.POLICY_KEY in rec or _policy.VERSION_KEY in rec)
        profile = _registry.profile(platform) if explicit else None
        routes = [r['id'] for r in (profile or {}).get('routes', [])
                  if r.get('transport') == 'browser' and reader.via == 'pane']
        route = routes[0] if len(routes) == 1 else ''
    _permissions.require_permission(account_id, platform, 'read', purpose='read_own',
                                    capability=capability, route_id=route,
                                    account_kind='account' if platform == 'x' else None)


def _denied(project_id: str, platform: str, reader: _eng.Reader, entry: dict,
            capability: str, error: _permissions.PermissionDenied) -> None:
    reason = str(error)
    entry.setdefault('permission_denied', {})[capability] = reason
    entry['error'] = reason
    _desk.record_read(platform=platform, project_id=project_id,
                      kind='replies' if capability == 'mentions' else 'metrics',
                      resources=0, cost=0.0, ok=False, error=reason)
    _desk.set_read_coverage(project_id, platform, ok=False, error=reason, via=reader.via)


def poll_platform(project_id: str, platform: str, reader: _eng.Reader,
                   entry: dict, now_dt: datetime) -> None:
    account = _eng.platform_account(project_id, platform)
    account_id = account.get('channel_id') if isinstance(account, dict) else None
    rows = [r for r in _desk.list_ledger(limit=100000, platform=platform, project_id=project_id)]
    known = {}
    for r in rows:
        ext = reader.post_external_id(r)
        if ext:
            known[ext] = r['id']

    # 1. replies + mentions
    cursor = (_desk.get_read_coverage(project_id).get(platform) or {}).get('cursor')
    try:
        _require_read(account_id, platform, reader, 'mentions')
        if not _eng._afford(project_id, reader, reader.mentions_units, now_dt):
            msg = 'read budget spent: replies not read this period'
            entry['budget_blocked'] = True
            _desk.set_read_coverage(project_id, platform, ok=False, error=msg, via=reader.via)
            return
        got = reader.fetch_mentions(since_id=cursor, known_posts=known)
    except _permissions.PermissionDenied as e:
        _denied(project_id, platform, reader, entry, 'mentions', e)
        got = None
    except _eng.ReadError as e:
        _desk.record_read(platform=platform, project_id=project_id, kind='replies',
                          resources=0, cost=0.0, ok=False, error=str(e))
        entry['error'] = str(e)
        _desk.set_read_coverage(project_id, platform, ok=False, error=str(e),
                                via=reader.via, error_kind=e.kind)
        if e.kind == 'not_signed_in':
            entry['state'] = 'not_connected'
            entry['message'] = f'Not connected ({reader.sign_in_short})'
        return
    if got is not None:
        cost = got['resources'] * reader.unit_cost
        _desk.record_read(platform=platform, project_id=project_id, kind='replies',
                          resources=got['resources'], cost=cost, ok=True)
        entry['spent'] += cost
        ledger_by_id = {r['id']: r for r in rows}
        for it in got['items']:
            led = ledger_by_id.get(it.get('post_id') or '')
            _row, created = _desk.upsert_engagement_item({
                **it, 'platform': platform, 'account': got.get('account'),
                'project_id': (led or {}).get('project_id') or project_id,
                'campaign_id': (led or {}).get('campaign_id'),
            })
            entry['new_items'] += 1 if created else 0
        _desk.set_read_coverage(project_id, platform, ok=True, cursor=got.get('cursor'),
                                via=reader.via)

    # 2. per-post metrics: once a day per post, recent posts only, each batch
    # costed before it is sent.
    cutoff = _eng._iso(now_dt - timedelta(days=_eng.METRICS_WINDOW_DAYS))
    today = _eng._iso(now_dt)[:10]
    todo = []
    for r in rows:
        ext = reader.post_external_id(r)
        if not ext or (r.get('published_at') or '') < cutoff:
            continue
        if any(o.get('source') == 'feed' and (o.get('at') or '')[:10] == today
               for o in r.get('outcomes') or []):
            continue          # already measured today; do not pay twice
        todo.append((r['id'], ext))
    todo = todo[:_eng.METRICS_MAX_POSTS]
    for i in range(0, len(todo), _eng.METRICS_BATCH):
        batch = todo[i:i + _eng.METRICS_BATCH]
        try:
            _require_read(account_id, platform, reader, 'post_metrics')
            if not _eng._afford(project_id, reader, len(batch), now_dt):
                entry['budget_blocked'] = True
                break
            res = reader.fetch_metrics([ext for _lid, ext in batch])
        except _permissions.PermissionDenied as e:
            _denied(project_id, platform, reader, entry, 'post_metrics', e)
            break
        except _eng.ReadError as e:
            _desk.record_read(platform=platform, project_id=project_id, kind='metrics',
                              resources=0, cost=0.0, ok=False, error=str(e))
            entry['error'] = str(e)
            if e.kind == 'not_signed_in':
                _desk.set_read_coverage(project_id, platform, ok=False, error=str(e),
                                        via=reader.via, error_kind=e.kind)
            break
        cost = res['resources'] * reader.unit_cost
        _desk.record_read(platform=platform, project_id=project_id, kind='metrics',
                          resources=res['resources'], cost=cost, ok=True)
        entry['spent'] += cost
        entry['metrics_unavailable'] += len(res.get('unavailable') or {})
        for lid, ext in batch:
            for metric, value in (res['metrics'].get(ext) or {}).items():
                if _desk.record_feed_outcome(lid, metric, value, at=_eng._iso(now_dt)):
                    entry['metrics_written'] += 1
    entry['spent'] = round(entry['spent'], 4)
