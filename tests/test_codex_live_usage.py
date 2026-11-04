"""The usage bar's Codex number must follow a reset/top-up done in the Codex
app, not only the last rollout line (mc/codex_live_usage.py +
system_routes `_fetch_codex_weekly_usage` / `_fetch_codex_usage_detail`).

Incident 2026-11-03: the bar read 100% (rollout event 2026-11-03T17:00:41Z)
while `account/rateLimits/read` said usedPercent=0, ordinaryUsageAllowed=true.

No test spawns codex: `codex_live_usage._read_snapshot` is replaced (the
suite-wide conftest fixture already defaults it to "no answer").
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import allowance_probe, codex_live_usage  # noqa: E402

_FUTURE = int(time.time()) + 6 * 86400


@pytest.fixture()
def sr():
    import server  # noqa: F401  (registers blueprints)
    from mc.blueprints import system_routes as sr_mod
    for c in (sr_mod._codex_usage_cache, sr_mod._codex_detail_cache):
        c['ts'] = 0.0
        c['data'] = None
    yield sr_mod
    for c in (sr_mod._codex_usage_cache, sr_mod._codex_detail_cache):
        c['ts'] = 0.0
        c['data'] = None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _rollout(tmp_path, monkeypatch, sr, *, used: float, age_s: float):
    """A rollout whose last token_count is `age_s` old, weekly window `used`%."""
    f = tmp_path / 'rollout-1.jsonl'
    f.write_text(json.dumps({
        'timestamp': _iso(time.time() - age_s),
        'type': 'event_msg',
        'payload': {'type': 'token_count', 'info': {}, 'rate_limits': {
            'limit_id': 'codex',
            'primary': {'used_percent': used, 'window_minutes': 10080,
                        'resets_at': _FUTURE},
            'secondary': None, 'plan_type': 'prolite', 'credits': None,
        }},
    }) + '\n', encoding='utf-8')
    monkeypatch.setattr(sr._agent_runtime, '_codex_rollout_files', lambda: [f])


def _snapshot(used: float):
    """What `codex_rate_limits_snapshot` returns, parsed from the app-server."""
    return allowance_probe.parse_rate_limits_result({
        'ordinaryUsageAllowed': True,
        'rateLimits': {
            'primary': {'usedPercent': used, 'windowDurationMins': 10080,
                        'resetsAt': _FUTURE},
            'secondary': None, 'planType': 'prolite',
            'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0'},
            'rateLimitReachedType': None,
        }})


def _counting_reader(monkeypatch, snapshot):
    calls = {'n': 0}

    def _read():
        calls['n'] += 1
        return snapshot() if callable(snapshot) else snapshot

    monkeypatch.setattr(codex_live_usage, '_read_snapshot', _read)
    return calls


# ── the bar ───────────────────────────────────────────────────────────────

def test_stale_rollout_100_probe_0_shows_0(sr, tmp_path, monkeypatch):
    _rollout(tmp_path, monkeypatch, sr, used=100.0, age_s=3600)
    _counting_reader(monkeypatch, _snapshot(0.0))

    weekly = sr._fetch_codex_weekly_usage()

    assert weekly['utilization'] == 0.0
    assert weekly['source'] == 'probe'
    assert weekly['resets_at'].startswith(
        datetime.fromtimestamp(_FUTURE, tz=timezone.utc).isoformat()[:19])


def test_stale_rollout_detail_takes_probe_windows_plan_credits(sr, tmp_path, monkeypatch):
    _rollout(tmp_path, monkeypatch, sr, used=100.0, age_s=3600)
    _counting_reader(monkeypatch, _snapshot(0.0))

    detail = sr._fetch_codex_usage_detail()

    assert detail['weekly']['utilization'] == 0.0
    assert detail['plan_type'] == 'prolite'
    assert detail['credits'] == {'has_credits': False, 'unlimited': False, 'balance': '0'}
    assert detail['source'] == 'probe'
    assert detail['event_at'] is None


def test_probe_failure_falls_back_to_rollout(sr, tmp_path, monkeypatch):
    _rollout(tmp_path, monkeypatch, sr, used=100.0, age_s=3600)
    calls = _counting_reader(monkeypatch, None)

    weekly = sr._fetch_codex_weekly_usage()
    detail = sr._fetch_codex_usage_detail()

    assert calls['n'] == 1                      # failure is cached, not retried
    assert weekly['utilization'] == 100.0 and weekly['source'] == 'rollout'
    assert detail['weekly']['utilization'] == 100.0 and detail['source'] == 'rollout'


def test_fresh_rollout_is_trusted_without_a_probe(sr, tmp_path, monkeypatch):
    _rollout(tmp_path, monkeypatch, sr, used=40.0, age_s=10)
    calls = _counting_reader(monkeypatch, _snapshot(0.0))

    weekly = sr._fetch_codex_weekly_usage()

    assert calls['n'] == 0
    assert weekly['utilization'] == 40.0 and weekly['source'] == 'rollout'


def test_rollout_newer_than_cached_probe_wins(sr, tmp_path, monkeypatch):
    """Probe taken while the rollout was stale; a run then writes a newer
    event. The rollout is newer than the cached probe, so it wins."""
    _rollout(tmp_path, monkeypatch, sr, used=100.0, age_s=3600)
    _counting_reader(monkeypatch, _snapshot(0.0))
    assert sr._fetch_codex_weekly_usage()['utilization'] == 0.0

    time.sleep(0.01)
    _rollout(tmp_path, monkeypatch, sr, used=7.0, age_s=-2)  # event clearly after the probe
    sr._codex_usage_cache['ts'] = 0.0          # drop only the bar's own cache

    weekly = sr._fetch_codex_weekly_usage()

    assert weekly['utilization'] == 7.0 and weekly['source'] == 'rollout'


def test_no_rollout_files_still_uses_the_probe(sr, monkeypatch):
    monkeypatch.setattr(sr._agent_runtime, '_codex_rollout_files', lambda: [])
    _counting_reader(monkeypatch, _snapshot(12.0))

    assert sr._fetch_codex_weekly_usage()['utilization'] == 12.0


# ── cache / single spawn ──────────────────────────────────────────────────

def test_cache_prevents_repeat_spawns_across_polls_and_readers(sr, tmp_path, monkeypatch):
    _rollout(tmp_path, monkeypatch, sr, used=100.0, age_s=3600)
    calls = _counting_reader(monkeypatch, _snapshot(0.0))

    for _ in range(3):
        sr._fetch_codex_weekly_usage()
        sr._fetch_codex_usage_detail()
        # outer 60s caches expire, probe cache is what must hold
        sr._codex_usage_cache['ts'] = 0.0
        sr._codex_detail_cache['ts'] = 0.0

    assert calls['n'] == 1


def test_probe_cache_expires_after_ttl(monkeypatch):
    calls = _counting_reader(monkeypatch, _snapshot(0.0))
    t = time.time()
    codex_live_usage.probe_if_stale(None, now=t)
    codex_live_usage.probe_if_stale(None, now=t + codex_live_usage.PROBE_TTL_S - 1)
    assert calls['n'] == 1
    codex_live_usage.probe_if_stale(None, now=t + codex_live_usage.PROBE_TTL_S + 1)
    assert calls['n'] == 2


def test_request_fresh_forces_a_read_even_with_a_fresh_rollout(monkeypatch):
    calls = _counting_reader(monkeypatch, _snapshot(0.0))
    t = time.time()
    assert codex_live_usage.probe_if_stale(t - 5, now=t) is None
    assert calls['n'] == 0

    codex_live_usage.request_fresh()
    assert codex_live_usage.probe_if_stale(t - 5, now=t) is not None
    assert calls['n'] == 1


def test_refresh_endpoint_forces_a_probe(sr, tmp_path, monkeypatch):
    _rollout(tmp_path, monkeypatch, sr, used=100.0, age_s=10)   # fresh rollout
    calls = _counting_reader(monkeypatch, _snapshot(0.0))
    monkeypatch.setattr(sr, '_fetch_oauth_usage_limits', lambda: None)
    monkeypatch.setattr(sr, '_mc_usage_from_agent_logs', lambda: {
        'today': {}, 'week': {}, 'month': {}, 'all_time': {}, 'last_data_date': ''})
    import server
    server.app.config['TESTING'] = True

    j = server.app.test_client().post('/api/system/usage/refresh').get_json()

    assert calls['n'] == 1
    assert j['provider_weekly_usage']['codex']['utilization'] == 0.0
    assert j['provider_weekly_usage']['codex']['source'] == 'probe'


def test_probe_sourced_detail_is_not_sampled_for_calibration(tmp_path):
    from mc import usage_breakdown_sampler as sampler
    from mc.usage_breakdown_store import UsageBreakdownStore

    store = UsageBreakdownStore(tmp_path / "ub.sqlite")
    detail = {'weekly': {'utilization': 0.0, 'resets_at': '2026-11-09T00:00:00+00:00'},
              'five_hour': None, 'sampled_at': '2026-11-03T20:00:00+00:00',
              'event_at': None, 'source': 'probe'}
    assert sampler.sample_codex(store, detail=detail) == 0


# ── parser / derivation (allowance_probe) ─────────────────────────────────

def test_parse_real_app_server_shape():
    snap = allowance_probe.parse_rate_limits_result({
        'ordinaryUsageAllowed': True,
        'rateLimits': {
            'primary': {'usedPercent': 0, 'windowDurationMins': 10080,
                        'resetsAt': 1794335853},
            'secondary': None, 'planType': 'prolite', 'credits': None,
            'rateLimitReachedType': None}})
    assert snap is not None
    assert snap['ordinary_usage_allowed'] is True
    assert snap['rate_limits']['primary'] == {
        'used_percent': 0, 'window_minutes': 10080, 'resets_at': 1794335853}
    assert snap['rate_limits']['plan_type'] == 'prolite'
    assert allowance_probe.ordinary_usage_allowed_from(snap) is True


def test_parse_non_dict_is_none():
    assert allowance_probe.parse_rate_limits_result(None) is None
    assert allowance_probe.ordinary_usage_allowed_from(None) is None
