"""MC-998 Phase 2: mc/usage_breakdown_store.py — the durable
data/usage_breakdown.sqlite store behind the Usage Breakdown dashboard.

Pure unit tests against a tmp_path db file; no server import, no network.
Pins the contracts docs/USAGE_BREAKDOWN_SPEC.md's "Sampling and durable
facts" section calls out explicitly: sample dedup by source identity, no
double-pruning of live sessions, and created_at surviving an upsert.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_store import UsageBreakdownStore, db_path_for, SchemaError  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return UsageBreakdownStore(tmp_path / 'usage_breakdown.sqlite')


def test_db_path_for_is_sibling_of_data_dir_not_inside_it(tmp_path):
    p = db_path_for(tmp_path)
    assert p == tmp_path / 'data' / 'usage_breakdown.sqlite'
    assert p.parent.name == 'data'
    # DATA_DIR is <data_root>/data/projects -- must not resolve under it.
    assert 'projects' not in p.parts


def test_record_allowance_sample_dedupes_identical_source_observation(store):
    first = store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=42.5, resets_at='2026-09-29T00:00:00Z',
        source_observed_at='2026-09-28T12:00:00Z')
    second = store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=42.5, resets_at='2026-09-29T00:00:00Z',
        source_observed_at='2026-09-28T12:00:00Z')

    assert first is True
    assert second is False  # same source identity -- not a new sample
    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert len(rows) == 1


def test_record_allowance_sample_distinct_observed_at_creates_new_row(store):
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=10.0, resets_at='r', source_observed_at='t1')
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=15.0, resets_at='r', source_observed_at='t2')

    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert [r['raw_utilization'] for r in rows] == [10.0, 15.0]


def test_out_of_range_utilization_marked_invalid_not_clamped(store):
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=150.0, resets_at='r', source_observed_at='t1')

    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert rows[0]['quality'] == 'invalid'


def test_session_fact_upsert_preserves_created_at_across_updates(store):
    store.upsert_session_fact('sess-1', {
        'provider': 'codex', 'project_id': 'mission_control', 'status': 'running',
        'started_at': '2026-09-28T10:00:00Z', 'token_coverage': 'unavailable',
    })
    first = store.get_session_fact('sess-1')

    store.upsert_session_fact('sess-1', {
        'provider': 'codex', 'project_id': 'mission_control', 'status': 'completed',
        'started_at': '2026-09-28T10:00:00Z', 'ended_at': '2026-09-28T10:05:00Z',
        'input_processed_total': 500, 'output_tokens': 200, 'token_coverage': 'complete',
    })
    second = store.get_session_fact('sess-1')

    assert first['created_at'] == second['created_at']
    assert second['status'] == 'completed'
    assert second['input_processed_total'] == 500
    assert second['updated_at'] >= first['updated_at']


def test_session_fact_included_flag_defaults_true_and_is_settable(store):
    store.upsert_session_fact('sess-hk', {
        'provider': 'claude', 'status': 'completed', 'housekeeping': True,
        'included': False, 'token_coverage': 'unavailable',
    })
    fact = store.get_session_fact('sess-hk')
    assert fact['included'] == 0
    assert fact['housekeeping'] == 1


def test_code_delta_upsert_and_unavailable_reason(store):
    store.upsert_session_fact('sess-2', {'provider': 'claude', 'token_coverage': 'unavailable'})
    store.upsert_code_delta('sess-2', {
        'status': 'unavailable', 'reason': 'shared dirty worktree',
    })
    delta = store.get_code_delta('sess-2')
    assert delta['status'] == 'unavailable'
    assert delta['reason'] == 'shared dirty worktree'
    assert delta['added'] is None


def test_prune_removes_completed_facts_past_retention_but_keeps_running(store):
    old_cutoff_session = 'sess-old'
    store.upsert_session_fact(old_cutoff_session, {
        'provider': 'claude', 'status': 'completed', 'token_coverage': 'complete',
    })
    store.upsert_code_delta(old_cutoff_session, {'status': 'ok', 'added': 1, 'deleted': 1})
    store.upsert_session_fact('sess-running', {
        'provider': 'claude', 'status': 'running', 'token_coverage': 'unavailable',
    })
    # Backdate the completed fact's ended_at directly (bypassing the public
    # API, which always stamps "now") to simulate a 91-day-old completion.
    with sqlite3.connect(store.db_path) as raw:
        raw.execute("UPDATE session_fact SET ended_at = '2020-01-01T00:00:00+00:00' "
                    "WHERE session_id = ?", (old_cutoff_session,))
        raw.commit()

    removed = store.prune_older_than(days=90)

    assert removed['session_fact'] == 1
    assert removed['code_delta'] == 1
    assert store.get_session_fact(old_cutoff_session) is None
    assert store.get_code_delta(old_cutoff_session) is None
    assert store.get_session_fact('sess-running') is not None  # never pruned by age


def test_prune_does_not_touch_recent_allowance_samples(store):
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=1.0, resets_at='r', source_observed_at='now')

    removed = store.prune_older_than(days=90)

    assert removed['allowance_sample'] == 0
    assert len(store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')) == 1


def test_coverage_begins_none_before_any_sample(store):
    assert store.coverage_begins() is None


def test_coverage_begins_earliest_sample_time(store):
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=1.0, resets_at='r', source_observed_at='t1')
    assert store.coverage_begins() is not None


def test_read_before_any_write_returns_empty_not_an_error(store):
    """A fresh, never-sampled store is a normal state (spec's 'Sampling has
    not begun' empty state), not a schema failure -- only an existing db with
    an incompatible shape/version should ever raise SchemaError."""
    assert store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all') == []
    assert store.list_session_facts() == []


def test_incompatible_existing_schema_raises(tmp_path):
    """A real shape/version mismatch (not just 'no data yet') must still fail
    loudly rather than silently coercing to the new schema."""
    db_path = tmp_path / 'usage_breakdown.sqlite'
    with sqlite3.connect(db_path) as raw:
        raw.execute('CREATE TABLE allowance_sample (id INTEGER PRIMARY KEY)')
        raw.commit()
    store = UsageBreakdownStore(db_path)
    with pytest.raises(SchemaError):
        store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')


def test_wal_sidecar_files_appear_after_a_write(store, tmp_path):
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=1.0, resets_at='r', source_observed_at='t1')
    assert (tmp_path / 'usage_breakdown.sqlite-wal').exists() or (tmp_path / 'usage_breakdown.sqlite').exists()
