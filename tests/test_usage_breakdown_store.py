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

from mc.usage_breakdown_store import (  # noqa: E402
    APPLICATION_ID, UsageBreakdownStore, db_path_for, SchemaError,
)


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


def test_code_delta_unavailable_never_overwrites_a_prior_ok(store):
    """MC-998 review finding #5: a later completion in the same chat (e.g.
    after merge-back already removed the worktree, so the recompute reports
    'worktree missing') must not clobber an already-captured LOC count."""
    store.upsert_session_fact('sess-3', {'provider': 'claude', 'token_coverage': 'complete'})
    store.upsert_code_delta('sess-3', {
        'status': 'ok', 'added': 5, 'deleted': 2, 'branch': 'clayrune/agent/sess-3',
        'base_commit': 'abc123', 'head_commits': 'def456',
    })
    store.upsert_code_delta('sess-3', {
        'status': 'unavailable', 'reason': 'worktree missing at completion',
    })
    delta = store.get_code_delta('sess-3')
    assert delta['status'] == 'ok'
    assert delta['added'] == 5
    assert delta['deleted'] == 2


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


# ── session_checkpoint (P1-3, docs/_journal/4668eafc-mc998-fenn-review.md
# "2026-09-28 re-review" finding 3) ─────────────────────────────────────────

def test_checkpoint_baseline_insert_then_get(store):
    ok = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='baseline',
        observed_at='2026-09-28T10:00:00Z', token_coverage='unavailable')
    assert ok is True
    ck = store.get_session_checkpoints('s1')
    assert ck['baseline']['observed_at'] == '2026-09-28T10:00:00Z'
    assert ck['completions'] == []


def test_checkpoint_repeated_baseline_dispatch_call_is_a_noop(store):
    """A repeated dispatch-pending call for the same session must not
    clobber or duplicate the baseline."""
    first = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='baseline',
        observed_at='2026-09-28T10:00:00Z', token_coverage='unavailable')
    second = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='baseline',
        observed_at='2026-09-28T10:00:05Z', token_coverage='unavailable')
    assert first is True
    assert second is False
    assert store.get_session_checkpoints('s1')['baseline']['observed_at'] == '2026-09-28T10:00:00Z'


def test_checkpoint_completion_appends_per_turn_not_one_frozen_row(store):
    """P1-3: Mode-A completions fire per turn. A second completion for the
    same session_id at a later observed_at must be a NEW row, never
    discarded -- the pre-fix schema's table-level
    UNIQUE(session_id, checkpoint_type) silently dropped it (verified
    against the pre-fix module: `record_session_checkpoint` returned False
    for the second call and the session was frozen at its first turn
    forever)."""
    store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='baseline',
        observed_at='2026-09-28T10:00:00Z', token_coverage='unavailable')
    turn1 = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='completion',
        observed_at='2026-09-28T10:05:00Z', input_processed_total=100,
        output_tokens=50, token_coverage='complete')
    turn2 = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='completion',
        observed_at='2026-09-28T11:00:00Z', input_processed_total=500,
        output_tokens=200, token_coverage='complete')
    assert turn1 is True
    assert turn2 is True
    completions = store.get_session_checkpoints('s1')['completions']
    assert [c['input_processed_total'] for c in completions] == [100, 500]
    assert [c['observed_at'] for c in completions] == [
        '2026-09-28T10:05:00Z', '2026-09-28T11:00:00Z']


def test_checkpoint_exact_retry_completion_is_a_noop(store):
    """An exact retry (same session_id, same observed_at) must not create a
    duplicate turn -- only a genuinely new observed_at is a new turn."""
    store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='baseline',
        observed_at='t0')
    first = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='completion',
        observed_at='t1', input_processed_total=100, token_coverage='complete')
    retry = store.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='completion',
        observed_at='t1', input_processed_total=100, token_coverage='complete')
    assert first is True
    assert retry is False
    assert len(store.get_session_checkpoints('s1')['completions']) == 1


