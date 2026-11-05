"""Shared, explicit impact collection (MC-1152 P3, DARK).

No worker, routes or scheduling is installed. A server caller must inject trusted
connection metadata, transport and clock. One lock per store/project serializes
manual/daily callers within the single server. P1 owns all durable writes.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import threading
from typing import Any, Callable, Mapping, Sequence

from mc import desk_impact_schema as S
from mc.desk_impact_sources import base as B
from mc.desk_impact_sources.registry import AdapterRegistry, REGISTRY
from mc.desk_impact_sources.github import repository_target
from mc.desk_impact_store import ImpactStore, OwnershipError
from mc.desk_impact_time import canonical_utc, canonical_window

_guard = threading.Lock()
_locks: dict[tuple[str, str], threading.Lock] = {}
MIN_RETRY_SECONDS = 900


def _lock(root: Path, project_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault((str(root.absolute()), project_id), threading.Lock())


def collect_campaigns(store: ImpactStore, project_id: str, campaign_ids: Sequence[str],
                      window: B.Window, *, transport: B.Transport, clock: B.Clock,
                      connection_resolver: Callable[[str], Mapping[str, Any] | None],
                      registry: AdapterRegistry = REGISTRY) -> dict:
    """Read shared targets once, then return per-campaign snapshot references.

    Definitions remain revisioned campaign bindings, while GitHub samples are
    deliberately unbound. No snapshots are copied/summed per overlapping campaign.
    Same-day successful reads and 15-minute failed retries reuse the cache;
    rate-limit retry-after is also shared across targets on the same connection.
    """
    pid = S.require_id('project_id', project_id)
    window = canonical_window(window)
    now = canonical_utc('clock', clock())
    stamp = S.parse_utc('clock', now)
    if window.start is None or S.parse_utc('end', window.end) > stamp:
        raise B.DefinitionRefused('collection: bounded historical interval required')
    groups: dict[str, dict] = {}
    bindings: dict[str, dict] = {}
    # Validate the whole batch before making a network call or writing state.
    for cid in dict.fromkeys(campaign_ids):
        definitions = store.get_definitions(pid, cid)
        definition = definitions['active']
        if not definition:
            bindings[cid] = {'definition': None, 'snapshot_ids': [], 'reason': 'No conversion definition'}
            continue
        conn = connection_resolver(definition['connection_id'])
        if not conn or conn.get('project_id') != pid:
            raise OwnershipError('connection not found in this project')
        adapter = registry.get(definition['source_id'])
        if adapter.source_id != 'github':
            raise B.DefinitionRefused('collection: P3 supports GitHub context only')
        adapter.validate_definition(definition, conn)
        target = repository_target(definition['target'])
        target.pop('referrer', None)  # endpoint supplies the native top-ten list
        key = S.coverage_key(adapter.source_id, definition['connection_id'], target)
        groups[key] = {'adapter': adapter, 'target': target, 'connection_id': definition['connection_id']}
        bindings[cid] = {'definition': definition, 'source_key': key, 'snapshot_ids': []}
    results: dict[str, dict] = {}
    request_count = 0
    with _lock(store.root, pid):
        for key, group in groups.items():
            sid, conn, target = group['adapter'].source_id, group['connection_id'], group['target']
            previous = store.get_coverage(pid, sid, conn, target)
            snapshots = store.effective_snapshots(pid, source_id=sid, connection_id=conn)
            shared = [s for s in snapshots if s['definition_id'] is None
                      and all(s['target'].get(k) == v for k, v in target.items())]
            # A rate limit is connection-wide, not a per-campaign read allowance.
            statuses = [store.get_coverage(pid, sid, conn, {'read_scope': 'connection'})]
            delayed = any(s and s['retry_after'] and S.parse_utc('retry_after', s['retry_after']) > stamp
                          for s in statuses)
            exact = bool(previous and previous['last_success_at'] and any(
                s['metric'] == 'stars' and s['window'] == window.as_dict()
                and s['value'] is not None
                and S.parse_utc('observed_at', s['observed_at']) <= S.parse_utc('last_success', previous['last_success_at'])
                for s in shared))
            cached = bool(previous and previous['status'] == 'ok'
                          and S.parse_utc('last_attempt', previous['last_attempt_at']).date() == stamp.date()
                          and (exact or 'asset_id' in target))
            recent_failure = bool(previous and previous['status'] == 'error' and
                (stamp - S.parse_utc('last_attempt', previous['last_attempt_at'])).total_seconds() < MIN_RETRY_SECONDS)
            if delayed or cached or recent_failure:
                results[key] = {'state': 'cached' if cached else 'deferred', 'status': previous,
                                'connection_status': statuses[0] if delayed else None,
                                'snapshot_ids': [s['snapshot_id'] for s in shared]}
                continue
            request = B.CollectRequest(pid, sid, conn, target, window)
            result = group['adapter'].collect(request, transport, lambda: now)
            request_count += result.request_count
            gap_records: list[dict] = []
            if previous:
                last = previous['last_success_at'] or previous['last_attempt_at']
                if stamp - S.parse_utc('last', last) > timedelta(days=1):
                    gap_records.append({'start': last, 'end': now,
                                        'reason': 'Collection downtime; unobserved continuity'})
            if not result.coverage.complete:
                gap_records.append({'start': window.start or now, 'end': window.end,
                                    'reason': 'Incomplete source read; requested coverage is unknown'})
                if previous and previous['last_attempt_at'] != now:
                    gap_records.append({'start': previous['last_attempt_at'], 'end': now,
                                        'reason': 'Endpoint observation missing; continuity broken'})
            for observation in result.observations:
                store.append_snapshot(pid, observation)
            error = result.errors[0] if result.errors else B.SourceError('partial', 'Source coverage incomplete')
            status = store.record_attempt(pid, sid, conn, target,
                ok=result.ok and result.coverage.complete, error_code=error.code,
                error_detail=error.detail, retry_after=result.retry_after, gaps=gap_records,
                incomplete_pages=result.coverage.incomplete_pages)
            if any(e.code == 'rate_limited' for e in result.errors):
                store.record_attempt(pid, sid, conn, {'read_scope': 'connection'}, ok=False,
                    error_code='rate_limited', error_detail='GitHub connection rate limited',
                    retry_after=result.retry_after or (stamp + timedelta(seconds=MIN_RETRY_SECONDS)).isoformat())
            shared = [s for s in store.effective_snapshots(pid, source_id=sid, connection_id=conn)
                      if s['definition_id'] is None and all(s['target'].get(k) == v for k, v in target.items())]
            results[key] = {'state': 'collected', 'status': status,
                            'snapshot_ids': [s['snapshot_id'] for s in shared]}
        for binding in bindings.values():
            if 'source_key' in binding:
                binding['snapshot_ids'] = results[binding['source_key']]['snapshot_ids']
    return {'campaigns': bindings, 'sources': results, 'request_count': request_count}
