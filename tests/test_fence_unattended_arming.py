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
    # No "Allow once" pass unless a test grants one, and never reach the
    # live server's consume route from the suite.
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: False)


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


def test_dispatched_agent_session_blocked_on_git_push(monkeypatch, tmp_path):
    # The exact scenario Piece 2 needed pinned: a session another agent
    # dispatched via POST .../agent/dispatch (trigger_type='dispatch',
    # agent_routes.py) is fenced from pushing, same as the steward always was.
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command='git push origin master',
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


# ── Unreadable transcript (marker=None) — the 2026-09-14 correction ──────────
# UNATTENDED_AGENT_PERMISSIONS_AUDIT §7: an unreadable/missing transcript used
# to fail CLOSED unconditionally. Now it falls through to the trigger_type
# check, same as a confirmed-non-steward transcript — these pin both halves:
# no signal → allow, a positive unattended signal → still block even though
# the marker itself couldn't be read (this is exactly how a steward cycle
# whose transcript file hasn't been flushed yet stays covered — steward
# cycles are dispatched with trigger_type='schedule', not a distinct value).

def _run_main_no_transcript(monkeypatch, *, transcript_path, command,
                            session_id=None, lookup=None):
    payload = {'tool_name': 'Bash', 'tool_input': {'command': command}}
    if transcript_path is not None:
        payload['transcript_path'] = transcript_path
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    if session_id is None:
        monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    else:
        monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', session_id)
    if lookup is not None:
        monkeypatch.setattr(fence, '_lookup_trigger_type', lambda sid: lookup)
    return fence.main()


def test_missing_transcript_with_no_session_id_allows(monkeypatch):
    rc = _run_main_no_transcript(monkeypatch, transcript_path=None,
                                 command='git push')
    assert rc == 0


def test_unreadable_transcript_path_with_no_session_id_allows(monkeypatch, tmp_path):
    rc = _run_main_no_transcript(
        monkeypatch, transcript_path=str(tmp_path / 'nope.jsonl'),
        command='rm -rf /home/user/project')
    assert rc == 0


def test_unreadable_transcript_combined_with_schedule_trigger_still_blocks(monkeypatch, tmp_path):
    # This is the steward-cycle-with-a-not-yet-flushed-transcript case: the
    # marker can't be confirmed, but MC's own server-side record of the
    # session (trigger_type='schedule' — what steward cycles are actually
    # stamped, scheduler_routes.py) still arms the fence.
    rc = _run_main_no_transcript(
        monkeypatch, transcript_path=str(tmp_path / 'nope.jsonl'),
        command='git push', session_id='sid-sched',
        lookup={'trigger_type': 'schedule', 'fence_unattended_enabled': True})
    assert rc == 2


def test_unreadable_transcript_combined_with_manual_trigger_still_allows(monkeypatch, tmp_path):
    rc = _run_main_no_transcript(
        monkeypatch, transcript_path=str(tmp_path / 'nope.jsonl'),
        command='git push', session_id='sid-manual',
        lookup={'trigger_type': 'manual', 'fence_unattended_enabled': True})
    assert rc == 0


# ── Worktree fix (2026-09-28, Dave): double-fire safety ───────────────────────
# tools/guards/install_hooks.py now injects fence.py into EVERY Claude
# launch's per-launch --settings file (closing the worktree hole — see that
# module's EXTRA_HOOK_SHAPES comment), on top of the pre-existing project-
# level <project>/.claude/settings.json entry a steward-enabled project
# already carries. That means an armed session can have BOTH copies of the
# hook registered for the SAME PreToolUse event and see it invoked twice per
# tool call. These pin the two properties that make that harmless: (1) an
# attended session still gets a no-op from EACH invocation (self-gating is
# per-call, not per-process — nothing latches state between calls), and (2) a
# blocked command is blocked identically on a second, independent call with
# the same stdin (idempotent — no shared state a second call could see
# differently).
def test_double_invocation_on_attended_session_is_a_noop_both_times(monkeypatch, tmp_path):
    for _ in range(2):
        rc = _run_main(monkeypatch, tmp_path,
                       first_user_text='hey can you fix this bug',
                       command='git push origin main')
        assert rc == 0


