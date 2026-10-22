"""Source-observed Codex sample boundaries (MC-998).

Rollout ``event_msg/token_count.info.total_token_usage`` is cumulative over
the native thread, including exec resume. ``last_token_usage`` is one model
request, not a turn. Never substitute the last completed turn's session.usage
for a mid-turn observation, nor move an older rollout reading to a newer time.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from mc.usage_breakdown_sampler import sample_tick_checkpoint_fields
from mc.usage_breakdown_store import UsageBreakdownStore


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _usage(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    required = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if any(type(value.get(k)) is not int or value[k] < 0 for k in required):
        return None
    if value['cached_input_tokens'] > value['input_tokens']:
        return None
    reasoning = value.get('reasoning_output_tokens')
    if reasoning is not None and (type(reasoning) is not int
                                  or not 0 <= reasoning <= value['output_tokens']):
        return None
    # Native fixtures report zero here; no verified nonzero cache-write contract.
    cache_write = value.get('cache_write_input_tokens', 0)
    if type(cache_write) is not int or cache_write != 0:
        return None
    total = value.get('total_tokens')
    if total is not None and (type(total) is not int
                              or total != value['input_tokens'] + value['output_tokens']):
        return None
    return {k: value[k] for k in required} | {'reasoning_output_tokens': reasoning}


def _read_boundary(path: Path, native_id: str, boundary: datetime,
                   baseline: datetime, prior: list[dict]) -> tuple[dict | None, str]:
    meta_at = None
    previous = None
    previous_at = None
    found = None
    saw_boundary = False
    with path.open(encoding='utf-8') as stream:
        for raw in stream:
            # Keep prompts/tool bodies out of the projection. Only native metadata
            # and usage records are decoded; no transcript content is retained.
            if '"session_meta"' not in raw and '"token_count"' not in raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                return None, 'rollout usage record is malformed or still being appended'
            if not isinstance(record, dict):
                continue
            payload = record.get('payload')
            if not isinstance(payload, dict):
                return None, 'rollout usage payload is malformed'
            if record.get('type') == 'session_meta':
                if meta_at is not None or payload.get('id') != native_id:
                    return None, 'native thread identity is ambiguous'
                meta_at = _time(record.get('timestamp'))
                if meta_at is None or meta_at < baseline:
                    return None, 'native history predates the session baseline or has no source time'
                if meta_at > boundary:
                    return None, 'native thread starts after the requested boundary'
                # A fresh native thread after an earlier checkpoint would reset
                # the MC lifetime scope, even if its new total already exceeds it.
                if any(row.get('token_coverage') == 'complete'
                       and (_time(row.get('observed_at')) or boundary) < meta_at for row in prior):
                    return None, 'native thread changed after earlier session evidence'
                continue
            if record.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                continue
            at = _time(record.get('timestamp'))
            if meta_at is None or at is None:
                return None, 'usage observation has no verified native identity or source time'
            if at > boundary:
                continue
            if at < meta_at or (previous_at is not None and at < previous_at):
                return None, 'usage source times are not ordered'
            info = payload.get('info')
            if info is None:
                if at == boundary:
                    return None, 'boundary contains allowance data without token evidence'
                continue
            if not isinstance(info, dict):
                return None, 'usage info is malformed'
            total, last = _usage(info.get('total_token_usage')), _usage(info.get('last_token_usage'))
            if total is None or last is None:
                return None, 'native usage counters are missing, malformed or unsupported'
            if any(last[k] > total[k] for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')):
                return None, 'last request exceeds native cumulative counters'
            if previous is None and total != last:
                return None, 'native cumulative history is incomplete or inherited'
            if previous is not None:
                keys = ('input_tokens', 'cached_input_tokens', 'output_tokens')
                if (any(total[k] < previous[k] for k in keys)
                        or (total['reasoning_output_tokens'] is not None
                            and previous['reasoning_output_tokens'] is not None
                            and total['reasoning_output_tokens'] < previous['reasoning_output_tokens'])
                        or total['input_tokens'] - total['cached_input_tokens']
                        < previous['input_tokens'] - previous['cached_input_tokens']):
                    return None, 'native cumulative counters reset'
            if at == boundary:
                if saw_boundary and found != total:
                    return None, 'boundary has conflicting native totals'
                found, saw_boundary = total, True
            previous, previous_at = total, at
    if not saw_boundary:
        return None, 'no token observation at allowance source time; last-turn usage is not live evidence'
    return found, ''


def write_sample_tick(session: dict, observed_at: str, *, store: UsageBreakdownStore,
                      project_path: str, log: Callable[[str], object]) -> None:
    """Write complete only at an exact, verified native source boundary.

    Repeated source observations share the existing store dedup key. Missing,
    stale, inherited, reset or rolled-thread evidence stays NULL/unavailable.
    This deliberately does not repair old carry/completion totals or backfill.
    """
    if session.get('incognito'):
        return
    from mc.agent_runtime import get_runtime

    sid = session['session_id']
    fact: dict = {}
    reason = ''
    try:
        boundary = _time(observed_at)
        checkpoints = store.get_session_checkpoints(sid)
        baseline_row = checkpoints.get('baseline') or {}
        baseline = _time(baseline_row.get('observed_at'))
        native_id = session.get('provider_session_id')
        if boundary is None or baseline is None:
            reason = 'source time or durable session baseline is unavailable'
        elif session.get('_codex_usage_carry'):
            reason = 'rolled-thread carry has no verified cumulative source history'
        elif not isinstance(native_id, str) or not native_id:
            reason = 'native thread identity is unavailable'
        else:
            path = get_runtime('codex').transcript_path(project_path, native_id)
            if path is None:
                reason = 'native rollout is unavailable'
            else:
                prior = checkpoints.get('completions', []) + checkpoints.get('sample_ticks', [])
                usage, reason = _read_boundary(Path(path), native_id, boundary, baseline, prior)
                if usage is not None:
                    inp, cached = usage['input_tokens'], usage['cached_input_tokens']
                    fact = {'input_fresh': inp - cached, 'input_cache_read': cached,
                            'input_processed_total': inp, 'output_tokens': usage['output_tokens'],
                            'output_reasoning': usage['reasoning_output_tokens'],
                            'token_coverage': 'complete'}
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        reason = f'native rollout read failed ({type(error).__name__})'
    # Reasons are diagnostic state, not source values. Log only transitions;
    # checkpoints retain the existing nullable schema and unknown categories.
    if reason and reason != session.get('_codex_sample_tick_reason'):
        log(f'[usage-breakdown] Codex sample-tick unavailable for {sid[:12]}: {reason}')
    session['_codex_sample_tick_reason'] = reason
    store.record_session_checkpoint(**sample_tick_checkpoint_fields(
        sid, provider='codex', observed_at=observed_at, telemetry=fact))
