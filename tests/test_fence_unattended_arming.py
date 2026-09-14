"""Tests for the generalized unattended-arming check in steward/fence.py
(2026-09-14, docs/UNATTENDED_AGENT_PERMISSIONS_AUDIT.md).

The fence originally enforced ONLY for sessions carrying the literal
`[Steward cycle]` transcript marker. This adds a second, independent signal —
the trigger_type MC recorded server-side for the session (schedule/workflow/
dispatch/hivemind_worker/hivemind_orchestrator), looked up via
GET /api/session/trigger-type keyed on CLAUDE_CODE_SESSION_ID — so a
scheduled task, a workflow step, an agent-dispatched helper, or a hivemind
worker gets the same backstop the steward already has, without needing the
marker. See test_steward_fence.py for the pre-existing marker-only behavior,
which these tests must not regress.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

import steward.fence as fence

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Every test controls CLAUDE_CODE_SESSION_ID explicitly.
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)


def _transcript(tmp_path, first_user_text):
    p = tmp_path / 'transcript.jsonl'
    p.write_text(json.dumps({'type': 'user',
                             'message': {'role': 'user', 'content': first_user_text}}) + '\n',
                 encoding='utf-8')
    return str(p)


# ── _should_arm_for_unattended_trigger — pure unit tests ──────────────────────

def test_no_session_id_never_calls_the_network(monkeypatch):
    def _boom(_sid):
        raise AssertionError("must not perform a lookup with no session id")
    monkeypatch.setattr(fence, '_lookup_trigger_type', _boom)
    assert fence._should_arm_for_unattended_trigger() is False


@pytest.mark.parametrize('trigger_type', [
    'schedule', 'workflow', 'dispatch', 'hivemind_orchestrator', 'hivemind_worker',
])
def test_known_unattended_trigger_types_arm(monkeypatch, trigger_type):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': trigger_type,
                                     'fence_unattended_enabled': True})
    assert fence._should_arm_for_unattended_trigger() is True


def test_manual_trigger_type_does_not_arm(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'manual',
                                     'fence_unattended_enabled': True})
    assert fence._should_arm_for_unattended_trigger() is False


def test_unreachable_server_fails_open(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setattr(fence, '_lookup_trigger_type', lambda sid: None)
    assert fence._should_arm_for_unattended_trigger() is False


def test_config_flag_off_disables_arming(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'schedule',
                                     'fence_unattended_enabled': False})
    assert fence._should_arm_for_unattended_trigger() is False


def test_unknown_trigger_type_does_not_arm(monkeypatch):
    # A future/unrecognized trigger_type must not silently arm the fence —
    # only the explicit allowlist does.
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'some_future_type',
                                     'fence_unattended_enabled': True})
    assert fence._should_arm_for_unattended_trigger() is False


# ── main() end-to-end (in-process, stdin/env monkeypatched) ───────────────────

def _run_main(monkeypatch, tmp_path, *, first_user_text, command,
              session_id=None, lookup=None):
    payload = {'tool_name': 'Bash', 'tool_input': {'command': command},
               'transcript_path': _transcript(tmp_path, first_user_text)}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    if session_id is None:
        monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    else:
        monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', session_id)
    if lookup is not None:
        monkeypatch.setattr(fence, '_lookup_trigger_type', lambda sid: lookup)
    return fence.main()


def test_scheduled_session_blocked_on_catastrophic_command(monkeypatch, tmp_path, capsys):
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Nightly competitor scan for project X',
                   command='git push origin master',
                   session_id='sid-sched',
                   lookup={'trigger_type': 'schedule', 'fence_unattended_enabled': True})
    assert rc == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_scheduled_session_allows_benign_command(monkeypatch, tmp_path):
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Nightly competitor scan for project X',
                   command='git status',
                   session_id='sid-sched',
                   lookup={'trigger_type': 'schedule', 'fence_unattended_enabled': True})
    assert rc == 0


def test_dispatched_agent_session_blocked_on_catastrophic_command(monkeypatch, tmp_path):
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go review the auth module',
                   command='rm -rf /home/user/project/src',
                   session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2


def test_workflow_step_session_blocked(monkeypatch, tmp_path):
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Step 2 of the release workflow',
                   command='gh pr merge 42',
                   session_id='sid-wf',
                   lookup={'trigger_type': 'workflow', 'fence_unattended_enabled': True})
    assert rc == 2


def test_hivemind_worker_session_blocked(monkeypatch, tmp_path):
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Workstream 3: implement the API change',
                   command='terraform apply -auto-approve',
                   session_id='sid-hive',
                   lookup={'trigger_type': 'hivemind_worker', 'fence_unattended_enabled': True})
    assert rc == 2


def test_manual_session_never_fenced_even_with_catastrophic_command(monkeypatch, tmp_path):
    # Regression pin: an ordinary interactive/manual dev session must stay
    # completely unfenced, even when the lookup succeeds and returns
    # trigger_type='manual'.
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='hey can you push this branch for me',
                   command='git push --force',
                   session_id='sid-manual',
                   lookup={'trigger_type': 'manual', 'fence_unattended_enabled': True})
    assert rc == 0


def test_flag_off_leaves_scheduled_session_unfenced(monkeypatch, tmp_path):
    # The documented off-switch: fence_unattended_enabled=False must fully
    # disable the generalized arming, leaving only the steward marker check.
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Nightly competitor scan for project X',
                   command='git push origin master',
                   session_id='sid-sched',
                   lookup={'trigger_type': 'schedule', 'fence_unattended_enabled': False})
    assert rc == 0


def test_no_claude_session_id_leaves_dev_session_unfenced(monkeypatch, tmp_path):
    # No env var at all (e.g. hook invoked outside a CLI-spawned subprocess)
    # must never arm the fence — the existing marker-only behavior survives.
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='just doing some manual dev work',
                   command='git push --force',
                   session_id=None)
    assert rc == 0


def test_steward_marker_still_wins_over_manual_lookup(monkeypatch, tmp_path):
    # If the transcript marker says steward, that enforces regardless of what
    # the (unused, in this branch) trigger_type lookup would say — the
    # original behavior is untouched.
    def _boom(_sid):
        raise AssertionError("steward-marker branch must not need the lookup")
    monkeypatch.setattr(fence, '_lookup_trigger_type', _boom)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='[Steward cycle] run one cycle',
                   command='git push', session_id='sid-steward')
    assert rc == 2
