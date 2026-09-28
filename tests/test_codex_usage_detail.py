"""Unit tests for `_fetch_codex_usage_detail` (mc/blueprints/system_routes.py,
added MC-989 Part A for the usage POPUP's Codex section).

A sibling of `_fetch_codex_weekly_usage` (tests/test_codex_weekly_usage.py),
not an extension of it — that function's "weekly window or None" contract is
pinned by its own suite and must not change. This one returns whatever detail
(weekly / five-hour / plan_type / credits) the latest rollout record actually
carries, each time-windowed field gated independently by the same staleness
rule (an elapsed `resets_at` means the window it describes has already reset,
so showing its % as "now" would be an invented number).

Real event shape, captured live 2026-09-27 from a `prolite`-plan rollout:
    {"type":"event_msg","payload":{"type":"token_count","info":{...},
      "rate_limits":{"limit_id":"codex","primary":{"used_percent":5.0,
        "window_minutes":10080,"resets_at":1791055226},"secondary":null,
        "credits":{"has_credits":false,"unlimited":false,"balance":"0"},
        "plan_type":"prolite","rate_limit_reached_type":null}}}

Determinism: monkeypatches `system_routes._agent_runtime._codex_rollout_files`
to point at a tmp rollout file; the module-level TTL cache is reset
before/after each test.
"""
import json
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def sr():
    import server  # noqa: F401  (registers blueprints)
    from mc.blueprints import system_routes as sr_mod
    sr_mod._codex_detail_cache['ts'] = 0.0
    sr_mod._codex_detail_cache['data'] = None
    yield sr_mod
    sr_mod._codex_detail_cache['ts'] = 0.0
    sr_mod._codex_detail_cache['data'] = None


def _rollout_line(primary=None, secondary=None, plan_type: str | None = 'prolite',
                   credits=None, rate_limit_reached_type=None):
    return json.dumps({
        "timestamp": "2026-09-27T20:48:29.393Z",
        "ordinal": 16,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 37820}},
            "rate_limits": {
                "limit_id": "codex",
                "primary": primary,
                "secondary": secondary,
                "credits": credits,
                "plan_type": plan_type,
                "rate_limit_reached_type": rate_limit_reached_type,
            },
        },
    })


def test_weekly_and_five_hour_both_present(sr, tmp_path, monkeypatch):
    """`plus`-plan shape: 5h is primary, weekly is secondary — both fresh."""
    f = tmp_path / "rollout-1.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 12.0, "window_minutes": 300, "resets_at": time.time() + 3600},
            secondary={"used_percent": 77.0, "window_minutes": 10080, "resets_at": time.time() + 7200},
            credits={"has_credits": True, "unlimited": False, "balance": "500"},
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_usage_detail()

    assert result is not None
    assert result["five_hour"]["utilization"] == 12.0
    assert result["weekly"]["utilization"] == 77.0
    assert result["plan_type"] == "prolite"
    assert result["credits"] == {"has_credits": True, "unlimited": False, "balance": "500"}
    assert result["sampled_at"]


def test_prolite_weekly_only_as_primary(sr, tmp_path, monkeypatch):
    """`prolite`-plan shape: weekly (10080min) is primary, no 5h window at all."""
    f = tmp_path / "rollout-2.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 5.0, "window_minutes": 10080, "resets_at": time.time() + 3600},
            secondary=None,
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_usage_detail()

    assert result is not None
    assert result["weekly"]["utilization"] == 5.0
    assert result["five_hour"] is None


def test_stale_weekly_omitted_but_five_hour_kept(sr, tmp_path, monkeypatch):
    """Weekly window elapsed, 5h window still fresh — each gated independently,
    not all-or-nothing (the contrast with `_fetch_codex_weekly_usage`)."""
    f = tmp_path / "rollout-3.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 20.0, "window_minutes": 300, "resets_at": time.time() + 3600},
            secondary={"used_percent": 92.0, "window_minutes": 10080, "resets_at": time.time() - 100},
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_usage_detail()

    assert result is not None
    assert result["five_hour"]["utilization"] == 20.0
    assert result["weekly"] is None


def test_plan_type_and_credits_survive_when_both_windows_stale(sr, tmp_path, monkeypatch):
    """plan_type/credits carry no time window — shown even when every
    time-windowed field is stale, since a stale % still leaves useful
    account-shape info (plan tier, whether credits exist at all)."""
    f = tmp_path / "rollout-4.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 20.0, "window_minutes": 300, "resets_at": time.time() - 500},
            secondary={"used_percent": 92.0, "window_minutes": 10080, "resets_at": time.time() - 100},
            credits={"has_credits": False, "unlimited": False, "balance": "0"},
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    result = sr._fetch_codex_usage_detail()

    assert result is not None
    assert result["weekly"] is None
    assert result["five_hour"] is None
    assert result["plan_type"] == "prolite"
    assert result["credits"] == {"has_credits": False, "unlimited": False, "balance": "0"}


def test_nothing_at_all_returns_none(sr, tmp_path, monkeypatch):
    """Neither window fresh, no plan_type, no credits -> genuinely nothing to show."""
    f = tmp_path / "rollout-5.jsonl"
    f.write_text(
        _rollout_line(
            primary={"used_percent": 20.0, "window_minutes": 300, "resets_at": time.time() - 500},
            secondary=None,
            plan_type=None,
            credits=None,
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    assert sr._fetch_codex_usage_detail() is None


def test_no_rollout_files_returns_none(sr, monkeypatch):
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [])
    assert sr._fetch_codex_usage_detail() is None


def test_cache_ttl_avoids_rereading_file(sr, tmp_path, monkeypatch):
    f = tmp_path / "rollout-6.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 25.0, "window_minutes": 10080, "resets_at": time.time() + 3600}) + "\n",
        encoding="utf-8",
    )
    calls = {"n": 0}

    def _files():
        calls["n"] += 1
        return [f]

    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", _files)

    first = sr._fetch_codex_usage_detail()
    second = sr._fetch_codex_usage_detail()

    assert first == second
    assert calls["n"] == 1


def test_returned_dict_is_a_copy_not_the_cached_object(sr, tmp_path, monkeypatch):
    f = tmp_path / "rollout-7.jsonl"
    f.write_text(
        _rollout_line(primary={"used_percent": 30.0, "window_minutes": 10080, "resets_at": time.time() + 3600}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sr._agent_runtime, "_codex_rollout_files", lambda: [f])

    first = sr._fetch_codex_usage_detail()
    first["mutated"] = True

    second = sr._fetch_codex_usage_detail()

    assert "mutated" not in second
    assert sr._codex_detail_cache["data"] is not first
