"""MC-998 round 4: `reconcile_dead_sessions` closes store sessions whose MC
session is no longer live.

A session that crashed mid-turn (or was running when the server restarted,
with nothing re-adopting it) used to stay open -- fact status 'running', or a
first-turn baseline with no fact -- until the 90-day prune. An open span has
no end, so it overlapped EVERY later calibration interval and window: one
crash blocked the calibration gate and marked every window incomplete for up
to 90 days (docs/_journal/4668eafc-mc998-fenn-review.md, round 4).

These tests go through the real store and the real aggregate, the same path
`/api/usage-breakdown` reads.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_aggregate import compute_calibration, filter_facts_in_range  # noqa: E402
from mc.usage_breakdown_sampler import (  # noqa: E402
    RECONCILE_FALLBACK_MAX_AGE_SECONDS, reconcile_dead_sessions,
)
from mc.usage_breakdown_store import UsageBreakdownStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return UsageBreakdownStore(tmp_path / 'usage_breakdown.sqlite')


# Wall-clock anchor: mark_session_running stamps updated_at with the real
# clock, so the fixture's times hang off real now, not a fixed date.
# Re-anchored per test: an import-time anchor drifts behind the real clock
# while a full suite runs, and the 120s grace / 24h fallback checks flip.
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_now():
    global NOW
    NOW = datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _read(store):
    """(facts list, facts_by_session, checkpoints) shaped exactly as
    system_routes' breakdown route builds them."""
    facts = store.list_session_facts()
    checkpoints: dict[str, dict] = {}
    for row in store.list_session_checkpoints():
        entry = checkpoints.setdefault(row['session_id'], {'baseline': None, 'completions': []})
        if row['checkpoint_type'] == 'baseline':
            entry['baseline'] = row
        else:
            entry['completions'].append(row)
    return facts, {f['session_id']: f for f in facts}, checkpoints


def _turn(store, sid, *, start, end, tokens):
    """One fully measured single-turn session: baseline at `start`,
    completion at `end`, completed fact."""
    store.record_session_checkpoint(session_id=sid, provider='claude', checkpoint_type='baseline',
                                    observed_at=_iso(start), input_processed_total=0,
                                    output_tokens=0, token_coverage='complete')
    store.record_session_checkpoint(session_id=sid, provider='claude', checkpoint_type='completion',
                                    observed_at=_iso(end), input_processed_total=tokens,
                                    output_tokens=10, token_coverage='complete')
    store.upsert_session_fact(sid, {
        'provider': 'claude', 'project_id': 'proj-a', 'status': 'completed',
        'started_at': _iso(start), 'ended_at': _iso(end),
        'input_fresh': tokens, 'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': tokens, 'output_tokens': 10, 'token_coverage': 'complete',
    })


def _crashed_resume(store, sid='crashed'):
    """A session that completed one turn 5h ago, was resumed (the dispatch
    writer re-marks the fact 'running', updated_at = real now) and then
    died mid-turn: no completion ever arrives."""
    _turn(store, sid, start=NOW - timedelta(hours=5), end=NOW - timedelta(hours=4, minutes=50),
          tokens=100)
    assert store.mark_session_running(sid)


def _later_calibration(offset):
    """Five fully measured single-session intervals starting `offset` after
    NOW, 20 minutes apart, each exactly one sample pair."""
    samples = []
    for i in range(5):
        t0 = NOW + offset + timedelta(minutes=20 * i)
        t1 = t0 + timedelta(minutes=2)
        samples += [
            {'raw_utilization': float(i * 10), 'source_observed_at': _iso(t0),
             'resets_at': _iso(NOW + timedelta(days=5)), 'quality': 'ok'},
            {'raw_utilization': float(i * 10 + 5), 'source_observed_at': _iso(t1),
             'resets_at': _iso(NOW + timedelta(days=5)), 'quality': 'ok'},
        ]
    return samples


def _add_later_sessions(store, offset):
    for i in range(5):
        t0 = NOW + offset + timedelta(minutes=20 * i)
        _turn(store, f'later-{i}', start=t0, end=t0 + timedelta(minutes=2), tokens=50)


def _incomplete(store, start, end):
    facts, _, checkpoints = _read(store)
    return filter_facts_in_range(facts, checkpoints, provider='claude',
                                 range_start=_iso(start), range_end=_iso(end))[1]