def test_double_invocation_on_armed_session_blocks_identically_both_times(monkeypatch, tmp_path, capsys):
    for _ in range(2):
        rc = _run_main(monkeypatch, tmp_path,
                       first_user_text='Nightly competitor scan for project X',
                       command='git push origin main',
                       session_id='sid-dispatch',
                       lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
        assert rc == 2
        assert 'STEWARD FENCE blocked' in capsys.readouterr().err


# ── One-shot "Allow once" pass (MC-994 follow-up, 2026-09-28) ────────────────
# _consume_attend_once_pass is stubbed to False by the autouse fixture above;
# these override it per-test to pin the three properties the brief calls out:
# a spent pass lets exactly the one blocked call through, a steward-marker
# session is never even eligible to spend one, and a supply-chain
# ("human-owned") reason can never be passed regardless of eligibility.

def test_consumed_pass_allows_an_otherwise_blocked_trigger_type_action(monkeypatch, tmp_path):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: True)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command='git push origin master',
                   session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 0


def test_no_open_pass_still_blocks_trigger_type_action(monkeypatch, tmp_path, capsys):
    # Default fixture already stubs consume to False; assert explicitly so
    # this test still documents the "no pass -> still blocked" contract even
    # if the fixture default ever changes.
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: False)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command='git push origin master',
                   session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_steward_marker_session_never_consumes_a_pass(monkeypatch, tmp_path, capsys):
    # A steward-marker session is not `pass_eligible` at all (brief item 3) —
    # even if a pass happens to be open server-side, the fence must not spend
    # it, and must not even ask.
    def _boom():
        raise AssertionError("steward-marker session must never call consume")
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _boom)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='[Steward cycle] run one cycle',
                   command='git push', session_id='sid-steward')
    assert rc == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_human_owned_supply_chain_block_never_consumes_a_pass(monkeypatch, tmp_path, capsys):
    # Editing fence.py's own enforcement code is the supply-chain case the
    # pass must never be spendable on (FenceDecision.overridable=False) —
    # even a trigger-type-armed, otherwise-pass-eligible session with an
    # open pass must stay blocked, and consume must never be called.
    def _boom():
        raise AssertionError("a human-owned supply-chain block must never consume a pass")
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _boom)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-dispatch')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    payload = {'tool_name': 'Edit',
               'tool_input': {'file_path': 'steward/fence.py', 'old_string': 'x', 'new_string': 'y'},
               'transcript_path': _transcript(tmp_path, 'please tweak the fence')}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    rc = fence.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert 'STEWARD FENCE blocked' in err
    assert 'human-owned' in err


# ── Fenn's review of the pass (2026-09-28) ───────────────────────────────────
# The block loop stops at the first hit, so the pass must be judged on the
# WHOLE call: one blocked operation, and that one overridable.

def _never_consume():
    raise AssertionError("this call must never spend a pass")


def test_chained_command_never_consumes_a_pass(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command='git push origin master && git reset --hard HEAD~1',
                   session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_push_with_hidden_fence_patch_never_consumes_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    cmd = ("git push origin master\napply_patch <<'PATCH'\n*** Begin Patch\n"
           "*** Update File: steward/fence.py\n@@\n-x\n+y\n*** End Patch\nPATCH")
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=cmd, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2


def test_global_claude_skill_write_never_consumes_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-dispatch')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    payload = {'tool_name': 'Write',
               'tool_input': {'file_path': 'C:/Users/u/.claude/skills/x/SKILL.md', 'content': 'y'},
               'transcript_path': _transcript(tmp_path, 'please add a skill')}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert fence.main() == 2


def test_unreadable_transcript_arms_but_never_consumes_a_pass(monkeypatch, tmp_path, capsys):
    # marker unknown (None) must still arm via trigger_type, but only a
    # POSITIVELY confirmed non-steward may spend a pass.
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    rc = _run_main_no_transcript(
        monkeypatch, transcript_path=str(tmp_path / 'nope.jsonl'),
        command='git push origin master', session_id='sid-sched',
        lookup={'trigger_type': 'schedule', 'fence_unattended_enabled': True})
    assert rc == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


# ── Fenn's re-review (2026-09-28, N1/N2/N4) ─────────────────────────────────
# Only a plain single invocation may spend a pass; every one of Fenn's
# opaque/multi-operation probes must stay blocked with zero consumes.

