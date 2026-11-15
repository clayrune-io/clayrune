"""Every agent child learns its Clayrune project + session (agent-mail routing).

`spawn_engine_proc` is the one spawn site; `LaunchTransaction.launch` names the
project for launches whose session is not registered yet (Claude's first turn).
"""
from __future__ import annotations

import pytest

from mc import agent_spawn as S
from mc import launch_transaction as transaction
from mc import state
from test_runtime_completion_log import env, _mode_b_session  # noqa: F401


class _Popen:
    calls: list = []

    def __init__(self, cmd, **kw):
        type(self).calls.append(kw)
        self.stdin = None


@pytest.fixture
def popen(monkeypatch):
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, "Popen", _Popen)
    monkeypatch.setitem(state.CONFIG, "agent_survive_restart_enabled", False)
    return _Popen


def test_registered_session_env_carries_both_vars_and_port(popen, monkeypatch):
    monkeypatch.setitem(state.agent_sessions, "sidA", {"project_id": "projA"})
    monkeypatch.setenv("MC_PORT", "6123")
    env_in = {"A": "1"}
    S.spawn_engine_proc(["x"], session_id="sidA", engine="qwen", env=env_in)
    (kw,) = popen.calls
    assert kw["env"]["CLAYRUNE_PROJECT_ID"] == "projA"
    assert kw["env"]["CLAYRUNE_SESSION_ID"] == "sidA"
    assert kw["env"]["CLAYRUNE_PORT"] == "6123"
    assert kw["env"]["A"] == "1" and kw["env"] is env_in   # caller's dict, as before


def test_stale_ids_inherited_from_a_spawner_are_overridden(popen, monkeypatch):
    monkeypatch.setitem(state.agent_sessions, "child", {"project_id": "projB"})
    S.spawn_engine_proc(["x"], session_id="child", engine="codex",
                        env={"CLAYRUNE_SESSION_ID": "parent", "CLAYRUNE_PROJECT_ID": "projA"})
    assert popen.calls[0]["env"]["CLAYRUNE_SESSION_ID"] == "child"
    assert popen.calls[0]["env"]["CLAYRUNE_PROJECT_ID"] == "projB"


def test_env_none_becomes_a_copy_of_the_process_env(popen, monkeypatch):
    monkeypatch.setitem(state.agent_sessions, "sidN", {"project_id": "projN"})
    monkeypatch.setenv("SOME_INHERITED", "yes")
    S.spawn_engine_proc(["x"], session_id="sidN", engine="gemini", env=None)
    e = popen.calls[0]["env"]
    assert e["SOME_INHERITED"] == "yes" and e["CLAYRUNE_SESSION_ID"] == "sidN"


def test_project_scope_names_an_unregistered_session(popen):
    with S.project_scope("projC"):
        S.spawn_engine_proc(["x"], session_id="not-registered-yet", engine="claude", env={})
    e = popen.calls[0]["env"]
    assert (e["CLAYRUNE_PROJECT_ID"], e["CLAYRUNE_SESSION_ID"]) == ("projC", "not-registered-yet")
    assert S._SPAWN_PROJECT.get() == ""   # scope restored


def test_unknown_project_leaves_env_untouched(popen):
    env_in = {"A": "1"}
    S.spawn_engine_proc(["x"], session_id="nobody", engine="qwen", env=env_in)
    assert popen.calls[0]["env"] is env_in and "CLAYRUNE_SESSION_ID" not in env_in


def test_launch_transaction_scopes_the_spawn_to_its_project(env):  # noqa: F811
    session = _mode_b_session(env, "tx-scope")
    tx = transaction.start_turn(session, None)
    seen = []
    transaction.launch(tx, lambda: seen.append(S._SPAWN_PROJECT.get()) or object())
    assert seen == [session["project_id"]] and session["project_id"] == "proj1"
    assert S._SPAWN_PROJECT.get() == ""
