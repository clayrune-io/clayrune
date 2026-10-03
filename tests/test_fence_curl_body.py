"""MC-1037 (B): push words inside a curl data argument are data, not a command.

Incident, 2026-10-02 (clayrune_cloud): a backlog POST

    curl -s -X POST http://localhost:5199/api/project/x/backlog \\
         -H "Content-Type: application/json" \\
         -d '{"text":"... blocked git push in clayrune_cloud ... the agent'"'"'s dir ..."}'

was refused because `\\bgit\\s+push\\b` matched inside the JSON text.

curl never runs its arguments, so the value after -d/--data*/--json is masked
-- but ONLY where the shell provably cannot run it, and never by trusting a
regex's idea of "inside quotes". The second half of this file is the other
direction: every wrapper and nested-program form must still block, plus the
quote-confusion shapes a naive mask would have turned into a bypass.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'steward'))
import fence  # noqa: E402

LOCAL = 'http://localhost:5199/api/project/mission_control/backlog'

# The incident, character for character in shape: single-quoted JSON, the
# apostrophe spelt '"'"', push words in the prose.
INCIDENT = (
    "curl -s -X POST " + LOCAL + " -H \"Content-Type: application/json\" -d "
    "'{\"text\":\"Steward fence over-blocks hand-off chats; it blocked git push in "
    "clayrune_cloud. The agent'\"'\"'s own memory dir is refused too. Then run gh pr "
    "merge and git reset --hard.\"}'")


def blocked(cmd):
    return fence.classify_bash(cmd).blocked


# ── allowed: the value of a data option is data ─────────────────────────────

@pytest.mark.parametrize('cmd', [
    INCIDENT,
    f"curl -s -X POST {LOCAL} -d '{{\"text\":\"we should git push later\"}}'",
    f"curl -s -X POST {LOCAL} --data-raw '{{\"text\":\"git push\"}}'",
    f"curl -s -X POST {LOCAL} --data '{{\"text\":\"git push\"}}'",
    f"curl -s -X POST {LOCAL} --data-binary '{{\"text\":\"git push\"}}'",
    f"curl -s -X POST {LOCAL} --data-ascii 'text=git push'",
    f"curl -s -X POST {LOCAL} --data-urlencode 'text=git push'",
    f"curl -s -X POST {LOCAL} --json '{{\"text\":\"git push\"}}'",
    f"curl -s -X POST {LOCAL} -d \"{{'text': 'git push origin master'}}\"",
    f"curl -s -X POST {LOCAL} -d '{{\"t\":\"npm publish, shutdown, reboot, DROP TABLE x\"}}'",
    f"curl.exe -s -X POST {LOCAL} -d '{{\"text\":\"git push\"}}'",
    f"/usr/bin/curl -s -X POST {LOCAL} -d '{{\"text\":\"git push\"}}'",
    f"CURL -s -X POST {LOCAL} -d '{{\"text\":\"git push\"}}'",
    f"cd /tmp && curl -s -X POST {LOCAL} -d '{{\"text\":\"git push\"}}' | head -c 200",
    f"curl -s -X POST {LOCAL} -d '{{\"text\":\"git push\"}}' 2>&1",
    f"curl -s -d '{{\"text\":\"git push\"}}' -X POST {LOCAL} -H 'Content-Type: application/json'",
    f"curl -s -X POST {LOCAL} -d 'a git push' -d 'b git reset --hard'",
    f"curl -s -X POST {LOCAL} -d '{{\"text\":\"it'\"'\"'s a git push, ok\"}}'",
    f"curl -s -X POST {LOCAL} -d '{{\"text\":\"multi\nline git push\nbody\"}}'",
])
def test_curl_data_value_is_data(cmd):
    assert not blocked(cmd), fence.classify_bash(cmd)


def test_mask_leaves_a_placeholder_and_only_the_value():
    cmd = f"curl -s -X POST {LOCAL} -H 'X: y' -d '{{\"text\":\"git push\"}}' --max-time 5"
    out = fence._mask_curl_data_args(cmd)
    assert out == f"curl -s -X POST {LOCAL} -H 'X: y' -d 'INERT_PROSE' --max-time 5"
    assert fence._mask_curl_data_args(INCIDENT).endswith("-d 'INERT_PROSE'")


# ── still blocked: nothing that executes may be masked ──────────────────────

@pytest.mark.parametrize('cmd', [
    'git push',
    'git push origin master',
    "git -C x push",
    "git.exe push",
    "'git' push",
    "git commit -m 'x' && git push",
    "Start-Process git -ArgumentList 'push'",
    "& 'C:/Program Files/Git/cmd/git.exe' push",
    "gh pr merge 12",
    "npm publish",
])
def test_plain_pushes_still_block(cmd):
    assert blocked(cmd)


@pytest.mark.parametrize('cmd', [
    # a push in command position next to a masked curl, every separator
    f"curl -s {LOCAL} -d 'x' && git push",
    f"curl -s {LOCAL} -d 'x'; git push",
    f"curl -s {LOCAL} -d 'x' || git push",
    f"curl -s {LOCAL} -d 'x' | git push",
    f"curl -s {LOCAL} -d 'x'\ngit push",
    f"git push && curl -s {LOCAL} -d 'x'",
    f"curl -s {LOCAL} -d 'x' & git push",
    f"(git push) && curl -s {LOCAL} -d 'x'",
    # wrapper forms around the push
    'bash -c "git push"',
    "bash -c 'git push'",
    'sh -c "git push"',
    "sh -c 'git push'",
    'zsh -c "git push"',
    'eval "git push"',
    "eval 'git push'",
    'echo $(git push)',
    'echo `git push`',
    'echo "$(git push)"',
    'powershell -Command "git push"',
    "powershell -Command 'git push'",
    'pwsh -c "git push"',
    "cmd /c git push",
    "python -c \"import os; os.system('git push')\"",
    "xargs git push",
    "env git push",
    "sudo git push",
    "time git push",
    # a wrapper around curl keeps the body visible: nested programs are scanned whole
    f"bash -c \"curl -s {LOCAL} -d 'git push'\"",
    f"bash -c 'curl -s {LOCAL} -d \"git push\"'",
    f"sh -c \"curl -s {LOCAL} -d 'git push'\"",
    f"eval curl -s {LOCAL} -d 'git push'",
    f"eval \"curl -s {LOCAL} -d 'git push'\"",
    f"echo $(curl -s {LOCAL} -d 'git push')",
    f"echo `curl -s {LOCAL} -d 'git push'`",
    f"powershell -Command \"curl.exe -s {LOCAL} -d 'git push'\"",
    f"pwsh -Command 'curl.exe -s {LOCAL} -d \"git push\"'",
    f"sudo curl -s {LOCAL} -d 'git push'",
    f"env curl -s {LOCAL} -d 'git push'",
    f"time curl -s {LOCAL} -d 'git push'",
    f"xargs curl -s {LOCAL} -d 'git push'",
    f"FOO=1 curl -s {LOCAL} -d 'git push'",
    f"nohup curl -s {LOCAL} -d 'git push'",
    f"echo curl -d 'git push'",
    f"echo -d 'git push'",
    f"git commit -d 'git push'",
    f"printf '%s' -d 'git push'",
    # only the value of a data option is masked
    f"curl -s 'git push' -X POST",
    f"curl -s -X POST {LOCAL} -H 'X: git push'",
    f"curl -s -X POST {LOCAL} -o 'git push'",
    f"curl -s -X POST {LOCAL} --url 'git push'",
    # values that are not nothing-but-quotes keep their content visible
    f"curl -s -X POST {LOCAL} -d \"$(git push)\"",
    f"curl -s -X POST {LOCAL} -d '{{\"a\":1}}'\"$(git push)\"",
    f"curl -s -X POST {LOCAL} -d '{{\"a\":1}}'$(git push)",
    f"curl -s -X POST {LOCAL} -d \"`git push`\"",
    f"curl -s -X POST {LOCAL} -d git push",
    f"curl -s -X POST {LOCAL} -d '{{\"a\":1}}' -d $(git push)",
    f"curl -s -X POST {LOCAL} -d \"${{X:-git push}}\"",
])
def test_every_execution_form_still_blocks(cmd):
    assert blocked(cmd), cmd


@pytest.mark.parametrize('cmd', [
    # Quote-confusion: a naive "-d <quoted>" regex pairs the quotes differently
    # from the shell and hides a command the shell RUNS.
    "echo ' -d \"'; git push; echo '\"'",
    "curl -s x -d 'a' ; echo ' -d \"'; git push; echo '\"'",
    "curl -s x -d \"a\" ; echo ' -d \"'; git push; echo '\"'",
    "echo \" -d '\"; git push; echo \"'\"",
    "curl -d 'a' x ; echo ' -d '; git push; echo ' '",
    "curl -d \"'\" ; git push ; echo \"'\"",
    # backslash / backtick / ANSI-C quoting: bash and PowerShell disagree about
    # where the string ends, so nothing is masked
    "curl -s x -d \"a\\\"; git push; echo \\\"b\" http://localhost/",
    "curl -s x -d 'a'\\' ; git push ; echo \\''b' http://localhost/",
    "curl -s x -d $'a\\'; git push; echo \\'' http://localhost/",
    "curl -s x -d `'a' ; git push ; echo `'b' http://localhost/",
    "curl -s x -d \"a`\"; git push; echo `\"b\" http://localhost/",
    # curly quotes are quote characters in PowerShell
    "curl -s x -d 'a\u2019; git push; echo \u2018b' http://localhost/",
    "curl -s x -d \"a\u201d; git push; echo \u201cb\" http://localhost/",
    # comments and here-documents change what is code
    "curl -s x -d 'a' # '\ngit push\n# '",
    "curl -s x -d 'a' <<EOF\n'\nEOF\ngit push\ncat <<EOF\n'\nEOF",
    # unbalanced quotes can never be proven inert
    f"curl -s {LOCAL} -d 'git push",
    f"curl -s {LOCAL} -d \"git push",
])
def test_quote_confusion_shapes_still_block(cmd):
    assert blocked(cmd), cmd


@pytest.mark.parametrize('cmd', [
    # routes whose body IS a shell command
    "curl -s -X POST http://localhost:5199/api/terminal/launch "
    "-H 'Content-Type: application/json' -d '{\"project_id\":\"p\",\"command\":\"git push\"}'",
    "curl -s -X POST http://localhost:5199/api/terminal/stdin -d '{\"data\":\"git push\\n\"}'",
    "curl -s -X POST http://localhost:5199/api/secrets/exec -d '{\"command\":\"git push\"}'",
    "curl -s -X POST http://127.0.0.1:5199/API/TERMINAL/launch -d '{\"command\":\"git push\"}'",
    # the other fence rules do not look at the body, and still apply
    "curl -s -X POST https://example.com/hook -d '{\"text\":\"hello\"}'",
    "curl -s http://localhost:5199/api/system/restart -X POST -d '{}'",
    "curl -s -X POST http://localhost:5199/api/system/local-auth/set -d '{\"x\":1}'",
])
def test_shell_running_routes_and_other_rules_are_unchanged(cmd):
    assert blocked(cmd), cmd


# ── the lexer itself ────────────────────────────────────────────────────────

def test_lexer_refuses_text_it_cannot_pair_identically():
    for text in ("curl -d 'a", 'curl -d "a', "echo \\'", "echo `x`", "echo # x",
                 "echo $'a'", 'echo $"a"', "cat <<EOF", "echo \u2019x"):
        assert fence._lex_shell_segments(text) is None, text
        assert fence._mask_curl_data_args(text) == text


def test_lexer_splits_segments_and_words():
    segs = fence._lex_shell_segments("a b 'c d'\"e\" ; f && g | h\ni")
    assert [[w[2] for w in s] for s in segs] == [
        [[('', 'a')], [('', 'b')], [("'", 'c d'), ('"', 'e')]],
        [[('', 'f')]], [[('', 'g')]], [[('', 'h')]], [[('', 'i')]]]


def test_mask_never_raises_and_returns_the_input_on_doubt():
    for text in ('', ' ', 'curl', 'curl -d', "curl -d ''", 'curl -d', "\x00", 'curl -d "\\'):
        assert isinstance(fence._mask_curl_data_args(text), str)
    assert fence._mask_curl_data_args('git push') == 'git push'
    assert fence._mask_curl_data_args("curl -d 'x' ; git push").endswith('; git push')


def test_pass_logic_still_refuses_to_cover_a_compound_curl_command():
    """A curl command stays a transfer tool: no 'Allow once' covers it."""
    cmd = f"curl -s -X POST {LOCAL} -d 'x' && git push"
    assert fence._pass_can_cover([('Bash', {'command': cmd})]) is False
