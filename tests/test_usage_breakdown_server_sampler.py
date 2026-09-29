"""MC-998 phase 4: server-side allowance-sampler wiring in
mc/blueprints/system_routes.py — `usage_breakdown_sample_once`,
`_usage_breakdown_sample_loop`'s cadence gate, and `_usage_breakdown_prune_loop`.

The pure sampling math (mc/usage_breakdown_sampler.py) and the store
(mc/usage_breakdown_store.py) already have their own dedicated unit tests;
this file only covers the thin server-side glue: does it call the two
existing 60s vendor caches, hand their result to the sampler, and write to
the store at the wired `_DATA_ROOT` -- redirected to `tmp_path` here so a
test run never touches the real `data/usage_breakdown.sqlite`.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def sr(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers blueprints, runs wire())
    from mc.blueprints import system_routes as sr_mod
    monkeypatch.setattr(sr_mod, '_DATA_ROOT', tmp_path)
    sr_mod._oauth_usage_cache['ts'] = 0.0
    sr_mod._oauth_usage_cache['data'] = None
    sr_mod._codex_detail_cache['ts'] = 0.0
    sr_mod._codex_detail_cache['data'] = None
    yield sr_mod
    sr_mod._oauth_usage_cache['ts'] = 0.0
    sr_mod._oauth_usage_cache['data'] = None
    sr_mod._codex_detail_cache['ts'] = 0.0
    sr_mod._codex_detail_cache['data'] = None


def test_sample_once_writes_through_to_the_wired_data_root(sr, monkeypatch):
    monkeypatch.setattr(sr, '_fetch_oauth_usage_limits',
                         lambda: {'five_hour': {'utilization': 12.5, 'resets_at': '2026-10-01T00:00:00Z'}})
    sr._oauth_usage_cache['ts'] = 1000.0
    monkeypatch.setattr(sr, '_fetch_codex_usage_detail', lambda: None)

    counts = sr.usage_breakdown_sample_once()

    assert counts == {'claude': 1, 'codex': 0}
    store = sr._usage_breakdown_store()
    rows = store.list_allowance_samples(provider='claude', window_kind='5h', window_scope='all')
    assert len(rows) == 1
    assert rows[0]['raw_utilization'] == 12.5
    assert store.db_path == (Path(sr._DATA_ROOT) / 'data' / 'usage_breakdown.sqlite')


def test_sample_once_is_best_effort_per_source(sr, monkeypatch):
    """A Claude fetch failure must not stop the Codex sample (and vice
    versa) -- each source is wrapped in its own try/except."""
    def _boom():
        raise RuntimeError('network down')
    monkeypatch.setattr(sr, '_fetch_oauth_usage_limits', _boom)
    monkeypatch.setattr(sr, '_fetch_codex_usage_detail',
                         lambda: {'event_at': '2026-09-28T12:00:00Z',
                                  'weekly': {'utilization': 5.0, 'resets_at': '2026-10-05T00:00:00Z'}})

    counts = sr.usage_breakdown_sample_once()

    assert counts == {'claude': 0, 'codex': 1}
    store = sr._usage_breakdown_store()
    assert len(store.list_allowance_samples(provider='codex', window_kind='7d', window_scope='all')) == 1


def test_cache_hit_across_two_calls_inserts_nothing_new(sr, monkeypatch):
    """Same fetched_at_epoch on both calls (a real 60s cache hit) must dedupe
    to a single stored row, per the loop's whole reason for existing."""
    monkeypatch.setattr(sr, '_fetch_oauth_usage_limits',
                         lambda: {'five_hour': {'utilization': 40.0, 'resets_at': '2026-10-01T00:00:00Z'}})
    sr._oauth_usage_cache['ts'] = 2000.0
    monkeypatch.setattr(sr, '_fetch_codex_usage_detail', lambda: None)

    first = sr.usage_breakdown_sample_once()
    second = sr.usage_breakdown_sample_once()

    assert first == {'claude': 1, 'codex': 0}
    assert second == {'claude': 0, 'codex': 0}


def test_any_session_active_true_only_when_a_session_is_running(sr):
    from mc.state import agent_sessions
    agent_sessions.clear()
    try:
        assert sr._usage_breakdown_any_session_active() is False
        agent_sessions['s1'] = {'status': 'idle'}
        assert sr._usage_breakdown_any_session_active() is False
        agent_sessions['s2'] = {'status': 'running'}
        assert sr._usage_breakdown_any_session_active() is True
    finally:
        agent_sessions.clear()


def test_prune_loop_interval_is_daily():
    from mc.blueprints import system_routes as sr_mod
    assert sr_mod._USAGE_BREAKDOWN_PRUNE_INTERVAL_S == 24 * 3600


def test_sample_once_closes_a_store_session_whose_mc_session_is_gone(sr, monkeypatch):
    """Round 4: the sampler tick is the reconcile point. A session still
    open in the store but absent from agent_sessions (crashed, or the server
    restarted and nothing re-adopted it) is closed 'ended_unknown'; one
    agent_sessions still has running is left alone."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(sr, '_fetch_oauth_usage_limits', lambda: None)
    monkeypatch.setattr(sr, '_fetch_codex_usage_detail', lambda: None)
    store = sr._usage_breakdown_store()
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    for sid in ('gone-session', 'live-session'):
        store.record_session_checkpoint(session_id=sid, provider='claude',
                                        checkpoint_type='baseline', observed_at=old)
    monkeypatch.setattr(sr, 'agent_sessions', {'live-session': {'status': 'running'}})

    sr.usage_breakdown_sample_once()

    assert store.get_session_fact('gone-session')['status'] == 'ended_unknown'
    assert store.get_session_fact('live-session') is None
    assert [r['session_id'] for r in store.list_open_sessions()] == ['live-session']
