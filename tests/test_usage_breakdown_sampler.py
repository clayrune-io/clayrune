"""MC-998 Phase 2: mc/usage_breakdown_sampler.py — pure sampling + session-fact
logic behind the Usage Breakdown store. No Flask/server import; fetch
results are passed in directly, matching the module's "no I/O of its own"
contract from docs/USAGE_BREAKDOWN_SPEC.md's Sampling section.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_sampler import (  # noqa: E402
    _normalize_resets_at, sample_claude, sample_codex, session_fact_from_entry,
    should_sample_interval_seconds,
)
from mc.usage_breakdown_store import UsageBreakdownStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return UsageBreakdownStore(tmp_path / 'usage_breakdown.sqlite')


# ── sample_claude ───────────────────────────────────────────────────────

def test_sample_claude_inserts_one_row_per_populated_window(store):
    n = sample_claude(store, usage_limits={
        'five_hour': {'utilization': 12.0, 'resets_at': 'r1'},
        'seven_day': {'utilization': 30.0, 'resets_at': 'r2'},
        'seven_day_opus': None,
        'seven_day_sonnet': None,
    }, fetched_at_epoch=1_000_000.0)

    assert n == 2
    rows5h = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    rows7d = store.list_allowance_samples(provider='claude', window_kind='7d', window_scope='all')
    assert rows5h[0]['raw_utilization'] == 12.0
    assert rows7d[0]['raw_utilization'] == 30.0


def test_sample_claude_cache_hit_same_fetch_time_inserts_nothing_new(store):
    payload = {'five_hour': {'utilization': 12.0, 'resets_at': 'r1'}}
    first = sample_claude(store, usage_limits=payload, fetched_at_epoch=1_000_000.0)
    second = sample_claude(store, usage_limits=payload, fetched_at_epoch=1_000_000.0)

    assert first == 1
    assert second == 0  # identical fetched_at_epoch = same cache read, not a new sample
    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert len(rows) == 1


def test_sample_claude_new_fetch_time_inserts_again(store):
    payload = {'five_hour': {'utilization': 12.0, 'resets_at': 'r1'}}
    sample_claude(store, usage_limits=payload, fetched_at_epoch=1_000_000.0)
    n = sample_claude(store, usage_limits=payload, fetched_at_epoch=1_000_100.0)

    assert n == 1
    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert len(rows) == 2


def test_sample_claude_no_data_or_no_fetch_time_is_a_noop(store):
    assert sample_claude(store, usage_limits=None, fetched_at_epoch=1.0) == 0
    assert sample_claude(store, usage_limits={'five_hour': {'utilization': 1.0}}, fetched_at_epoch=None) == 0


def test_sample_claude_normalizes_jittered_resets_at_before_storing(store):
    """MC-998 follow-up: the vendor endpoint returns resets_at with
    sub-second jitter on every poll; round to the nearest minute at ingest
    so new rows are clean (the aggregate-side tolerance is what fixes rows
    already in the DB)."""
    sample_claude(store, usage_limits={
        'five_hour': {'utilization': 12.0, 'resets_at': '2026-09-28T19:00:00.444543+00:00'},
    }, fetched_at_epoch=1_000_000.0)
    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert rows[0]['resets_at'] == '2026-09-28T19:00:00+00:00'


# ── _normalize_resets_at ────────────────────────────────────────────────

def test_normalize_resets_at_rounds_down_under_30_seconds():
    assert _normalize_resets_at('2026-09-28T18:59:59.567670+00:00') == '2026-09-28T19:00:00+00:00'


def test_normalize_resets_at_rounds_up_at_30_seconds_or_more():
    assert _normalize_resets_at('2026-09-28T19:00:29.999999+00:00') == '2026-09-28T19:00:00+00:00'
    assert _normalize_resets_at('2026-09-28T19:00:30.000001+00:00') == '2026-09-28T19:01:00+00:00'


def test_normalize_resets_at_passes_through_none_and_unparseable():
    assert _normalize_resets_at(None) is None
    assert _normalize_resets_at('r1') == 'r1'  # e.g. a test fixture placeholder -- never guessed at


# ── sample_codex ────────────────────────────────────────────────────────

def test_sample_codex_prefers_event_at_over_sampled_at_for_dedup(store):
    detail = {
        'five_hour': {'utilization': 20.0, 'resets_at': 'r'},
        'weekly': {'utilization': 77.0, 'resets_at': 'r2'},
        'sampled_at': '2026-09-28T00:00:00Z',  # file mtime -- would look "fresh" every poll
        'event_at': '2026-09-27T20:48:29.393Z',  # the real event identity
    }
    first = sample_codex(store, detail=detail)
    second = sample_codex(store, detail=dict(detail, sampled_at='2026-09-28T00:05:00Z'))  # mtime moved, event_at didn't

    assert first == 2
    assert second == 0  # same event_at -> not a new sample despite the mtime bump


def test_sample_codex_falls_back_to_sampled_at_when_no_event_at(store):
    detail = {'five_hour': {'utilization': 20.0, 'resets_at': 'r'}, 'sampled_at': '2026-09-28T00:00:00Z'}
    n = sample_codex(store, detail=detail)
    assert n == 1


def test_sample_codex_no_detail_is_a_noop(store):
    assert sample_codex(store, detail=None) == 0


# ── session_fact_from_entry ─────────────────────────────────────────────

def test_claude_transcript_path_sums_processed_total_and_marks_complete():
    entry = {
        'provider': 'claude', 'status': 'completed', 'ts': '2026-09-28T12:00:00Z',
        'started_at': '2026-09-28T11:55:00Z',
        'input_tokens': 500, 'output_tokens': 200, 'cache_read_tokens': 100,
        'usage': {'cache_creation_input_tokens': 50},
        'model': 'claude-sonnet-5', 'observed_model': 'claude-sonnet-5',
        'trigger_type': 'manual', 'character': {'name': 'Tobin'},
    }
    fact = session_fact_from_entry(entry, project_id='mission_control')

    assert fact['token_source'] == 'transcript'
    assert fact['token_coverage'] == 'complete'
    assert fact['input_fresh'] == 500
    assert fact['input_cache_write'] == 50
    assert fact['input_cache_read'] == 100
    assert fact['input_processed_total'] == 650  # 500 + 50 + 100
    assert fact['output_tokens'] == 200
    assert fact['character'] == 'Tobin'
    assert fact['status'] == 'completed'
    assert fact['ended_at'] == '2026-09-28T12:00:00Z'


def test_claude_nested_fallback_marks_partial_coverage():
    entry = {
        'provider': 'claude', 'status': 'completed', 'ts': 't',
        'input_tokens': 0, 'output_tokens': 0,
        'usage': {'input_tokens': 10, 'output_tokens': 5,
                   'cache_creation_input_tokens': 1, 'cache_read_input_tokens': 2},
    }
    fact = session_fact_from_entry(entry, project_id='p')

    assert fact['token_source'] == 'nested_usage'
    assert fact['token_coverage'] == 'partial'
    assert fact['input_processed_total'] == 13  # 10 + 1 + 2


def test_codex_nested_usage_input_total_not_double_counted_with_cached():
    entry = {
        'provider': 'codex', 'status': 'completed', 'ts': 't',
        'usage': {'input_tokens': 1000, 'output_tokens': 50, 'cached_input_tokens': 900},
        'observed_model': 'gpt-5-codex',
    }
    fact = session_fact_from_entry(entry, project_id='p')

    assert fact['provider'] == 'codex'
    assert fact['input_processed_total'] == 1000  # NOT 1000 + 900
    assert fact['output_tokens'] == 50
    assert fact['token_coverage'] == 'complete'


def test_codex_cached_input_split_into_cache_read_not_fresh():
    """Review finding #9 (docs/_journal/4668eafc-mc998-fenn-review.md): with
    input=1000, cached=900, `input_fresh` must be the non-cached 100, not the
    full 1000 -- the segmented-bar's estimate range uses input_fresh as its
    low-bound proxy, and treating all input as fresh overstates it."""
    entry = {
        'provider': 'codex', 'status': 'completed', 'ts': 't',
        'usage': {'input_tokens': 1000, 'output_tokens': 50, 'cached_input_tokens': 900},
        'observed_model': 'gpt-5-codex',
    }
    fact = session_fact_from_entry(entry, project_id='p')

    assert fact['input_fresh'] == 100
    assert fact['input_cache_read'] == 900
    assert fact['input_processed_total'] == 1000  # unchanged, still not double-counted


def test_no_evidence_at_all_is_unavailable_not_zero():
    entry = {'provider': 'codex', 'status': 'completed', 'ts': 't', 'usage': {}}
    fact = session_fact_from_entry(entry, project_id='p')

    assert fact['token_coverage'] == 'unavailable'
    assert fact['input_processed_total'] is None
    assert fact['output_tokens'] is None


def test_stopped_status_folds_into_error_running_has_no_ended_at():
    stopped = session_fact_from_entry({'provider': 'claude', 'status': 'stopped', 'ts': 't'}, project_id='p')
    running = session_fact_from_entry({'provider': 'claude', 'status': 'in_progress'}, project_id='p')

    assert stopped['status'] == 'error'
    assert stopped['ended_at'] == 't'
    assert running['status'] == 'running'
    assert running['ended_at'] is None


def test_missing_trigger_type_is_unknown_not_defaulted_to_manual():
    fact = session_fact_from_entry({'provider': 'claude', 'status': 'completed', 'ts': 't'}, project_id='p')
    assert fact['trigger_type'] == 'unknown'


def test_housekeeping_and_included_flags_pass_through():
    fact = session_fact_from_entry({'provider': 'claude', 'status': 'completed', 'ts': 't'},
                                    project_id='p', housekeeping=True, included=False)
    assert fact['housekeeping'] is True
    assert fact['included'] is False


# ── should_sample_interval_seconds ──────────────────────────────────────

def test_sample_interval_active_vs_idle():
    assert should_sample_interval_seconds(any_session_active=True) == 60
    assert should_sample_interval_seconds(any_session_active=False) == 300
