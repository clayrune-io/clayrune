"""Tests for the steward reversibility fence (steward/fence.py).

The fence is a hard backstop for an unattended agent: the critical property is
ZERO false-negatives on the catastrophic/irreversible set. False-positives
(blocking a safe command) are acceptable — they just make the steward ask.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from steward.fence import FenceDecision, check_install_dir_write, classify_action, classify_bash

REPO = Path(__file__).resolve().parents[1]
FENCE = REPO / 'steward' / 'fence.py'


# ── Must BLOCK — irreversible / mutating ──────────────────────────────────────
BLOCK_CASES = [
    'git push',
    'git push origin master',
    'git push --force origin main',
    'cd /repo && git push -f',
    'git reset --hard HEAD~3',
    'git clean -fd',
    'git branch -D feature',
    'git push origin --delete oldbranch',
    'npm publish',
    'twine upload dist/*',
    'docker push myrepo/img:latest',
    'gh release create v1.2.3',
    'gh pr create --title x --body y',
    'gh pr merge 42',
    'gh api -X POST /repos/o/r/issues',
    'gcloud run deploy svc --image x',
    'aws s3 rm s3://bucket/key',
    'az vm delete --name x',
    'terraform apply -auto-approve',
    'kubectl delete pod x',
    'alembic upgrade head',
    'psql -c "DROP TABLE users"',
    'psql -c "TRUNCATE TABLE events"',
    'curl -X POST https://api.example.com/send -d @payload.json',
    'curl -X DELETE https://api.stripe.com/v1/x',
    'curl -T bigfile.zip https://upload.example.com/',
    'rm -rf /home/user/project/src',
    'rm -f important.txt',
    'rmdir /s C:\\data',
    'Remove-Item -Recurse -Force C:\\Users\\me\\docs',
    'curl -s -X POST http://localhost:5199/api/system/restart',
    'dd if=/dev/zero of=/dev/sda',
    'shutdown -h now',
]


@pytest.mark.parametrize('cmd', BLOCK_CASES)
def test_blocks_irreversible(cmd):
    d = classify_bash(cmd)
    assert d.blocked, f"fence FAILED to block catastrophic command: {cmd!r}"
    assert d.reason


# ── Must ALLOW — reversible / local ───────────────────────────────────────────
ALLOW_CASES = [
    'ls -la',
    'git status',
    'git add -A',
    'git commit -m "wip"',
    'git diff HEAD',
    'git log --oneline -20',
    'pytest -q',
    'grep -rn foo src/',
    'cat file.py',
    'python script.py',
    'npm install',
    'npm run build',
    'echo hello',
    # steward's own reporting — localhost API calls must pass
    'curl -s -X POST http://localhost:5199/api/project/abc/backlog/123/note -d \'{"text":"FYI"}\'',
    'curl -s http://localhost:5199/api/skills/search?q=x',
    'curl -s https://example.com/data.json',            # plain GET read, non-local
    'rm -rf _scratch/tmpdir',                            # scratch-scoped delete
    'rm /tmp/throwaway.log',
]


@pytest.mark.parametrize('cmd', ALLOW_CASES)
def test_allows_reversible(cmd):
    d = classify_bash(cmd)
    assert not d.blocked, f"fence WRONGLY blocked safe command {cmd!r}: {d.reason}"


def test_scratch_delete_with_escape_is_blocked():
    # scratch marker present but a `..` escape → still blocked (no false-allow)
    assert classify_bash('rm -rf _scratch/../secrets').blocked


def test_classify_action_bash():
    assert classify_action('Bash', {'command': 'git push'}).blocked
    assert not classify_action('Bash', {'command': 'ls'}).blocked


def test_classify_action_non_bash_default_allow():
    assert not classify_action('Read', {'file_path': '/x'}).blocked
    assert not classify_action('Write', {'file_path': '/repo/src/x.py'}).blocked


def test_classify_action_blocks_global_config_edit():
    assert classify_action('Write', {'file_path': '/home/me/.claude/settings.json'}).blocked
    assert classify_action('Edit', {'file_path': 'C:\\Users\\me\\.claude\\skills\\x\\SKILL.md'}).blocked


def test_empty_and_none_safe():
    assert not classify_bash('').blocked
    assert not classify_bash('   ').blocked
    assert not classify_action('Bash', {}).blocked


# ── Hook entrypoint (subprocess) — real stdin/stdout/exit-code contract ────────
def _run_hook(payload: dict, *, claude_session_id=None):
    """Run the real hook subprocess. CLAUDE_CODE_SESSION_ID is stripped from
    the child's environment by default (not just left unset in the test) —
    without this, a test run FROM INSIDE a live Claude Code session (this
    suite's own dev/CI shell included) would leak the outer session's real id
    into the child, making `_should_arm_for_unattended_trigger` attempt a
    genuine network call to the live MC server instead of the deterministic
    no-session-id short-circuit these tests are pinning. Pass
    `claude_session_id=` to deliberately exercise the opposite path."""
    env = dict(os.environ)
    if claude_session_id is None:
        env.pop('CLAUDE_CODE_SESSION_ID', None)
    else:
        env['CLAUDE_CODE_SESSION_ID'] = claude_session_id
    return subprocess.run(
        [sys.executable, str(FENCE)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


def test_hook_blocks_with_exit_2_and_stderr(tmp_path):
    # Fail-closed contract: exit 2 + stderr reason, nothing on stdout. A
    # confirmed-steward transcript (not "no transcript at all" — see
    # test_unknown_session_with_no_signal_is_allowed_not_fail_closed below
    # for that case, which is a DIFFERENT, no-longer-blocking contract since
    # 2026-09-14) is what makes this session unambiguously enforced.
    tp = _transcript(tmp_path, '[Steward cycle] run one cycle')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'},
                   'transcript_path': tp})
    assert r.returncode == 2
    assert 'STEWARD FENCE blocked' in r.stderr
    assert r.stdout.strip() == ''


def test_hook_allows_with_exit_0():
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git status'}})
    assert r.returncode == 0


def test_hook_fails_open_on_garbage():
    r = subprocess.run([sys.executable, str(FENCE)], input='not json',
                       capture_output=True, text=True)
    assert r.returncode == 0


# ── Self-gating: fence enforces ONLY for steward-cycle sessions ───────────────
def _transcript(tmp_path, first_user_text):
    p = tmp_path / 'transcript.jsonl'
    p.write_text(json.dumps({'type': 'user',
                             'message': {'role': 'user', 'content': first_user_text}}) + '\n',
                 encoding='utf-8')
    return str(p)


def test_steward_session_is_fenced(tmp_path):
    tp = _transcript(tmp_path, '[Steward cycle] You are the steward...')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'},
                   'transcript_path': tp})
    assert r.returncode == 2  # steward cycle → enforced


def test_dev_session_is_NOT_fenced(tmp_path):
    # A normal dev/manual session (no steward marker) must run unfenced.
    tp = _transcript(tmp_path, 'hey can you push this branch for me')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push --force'},
                   'transcript_path': tp})
    assert r.returncode == 0  # confirmed non-steward → allowed


def test_steward_session_allows_reversible(tmp_path):
    tp = _transcript(tmp_path, '[Steward cycle] run one cycle')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git status'},
                   'transcript_path': tp})
    assert r.returncode == 0


def test_unknown_session_with_no_signal_is_allowed_not_fail_closed():
    # 2026-09-14, UNATTENDED_AGENT_PERMISSIONS_AUDIT §7 — reversed a real,
    # live bug. The fence used to fail CLOSED here on the (no longer safe)
    # assumption that it is only ever installed in steward-enabled projects.
    # Once it can be installed into every project (§7), an unreadable
    # transcript with no CLAUDE_CODE_SESSION_ID — a brand-new interactive
    # session's very FIRST tool call has exactly this shape — must never
    # block a human's own git push. No transcript_path key at all.
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'}})
    assert r.returncode == 0


def test_empty_transcript_path_with_no_signal_is_allowed(tmp_path):
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'},
                   'transcript_path': ''})
    assert r.returncode == 0


def test_nonexistent_transcript_path_with_no_signal_is_allowed(tmp_path):
    missing = str(tmp_path / 'does-not-exist-yet.jsonl')
    r = _run_hook({'tool_name': 'Bash', 'tool_input': {'command': 'git push'},
                   'transcript_path': missing})
    assert r.returncode == 0


# ── Inert-prose masking (2026-07-16 precision fix) ────────────────────────────
# Real incident (2026-07-13 DECISION NEEDED note): the steward could not
# commit the wake-lock feature because the commit MESSAGE contained OS power
# vocabulary. Prose is data; only command-position text may block.

PROSE_ALLOW_CASES = [
    # The literal incident shape.
    'git commit -m "feat(wake-lock): keep machine awake, prevent system shutdown while agents run"',
    "git commit -m 'fix: handle reboot and shutdown power events in wake_lock'",
    # Repo heredoc commit convention (quoted delimiter), prose mentions blocked verbs.
    'git commit -m "$(cat <<\'EOF\'\nfeat: wake lock\n\nPrevents host shutdown/reboot while agents run.\nAlso documents the gcloud run deploy steps.\nEOF\n)"',
    # PowerShell literal here-string commit (repo convention for pwsh).
    "git commit -m @'\nfix: power handling\n\nCovers shutdown, reboot, and git push docs.\n'@",
    # Cloud words in prose.
    'git commit -m "docs: explain why terraform apply is human-gated"',
    'git commit --message="chore: note that aws s3 rm needs approval"',
]


@pytest.mark.parametrize('cmd', PROSE_ALLOW_CASES)
def test_prose_in_commit_messages_is_not_a_command(cmd):
    d = classify_bash(cmd)
    assert not d.blocked, f"prose false-positive: {d.reason!r} for {cmd!r}"


PROSE_STILL_BLOCK_CASES = [
    # Unmasked command tail after a masked message.
    'git commit -m "safe words" && shutdown /s',
    'git commit -m "safe words" && git push',
    "git commit -m 'safe' ; reboot",
    # Substitution inside a double-quoted message CAN execute — never masked.
    'git commit -m "$(shutdown /s)"',
    'git commit -m "`reboot`"',
    # Heredoc fed to an interpreter executes its body — header check catches it.
    "bash <<'EOF'\nshutdown /s\nEOF",
    "pwsh <<'EOF'\ngit push\nEOF",
    # Unquoted heredoc delimiter (expansion live) is never masked.
    'git commit -m "$(cat <<EOF\n$(git push)\nEOF\n)"',
]


@pytest.mark.parametrize('cmd', PROSE_STILL_BLOCK_CASES)
def test_masking_introduces_no_false_negatives(cmd):
    assert classify_bash(cmd).blocked, f"false NEGATIVE: {cmd!r}"


# ── Learning-loop supply-chain fence (committee M3, 2026-07-16) ───────────────
# Proposal frontmatter + skill-stats records are what decides loadout entry;
# an unattended agent must not be able to edit its own provenance.

SUPPLY_CHAIN_BLOCK_PATHS = [
    r'C:\repo\data\skills\_proposed\proj\2026-07-16T00-00-00-abcd-x\SKILL.md',
    'data/skills/_proposed/global/2026-07-16T00-00-00-abcd-x/EXPLORATION.md',
    'data/skills/_rejected/old/PREFERENCE.md',
    'data/projects/mission_control_skill_stats.json',
    r'C:\repo\data\projects\proj_skill_stats_archive.jsonl',
]


@pytest.mark.parametrize('path', SUPPLY_CHAIN_BLOCK_PATHS)
def test_fence_blocks_learning_supply_chain_writes(path):
    for tool in ('Write', 'Edit', 'MultiEdit'):
        d = classify_action(tool, {'file_path': path})
        assert d.blocked, f"{tool} to {path} must be fenced"


def test_fence_still_allows_ordinary_project_writes():
    for path in (r'C:\repo\server.py', 'docs/PLAN.md',
                 'data/projects/notes.md', 'data/uploads/x.png'):
        d = classify_action('Write', {'file_path': path})
        assert not d.blocked, f"ordinary write false-positive: {path}"


# ── Fence supply-chain self-protection (MC-914, 2026-08-31) ───────────────────
# The hook script is re-read from disk fresh on every tool call — the same
# "hot re-read of a mutable gate" shape as an unfrozen os.environ check, except
# the mutable value is the enforcement code itself. A steward session editing
# its own fence must be blocked, or the very next tool call runs the tampered
# version.

FENCE_SUPPLY_CHAIN_BLOCK_PATHS = [
    'steward/fence.py',
    'steward/core.py',
    'steward/_config.py',
    'steward/__init__.py',
    r'C:\repo\steward\fence.py',
    '/home/me/project/steward/core.py',
]


@pytest.mark.parametrize('path', FENCE_SUPPLY_CHAIN_BLOCK_PATHS)
def test_fence_blocks_edits_to_its_own_source(path):
    for tool in ('Write', 'Edit', 'MultiEdit'):
        d = classify_action(tool, {'file_path': path})
        assert d.blocked, f"{tool} to {path} must be fenced (fence supply chain)"


# ── Install-dir project-boundary guard (2026-09-14, Amit's "update blocked"
#    report) ────────────────────────────────────────────────────────────────
# Root cause: a project's workspace pointed at the running Clayrune install's
# own source tree, so an agent dispatched there did real feature work IN the
# app itself. check_install_dir_write is the defense-in-depth backstop: it
# runs UNCONDITIONALLY from main() (not gated by steward/unattended status
# like the rest of classify_action), because "don't edit a different
# project's install directory" is a project-boundary rule, not a judgment
# call about irreversibility.

def test_write_under_install_dir_from_elsewhere_is_blocked(tmp_path, monkeypatch):
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    install.mkdir()
    other_project = tmp_path / 'other-project'
    other_project.mkdir()
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    d = check_install_dir_write(
        'Write', {'file_path': str(install / 'server.py')},
        session_cwd=str(other_project))
    assert d.blocked
    assert 'install directory' in d.reason


def test_write_under_install_dir_nested_is_blocked(tmp_path, monkeypatch):
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    (install / 'mc' / 'blueprints').mkdir(parents=True)
    other_project = tmp_path / 'other-project'
    other_project.mkdir()
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    d = check_install_dir_write(
        'Edit', {'file_path': str(install / 'mc' / 'blueprints' / 'project_routes.py')},
        session_cwd=str(other_project))
    assert d.blocked


def test_write_relative_path_resolved_against_cwd_is_blocked(tmp_path, monkeypatch):
    """A relative file_path that lands under the install dir once resolved
    against the session's cwd must be caught too, not just absolute paths."""
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    install.mkdir()
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    d = check_install_dir_write(
        'Write', {'file_path': 'server.py'}, session_cwd=str(install))
    # cwd IS the install dir here, so this is the dev-checkout case, not the
    # blocked one — covered by test_dev_checkout_own_project_is_allowed
    # below. This test only pins that relative resolution happens at all:
    assert not d.blocked  # own project == install dir -> allowed


def test_dev_checkout_own_project_is_allowed(tmp_path, monkeypatch):
    """The escape hatch: a session whose OWN project IS the install dir (a
    legitimate source checkout — this box's mission_control project) may
    still edit its own source."""
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    install.mkdir()
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    d = check_install_dir_write(
        'Write', {'file_path': str(install / 'server.py')}, session_cwd=str(install))
    assert not d.blocked


def test_dev_checkout_nested_cwd_is_allowed(tmp_path, monkeypatch):
    """The session's cwd being a SUBDIRECTORY of the install dir (Claude
    launched somewhere under the checkout) still counts as 'own project'."""
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    nested_cwd = install / 'mc'
    nested_cwd.mkdir(parents=True)
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    d = check_install_dir_write(
        'Write', {'file_path': str(install / 'server.py')}, session_cwd=str(nested_cwd))
    assert not d.blocked


def test_write_outside_install_dir_always_allowed(tmp_path, monkeypatch):
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    install.mkdir()
    other_project = tmp_path / 'other-project'
    other_project.mkdir()
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    d = check_install_dir_write(
        'Write', {'file_path': str(other_project / 'notes.md')},
        session_cwd=str(other_project))
    assert not d.blocked


def test_non_write_tools_are_not_checked(tmp_path, monkeypatch):
    import steward.fence as fence_mod
    install = tmp_path / 'install'
    install.mkdir()
    monkeypatch.setattr(fence_mod, '_INSTALL_DIR', install)

    for tool in ('Bash', 'Read', 'Grep', 'Glob'):
        d = check_install_dir_write(
            tool, {'command': 'x', 'file_path': str(install / 'server.py')},
            session_cwd=str(tmp_path / 'elsewhere'))
        assert not d.blocked, f"{tool} must not be checked by this guard"


def test_empty_file_path_is_safe():
    assert not check_install_dir_write('Write', {}).blocked
    assert not check_install_dir_write('Write', {'file_path': ''}).blocked


# ── Same guard, exercised through the real hook subprocess — proves it is
#    UNCONDITIONAL (fires for an ordinary dev session with no steward marker
#    and no unattended trigger_type, unlike the rest of the fence) ─────────

def _run_hook_in(payload: dict, cwd: Path):
    env = dict(os.environ)
    env.pop('CLAUDE_CODE_SESSION_ID', None)
    return subprocess.run(
        [sys.executable, str(FENCE)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
        cwd=str(cwd),
    )


def test_hook_blocks_install_dir_write_for_ordinary_dev_session(tmp_path):
    # No transcript_path (no steward marker) and no CLAUDE_CODE_SESSION_ID
    # (no unattended trigger_type lookup) — the shape of a plain interactive
    # dev session. The install-dir guard must still fire: it is NOT part of
    # the steward/unattended-gated backstop.
    other_project = tmp_path / 'other-project'
    other_project.mkdir()
    target = REPO / 'server.py'  # a real file inside THIS install's own repo
    r = _run_hook_in(
        {'tool_name': 'Write', 'tool_input': {'file_path': str(target)}},
        cwd=other_project,
    )
    assert r.returncode == 2
    assert 'install directory' in r.stderr


def test_hook_allows_install_dir_write_when_cwd_is_the_install_dir():
    # Same write, but the session's own cwd IS this repo — the dev-checkout
    # case (this box's own mission_control project). Must be allowed even
    # with zero steward/unattended signal.
    target = REPO / 'server.py'
    r = _run_hook_in(
        {'tool_name': 'Write', 'tool_input': {'file_path': str(target)}},
        cwd=REPO,
    )
    assert r.returncode == 0


def test_hook_allows_write_outside_install_dir_for_ordinary_session(tmp_path):
    other_project = tmp_path / 'other-project'
    other_project.mkdir()
    r = _run_hook_in(
        {'tool_name': 'Write', 'tool_input': {'file_path': str(other_project / 'notes.md')}},
        cwd=other_project,
    )
    assert r.returncode == 0


def test_fence_does_not_block_unrelated_steward_data_files():
    # Only the enforcement CODE files are protected here; this is a narrow,
    # surgical fix — not a blanket ban on the word "steward" in a path.
    d = classify_action('Write', {'file_path': 'docs/AUTONOMOUS_STEWARD_SCOPE.md'})
    assert not d.blocked


# ── Enabling-construct hardening (2026-09-12) ─────────────────────────────────
# Dave measured (2026-09-11) that the fence matches SPELLING, not ACT: these 8
# shapes all sailed through unmatched even though each performs the exact
# catastrophic act the verb denylist exists to stop. One test per numbered row.

def test_row1_variable_as_git_subcommand_is_blocked():
    d = classify_bash('P=push; git $P --force')
    assert d.blocked, f"row 1 false NEGATIVE: {d}"


def test_row2_variable_as_terraform_subcommand_is_blocked():
    d = classify_bash('T=destroy; terraform $T -auto-approve')
    assert d.blocked, f"row 2 false NEGATIVE: {d}"


def test_row3_concatenated_variable_in_command_position_is_blocked():
    d = classify_bash('G="git pu"; H="sh"; $G$H --force')
    assert d.blocked, f"row 3 false NEGATIVE: {d}"


def test_row4_command_substitution_as_command_word_is_blocked():
    d = classify_bash('cmd=$(printf "git pu%s" "sh"); $cmd --force')
    assert d.blocked, f"row 4 false NEGATIVE: {d}"


def test_row5_xargs_feeding_git_a_blocked_verb_is_blocked():
    d = classify_bash('echo push | xargs -I{} git {} --force')
    assert d.blocked, f"row 5 false NEGATIVE: {d}"


def test_row6_base64_decode_piped_to_interpreter_is_blocked():
    d = classify_bash('echo Z2l0IHB1c2ggLS1mb3JjZQo= | base64 -d | bash')
    assert d.blocked, f"row 6 false NEGATIVE: {d}"


def test_row7_function_definition_site_is_blocked():
    # The successor call (`gp push`, a later turn) is NOT and cannot be
    # blocked by a per-turn matcher in isolation — see the residual-gap test
    # below. Blocking the DEFINITION is what closes this row: the function
    # never gets defined, so the successor call is inert.
    assert classify_bash('gp(){ git "$1" --force; }').blocked
    assert classify_bash('function gp { git "$1" --force; }').blocked


def test_row7_residual_gap_bare_successor_call_is_unblockable_per_turn():
    # Documents the known, unclosed gap named in the brief: in ISOLATION (no
    # memory of the earlier definition turn) a bare `gp push` carries no
    # spelling or construct the fence can act on. This is expected to stay
    # ALLOWED — the mitigation is blocking the definition turn above, not this.
    assert not classify_bash('gp push').blocked


def test_row8_alias_definition_already_blocked_regression_pin():
    # Pre-existing behavior (the literal "git push" substring already matches
    # the verb denylist) — pinned so a future refactor cannot silently drop it.
    assert classify_bash('alias gp="git push"').blocked


def test_row8_residual_gap_bare_successor_call_is_unblockable_per_turn():
    # Same residual gap as row 7: the alias NAME carries no spelling tying it
    # to "git push" in a later, independent turn. Cannot be closed per-turn.
    assert not classify_bash('gp --force origin main').blocked


# ── Enabling-construct hardening — must still ALLOW (false-positive pins) ────
# Command-position expansion is the deny signal; ARGUMENT-position expansion
# (data a command consumes) must keep passing, per the 2026-07-13/07-23
# incidents recorded above the masking code. One test per allow-case named in
# the hardening brief, plus a couple more exercising the new checks directly.

ENABLING_CONSTRUCT_ALLOW_CASES = [
    'echo "$HOME"',
    'foo=$(date)',
    'curl -s localhost:5199/api/skills/search?q=x | python -c "print(1)"',
    'git commit -m "$(cat msg.txt)"',
    'content=$(cat file.txt)',
    'VAR=$(git rev-parse HEAD); echo "$VAR"',
    'base64 -d file.b64 -o out.bin',            # decode w/o piping to an interpreter
    'find . -name "*.log" | xargs -n1 echo',    # xargs feeding a harmless verb
]


@pytest.mark.parametrize('cmd', ENABLING_CONSTRUCT_ALLOW_CASES)
def test_enabling_construct_checks_allow_argument_position_expansion(cmd):
    d = classify_bash(cmd)
    assert not d.blocked, f"false positive: {d.reason!r} for {cmd!r}"


# ── Review pass over the hardening (2026-09-12) ──────────────────────────────
# Two gaps found by probing the merged branch against realistic agent commands.

def test_grep_for_the_word_function_is_not_a_function_definition():
    # The original `function\s+\w+\b` half blocked this — and passed the same
    # search once it was quoted, which is an arbitrary line. A definition has
    # a body; the pattern is anchored on the brace now.
    assert not classify_bash('grep -n function renderChat static/js/app.js').blocked
    assert not classify_bash('grep -rn function static/js/').blocked


def test_function_keyword_form_with_a_body_still_blocks():
    assert classify_bash('function doit { echo hi; }').blocked
    assert classify_bash('function doit() { echo hi; }').blocked


@pytest.mark.parametrize('cmd', [
    'eval "$CMD"',
    'eval $(cat /tmp/payload)',
    'bash -c "$CMD"',
    'sh -c "$(cat /tmp/x)"',
    'python -c "$CODE"',
    'powershell -c "$s"',
    'iex "$payload"',
])
def test_eval_and_dash_c_on_an_expansion_block(cmd):
    d = classify_bash(cmd)
    assert d.blocked, f"missed enabling construct: {cmd!r}"


@pytest.mark.parametrize('cmd', [
    'python -c "print(1)"',
    'bash -c "ls -la"',
    'node -e "console.log(1)"',
    'pytest -c setup.cfg tests/',
])
def test_literal_dash_c_programs_still_pass(cmd):
    d = classify_bash(cmd)
    assert not d.blocked, f"false positive: {d.reason!r} for {cmd!r}"
