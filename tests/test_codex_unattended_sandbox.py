"""Tests for the Codex unattended-sandbox fix (2026-09-14,
docs/UNATTENDED_AGENT_PERMISSIONS_AUDIT.md §4 risk #1).

Every Codex launch used to carry `--dangerously-bypass-approvals-and-sandbox`
unconditionally — Codex has no PreToolUse-hook equivalent to steward/fence.py,
so no Codex session, on any launch path, ever had a backstop. This closes it
for UNATTENDED launches only (schedule/workflow/dispatch/hivemind trigger_type
+ the `codex_unattended_sandbox` config flag): `-s workspace-write`, confined
to the launch cwd, replaces the bypass flag. Manual (interactive) Codex
sessions are unaffected — see test_manual_trigger_type_never_sandboxes below.

`codex_unattended_sandbox_decision()` runs IN-PROCESS (CodexRuntime.dispatch/
write_followup already hold the session dict), unlike steward/fence.py's
detached-subprocess PreToolUse hook, which has to ask MC over HTTP
(GET /api/session/trigger-type) because it has no access to server memory.
No second detector either way: both reuse steward/fence.py's own
_UNATTENDED_TRIGGER_TYPES set.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402


# ── codex_unattended_sandbox_decision — pure unit tests ───────────────────────

@pytest.mark.parametrize('trigger_type', [
    'schedule', 'workflow', 'dispatch', 'hivemind_orchestrator', 'hivemind_worker',
])
def test_known_unattended_trigger_types_sandbox(trigger_type):
    session = {'trigger_type': trigger_type}
    assert agent_runtime_mod.codex_unattended_sandbox_decision(session, True) is True


def test_manual_trigger_type_never_sandboxes():
    session = {'trigger_type': 'manual'}
    assert agent_runtime_mod.codex_unattended_sandbox_decision(session, True) is False


def test_manual_trigger_type_with_flag_off_still_bypasses():
    session = {'trigger_type': 'manual'}
    assert agent_runtime_mod.codex_unattended_sandbox_decision(session, False) is False


def test_flag_off_restores_bypass_even_for_unattended_trigger():
    session = {'trigger_type': 'schedule'}
    assert agent_runtime_mod.codex_unattended_sandbox_decision(session, False) is False


def test_unknown_trigger_type_fails_safe_to_sandbox():
    # A future/unrecognized trigger_type must not silently bypass — only a
    # POSITIVE, confirmed 'manual' value ever un-sandboxes. Deliberately
    # stricter than fence.py's own "unknown -> don't arm" precedent (see
    # codex_unattended_sandbox_decision's docstring): this decision is made
    # once per launch, not once per tool call, so it can afford to be the
    # more conservative of the two.
    session = {'trigger_type': 'some_future_type'}
    assert agent_runtime_mod.codex_unattended_sandbox_decision(session, True) is True


def test_missing_trigger_type_fails_safe_to_sandbox():
    # No trigger_type recorded at all -> can't confirm this is a manual
    # session -> sandbox, the opposite of the fence's fail-OPEN posture (see
    # module docstring: this decision runs once per launch, not once per tool
    # call, so a wrong 'sandbox' guess costs a rerun, not a silent compromise).
    session = {}
    assert agent_runtime_mod.codex_unattended_sandbox_decision(session, True) is True


def test_none_session_dict_fails_safe_to_sandbox():
    assert agent_runtime_mod.codex_unattended_sandbox_decision(None, True) is True


def test_broken_session_dict_lookup_does_not_crash_and_fails_safe():
    """The trigger_type lookup failing must not crash the launch — and must
    resolve to the SANDBOXED posture, not the bypass."""
    class _Boom:
        def get(self, *a, **kw):
            raise RuntimeError("simulated lookup failure")
    assert agent_runtime_mod.codex_unattended_sandbox_decision(_Boom(), True) is True


# ── CodexRuntime.build_command — flag shape ────────────────────────────────────

def test_build_command_bypass_flag_by_default():
    rt = agent_runtime_mod.CodexRuntime()
    rt._bin_cache = 'codex'  # skip real binary resolution
    cmd = rt.build_command()
    assert '--dangerously-bypass-approvals-and-sandbox' in cmd
    assert '-s' not in cmd


def test_build_command_sandbox_flags_when_unattended():
    rt = agent_runtime_mod.CodexRuntime()
    rt._bin_cache = 'codex'
    cmd = rt.build_command(unattended_sandbox=True)
    assert '--dangerously-bypass-approvals-and-sandbox' not in cmd
    assert '-s' in cmd
    assert cmd[cmd.index('-s') + 1] == 'workspace-write'


def test_build_command_sandbox_flags_on_resume_branch_too():
    """`exec resume` (codex-cli 0.155.1) has no `-s`/`--sandbox` option --
    `codex exec resume -s workspace-write --help` exits 2, 'unexpected
    argument' (MC-975 gap 5). The resume branch must express the same
    workspace-write policy via `-c sandbox_mode=`, which `exec resume` does
    accept (verified live: exit 0, prints resume help)."""
    rt = agent_runtime_mod.CodexRuntime()
    rt._bin_cache = 'codex'
    cmd = rt.build_command(resume_id='last', unattended_sandbox=True)
    assert '--dangerously-bypass-approvals-and-sandbox' not in cmd
    assert '-s' not in cmd
    assert '-c' in cmd
    assert cmd[cmd.index('-c') + 1] == 'sandbox_mode="workspace-write"'


def test_build_command_bypass_on_resume_branch_when_not_unattended():
    rt = agent_runtime_mod.CodexRuntime()
    rt._bin_cache = 'codex'
    cmd = rt.build_command(resume_id='some-thread-id', unattended_sandbox=False)
    assert '--dangerously-bypass-approvals-and-sandbox' in cmd
    assert '-s' not in cmd


# ── sandbox-denial detection regex — matched against REAL observed output ────
# (2026-09-14, Windows, codex-cli 0.154.0, `-s workspace-write`, a Set-Content
# write denied outside the sandboxed cwd — see UNATTENDED_AGENT_PERMISSIONS_
# AUDIT.md §6 for the full captured JSONL.)

_REAL_DENIAL_OUTPUT = (
    "Set-Content : Access to the path \r\n"
    "'C:\\Users\\levir\\Documents\\_claude\\mission-control\\.clayrune\\agents\\"
    "63e96270047c\\_scratch\\outside_write_test.txt' is \r\n"
    "denied.\r\nAt line:2 char:1\r\n"
    "+ Set-Content -LiteralPath '../outside_write_test.txt' -Value 'outside- ...\r\n"
    "    + CategoryInfo          : PermissionDenied: "
    "(C:\\Users\\levir\\..._write_test.txt:String) [Set-Content], Unauthorized \r\n"
    "   AccessException\r\n"
    "    + FullyQualifiedErrorId : GetContentWriterUnauthorizedAccessError,"
    "Microsoft.PowerShell.Commands.SetContentCommand\r\n"
)


def test_sandbox_denial_regex_matches_real_captured_output():
    assert agent_runtime_mod._CODEX_SANDBOX_DENIAL_RE.search(_REAL_DENIAL_OUTPUT)


def test_sandbox_denial_regex_does_not_match_ordinary_output():
    assert not agent_runtime_mod._CODEX_SANDBOX_DENIAL_RE.search(
        "inside-ok\nfile written successfully, exit code 0")


def test_sandboxed_launch_skips_git_repo_check_for_non_git_projects():
    """Regression 2026-09-14: `-s workspace-write` (unlike the bypass flag)
    enforces codex's trusted-directory check, so apex_trader (not a git repo)
    failed every unattended run with 'Not inside a trusted directory and
    --skip-git-repo-check was not specified'. Both launch branches must pass it."""
    rt = agent_runtime_mod.CodexRuntime()
    rt._bin_cache = 'codex'  # skip real binary resolution
    for kwargs in ({}, {'resume_id': 'last'}, {'resume_id': 'some-thread-id'}):
        cmd = rt.build_command(unattended_sandbox=True, **kwargs)
        assert '--skip-git-repo-check' in cmd, (kwargs, cmd)
        assert '--dangerously-bypass-approvals-and-sandbox' not in cmd
