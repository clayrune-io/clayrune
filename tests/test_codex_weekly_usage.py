"""Unit tests for `_fetch_codex_weekly_usage` (mc/blueprints/system_routes.py,
added MC-966 for the bottom usage-bar strip).

Real event shape, captured live 2026-09-26 from a `prolite`-plan rollout under
~/.codex/sessions:
    {"type":"event_msg","payload":{"type":"token_count","info":{...},
      "rate_limits":{"primary":{"used_percent":0.0,"window_minutes":10080,
        "resets_at":1791055226},"secondary":null, ...}}}
`rate_limits` sits directly under `payload` (sibling of `info`, not nested
inside it) — the function's `info = rec.get('payload')` variable is really
"the payload dict", read that way on purpose.

Determinism: monkeypatches `system_routes._agent_runtime._codex_rollout_files`
to point at a tmp rollout file — no dependency on a real ~/.codex install, and
the module-level TTL cache is reset before/after each test so runs can't leak.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def sr():
    import server  # noqa: F401  (registers blueprints)
    from mc.blueprints import system_routes as sr_mod
    sr_mod._codex_usage_cache['ts'] = 0.0
    sr_mod._codex_usage_cache['data'] = None
    yield sr_mod
    sr_mod._codex_usage_cache['ts'] = 0.0
    sr_mod._codex_usage_cache['data'] = None


def _rollout_line(primary=None, secondary=None):
    return json.dumps({
        "timestamp": "2026-09-26T20:48:29.393Z",
        "ordinal": 16,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 37820}},
            "rate_limits": {
                "limit_id": "codex",
                "primary": primary,
                "secondary": secondary,
                "plan_type": "prolite",
            },
        },
    })


def test_weekly_window_as_primary(sr, tmp_path, monkeypatch):
    """`prolite`-plan shape: the weekly (10080min) window is `primary`."""
    f = tmp_path / "rollout-1.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 41.0, "window_minutes": 10080, "resets_at": 1791055226}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_weekly_usage()

    assert result is not None
    assert result["utilization"] == 41.0
    assert result["resets_at"] == "2026-10-03T19:20:26+00:00"


def test_weekly_window_as_secondary(sr, tmp_path, monkeypatch):
    """`plus`-plan shape: 5h window is `primary`, weekly (10080min) is `secondary`."""
    f = tmp_path / "rollout-2.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 12.0, "window_minutes": 300, "resets_at": 1791000000},
            secondary={"used_percent": 77.0, "window_minutes": 10080, "resets_at": 1791999999},
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_weekly_usage()

    assert result is not None
    assert result["utilization"] == 77.0


def test_no_rollout_files_returns_none(sr, monkeypatch):
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [])
    assert sr._fetch_codex_weekly_usage() is None


def test_secondary_null_is_skipped(sr, tmp_path, monkeypatch):
    """`secondary: null` (real prolite shape) must not raise or be treated as a match."""
    f = tmp_path / "rollout-3.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 5.0, "window_minutes": 10080, "resets_at": 1791000000}, secondary=None) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_weekly_usage()

    assert result is not None
    assert result["utilization"] == 5.0


def test_no_weekly_window_present_returns_none(sr, tmp_path, monkeypatch):
    """Both slots present but neither is the 10080-minute weekly window."""
    f = tmp_path / "rollout-4.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 12.0, "window_minutes": 300, "resets_at": 1791000000},
            secondary={"used_percent": 3.0, "window_minutes": 60, "resets_at": 1791000000},
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    assert sr._fetch_codex_weekly_usage() is None


def test_uses_latest_mtime_file(sr, tmp_path, monkeypatch):
    """Account-wide limit: only the most-recently-modified rollout is read."""
    import os
    import time

    old = tmp_path / "rollout-old.jsonl"
    old.write_text(
        _rollout_line(primary={"used_percent": 10.0, "window_minutes": 10080, "resets_at": 1791000000}) + "\n",
        encoding="utf-8",
    )
    new = tmp_path / "rollout-new.jsonl"
    new.write_text(
        _rollout_line(primary={"used_percent": 90.0, "window_minutes": 10080, "resets_at": 1791000000}) + "\n",
        encoding="utf-8",
    )
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [old, new])

    result = sr._fetch_codex_weekly_usage()

    assert result is not None
    assert result["utilization"] == 90.0


def test_cache_ttl_avoids_rereading_file(sr, tmp_path, monkeypatch):
    f = tmp_path / "rollout-5.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 25.0, "window_minutes": 10080, "resets_at": 1791000000}) + "\n",
        encoding="utf-8",
    )
    calls = {"n": 0}

    def _files():
        calls["n"] += 1
        return [f]

    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", _files)

    first = sr._fetch_codex_weekly_usage()
    second = sr._fetch_codex_weekly_usage()

    assert first == second
    assert calls["n"] == 1  # second call served from the 60s TTL cache


def test_malformed_line_does_not_raise(sr, tmp_path, monkeypatch):
    f = tmp_path / "rollout-6.jsonl"
    f.write_text('not json but mentions "token_count" and "rate_limits"\n', encoding="utf-8")
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    assert sr._fetch_codex_weekly_usage() is None


# ── Staleness (Ron's review of df34611) ──────────────────────────────────
# The freshest recorded event's own `resets_at` may already be in the past —
# the CLI simply hasn't run since the weekly window it describes reset. The
# recorded % then belongs to a window that no longer exists; reporting it as
# "now" is exactly the invented-number failure the whole feature exists to
# avoid. `now` is real wall-clock time.time(), not a fixture constant, so
# these assertions hold regardless of when the suite runs.
import time as _real_time


def test_past_resets_at_is_omitted(sr, tmp_path, monkeypatch):
    """resets_at already elapsed -> the reading is stale, omitted (None)."""
    stale_epoch = _real_time.time() - 100
    f = tmp_path / "rollout-stale.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 92.0, "window_minutes": 10080, "resets_at": stale_epoch}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    assert sr._fetch_codex_weekly_usage() is None


def test_missing_resets_at_is_omitted(sr, tmp_path, monkeypatch):
    """No resets_at at all -> freshness can't be verified, treated as stale."""
    f = tmp_path / "rollout-noresets.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 50.0, "window_minutes": 10080}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    assert sr._fetch_codex_weekly_usage() is None


def test_future_resets_at_is_shown_with_sampled_at(sr, tmp_path, monkeypatch):
    """resets_at still in the future -> shown, and carries sampled_at (file mtime)."""
    future_epoch = _real_time.time() + 3600
    f = tmp_path / "rollout-fresh.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 63.0, "window_minutes": 10080, "resets_at": future_epoch}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_weekly_usage()

    assert result is not None
    assert result["utilization"] == 63.0
    assert "sampled_at" in result and result["sampled_at"]


def test_returned_dict_is_a_copy_not_the_cached_object(sr, tmp_path, monkeypatch):
    """Mutating the caller's copy (as system_usage_get's exhaustion overlay
    does) must not leak into the module's own TTL cache — a provider's
    'exhausted' flag would otherwise stick for up to 60s after the vendor
    block actually clears."""
    f = tmp_path / "rollout-copy.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 30.0, "window_minutes": 10080, "resets_at": _real_time.time() + 3600}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    first = sr._fetch_codex_weekly_usage()
    first["exhausted"] = True  # simulate the exhaustion overlay mutating its copy

    second = sr._fetch_codex_weekly_usage()  # still within the 60s TTL

    assert "exhausted" not in second
    assert sr._codex_usage_cache["data"] is not first
    assert "exhausted" not in sr._codex_usage_cache["data"]
