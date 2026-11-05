"""Local cached comparison, DARK (MC-1152 P3, D1=A).

No transport, reader, publisher or vault imports. Reuses persisted feed outcomes;
opening this projection cannot incur a platform read or change read consent.
P4 owns the route/UI. Manual Goal and automatic-progress darkness are preserved.
"""
from __future__ import annotations

import copy
from typing import Mapping, Sequence

from mc import desk_impact_schema as S
from mc.desk_impact_binding import goal_binding
from mc.desk_impact_sources.base import Window
from mc.desk_impact_store import ImpactStore
from mc.desk_impact_time import canonical_utc, canonical_window

NOT_ATTRIBUTABLE = 'Not attributable by this source'
POST_TOTALS_LABEL = 'Latest totals for posts published in this period'


def _at(value: str):
    return S.parse_utc('timestamp', value)


def counter_delta(samples: Sequence[dict], start: str, end: str,
                  gaps: Sequence[dict] = ()) -> dict:
    """Exact compatible endpoint observations only. Never interpolate/reset/clamp.

    Net star changes are never called new stars. Negative changes are unknown
    for interval conversions, and retain their signed net_change as context.
    """
    start, end = canonical_utc('start', start), canonical_utc('end', end)
    S.validate_window(Window(start, end, 'instant').as_dict())
    lo, hi = _at(start), _at(end)
    rows = sorted(samples, key=lambda s: _at(s['window']['end']))
    before = [s for s in rows if _at(s['window']['end']) == lo]
    after = [s for s in rows if _at(s['window']['end']) == hi]
    result: dict = {'value': None, 'reason': None, 'net_change': None,
                    'window': Window(start, end, 'instant').as_dict()}
    if not before or not after:
        result['reason'] = 'Two compatible endpoint observations required; first observation is a baseline'
        return result
    first, last = before[-1], after[-1]
    if any(first[k] != last[k] for k in ('source_id', 'connection_id', 'target', 'metric',
                                       'unit', 'definition_id', 'definition_revision')):
        result['reason'] = 'Counter identity or definition revision changed'
        return result
    path = [s for s in rows if lo <= _at(s['window']['end']) <= hi]
    if any(s['value'] is None or not s['coverage']['complete'] for s in path) \
            or any(_at(g['start']) < hi and _at(g['end']) > lo for g in gaps):
        result['reason'] = 'Counter continuity is unknown across a failed read or downtime gap'
        return result
    if any(b['value'] < a['value'] for a, b in zip(path, path[1:])):
        result['reason'] = 'Counter decreased or reset; this is not new conversions'
        result['net_change'] = last['value'] - first['value']
        return result
    result['net_change'] = last['value'] - first['value']
    result['value'] = result['net_change']
    return result


