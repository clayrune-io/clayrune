"""MC-998 phase 4b: route-level tests for GET /api/system/usage/breakdown
and GET /api/system/usage/windows (mc/blueprints/system_routes.py).

Exercises the real Flask test client + a real UsageBreakdownStore against a
tmp_path sqlite file (via `_DATA_ROOT` monkeypatch) -- not mocked aggregate
output -- so this catches wiring bugs the aggregate/store unit tests can't
(query-param validation, store-to-aggregate argument mapping, JSON shape).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _iso(dt):
    return dt.isoformat()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server
    from mc.blueprints import system_routes as sr_mod
    monkeypatch.setattr(sr_mod, '_DATA_ROOT', tmp_path)
    yield server.app.test_client()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from mc.blueprints import system_routes as sr_mod
    monkeypatch.setattr(sr_mod, '_DATA_ROOT', tmp_path)
    return sr_mod._usage_breakdown_store()


def test_breakdown_defaults_to_no_runs_empty_state_with_no_data(client):
    r = client.get('/api/system/usage/breakdown')
    assert r.status_code == 200
    body = r.get_json()
    assert body['empty_state'] == 'no_runs'
    assert body['provider'] == 'claude'
    assert body['window_kind'] == '5h'
    assert body['rankings']['rows'] == []


def test_breakdown_rejects_invalid_provider(client):
    r = client.get('/api/system/usage/breakdown?provider=bogus')
    assert r.status_code == 400
    assert 'provider' in r.get_json()['error']


def test_breakdown_rejects_invalid_window_kind(client):
    r = client.get('/api/system/usage/breakdown?window_kind=1h')
    assert r.status_code == 400
    assert 'window_kind' in r.get_json()['error']


def test_breakdown_rejects_invalid_window_scope(client):
    r = client.get('/api/system/usage/breakdown?window_scope=bogus')
    assert r.status_code == 400
    assert 'window_scope' in r.get_json()['error']


def test_breakdown_codex_forces_window_scope_to_all(client, store):
    now = datetime.now(timezone.utc)
    store.record_allowance_sample(
        provider='codex', window_kind='5h', window_scope='all',
        raw_utilization=10.0, resets_at=_iso(now + timedelta(hours=4)),
        source_observed_at=_iso(now))
    r = client.get('/api/system/usage/breakdown?provider=codex&window_scope=opus')
    assert r.status_code == 200
    assert r.get_json()['window_scope'] == 'all'


def test_breakdown_reports_real_totals_from_a_populated_store(client, store):
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=1)
    resets_at = _iso(now + timedelta(hours=4))
    for i, util in enumerate((10.0, 20.0, 30.0)):
        store.record_allowance_sample(
            provider='claude', window_kind='5h', window_scope='all',
            raw_utilization=util, resets_at=resets_at,
            source_observed_at=_iso(window_start + timedelta(minutes=i)))
    store.upsert_session_fact('sess-1', {
        'provider': 'claude', 'project_id': 'proj-a', 'character': 'builder',
        'trigger_type': 'chat', 'requested_model': 'sonnet', 'observed_model': 'sonnet',
        'status': 'idle', 'started_at': _iso(window_start), 'ended_at': _iso(now),
        'input_fresh': 1000, 'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': 1000, 'output_tokens': 500,
        'token_source': 'transcript', 'token_coverage': 'full', 'included': True,
    })

    r = client.get(
        f'/api/system/usage/breakdown?range_start={_iso(window_start)}&range_end={_iso(now)}'
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body['totals']['session_count'] == 1
    assert body['totals']['tokens']['input_fresh'] == 1000
    assert body['totals']['tokens']['output_tokens'] == 500
    assert body['rankings']['rows'], 'expected at least one ranking row for proj-a'
    assert body['rankings']['rows'][0]['label'] == 'proj-a'


def test_breakdown_dimension_and_sort_params_pass_through(client):
    r = client.get('/api/system/usage/breakdown?dimension=character&sort=output')
    assert r.status_code == 200
    body = r.get_json()
    assert body['dimension'] == 'character'
    assert body['sort_by'] == 'output'


def test_windows_empty_store_returns_no_windows(client):
    r = client.get('/api/system/usage/windows')
    assert r.status_code == 200
    body = r.get_json()
    assert body == {'windows': [], 'coverage_begins': None}


def test_windows_rejects_invalid_provider(client):
    r = client.get('/api/system/usage/windows?provider=bogus')
    assert r.status_code == 400


def test_windows_groups_samples_by_resets_at(client, store):
    now = datetime.now(timezone.utc)
    reset_a = _iso(now + timedelta(hours=4))
    reset_b = _iso(now + timedelta(hours=9))
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=10.0, resets_at=reset_a,
        source_observed_at=_iso(now - timedelta(minutes=10)))
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=20.0, resets_at=reset_a,
        source_observed_at=_iso(now - timedelta(minutes=5)))
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=5.0, resets_at=reset_b,
        source_observed_at=_iso(now))

    r = client.get('/api/system/usage/windows')
    assert r.status_code == 200
    body = r.get_json()
    assert len(body['windows']) == 2
    assert body['windows'][0]['resets_at'] == reset_a
    assert body['windows'][1]['resets_at'] == reset_b


def test_windows_and_default_range_tolerate_resets_at_jitter(client, store):
    """MC-998 follow-up: the Anthropic endpoint jitters resets_at by
    sub-seconds on every poll. Three jittered readings of ONE window must be
    one picker window, and the default (current-window) breakdown range must
    start at the first of them, not at the latest sample."""
    now = datetime.now(timezone.utc)
    base = (now + timedelta(hours=4)).replace(microsecond=0)
    jittered = [base + timedelta(microseconds=444543),
                base - timedelta(microseconds=432330),
                base + timedelta(microseconds=5273)]
    observed = [now - timedelta(minutes=10), now - timedelta(minutes=5), now - timedelta(minutes=1)]
    for util, r_at, obs in zip((10.0, 12.0, 15.0), jittered, observed):
        store.record_allowance_sample(
            provider='claude', window_kind='5h', window_scope='all',
            raw_utilization=util, resets_at=_iso(r_at), source_observed_at=_iso(obs))

    windows = client.get('/api/system/usage/windows').get_json()['windows']
    assert len(windows) == 1
    assert windows[0]['range_start'] == _iso(observed[0])
    assert windows[0]['range_end'] == _iso(observed[2])

    body = client.get('/api/system/usage/breakdown').get_json()
    assert body['range_start'] == _iso(observed[0])
    assert body['bar_change']['status'] == 'ok'
    assert body['bar_change']['delta_pp'] == 5.0


def test_windows_and_breakdown_use_source_time_not_receipt_time_for_bounds(client, store):
    """P2-8 (finding 8): the picker's range must be identified and bounded
    by source_observed_at, never server_received_at -- a sample observed
    hours ago but only just received (network/disk delay, or a backfill)
    must still land at its own observed time, and re-querying breakdown
    with the picker's own returned bounds must retain both readings."""
    now = datetime.now(timezone.utc)
    observed_first = now - timedelta(hours=2)
    observed_last = observed_first + timedelta(minutes=5)
    resets_at = _iso(now + timedelta(hours=4))
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=10.0, resets_at=resets_at, source_observed_at=_iso(observed_first))
    store.record_allowance_sample(
        provider='claude', window_kind='5h', window_scope='all',
        raw_utilization=15.0, resets_at=resets_at, source_observed_at=_iso(observed_last))

    windows_body = client.get('/api/system/usage/windows').get_json()
    assert len(windows_body['windows']) == 1
    w = windows_body['windows'][0]
    # server_received_at is "now" (insertion time) for both rows -- if the
    # picker were still keyed on it, these would equal `now`, not the
    # samples' own (much earlier) observed times.
    assert w['range_start'] == _iso(observed_first)
    assert w['range_end'] == _iso(observed_last)

    r = client.get(
        f"/api/system/usage/breakdown?range_start={quote(w['range_start'])}"
        f"&range_end={quote(w['range_end'])}"
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body['bar_change']['status'] == 'ok'
    assert body['bar_change']['delta_pp'] == 5.0


def test_windows_codex_forces_window_scope_to_all(client, store):
    now = datetime.now(timezone.utc)
    store.record_allowance_sample(
        provider='codex', window_kind='5h', window_scope='all',
        raw_utilization=10.0, resets_at=_iso(now + timedelta(hours=4)),
        source_observed_at=_iso(now))
    r = client.get('/api/system/usage/windows?provider=codex&window_scope=opus')
    assert r.status_code == 200
    assert len(r.get_json()['windows']) == 1