def test_crashed_session_no_longer_blocks_calibration_once_closed(store):
    offset = timedelta(hours=1)
    _crashed_resume(store)
    _add_later_sessions(store, offset)
    samples = _later_calibration(offset)

    _, by_sid, checkpoints = _read(store)
    blocked = compute_calibration(samples, checkpoints, by_sid, provider='claude', window_scope='all')
    assert blocked['status'] == 'insufficient_samples'  # the open crash overlaps all five

    closed = reconcile_dead_sessions(store, live_session_ids=set(),
                                     now=NOW + timedelta(minutes=10))
    assert closed == ['crashed']
    fact = store.get_session_fact('crashed')
    assert fact['status'] == 'ended_unknown'
    assert fact['ended_at'] == _iso(NOW + timedelta(minutes=10))
    assert fact['input_processed_total'] == 100  # counters kept

    _, by_sid, checkpoints = _read(store)
    cal = compute_calibration(samples, checkpoints, by_sid, provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5


def test_crashed_session_stays_incomplete_only_for_windows_it_overlapped(store):
    _crashed_resume(store)
    reconcile_dead_sessions(store, live_session_ids=set(), now=NOW + timedelta(minutes=10))

    # The crashed turn ran from its last completion to when it was seen gone.
    assert _incomplete(store, NOW - timedelta(hours=1), NOW) == 1
    # Nothing after the close.
    assert _incomplete(store, NOW + timedelta(minutes=30), NOW + timedelta(hours=1)) == 0
    # The completed first turn is still measured in its own window.
    facts, _, checkpoints = _read(store)
    rows, inc = filter_facts_in_range(facts, checkpoints, provider='claude',
                                      range_start=_iso(NOW - timedelta(hours=6)),
                                      range_end=_iso(NOW - timedelta(hours=4, minutes=50)))
    assert inc == 0
    assert [r['input_processed_total'] for r in rows] == [100]


def test_live_session_is_left_open(store):
    _crashed_resume(store, sid='working')
    closed = reconcile_dead_sessions(store, live_session_ids={'working'},
                                     now=NOW + timedelta(hours=2))
    assert closed == []
    assert store.get_session_fact('working')['status'] == 'running'


def test_server_restart_closes_unadopted_session_at_process_start(store):
    """After a restart agent_sessions has nothing running and nothing
    re-adopts the old session. It died no later than the previous server
    process, so it closes at this process's start, not at the first tick --
    a server left off for hours must not mark those hours incomplete."""
    _crashed_resume(store)
    booted = NOW + timedelta(hours=1)
    closed = reconcile_dead_sessions(store, live_session_ids=set(),
                                     now=NOW + timedelta(hours=3), process_started_at=_iso(booted))
    assert closed == ['crashed']
    assert store.get_session_fact('crashed')['ended_at'] == _iso(booted)
    assert _incomplete(store, NOW + timedelta(hours=2), NOW + timedelta(hours=3)) == 0


def test_first_turn_crash_gets_an_ended_unknown_fact(store):
    """A first turn has a baseline and no fact yet. Closing it writes a
    counter-less 'ended_unknown' fact, so the baseline no longer reads as a
    live session forever."""
    base = NOW - timedelta(hours=1)
    store.record_session_checkpoint(session_id='first', provider='claude',
                                    checkpoint_type='baseline', observed_at=_iso(base),
                                    input_processed_total=0, output_tokens=0,
                                    token_coverage='complete')
    assert _incomplete(store, NOW + timedelta(hours=1), NOW + timedelta(hours=2)) == 1

    closed = reconcile_dead_sessions(store, live_session_ids=set(), now=NOW)
    assert closed == ['first']
    fact = store.get_session_fact('first')
    assert (fact['status'], fact['started_at'], fact['ended_at']) == ('ended_unknown', _iso(base), _iso(NOW))
    assert fact['input_processed_total'] is None
    assert _incomplete(store, base + timedelta(minutes=10), base + timedelta(minutes=20)) == 1
    assert _incomplete(store, NOW + timedelta(hours=1), NOW + timedelta(hours=2)) == 0
    assert store.list_open_sessions() == []


def test_just_dispatched_session_inside_the_grace_period_is_left_open(store):
    """agent_routes writes the baseline before the session is registered
    'running'; a tick landing in between must not close it."""
    store.record_session_checkpoint(session_id='fresh', provider='claude',
                                    checkpoint_type='baseline',
                                    observed_at=_iso(NOW - timedelta(seconds=30)),
                                    input_processed_total=0, output_tokens=0,
                                    token_coverage='complete')
    assert reconcile_dead_sessions(store, live_session_ids=set(), now=NOW) == []
    assert store.get_session_fact('fresh') is None


def test_registry_unavailable_falls_back_to_max_age(store):
    """live_session_ids=None: the registry could not be read. Only a session
    idle past RECONCILE_FALLBACK_MAX_AGE_SECONDS is closed."""
    _crashed_resume(store)
    assert reconcile_dead_sessions(store, live_session_ids=None,
                                   now=NOW + timedelta(hours=2)) == []
    later = NOW + timedelta(seconds=RECONCILE_FALLBACK_MAX_AGE_SECONDS + 60)
    assert reconcile_dead_sessions(store, live_session_ids=None, now=later) == ['crashed']


def test_completion_that_lands_before_the_close_wins(store):
    _crashed_resume(store)
    store.upsert_session_fact('crashed', {'status': 'completed', 'ended_at': _iso(NOW)})
    assert store.close_session_ended_unknown('crashed', provider='claude', started_at=None,
                                             ended_at=_iso(NOW + timedelta(minutes=5))) is False
    assert store.get_session_fact('crashed')['status'] == 'completed'


def test_resuming_a_closed_session_reopens_it(store):
    """A later turn of a session closed as ended_unknown goes through the
    same dispatch writer, which re-marks it running."""
    _crashed_resume(store)
    reconcile_dead_sessions(store, live_session_ids=set(), now=NOW + timedelta(minutes=10))
    assert store.mark_session_running('crashed')
    assert [r['session_id'] for r in store.list_open_sessions()] == ['crashed']