def repository_context(store: ImpactStore, project_id: str, definition: dict,
                       window: Window) -> dict:
    """Reference shared observations, without assigning them to a campaign."""
    window = canonical_window(window)
    if window.start is None:
        raise S.SchemaError('repository context: bounded interval required')
    target = definition['target']
    connection = definition['connection_id']
    all_rows = store.effective_snapshots(project_id, source_id=definition['source_id'], connection_id=connection)
    rows = [r for r in all_rows
            if r['definition_id'] is None and all(r['target'].get(k) == v for k, v in target.items())]
    collection_target = {k: v for k, v in target.items() if k != 'referrer'}
    status = store.get_coverage(project_id, definition['source_id'], connection, collection_target)
    by_series: dict[str, list[dict]] = {}
    for row in rows:
        key = S.canonical_hash({'metric': row['metric'], 'target': row['target']})
        by_series.setdefault(key, []).append(row)
    context: list[dict] = []
    for series in by_series.values():
        latest = max(series, key=lambda r: (_at(r['observed_at']), _at(r['window']['end'])))
        metric = latest['metric']
        entry: dict = {'metric': metric, 'unit': latest['unit'], 'target': latest['target'],
            'scope': latest['scope'], 'counter_kind': latest['counter_kind'],
            'observed_at': latest['observed_at'], 'provenance': latest['provenance'],
            'value': None, 'reason': None, 'window': window.as_dict(),
            'snapshot_ids': [r['snapshot_id'] for r in series],
            'stale': bool(status and status['status'] != 'ok') or _at(latest['observed_at']) < _at(window.end),
            'coverage': copy.deepcopy(status)}
        if latest['counter_kind'] == 'cumulative':
            entry['latest_total'] = latest['value']
            entry['latest_total_at'] = latest['observed_at']
            entry.update(counter_delta(series, window.start or window.end, window.end,
                                       (status or {}).get('gaps') or []))
            entry['label'] = ('Observed net star change; not gross new stars' if metric == 'star_total'
                              else 'Observed asset download counter change; not people or installs')
        elif latest['counter_kind'] == 'window':
            # Only the latest complete native list; never carry hosts omitted by
            # a newer top-ten list and never add/subtract overlapping windows.
            lists = [r for r in all_rows if r['metric'] == 'referrers_listed'
                     and all(r['target'].get(k) == target[k] for k in ('owner', 'repo'))]
            newest_list = max((_at(r['observed_at']) for r in lists), default=None)
            if newest_list is not None and _at(latest['observed_at']) != newest_list:
                continue
            entry.update(value=latest['value'], reason=latest['null_reason'], window=latest['window'])
            entry['label'] = 'Native rolling 14-day top-ten referrers; not an exact requested-range count'
        elif metric == 'stars':
            exact = [r for r in series if r['window'] == window.as_dict()]
            if exact:
                chosen = max(exact, key=lambda r: _at(r['observed_at']))
                entry.update(value=chosen['value'], reason=chosen['null_reason'],
                             observed_at=chosen['observed_at'], provenance=chosen['provenance'],
                             snapshot_ids=[chosen['snapshot_id']])
                entry['stale'] = bool(status and status['status'] != 'ok') or _at(chosen['observed_at']) < _at(window.end)
            else:
                entry['reason'] = 'No complete cached stargazer pagination for this exact interval'
            entry['label'] = 'Retained stars timestamped in this interval; not all star/unstar events'
        else:
            lo, hi = _at(window.start or window.end), _at(window.end)
            daily = { _at(r['window']['start']): r for r in series
                      if r['window']['start'] and lo <= _at(r['window']['start']) < hi }
            expected = (hi - lo).days
            aligned = lo == lo.replace(hour=0, minute=0, second=0, microsecond=0) \
                      and hi == hi.replace(hour=0, minute=0, second=0, microsecond=0)
            complete = aligned and expected > 0 and len(daily) == expected \
                       and all(r['value'] is not None for r in daily.values())
            if daily:
                entry['observed_at'] = min((r['observed_at'] for r in daily.values()), key=_at)
                entry['stale'] = bool(status and status['status'] != 'ok') or _at(entry['observed_at']) < _at(window.end)
                entry['snapshot_ids'] = [r['snapshot_id'] for r in daily.values()]
            if complete and (metric != 'traffic_unique_visitors' or expected == 1):
                entry['value'] = sum(r['value'] for r in daily.values())
            else:
                entry['reason'] = ('Unique visitors cannot be summed across days' if metric == 'traffic_unique_visitors'
                                   and expected > 1 else 'Missing, partial or non-UTC-day traffic coverage')
            entry['daily_observations'] = list(daily.values())
            entry['label'] = 'Native UTC-day traffic; daily unique visitors are not additive'
        context.append(entry)
    return {'label': 'Repository context — not attributed to this campaign',
            'target': copy.deepcopy(target), 'requested_window': window.as_dict(),
            'status': status, 'observations': context,
            'reason': None if context else 'No successful cached observations',
            'truncated_snapshot_tail': store.read_snapshots(project_id).truncated_tail}


def _metric_totals(posts: Sequence[dict]) -> dict:
    metrics: dict[str, list[dict]] = {}
    for post in posts:
        latest: dict[str, dict] = {}
        for outcome in post.get('outcomes') or []:
            if outcome.get('source') != 'feed':
                continue
            try:
                metric = S.require_slug('metric', outcome.get('metric'))
                S.count('outcome.value', outcome.get('value'))
                stamp = _at(outcome.get('at'))
            except (S.SchemaError, TypeError):
                continue
            if metric not in latest or stamp >= _at(latest[metric]['at']):
                latest[metric] = outcome
        for metric, outcome in latest.items():
            metrics.setdefault(metric, []).append(outcome)
    return {metric: {'value': sum(o['value'] for o in observed) if len(observed) == len(posts) else None,
                     'reason': None if len(observed) == len(posts) else 'Some posts have no cached read for this metric',
                     'observed_posts': len(observed), 'total_posts': len(posts),
                     'last_read_at': min((o['at'] for o in observed), key=_at),
                     'label': POST_TOTALS_LABEL}
            for metric, observed in metrics.items()}