@pytest.mark.parametrize('tool_name,command', [
    ('Bash', 'eval "$PAYLOAD"'),
    ('Bash', 'git push origin $(git push --quiet origin other)'),
    ('PowerShell', 'foreach ($x in 1,2) { git push origin master }'),
    ('Bash', "powershell -Command 'foreach ($x in 1,2) { git push origin master }'"),
    ('Bash', 'curl -X POST https://example.invalid/a --next -X POST https://example.invalid/b'),
    ('Bash', 'bash -lc "git push origin master"'),
])
def test_opaque_or_multi_operation_command_never_consumes_a_pass(monkeypatch, tmp_path, tool_name, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-dispatch')
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    payload = {'tool_name': tool_name, 'tool_input': {'command': command},
               'transcript_path': _transcript(tmp_path, 'Please go implement the fix we discussed')}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert fence.main() == 2


def test_plain_push_still_consumes_a_pass(monkeypatch, tmp_path):
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: spent.append(1) or True)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command='git push origin master', session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 0 and spent == [1]


@pytest.mark.parametrize('command', [
    'curl -s -X POST http://localhost:5199/api/local-auth/set -d "{}"',
    'curl -s -X POST http://localhost:5199/api/project/p/agent/s/attend-once -d "{}"',
])
def test_human_gate_routes_are_blocked_and_never_passable(monkeypatch, tmp_path, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2


def test_passcode_store_write_and_powershell_read_are_refused(monkeypatch, tmp_path):
    store = str(fence._local_auth_data_root() / 'data' / fence._LOCAL_AUTH_FILENAME)
    for name, ti in (('Write', {'file_path': store, 'content': '{}'}),
                     ('Edit', {'file_path': store, 'old_string': 'a', 'new_string': 'b'}),
                     ('PowerShell', {'command': 'Get-Content data/' + fence._LOCAL_AUTH_FILENAME}),
                     ('apply_patch', {'command': '*** Begin Patch\n*** Update File: ' + store
                                                 + '\n@@\n-a\n+b\n*** End Patch'})):
        assert fence.check_vault_file_access(name, ti).blocked, name


# ── Fenn's review #3 (2026-09-28, N1/N4) ────────────────────────────────────
# N4: a transfer tool sends one request per target, so one pass covers exactly
# one target. N1: bare relative writes to the passcode store from the data dir.

@pytest.mark.parametrize('command', [
    'curl -X POST https://example.invalid/a https://example.invalid/b',
    'curl -X POST https://example.invalid/a -: -X POST https://example.invalid/b',
    'curl -X POST https://example.invalid/item[1-3]',
    'curl -X POST --url https://example.invalid/a https://example.invalid/b',
    'curl -K cfg.txt -X POST https://example.invalid/a',
    'curl -sK cfg.txt -X POST https://example.invalid/a',
    'curl -Z -X POST https://example.invalid/a',
    'wget --post-data=x -i urls.txt',
    'wget --post-data=x https://example.invalid/a https://example.invalid/b',
])
def test_multi_target_transfer_never_consumes_a_pass(monkeypatch, tmp_path, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2


@pytest.mark.parametrize('command', [
    'curl -s -X POST https://example.invalid/a -H "Content-Type: application/json" -d "x"',
    'curl -XPOST --url https://example.invalid/a',
    'curl -s -X POST https://example.invalid/a',
])
def test_review6_curl_never_spends_a_pass(monkeypatch, tmp_path, command):
    # Fenn's review #6 N4: a default ~/.curlrc (`location`, `retry = 1`)
    # replays the POST with nothing on the command line, so no argv check
    # can bound curl to one request. It is refused even with a pass on hand.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: spent.append(1) or True)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


def test_git_push_still_spends_one_pass(monkeypatch, tmp_path):
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: spent.append(1) or True)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command='git push', session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 0 and spent == [1]


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl --request POST https://example.invalid/single',
    'curl --json fixture https://example.invalid/single',
    'curl -dfixture https://example.invalid/single',
    'curl -X "POST" https://example.invalid/single',
    'curl -sX POST https://example.invalid/single',
    'curl --request POST -L https://example.invalid/redirect',
    'curl --request POST https://example.invalid/a https://example.invalid/b',
    'irm -Method POST -Uri https://example.invalid/single',
    'iwr -Method:Post https://example.invalid/single',
    'iwr -CustomMethod DELETE https://example.invalid/single',
    'wget --method=POST https://example.invalid/single',
])
def test_review6_ordinary_mutation_spellings_are_blocked(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #6 N6: these returned exit 0 with zero consumes, i.e. a
    # remote mutation with no pass at all.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('command', [
    'iwr -TimeoutSec 5 https://example.invalid/f -OutFile a',
    'curl -fsSL https://example.invalid/i.sh -o i.sh',
    'curl -D h.txt https://example.invalid/',
    'curl -G --data-urlencode q=1 https://example.invalid/',
])
def test_review6_reads_are_not_sends(command):
    assert not fence._touches_nonlocal_network(command).blocked


