"""MC-998 / backlog 4668eafc: a session RE-RUN under the same session_id must
get a 'turn_start' checkpoint from `_log_agent_dispatch_pending`.

Scheduled cadence jobs ('[Nora TikTok watch]'), the '[Backlog run]' continuing
thread and revived dispatches all start their new turn through
`_log_agent_dispatch_pending` with a baseline that already exists (the
partial unique index makes the baseline write a no-op past run 1). Without a
turn_start the aggregate started the new turn at the PREVIOUS completion --
~23h earlier -- so its first sample_tick closed one segment spanning the whole
idle day, and that segment 'crossed' every calibration interval inside it.

These tests drive the real writer against a real store, then read the result
back through the real aggregate (`_eligible_intervals`).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SID = 'rerun-sess'
D1 = datetime(2026, 9, 29, 8, 0, 0, tzinfo=timezone.utc)
D2 = D1 + timedelta(days=1)


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (imports + wires the blueprints)
    from mc.blueprints import agent_routes as ar
    from mc.usage_breakdown_store import UsageBreakdownStore

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    monkeypatch.setattr(ar, '_update_agent_log', lambda project_id, mutate: None)
    clock = {'now': D1}
    monkeypatch.setattr(ar, 'now_iso', lambda: _iso(clock['now']))
    store = UsageBreakdownStore(tmp_path / 'usage_breakdown.sqlite')
    return {'ar': ar, 'store': store, 'clock': clock}


def _session(**extra):
    s = {'session_id': SID, 'project_id': 'proj1', 'provider': 'claude',
         'task': 'cadence job', 'started_at': _iso(D1)}
    s.update(extra)
    return s


def _complete(env_, at, input_total, output_total):
    """What `_log_agent_completion_body` leaves behind for one finished turn:
    the session_fact and the matching 'completion' checkpoint."""
    from mc import usage_breakdown_sampler as sampler
    fact = {'provider': 'claude', 'project_id': 'proj1', 'status': 'completed',
            'started_at': _iso(D1), 'ended_at': _iso(at),
            'input_fresh': input_total, 'input_cache_write': 0, 'input_cache_read': 0,
            'input_processed_total': input_total, 'output_tokens': output_total,
            'output_reasoning': 0, 'token_coverage': 'complete'}
    env_['store'].upsert_session_fact(SID, fact)
    env_['store'].record_session_checkpoint(**sampler.completion_checkpoint_fields(
        fact, session_id=SID, observed_at=_iso(at)))


def _tick(env_, at, input_total, output_total):
    from mc import usage_breakdown_sampler as sampler
    env_['store'].record_session_checkpoint(**sampler.sample_tick_checkpoint_fields(
        SID, provider='claude', observed_at=_iso(at),
        telemetry={'input_fresh': input_total, 'input_cache_write': 0, 'input_cache_read': 0,
                   'input_processed_total': input_total, 'output_tokens': output_total,
                   'output_reasoning': 0, 'token_coverage': 'complete'}))


def _turn_starts(env_, sid=SID):
    return env_['store'].get_session_checkpoints(sid)['turn_starts']


def _sample(at, util):
    return {'raw_utilization': util, 'source_observed_at': _iso(at),
            'resets_at': '2026-10-05T00:00:00Z', 'quality': 'ok'}


def test_rerun_under_same_session_id_keeps_idle_day_out_of_the_new_turn(env):
    from mc.usage_breakdown_aggregate import _eligible_intervals
    ar, store, clock = env['ar'], env['store'], env['clock']

    # Day 1: first run -- dispatch writes the baseline, the turn completes.
    clock['now'] = D1
    ar._log_agent_dispatch_pending(_session())
    _complete(env, D1 + timedelta(minutes=10), 1000, 100)
    assert _turn_starts(env) == []   # a first turn keeps its baseline as its start

    # Day 2: the same session_id is re-dispatched ~23h50m after completing.
    clock['now'] = D2
    ar._log_agent_dispatch_pending(_session())
    starts = _turn_starts(env)
    assert [r['observed_at'] for r in starts] == [_iso(D2)]
    # Carries the session's cumulative counters forward -- the idle span
    # before it deltas to zero, never a fabricated figure.
    assert (starts[0]['input_processed_total'], starts[0]['output_tokens']) == (1000, 100)

    _tick(env, D2 + timedelta(minutes=5), 1500, 150)
    _complete(env, D2 + timedelta(minutes=10), 2000, 200)

    idle_a, idle_b = D1 + timedelta(hours=6), D1 + timedelta(hours=6, minutes=5)
    samples = [_sample(idle_a, 10.0), _sample(idle_b, 12.0),
               _sample(D2, 20.0), _sample(D2 + timedelta(minutes=5), 25.0),
               _sample(D2 + timedelta(minutes=10), 30.0)]
    ivs = _eligible_intervals(
        samples, {SID: store.get_session_checkpoints(SID)}, {SID: store.get_session_fact(SID)},
        provider='claude', window_scope='all')
    by_start = {iv['start']: iv for iv in ivs}

    # Inside the idle gap: the session is simply not there.
    gap = by_start[idle_a]
    assert gap['session_ids'] == set(), gap
    assert gap['coverage_complete'] is True

    # Inside the new run: each tick-bounded segment is contained and measured.
    first, second = by_start[D2], by_start[D2 + timedelta(minutes=5)]
    for iv in (first, second):
        assert iv['session_ids'] == {SID}
        assert iv['coverage_complete'] is True, iv
        assert (iv['input_processed_total'], iv['output_tokens']) == (500, 50)


def test_first_dispatch_writes_no_turn_start(env):
    env['ar']._log_agent_dispatch_pending(_session())
    assert _turn_starts(env) == []


def test_turn_start_carries_no_fabricated_zero_when_fact_is_missing(env):
    """A baseline exists (older row) but no session_fact yet: the turn_start
    is written with unavailable tokens, the same rule the follow-up writer
    follows, never a zero that would read as a measured idle span."""
    from mc import usage_breakdown_sampler as sampler
    env['store'].record_session_checkpoint(**sampler.baseline_checkpoint_fields(
        SID, provider='claude', observed_at=_iso(D1)))
    env['clock']['now'] = D2
    env['ar']._log_agent_dispatch_pending(_session())
    [row] = _turn_starts(env)
    assert row['token_coverage'] == 'unavailable'
    assert row['input_processed_total'] is None


def test_identity_only_backfill_is_not_a_turn(env):
    ar = env['ar']
    ar._log_agent_dispatch_pending(_session())
    env['clock']['now'] = D2
    ar._log_agent_dispatch_pending(_session(claude_session_id='late-csid'), identity_only=True)
    assert _turn_starts(env) == []


def test_caller_that_writes_its_own_turn_start_suppresses_the_duplicate(env):
    """`_advance_delegation_turn` (follow-up / interrupt / queued replay) and
    the second call of a notify_session dispatch pass write_turn_start=False:
    one new turn, one row."""
    ar = env['ar']
    ar._log_agent_dispatch_pending(_session())
    env['clock']['now'] = D2
    ar._log_agent_dispatch_pending(_session(), write_turn_start=False)
    assert _turn_starts(env) == []
    ar._write_usage_breakdown_turn_start_checkpoint(_session())
    assert len(_turn_starts(env)) == 1


def test_advance_delegation_turn_does_not_double_write(env, monkeypatch):
    ar = env['ar']
    ar._log_agent_dispatch_pending(_session())
    env['clock']['now'] = D2
    monkeypatch.setattr(ar, '_allocate_delegation_turn', lambda session: 1)
    ar._advance_delegation_turn(_session())
    assert _turn_starts(env) == []


@pytest.mark.parametrize('flag', ['incognito', 'housekeeping'])
def test_excluded_sessions_write_no_turn_start(env, flag):
    ar = env['ar']
    ar._log_agent_dispatch_pending(_session())           # baseline for SID
    env['clock']['now'] = D2
    ar._log_agent_dispatch_pending(_session(**{flag: True}))
    assert _turn_starts(env) == []


def test_scheduled_run_appended_to_idle_mode_b_session_records_turn_start(monkeypatch):
    """`_scheduled_continue`'s 'appended' branch (a '[Backlog run]' continuing
    thread's next cadence tick, delivered to a live idle Mode B process) never
    passes `_log_agent_dispatch_pending`, and it flips status to 'running'
    itself, so `_note_self_started_turn` can't catch it either. It must call
    the wired turn-started recorder or the run starts at the previous
    completion in calibration."""
    import threading
    import server  # noqa: F401  (wires the blueprints)
    from mc.blueprints import scheduler_routes as sr
    from mc.blueprints import agent_routes as ar

    assert sr._record_usage_breakdown_turn_started is ar._record_usage_breakdown_turn_started

    recorded = []
    monkeypatch.setattr(sr, '_record_usage_breakdown_turn_started',
                        lambda s: recorded.append((s['session_id'], s['status'])))

    class _Mgr:
        lock = threading.Lock()
        def ensure_guardian(self):
            pass

    class _Stdin:
        def write(self, _):
            pass
        def flush(self):
            pass

    class _Proc:
        pid = 4242
        stdin = _Stdin()
        def poll(self):
            return None

    monkeypatch.setattr(sr, 'get_manager', lambda pid: _Mgr())
    monkeypatch.setattr(sr, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *a, **k: None)
    session = {'session_id': 'sched-sess', 'project_id': 'p1', 'status': 'idle', 'mode': 'B',
               'process_alive': True, 'proc': _Proc(), 'log_lines': [],
               'stdin_lock': threading.Lock()}
    monkeypatch.setitem(sr.agent_sessions, 'sched-sess', session)

    assert sr._scheduled_continue({'project_path': ''}, 'p1', 'sched-sess', 'next tick') == 'appended'
    assert recorded == [('sched-sess', 'running')]