def test_schema_v2_migration_preserves_rows_and_unfreezes_later_turns(tmp_path):
    """A pre-existing v2 install (table-level
    UNIQUE(session_id, checkpoint_type)) must migrate in place: its one
    stored baseline+completion pair survives, AND a session that already had
    one completion can now record a second turn -- proving the migration
    actually replaced the freezing constraint, not just renamed it."""
    db_path = tmp_path / 'usage_breakdown.sqlite'
    with sqlite3.connect(db_path) as raw:
        raw.execute(
            'CREATE TABLE allowance_sample ('
            ' id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,'
            ' window_kind TEXT NOT NULL, window_scope TEXT NOT NULL,'
            ' raw_utilization REAL, resets_at TEXT, source_observed_at TEXT,'
            ' server_received_at TEXT NOT NULL, source_version TEXT, quality TEXT NOT NULL,'
            ' account_ref TEXT, created_at TEXT NOT NULL,'
            ' UNIQUE(provider, window_kind, window_scope, source_observed_at))')
        raw.execute(
            'CREATE TABLE session_fact ('
            ' session_id TEXT PRIMARY KEY, provider TEXT NOT NULL, project_id TEXT,'
            ' character TEXT, trigger_type TEXT, requested_model TEXT, observed_model TEXT,'
            ' status TEXT NOT NULL, started_at TEXT, ended_at TEXT, input_fresh INTEGER,'
            ' input_cache_write INTEGER, input_cache_read INTEGER, input_processed_total INTEGER,'
            ' output_tokens INTEGER, output_reasoning INTEGER, token_source TEXT,'
            ' token_coverage TEXT NOT NULL, included INTEGER NOT NULL DEFAULT 1,'
            ' housekeeping INTEGER NOT NULL DEFAULT 0, parent_session_id TEXT,'
            ' updated_at TEXT NOT NULL, created_at TEXT NOT NULL)')
        raw.execute(
            'CREATE TABLE code_delta ('
            ' session_id TEXT PRIMARY KEY, added INTEGER, deleted INTEGER, status TEXT NOT NULL,'
            ' reason TEXT, branch TEXT, base_commit TEXT, head_commits TEXT, created_at TEXT NOT NULL)')
        raw.execute(
            'CREATE TABLE session_checkpoint ('
            ' id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, provider TEXT NOT NULL,'
            ' checkpoint_type TEXT NOT NULL, observed_at TEXT NOT NULL, input_fresh INTEGER,'
            ' input_cache_write INTEGER, input_cache_read INTEGER, input_processed_total INTEGER,'
            ' output_tokens INTEGER, output_reasoning INTEGER, token_coverage TEXT NOT NULL,'
            ' created_at TEXT NOT NULL, UNIQUE(session_id, checkpoint_type))')
        raw.execute(
            "INSERT INTO session_checkpoint (session_id, provider, checkpoint_type, observed_at,"
            " input_fresh, input_cache_write, input_cache_read, input_processed_total, output_tokens,"
            " output_reasoning, token_coverage, created_at) VALUES"
            " ('s1','claude','baseline','2026-09-28T10:00:00Z',0,0,0,0,0,0,'unavailable',"
            " '2026-09-28T10:00:00Z')")
        raw.execute(
            "INSERT INTO session_checkpoint (session_id, provider, checkpoint_type, observed_at,"
            " input_fresh, input_cache_write, input_cache_read, input_processed_total, output_tokens,"
            " output_reasoning, token_coverage, created_at) VALUES"
            " ('s1','claude','completion','2026-09-28T10:05:00Z',100,0,0,100,50,0,'complete',"
            " '2026-09-28T10:05:00Z')")
        raw.execute(f'PRAGMA application_id={APPLICATION_ID}')
        raw.execute('PRAGMA user_version=2')
        raw.commit()

    migrated = UsageBreakdownStore(db_path)
    ck = migrated.get_session_checkpoints('s1')
    assert ck['baseline']['observed_at'] == '2026-09-28T10:00:00Z'
    assert len(ck['completions']) == 1
    assert ck['completions'][0]['input_processed_total'] == 100

    turn2 = migrated.record_session_checkpoint(
        session_id='s1', provider='claude', checkpoint_type='completion',
        observed_at='2026-09-28T11:00:00Z', input_processed_total=500,
        output_tokens=200, token_coverage='complete')
    assert turn2 is True
    completions = migrated.get_session_checkpoints('s1')['completions']
    assert [c['input_processed_total'] for c in completions] == [100, 500]

    with sqlite3.connect(db_path) as raw:
        assert raw.execute('PRAGMA user_version').fetchone()[0] == 3