_STORE = fence._LOCAL_AUTH_FILENAME


@pytest.mark.parametrize('tool_name,command', [
    ('PowerShell', 'Set-Content ' + _STORE + ' -Value "{}"'),
    ('Bash', "printf '{}' > " + _STORE),
    ('Bash', 'rm ' + _STORE),
    ('PowerShell', 'Remove-Item ' + _STORE),
    ('Bash', 'mv other.json ' + _STORE),
])
def test_relative_shell_write_to_passcode_store_is_refused(monkeypatch, tmp_path, tool_name, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    assert fence.check_vault_file_access(tool_name, {'command': command}).blocked
    data_dir = str(fence._local_auth_data_root() / 'data')
    payload = {'tool_name': tool_name, 'tool_input': {'command': command}, 'cwd': data_dir,
               'transcript_path': _transcript(tmp_path, 'Please go implement the fix we discussed')}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert fence.main() == 2


def test_grep_tool_search_for_store_name_still_passes():
    # The shell has no exception any more (review #5); the Grep tool's
    # content pattern is not a path and stays usable.
    assert not fence.check_vault_file_access(
        'Grep', {'pattern': _STORE, 'path': 'mc'}).blocked


# ── Fenn's review #4 (2026-09-28, N1/N4) ────────────────────────────────────
# Both guards are allowlists now; each spelling that slipped past review #3's
# fixes is pinned here.

@pytest.mark.parametrize('command', [
    'curl -X POST --url https://example.invalid/item[1-3]',
    'curl -X POST -Kreview4.conf https://example.invalid/a',
    '"curl" -X POST https://example.invalid/a https://example.invalid/b',
    r'C:\tools\curl.exe -X POST https://example.invalid/a https://example.invalid/b',
    'curl -X POST --unknown-option https://example.invalid/a',
    'iwr -Ur https://example.invalid/a -Method Post',
    'wget -e input=urls.txt --post-data=x https://example.invalid/a',
])
def test_review4_transfer_fanout_never_consumes_a_pass(monkeypatch, tmp_path, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2


@pytest.mark.parametrize('tool_name,command', [
    ('Bash', 'curl -o ' + _STORE + ' http://127.0.0.1:9/empty'),
    ('PowerShell', 'Invoke-WebRequest http://127.0.0.1:9/empty -OutFile ' + _STORE),
    ('Bash', 'grep . ' + _STORE),
    ('Bash', 'grep -rn "' + _STORE + '|pw_hash" data/'),
    ('Bash', 'grep -f ' + _STORE + ' mc/'),
])
def test_review4_store_named_commands_are_refused(monkeypatch, tmp_path, tool_name, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: True)
    assert fence.check_vault_file_access(tool_name, {'command': command}).blocked
    data_dir = str(fence._local_auth_data_root() / 'data')
    payload = {'tool_name': tool_name, 'tool_input': {'command': command}, 'cwd': data_dir,
               'transcript_path': _transcript(tmp_path, 'Please go implement the fix we discussed')}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert fence.main() == 2


# ── Fenn's review #5 (2026-09-28, N1/N4) ────────────────────────────────────

@pytest.mark.parametrize('command', [
    'grep -e. ' + _STORE + ' other.txt',
    'grep --regexp=. ' + _STORE + ' other.txt',
    'rg -e. ' + _STORE + ' other.txt',
    'rg --regexp=. ' + _STORE + ' other.txt',
    'git grep --no-index -e. ' + _STORE + ' other.txt',
    'grep -rn "' + _STORE + '" mc/',
])
def test_review5_any_shell_mention_of_store_is_refused(monkeypatch, tmp_path, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: True)
    assert fence.check_vault_file_access('Bash', {'command': command}).blocked
    data_dir = str(fence._local_auth_data_root() / 'data')
    payload = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': data_dir,
               'transcript_path': _transcript(tmp_path, 'Please go implement the fix we discussed')}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert fence.main() == 2


@pytest.mark.parametrize('command', [
    'curl -L -X POST https://example.invalid/redirect',
    'curl --location -X POST https://example.invalid/redirect',
    'curl -sL -X POST https://example.invalid/redirect',
    'curl --retry 1 -X POST https://example.invalid/retry',
    'curl --retry=1 -X POST https://example.invalid/retry',
    'wget -qO- --post-data=x https://example.invalid/a',
    'iwr -Uri https://example.invalid/a -Method Post',
    'Invoke-RestMethod -Uri https://example.invalid/a -Method Post',
])
def test_review5_replaying_transfer_never_consumes_a_pass(monkeypatch, tmp_path, command):
    monkeypatch.setattr(fence, '_consume_attend_once_pass', _never_consume)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl -sdfixture https://example.invalid/single',
    'curl -sTupload.txt https://example.invalid/single',
    'curl -sFk=v https://example.invalid/single',
    'curl -G -X POST --json q=1 https://example.invalid/single',
    'bash -c "curl -X POST https://example.invalid/single"',
])
def test_review7_clustered_and_nested_sends_are_blocked(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #7 N6: curl short clusters carry data/upload options.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl --output download.bin https://example.invalid/single',
    'curl --output=download.bin https://example.invalid/single',
    'curl -o post.json https://example.invalid/single',
    'curl -o delete-report.json https://example.invalid/single',
    'curl -A POST https://example.invalid/single',
    'iwr -Uri https://example.invalid/single -OutFile post.json',
    'wget -O post.html https://example.invalid/single',
    'wget --output-document=download.bin https://example.invalid/single',
    'curl --get --json q=1 https://example.invalid/single',
    'curl -G --json q=1 https://example.invalid/single',
])
def test_review7_reads_are_allowed_without_a_pass(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #7 N7/N8: GET downloads and JSON-query GETs are reads.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 0 and spent == []


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl --request="POST" https://example.invalid/single',
    'curl -X"POST" https://example.invalid/single',
    'iwr -UseBasicParsing -Method:"Post" -Uri https://example.invalid/single',
    'curl -X POST https://example.invalid/a --next -X GET https://example.invalid/b',
    'curl -X POST https://example.invalid/a -: -X GET https://example.invalid/b',
    'curl -G --data q=1 https://example.invalid/a --next --data fixture https://example.invalid/b',
    'curl -G --no-get --data fixture https://example.invalid/single',
    'http --timeout 5 POST https://example.invalid/single',
    'http --auth user:fixture DELETE https://example.invalid/single',
])
def test_review8_sends_are_blocked(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #8 N9 (quoted method values), N10 (curl --next groups),
    # N11 (HTTPie option values before the method).
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl -X POST -X GET https://example.invalid/single',
    'curl -X "GET" https://example.invalid/single',
    'http https://example.invalid/single q==1',
    'http GET https://example.invalid/single k=v',
])
def test_review8_reads_are_allowed(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #8 N12 and the same-group last-method control.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 0 and spent == []


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl --data fixture --header="X-Debug: --get" https://example.invalid/single',
    'curl -X POST --header="X-Debug: --request GET" https://example.invalid/single',
    'curl --data fixture --header "X-Debug: --get" https://example.invalid/single',
    'curl -X POST https://example.invalid/a -s: -X GET https://example.invalid/b',
])
def test_review9_sends_are_blocked(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #9 N13 (quoted header text is one argument) and N10
    # (`:` inside a short cluster ends a transfer).
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl -X POST --data --next -X GET https://example.invalid/single',
    'curl -X POST -H --next -X GET https://example.invalid/single',
])
def test_review9_option_value_next_is_not_a_transfer(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #9 N14: `--next` consumed as a value starts nothing.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 0 and spent == []


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl -H "Content-Type: application/json; charset=utf-8" --data fixture https://example.invalid/single',
    'curl --data fixture -H "Content-Type: application/json; charset=utf-8" https://example.invalid/single',
    'curl --proxy-header --get --data fixture https://example.invalid/single',
    'curl --proxy-header "X-Test: fixture" --data fixture https://example.invalid/single',
    r'curl -H X-Debug:\ --get --data fixture https://example.invalid/single',
    'curl -H "X-Debug: --get" --data fixture https://example.invalid/single',
    'curl --some-future-option --get --data fixture https://example.invalid/single',
    'echo ok; curl -X POST https://example.invalid/single',
])
def test_review10_sends_are_blocked(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #10: N15 quoted `;` is not a separator, N16 an option of
    # unknown arity cannot downgrade a send, N17 `\ ` joins one argument.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('command', [
    'curl -H "Accept: text/html; q=0.9" https://example.invalid/single',
    'curl https://example.invalid/single; cut -d" " -f1 x',
    'curl -L --retry 3 -o f https://example.invalid/single',
])
def test_review10_reads_are_allowed(command):
    assert not fence._touches_nonlocal_network(command).blocked


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'bash -c "curl --data fixture https://example.invalid/a; curl -X GET https://example.invalid/b"',
    'bash -c "curl --data fixture https://example.invalid/a && curl -X GET https://example.invalid/b"',
    'bash -c "curl -X POST http://localhost:5199/x; curl -X POST https://example.invalid/b"',
    "bash -c 'curl -H \"A: b; c\" --data fixture https://example.invalid/single'",
])
def test_review11_nested_program_sends_are_blocked(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #11 N18: a nested program is split at its own operators.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('command', [
    'bash -c "curl https://example.invalid/a; curl https://example.invalid/b"',
    'bash -c "curl -X POST -X GET https://example.invalid/single"',
    'bash -c "curl -X POST http://localhost:5199/api/x"',
])
def test_review11_nested_reads_are_allowed(command):
    assert not fence._touches_nonlocal_network(command).blocked


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'curl -H "X-Source: localhost" --data fixture https://example.invalid/single',
    'curl http://localhost:5199/local --next --data fixture https://example.invalid/external',
    'curl -X POST http://localhost.example.invalid/single',
    'curl -X POST -x http://proxy.example.invalid:8080 http://localhost:5199/x',
    'bash -c "curl -X POST http://localhost:5199/x; curl -X POST https://example.invalid/b"',
    'wget --post-data=x --header="Host: localhost" https://example.invalid/single',
])
def test_review12_localhost_text_is_not_a_local_destination(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #12 N19: locality comes from each transfer's parsed URL
    # hostname, never from text elsewhere in the command.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


@pytest.mark.parametrize('command', [
    'curl -s -X POST http://localhost:5199/api/x -H "Content-Type: application/json" -d "{}"',
    'curl -X POST http://127.0.0.1:5199/api/x -d x',
    'curl -X POST --url http://localhost:5199/api/x',
    'wget --post-data=x http://localhost:5199/api/x',
    'iwr -Method POST -Uri http://localhost:5199/api/x -Body "{}"',
    'curl https://example.invalid/a --next -d x http://localhost:5199/b',
])
def test_review12_local_sends_stay_exempt(command):
    assert not fence._touches_nonlocal_network(command).blocked


@pytest.mark.parametrize('have_pass', [True, False])
@pytest.mark.parametrize('command', [
    'iwr -UseBasicParsing -Method POST -Uri:https://example.invalid/single -Body http://localhost',
    'iwr -Method POST -Uri https://example.invalid/single -Body http://localhost',
    'iwr -Method POST -Uri:https://example.invalid/single -Body fixture',
])
def test_review13_attached_uri_is_the_destination(monkeypatch, tmp_path, command, have_pass):
    # Fenn's review #13 N19: `-Uri:URL` must count as a destination, so
    # local-looking body text can never be the only one.
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass',
                        lambda: spent.append(1) or have_pass)
    rc = _run_main(monkeypatch, tmp_path,
                   first_user_text='Please go implement the fix we discussed',
                   command=command, session_id='sid-dispatch',
                   lookup={'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert rc == 2 and spent == []


def test_review13_attached_local_uri_stays_exempt():
    assert not fence._touches_nonlocal_network(
        'iwr -Method POST -Uri:http://localhost:5199/local -Body fixture').blocked