def comparison(store: ImpactStore, project_id: str, campaign_id: str, window: Window,
               *, ledger: Sequence[dict] | None = None,
               accounts: Mapping[str, dict] | None = None) -> dict:
    window = canonical_window(window)
    if window.start is None:
        raise S.SchemaError('comparison: bounded interval required')
    binding = goal_binding(store, project_id, campaign_id)
    campaign = store.get_campaign(project_id, campaign_id)
    if ledger is None or accounts is None:
        from mc import desk
        with desk._store_lock:
            state = desk._read_store()
            if ledger is None:
                ledger = copy.deepcopy(state['ledger'])  # no list_ledger limit/truncation
            if accounts is None:
                accounts = copy.deepcopy(state['accounts'])
    assert ledger is not None and accounts is not None
    selected = [r for r in ledger if r.get('project_id') == project_id and r.get('campaign_id') == campaign_id]
    planned: dict[str, dict] = {}
    for account in (campaign.get('plan') or {}).get('accounts') or []:
        ident = account if isinstance(account, str) else account.get('channel_id') or account.get('id')
        if ident:
            planned[ident] = accounts.get(ident) or {'id': ident}
    grouped: dict[str, list[dict]] = {ident: [] for ident in planned}
    unknown: dict[str, dict] = {}
    for post in selected:
        # Modern ledger.account is the stable workspace account ID. Legacy
        # human handles/platform keys are not promoted to an account match.
        ident = post.get('account_id') or post.get('account')
        if not isinstance(ident, str) or not S.valid_id(ident) \
                or (ident not in planned and ident not in accounts and ident == post.get('platform')):
            ident = 'unknown-account-' + str(post.get('platform') or 'unknown')
            unknown[ident] = {'id': None, 'label': 'Historical account identity unavailable',
                              'platform': post.get('platform')}
        grouped.setdefault(ident, []).append(post)
    rows: list[dict] = []
    for ident, history in grouped.items():
        account = planned.get(ident) or accounts.get(ident) or unknown.get(ident) or {
            'id': ident, 'label': 'Removed account ' + ident}
        posts = [r for r in history if r.get('published_at')
                 and _at(window.start) <= _at(r['published_at']) < _at(window.end)]
        # Duplicate receipts from retries share an id; never add them twice.
        posts = list({p['id']: p for p in posts}.values())
        metrics = _metric_totals(posts)
        # Existing Reader normalizes X's url_link_clicks to the stored 'clicks'
        # outcome, also used by the selected generic pane reader. Read only the
        # persisted name; no API fields/permissions are requested here.
        clicks = metrics.pop('clicks', None)
        platforms = {p.get('platform') for p in history if p.get('platform')}
        rows.append({'account_id': account.get('id'), 'label': account.get('label') or ident,
                     'platform': account.get('platform') or (next(iter(platforms)) if len(platforms) == 1 else None),
                     'in_plan': ident in planned,
                     'removed': ident not in accounts, 'published_posts': len(posts),
                     'receipt_ids': [p['id'] for p in posts], 'post_metrics': metrics,
                     'post_metrics_coverage': {
                         'reason': ('No cached post metrics' if posts else 'No posts published in this period')
                                   if not metrics and not clicks else None},
                     'platform_clicks': clicks or {'value': None, 'reason': 'No cached outbound-click read'},
                     'conversions': {'value': None, 'reason': NOT_ATTRIBUTABLE if binding['definition']
                                     else 'No conversion definition'}})
    definition = binding['definition']
    return {**binding, 'requested_window': window.as_dict(), 'channels': rows,
            'post_metrics_label': POST_TOTALS_LABEL,
            'repository_context': repository_context(store, project_id, definition, window)
                                  if definition and definition['source_id'] == 'github' else None}
