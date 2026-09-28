#!/usr/bin/env python3
"""Steward reversibility FENCE — the load-bearing safety backstop.

An unattended steward runs with `--dangerously-skip-permissions` like every MC
agent, so its ONLY hard constraint is this fence, wired as a PreToolUse hook.
The prompt directive (mc-steward SKILL.md) is the *primary* control — it teaches
the steward to ask before irreversible actions. This fence is the *backstop*: a
denylist of catastrophic / irreversible command shapes that get hard-BLOCKED in
code even if the model tries them anyway. Belt (fence) + suspenders (directive).

DESIGN — asymmetric risk. A false-block (stopping a safe command) merely makes
the steward pause and post a decision-needed item; a false-allow (letting an
irreversible command through) can't be undone. So the fence biases to BLOCK on
the catastrophic verbs and only needs zero false-NEGATIVES on that set — it does
NOT try to catch everything (the directive covers judgment). Default is ALLOW so
reversible work (edits, reads, analysis, localhost API calls) flows unattended.

Self-contained (stdlib only) so it runs as a standalone hook script from any cwd:
    python "<repo>/steward/fence.py"      # reads PreToolUse JSON on stdin
"""
import json
import os
import re
import shlex
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional


class FenceDecision(NamedTuple):
    blocked: bool
    reason: str
    # False = a human's one-shot "Allow once" pass can never let this through
    # (MC-994 follow-up): changes to the guard itself or to what agents load.
    overridable: bool = True


# ── Install-dir write guard (2026-09-14, Amit's "update blocked" report) ────
# Every project that has ever enabled steward gets this exact file copied
# nowhere — the hook entry (steward/core.py:_fence_settings_content) invokes
# THIS script by its path in the Clayrune install that installed the hook, so
# __file__ here always resolves inside that install's own repo root, in every
# project the hook runs for. That makes it a free, tamper-proof handle on
# "the running app's own source tree" with no server round-trip needed.
_INSTALL_DIR = Path(__file__).resolve().parent.parent


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


# Markers that make a destructive path clearly scratch-scoped (→ allow deletes).
_SCRATCH_MARKERS = ('_scratch', 'scratchpad', '/tmp/', '\\temp\\', 'appdata/local/temp',
                    'appdata\\local\\temp')

# Hosts that count as "local" — the steward's own reporting/API calls target
# these and must pass the fence.
_LOCAL_HOSTS = ('localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]')

# Irreversible command shapes → BLOCK. Each entry: (compiled regex, human reason).
# Matched case-insensitively against the WHOLE command string (so chained
# commands like `cd x && git push` are still caught).
_BLOCK_PATTERNS = [
    (re.compile(r'\bgit\s+push\b', re.I),
     "git push writes to a shared remote (irreversible once others fetch)"),
    (re.compile(r'\bgit\s+reset\s+--hard\b', re.I),
     "git reset --hard discards working-tree state destructively"),
    (re.compile(r'\bgit\s+clean\b\s+-\w*f', re.I),
     "git clean -f deletes untracked files irrecoverably"),
    (re.compile(r'\bgit\s+(tag\s+-d|push\s+.*--delete|branch\s+-D)\b', re.I),
     "destructive git ref deletion"),
    # Publishes / deploys / releases.
    (re.compile(r'\bnpm\s+publish\b', re.I), "npm publish is an irreversible release"),
    (re.compile(r'\btwine\s+upload\b', re.I), "twine upload publishes a package"),
    (re.compile(r'\bdocker\s+push\b', re.I), "docker push publishes an image"),
    (re.compile(r'\bpip\s+.*\bupload\b', re.I), "package upload"),
    (re.compile(r'\bgh\s+release\s+(create|edit|delete)\b', re.I), "GitHub release mutation"),
    (re.compile(r'\bgh\s+(pr|issue|repo)\s+(create|edit|merge|close|delete|comment)\b', re.I),
     "GitHub write (leaves this box, notifies others)"),
    (re.compile(r'\bgh\s+gist\s+(create|edit|delete)\b', re.I),
     "GitHub gist write (leaves this box, notifies others)"),
    (re.compile(r'\bgh\s+api\b.*-X\s*(POST|PUT|PATCH|DELETE)', re.I),
     "GitHub API mutation"),
    # PowerShell-only publish/release surfaces (Bash equivalents already above).
    (re.compile(r'\bPublish-Module\b|\bPublish-Script\b', re.I),
     "PowerShell Gallery publish is an irreversible release"),
    # Raw network sockets: unrestricted send/receive with no verb shape the
    # fence can bound (unlike curl/wget, nc has no request-method to check).
    (re.compile(r'\b(nc|ncat|netcat)\b', re.I),
     "raw network socket tool (nc/ncat/netcat), unrestricted send/receive is out of steward scope"),
    # Cloud spend / provisioning.
    (re.compile(r'\b(gcloud|aws|az)\b[\s\S]*\b(create|delete|deploy|apply|update|run|start|stop|remove|rm|set|put|destroy)\b', re.I),
     "cloud provisioning/spend (gcloud/aws/az mutation)"),
    (re.compile(r'\bterraform\s+(apply|destroy)\b', re.I), "terraform state mutation"),
    (re.compile(r'\bkubectl\s+(apply|delete|create|scale|rollout)\b', re.I), "kubernetes mutation"),
    # Schema / migrations.
    (re.compile(r'\balembic\s+(upgrade|downgrade)\b', re.I), "database migration"),
    (re.compile(r'\b\w*migrate\b\s+(up|down|deploy|latest)\b', re.I), "database migration"),
    (re.compile(r'\bDROP\s+(TABLE|DATABASE|SCHEMA)\b', re.I), "destructive SQL"),
    (re.compile(r'\bTRUNCATE\s+TABLE\b', re.I), "destructive SQL"),
    # Restarting Clayrune itself (separately human-gated).
    (re.compile(r'/api/system/restart\b', re.I), "server restart is human-gated"),
    # System-destructive.
    (re.compile(r'\bmkfs\b|\bdd\s+if=', re.I), "disk-destructive operation"),
    (re.compile(r'\bshutdown\b|\breboot\b', re.I), "host power operation"),
]

# KNOWN LIMIT, documented rather than chased (2026-09-27): DNS lookups
# (nslookup/dig/ping/Resolve-DnsName) are NOT blocked. A DNS query can carry a
# small amount of exfiltrated data in the queried name, but blocking DNS
# outright would break ordinary connectivity checks the steward legitimately
# needs, and a nslookup-based exfil channel is far lower-bandwidth than the
# HTTP/scp/rsync sends this module already blocks. Not in scope for this
# denylist; a defense against it would need to live at the network layer,
# not a command-text fence.


# ── Global-option / disguised-program bypass fix (2026-09-28) ────────────────
# Verified live: every _BLOCK_PATTERNS regex above only matches when the
# VERB directly follows the bare program name (`\bgit\s+push\b`). Any of the
# following put something else between them, or spelled the program token
# differently, and sailed through unmatched:
#   • a global option before the verb — `git -C ../other push`, `docker
#     --context prod push img`, `terraform -chdir=infra destroy`, `kubectl
#     -n prod delete pod x`, `gh -R o/r release create v1`
#   • a disguised program token — `git.exe push`, `npm.cmd publish`, a
#     quoted/path-prefixed interpreter (`& 'C:/Program Files/Git/cmd/git.exe'
#     push`), or a PowerShell `Start-Process` wrapper around either
#
# Fix shape: normalise a COPY of the (already inert-prose-masked) command —
# unwrap Start-Process, strip quoting/path/.exe-suffix disguises off the
# known program tokens, then eat recognised global options sitting between
# a program name and whatever follows it — and run _BLOCK_PATTERNS against
# BOTH the original masked command and this normalised copy (classify_bash
# below ORs the two). This only ADDS a second pass; it never replaces or
# loosens the original pass, so nothing that blocked before this fix can
# stop blocking because of it — a normalisation bug just fails to strip
# something, which leaves the original check as the fallback.
_KNOWN_PROGRAMS = ('git', 'npm', 'docker', 'terraform', 'kubectl', 'gh', 'twine', 'pip')
_PROG_EXT_RE = re.compile(r'\.(?:exe|cmd|bat|ps1)$', re.I)


def _program_basename(token: str) -> Optional[str]:
    """`token` (a bare name, a path, or a path with a .exe/.cmd/.bat/.ps1
    suffix) -> the bare program name if its basename names one of
    _KNOWN_PROGRAMS, else None."""
    base = re.split(r'[\\/]', token)[-1]
    base = _PROG_EXT_RE.sub('', base)
    return base if base.lower() in _KNOWN_PROGRAMS else None


# A quoted token in COMMAND POSITION (string start, or right after a `;`,
# `&`/call-operator, `|`, `(`, or newline) — `& 'C:/.../git.exe' push` and a
# bare `"C:/.../git.exe" push origin main` both invoke the quoted path as the
# program. A quoted token elsewhere (an argument, e.g. a commit message) is
# untouched — it never matches this position-anchored pattern.
_QUOTED_PROG_TOKEN_RE = re.compile(
    r'''(?P<pre>^|[;&|(\n])(?P<ws>\s*)(?P<q>['"])(?P<body>[^'"]*)(?P=q)''')


def _unwrap_quoted_program_tokens(cmd: str) -> str:
    def repl(m):
        prog = _program_basename(m.group('body'))
        return m.group('pre') + m.group('ws') + prog if prog else m.group(0)
    return _QUOTED_PROG_TOKEN_RE.sub(repl, cmd)


# An unquoted disguised token — `git.exe`, `cmd/git.exe`, `npm.cmd` — same
# command-position anchor as the quoted form above, plus a trailing `\b` so
# a program name that is merely a SUBSTRING of a longer word (`ghost`,
# `legitimate`) can never match.
_BARE_PROG_TOKEN_RE = re.compile(
    r'''(?P<pre>^|[\s;&|(\n])(?P<path>(?:[^\s'"]*[\\/])?)'''
    r'''(?P<prog>git|npm|docker|terraform|kubectl|gh|twine|pip)'''
    r'''(?:\.exe|\.cmd|\.bat|\.ps1)?\b''', re.I)


def _unwrap_bare_disguised_tokens(cmd: str) -> str:
    return _BARE_PROG_TOKEN_RE.sub(lambda m: m.group('pre') + m.group('prog'), cmd)


# `Start-Process git -ArgumentList 'push origin main' -Wait` and
# `Start-Process -FilePath git.exe -ArgumentList 'push','origin','main'` both
# run `git push origin main` but name neither "git push" nor "git" then
# "push" adjacently anywhere in the line — reconstruct the plain invocation
# text so the rest of this pipeline (disguise-stripping, global-option
# stripping, _BLOCK_PATTERNS) can see it the normal way. Stops at `;`/`|`/
# newline, the same rough segment boundary _SHELL_SPLIT_RE uses elsewhere.
_START_PROCESS_RE = re.compile(r'\bStart-Process\b(?P<rest>[^\n;|]*)', re.I)
_SP_FILEPATH_RE = re.compile(
    r'''-FilePath\s+(?P<q>['"]?)(?P<val>[^\s'"]+)(?(q)(?P=q))''', re.I)
_SP_POSITIONAL_RE = re.compile(
    r'''^\s*(?!-)(?P<q>['"]?)(?P<val>[^\s'"]+)(?(q)(?P=q))''')
_SP_ARGLIST_RE = re.compile(r'-ArgumentList\s+(?P<val>.+?)(?=\s+-\w|$)', re.I)


def _start_process_program(rest: str) -> Optional[str]:
    m = _SP_FILEPATH_RE.search(rest)
    if m:
        return m.group('val')
    m = _SP_POSITIONAL_RE.match(rest)
    return m.group('val') if m else None


def _start_process_args(rest: str) -> str:
    m = _SP_ARGLIST_RE.search(rest)
    if not m:
        return ''
    parts = []
    for piece in re.split(r'\s*,\s*', m.group('val').strip()):
        if len(piece) >= 2 and piece[0] == piece[-1] and piece[0] in ('"', "'"):
            piece = piece[1:-1]
        if piece:
            parts.append(piece)
    return ' '.join(parts)


def _unwrap_start_process(cmd: str) -> str:
    def repl(m):
        rest = m.group('rest')
        prog = _start_process_program(rest)
        if not prog:
            return m.group(0)
        args = _start_process_args(rest)
        return f'{prog} {args}'.strip()
    return _START_PROCESS_RE.sub(repl, cmd)


# Global options recognised BETWEEN a program name and its verb, so
# `git -C ../other push` normalises to `git push` the same way `git push`
# already reads. `_opt_value` accepts both `--opt value` and `--opt=value`
# (and the short-flag `-o value` form), for options that consume a separate
# token when not written with `=`. Everything else (bare boolean flags, or
# any option written with `=`) is handled by the generic fallback below.
# Deliberately generous about which spellings are accepted — over-matching
# an option here can only cause EXTRA stripping (still fed through the same
# downstream _BLOCK_PATTERNS check), never a new ALLOW.
_OPT_VAL = r'''(?:"[^"]*"|'[^']*'|\S+)'''


def _opt_value(*names: str) -> str:
    alt = '|'.join(re.escape(n) for n in names)
    return rf'(?:{alt})(?:=(?:{_OPT_VAL})|\s+(?:{_OPT_VAL}))'


# ── Residual global-option bypass fix (2026-09-28, Quill) ────────────────────
# The NAMED lists below (_GIT_OPTS etc.) only stripped options someone had
# already thought to enumerate. Anything else — `--no-optional-locks`,
# `--literal-pathspecs`, `--paginate`, a brand-new flag added in a future git
# release — put a token between the program and its verb that the old
# `_global_opt_strip_re` did not recognise, so the verb never lined up with
# `_BLOCK_PATTERNS`'s `\bgit\s+push\b` and the command sailed through. Live:
# `git --no-optional-locks push origin HEAD:refs/heads/main` exited 0 through
# an armed fence.
#
# Fix: skip EVERY dash-led token between the program and the verb, not just
# named ones. `_GENERIC_DASH_OPT` matches any `-x`/`--xxx` token, with an
# optional `=value` suffix already attached (no separate token consumed) —
# that alone covers every one of the 11 reported spellings, all of which are
# boolean-shaped or self-contained with `=`. The one thing a fully generic
# token walker cannot know on its own is which options take their value as a
# SEPARATE token when not written with `=` (`-C ../x`, not `-C=../x`) — for
# those, eating the option but leaving its value behind would misidentify the
# value token as the verb. That set is still named per tool below
# (`_GIT_VALUE_OPTS` etc.) and tried FIRST in the alternation, so it wins over
# the generic fallback; the fallback only ever handles what the named list
# doesn't. Over-matching here still only causes EXTRA stripping fed through
# the same downstream check — never a new ALLOW — so the generic fallback is
# safe to be this broad.
_GENERIC_DASH_OPT = rf'--?[A-Za-z][\w-]*(?:=(?:{_OPT_VAL}))?'


def _opt_value_or_generic(value_opts_alt: str) -> str:
    """Named value-consuming options (space or `=` form, consuming a
    separate token when not written with `=`) tried first, then any other
    dash-led token (own `=value` only, no separate-token consumption).
    `value_opts_alt` is already a complete `_opt_value(...)` pattern — it
    consumes its own value, so it is NOT wrapped in another value suffix
    here (that would require the value to appear twice)."""
    return rf'{value_opts_alt}|{_GENERIC_DASH_OPT}'


# Options that take their value as a SEPARATE token when not written with
# `=` — these must be named, or the fix above would swallow the value as if
# it were the next option and misread it (or an innocent word) as the verb.
# `--exec-path` deliberately excluded: git only accepts it as `--exec-path`
# (bare, prints the path) or `--exec-path=<path>`, never a separate-token
# form (checked against git 2.51's parse-options usage) — the generic
# fallback already handles both of those shapes via its own `=value` suffix.
_GIT_VALUE_OPTS = _opt_value('-C', '-c', '--git-dir', '--work-tree',
                              '--namespace', '--config-env')
_NPM_VALUE_OPTS = _opt_value('--prefix', '--workspace', '-w')
_DOCKER_VALUE_OPTS = _opt_value('--context', '-c', '--host', '-H',
                                 '--config', '--log-level')
_TERRAFORM_VALUE_OPTS = _opt_value('-chdir')
_KUBECTL_VALUE_OPTS = _opt_value('-n', '--namespace', '--context',
                                  '--kubeconfig', '-s', '--server',
                                  '--cluster', '--user')
_GH_VALUE_OPTS = _opt_value('-R', '--repo')

_GIT_OPTS = _opt_value_or_generic(_GIT_VALUE_OPTS)
_NPM_OPTS = _opt_value_or_generic(_NPM_VALUE_OPTS)
_DOCKER_OPTS = _opt_value_or_generic(_DOCKER_VALUE_OPTS)
_TERRAFORM_OPTS = _opt_value_or_generic(_TERRAFORM_VALUE_OPTS)
_KUBECTL_OPTS = _opt_value_or_generic(_KUBECTL_VALUE_OPTS)
_GH_OPTS = _opt_value_or_generic(_GH_VALUE_OPTS)


def _global_opt_strip_re(prog: str, opts_alt: str):
    return re.compile(rf'\b(?P<prog>{re.escape(prog)})\b(?:\s+(?:{opts_alt}))*(?=\s)', re.I)


_GLOBAL_OPT_STRIP_PATTERNS = (
    _global_opt_strip_re('git', _GIT_OPTS),
    _global_opt_strip_re('npm', _NPM_OPTS),
    _global_opt_strip_re('docker', _DOCKER_OPTS),
    _global_opt_strip_re('terraform', _TERRAFORM_OPTS),
    _global_opt_strip_re('kubectl', _KUBECTL_OPTS),
    _global_opt_strip_re('gh', _GH_OPTS),
)


def _strip_global_options(cmd: str) -> str:
    out = cmd
    for pat in _GLOBAL_OPT_STRIP_PATTERNS:
        out = pat.sub(lambda m: m.group('prog'), out)
    return out


def _normalize_for_block_patterns(cmd: str) -> str:
    """Best-effort normalised copy of `cmd` for the _BLOCK_PATTERNS pass only
    (see classify_bash) — un-disguises the program token and collapses
    global options so a verb that directly follows the program name in the
    ORIGINAL patterns' sense also directly follows it here."""
    out = _unwrap_start_process(cmd)
    out = _unwrap_quoted_program_tokens(out)
    out = _unwrap_bare_disguised_tokens(out)
    out = _strip_global_options(out)
    return out


# Destructive-delete verbs. Blocked UNLESS the command is clearly scratch-scoped.
_DELETE_PATTERNS = [
    re.compile(r'\brm\s+-\w*[rf]', re.I),       # rm -r / -f / -rf
    re.compile(r'\brmdir\b', re.I),
    re.compile(r'\bRemove-Item\b', re.I),
    re.compile(r'(^|[\s&|;])del\s', re.I),
    re.compile(r'\brd\s+/s', re.I),
]


# ── Inert-prose masking (false-positive precision fix, 2026-07-16) ───────────
# The block patterns match the WHOLE command string, so PROSE used to trip
# them: the steward could not `git commit` the wake-lock feature because the
# commit message legitimately said "shutdown" (real incident, 2026-07-13
# DECISION NEEDED note — the #1-priority feature sat uncommitted for days).
#
# Masking replaces spans that are PROVABLY inert data with a placeholder
# before classification. "Provably inert" means the shell cannot execute the
# span's content:
#   • a quoted argument directly following -m/--message — an argument to a
#     flag is data, never a command. Double-quoted args are masked only when
#     they contain no substitution tokens ($( or `), which WOULD execute.
#   • a quoted-delimiter heredoc body (<<'EOF' / <<"EOF" — quoting the
#     delimiter disables expansion) and a PowerShell LITERAL here-string
#     (@'...'@) — both are raw data, UNLESS the heredoc's own header line
#     mentions a shell/interpreter (bash <<'EOF' executes its stdin), in
#     which case that span is left unmasked and the patterns see everything.
#
# Command-position text is NEVER masked, so the deny-scope is unchanged:
# `git commit -m "x" && shutdown` still blocks on the unmasked tail, and
# `git commit -m "$(shutdown)"` is never masked at all. False-negative risk
# stays where the design puts it: zero on the catastrophic set.
_MASK_TOKEN = 'INERT_PROSE'

_MSG_ARG_RE = re.compile(
    r"""((?:^|\s)(?:-m|--message)(?:=|\s+))(?P<q>['"])(?P<body>(?:\\.|[^\\])*?)(?P=q)""",
    re.S)

_HEREDOC_RE = re.compile(
    r"""(?P<head>[^\n]*<<-?\s*(?P<q>['"])(?P<tag>\w+)(?P=q)[^\n]*\n)"""
    r"""(?P<body>.*?\n)(?P<term>[ \t]*(?P=tag)[ \t]*(?=\n|$|[)"';]))""",
    re.S)

_PS_HERESTRING_RE = re.compile(
    r"(?P<head>[^\n]*@'[ \t]*\n)(?P<body>.*?)(?P<term>\n'@)", re.S)

# Anything that can turn stdin/data back into execution.
_INTERPRETER_RE = re.compile(
    r'\b(bash|sh|zsh|dash|ksh|pwsh|powershell|python\w*|node|perl|ruby|'
    r'eval|source|cmd|iex|invoke-expression)\b', re.I)

# A grep/rg SEARCH PATTERN is data the tool consumes, never text the shell
# executes — `grep -rn "git push" docs/` (searching this repo's OWN source,
# including this file's comments, for the literal string) is not a git push
# and must not block on it (false-positive incident, 2026-09-27). Left
# unmasked, like the -m/--message body, when the pattern itself carries a
# substitution token that WOULD execute.
_GREP_PATTERN_RE = re.compile(
    r"""(?P<head>\b(?:grep|egrep|fgrep|rg)\b(?:\s+-{1,2}[\w-]+(?:=\S+)?)*\s+)"""
    r"""(?P<q>['"])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)""",
    re.S | re.I)

# A `python -c '<literal>'` program's own body is likewise data the fence
# cannot execute — UNLESS the literal itself hands off to a shell (subprocess/
# os.system/eval) or sends a network request directly (requests/urlopen/
# httpx), in which case it is left unmasked so classify_bash's own checks
# (curl-text-inside-a-python-string false positive, 2026-09-27) and
# _python_network_send below can still see it.
_PY_C_ARG_RE = re.compile(
    r"""(?P<head>\bpython\w*\b(?:\s+-{1,2}[\w-]+(?:=\S+)?)*\s+-c\s+)"""
    r"""(?P<q>['"])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)""",
    re.S | re.I)

_PY_EXEC_OR_SEND_RE = re.compile(
    r'\b(subprocess\.\w+\s*\(|os\.system\s*\(|os\.popen\s*\(|os\.exec\w*\s*\(|'
    r'eval\s*\(|exec\s*\(|requests\.\w+\s*\(|urlopen\s*\(|httpx\.\w+\s*\()', re.I)

# An unresolved shell expansion inside the literal (`python -c "$CODE"`) means
# the program text is a runtime VALUE, not the string on this line — the
# existing `_DASH_C_EXPANSION_RE` enabling-construct check needs to still see
# it, so it must never be masked away as "inert".
_PY_C_HAS_EXPANSION_RE = re.compile(r'\$\{?\w+|\$\(|`')


def _mask_inert_prose(cmd: str) -> str:
    """Replace provably-inert data spans with a placeholder. Best-effort and
    conservative: any span we cannot PROVE inert is left untouched (fails
    toward blocking, never toward allowing)."""
    def _msg_repl(m):
        if m.group('q') == '"' and ('$(' in m.group('body') or '`' in m.group('body')):
            return m.group(0)          # substitution could execute — leave it
        return f"{m.group(1)}{m.group('q')}{_MASK_TOKEN}{m.group('q')}"

    def _grep_repl(m):
        if '$(' in m.group('body') or '`' in m.group('body'):
            return m.group(0)          # substitution could execute — leave it
        return f"{m.group('head')}{m.group('q')}{_MASK_TOKEN}{m.group('q')}"

    def _py_c_repl(m):
        body = m.group('body')
        if _PY_EXEC_OR_SEND_RE.search(body) or _PY_C_HAS_EXPANSION_RE.search(body):
            return m.group(0)          # shells out, sends, or is a runtime value — leave it visible
        return f"{m.group('head')}{m.group('q')}{_MASK_TOKEN}{m.group('q')}"

    def _block_repl(m):
        # If the consuming line mentions an interpreter, the "data" may be
        # executed (bash <<'EOF') — leave the whole span for the patterns.
        if _INTERPRETER_RE.search(m.group('head')):
            return m.group(0)
        return f"{m.group('head')}{_MASK_TOKEN}\n{m.group('term')}"

    out = _MSG_ARG_RE.sub(_msg_repl, cmd)
    out = _GREP_PATTERN_RE.sub(_grep_repl, out)
    out = _PY_C_ARG_RE.sub(_py_c_repl, out)
    out = _HEREDOC_RE.sub(_block_repl, out)
    out = _PS_HERESTRING_RE.sub(_block_repl, out)
    return out


_SHELL_SPLIT_RE = re.compile(r'&&|\|\||[|;\n]')


_NET_TOOL_RE = re.compile(
    r'\b(curl|wget|http|invoke-webrequest|invoke-restmethod|iwr|irm)\b', re.I)


_MUTATING_VERBS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_NET_HEAD_RE = re.compile(
    r'(?:^|[\\/])(curl|wget|http|https|invoke-webrequest|invoke-restmethod|iwr|irm)'
    r'(?:\.exe)?$', re.I)
# curl short options that take a value (the rest are flags), so a cluster
# like `-sdfixture` is walked letter by letter: `s` flag, `d` + "fixture".
_CURL_SHORT_ARG = set('dHXouFAebcTwmxErCyYzQUtDPK')
_CURL_LONG_ARG = {
    '--data', '--data-raw', '--data-binary', '--data-urlencode', '--data-ascii',
    '--json', '--header', '--request', '--output', '--user', '--form',
    '--form-string', '--user-agent', '--referer', '--cookie', '--cookie-jar',
    '--upload-file', '--write-out', '--max-time', '--connect-timeout', '--cacert',
    '--cert', '--key', '--oauth2-bearer', '--proxy', '--resolve', '--url',
    '--range', '--dump-header', '--limit-rate', '--max-filesize', '--config',
    '--retry', '--retry-delay', '--retry-max-time', '--output-dir', '--trace',
    '--trace-ascii', '--stderr', '--interface', '--connect-to', '--max-redirs',
}
_CURL_BODY_LONG = {'--data', '--data-raw', '--data-binary', '--data-urlencode',
                   '--data-ascii', '--json'}
_CURL_UPLOAD_LONG = {'--form', '--form-string', '--upload-file'}


def _net_tokens(seg: str) -> list:
    try:
        lex = shlex.shlex(seg, posix=False)
        lex.whitespace_split = True
        lex.commenters = ''
        toks = list(lex)
    except ValueError:
        toks = seg.split()
    return [tk[1:-1] if len(tk) >= 2 and tk[0] == tk[-1] and tk[0] in '"\'' else tk
            for tk in toks]


def _curl_mutates(args: list) -> bool:
    method = None
    body = upload = get = False
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok.startswith('--') and len(tok) > 2:
            name, eq, val = tok.partition('=')
            if name in _CURL_LONG_ARG and not eq:
                val = args[i] if i < len(args) else ''
                i += 1
            if name == '--request':
                method = val
            elif name in _CURL_BODY_LONG:
                body = True
            elif name in _CURL_UPLOAD_LONG:
                upload = True
            elif name == '--get':
                get = True
            continue
        if tok.startswith('-') and len(tok) > 1:
            letters = tok[1:]
            for j, ch in enumerate(letters):
                if ch == 'G':
                    get = True
                if ch not in _CURL_SHORT_ARG:
                    continue
                val = letters[j + 1:]
                if not val:
                    val = args[i] if i < len(args) else ''
                    i += 1
                if ch == 'X':
                    method = val
                elif ch == 'd':
                    body = True
                elif ch in 'FT':
                    upload = True
                break
    if method is not None:
        return method.upper() in _MUTATING_VERBS
    return upload or (body and not get)


def _wget_mutates(args: list) -> bool:
    method = None
    body = False
    for i, tok in enumerate(args):
        name, eq, val = tok.partition('=')
        if name == '--method':
            method = val if eq else (args[i + 1] if i + 1 < len(args) else '')
        elif name in ('--post-data', '--post-file', '--body-data', '--body-file'):
            body = True
    if method is not None:
        return method.upper() in _MUTATING_VERBS
    return body


def _ps_web_mutates(args: list) -> bool:
    method = None
    body = False
    for i, tok in enumerate(args):
        if not tok.startswith('-'):
            continue
        name, colon, val = tok[1:].partition(':')
        name = name.lower()
        if name in ('method', 'custommethod'):
            method = val if colon else (args[i + 1] if i + 1 < len(args) else '')
        elif name in ('body', 'infile', 'form'):
            body = True
    if method is not None:
        return method.upper() in _MUTATING_VERBS
    return body


def _httpie_mutates(args: list) -> bool:
    pos = [a for a in args if not a.startswith('-')]
    if pos and pos[0].upper() in _MUTATING_VERBS:
        return True
    if any(a in ('-f', '--form', '--raw') or a.startswith('--raw=') for a in args):
        return True
    return any(re.match(r'^[\w.-]+(:=@|:=|=@|=|@)', a) for a in pos[1:])


def _segment_mutates(seg: str) -> bool:
    """True when this shell segment runs a named HTTP tool that sends a
    mutating request. Parsed from argv with each tool's own option rules
    (Fenn's reviews #6-#7, N6-N8): an option counts as a method only when it
    IS the tool's method option, so `--output`, `-o post.json` or
    `-OutFile delete.txt` never read as sends, and curl short clusters
    (`-sdfixture`) are walked the way curl walks them. These are the tools'
    documented forms; the 2026-09-12 position against chasing evasions
    still stands. A quoted argument that itself names a tool (`bash -c
    "curl -X POST ..."`) is judged as its own segment."""
    toks = _net_tokens(seg)
    for idx, tok in enumerate(toks):
        m = _NET_HEAD_RE.search(tok)
        if not m:
            if _NET_TOOL_RE.search(tok) and ' ' in tok and _segment_mutates(tok):
                return True
            continue
        head = m.group(1).lower()
        args = toks[idx + 1:]
        if head == 'curl':
            return _curl_mutates(args)
        if head == 'wget':
            return _wget_mutates(args)
        if head in ('http', 'https'):
            return _httpie_mutates(args)
        return _ps_web_mutates(args)
    return False


def _touches_nonlocal_network(cmd: str) -> FenceDecision:
    """Block external network SENDS (mutating HTTP verbs / uploads to a non-local
    host). Reads (plain GET) and anything targeting localhost are allowed.

    Scoped PER shell segment (false-positive precision fix, 2026-07-23): the
    mutating-flag short forms (-d/-F/-T) also name flags of OTHER tools
    (`cut -d','`, `grep -F`, `tar -T`), so checking them against the whole
    command made a GET download read as a mutating send whenever such a flag
    appeared anywhere on the line (real incident: `curl ... -o installer.exe;
    sha256sum ... | cut -d' '`). The mutating flag must now sit in the SAME
    segment as the curl/wget/http invocation.

    The browser-API check is deliberately NOT inside the mutating gate
    (2026-09-10): Clayrune's own HTTP API is the browser capability, so
    `curl localhost:5199/api/browser/launch` + `/read` handed a steward any
    hostile page straight past the mcp__browser__* block. Verb-shape must not
    be a way round it, and neither must segment position."""
    for seg in _SHELL_SPLIT_RE.split(cmd):
        if not _NET_TOOL_RE.search(seg):
            continue
        if re.search(r'/api/browser/(launch|read|input|navigate)', seg, re.I):
            return FenceDecision(True, "autonomous web browsing is out of steward scope - "
                                       "the browser HTTP API is the same capability as the "
                                       "browser MCP tools, which are blocked")
        mutating = _segment_mutates(seg)
        if not mutating:
            continue
        if any(h in seg.lower() for h in _LOCAL_HOSTS):
            continue  # steward's own API calls
        return FenceDecision(True, "external network send (mutating HTTP to a non-local host)")
    return FenceDecision(False, '')


# ── python -c network sends (miss fix, 2026-09-27) ───────────────────────────
# `curl`/`wget`/Invoke-WebRequest are covered above by name, but the exact
# same POST-shaped send done from inline Python (`python -c
# "requests.post(url, data=...)"`) named none of those tools, so it sailed
# through unmatched. Scoped to a `-c` literal, same discipline as the
# curl/wget check: the fence can see what's ON THE LINE, not what a script
# FILE does.
_PY_NETWORK_SEND_RE = re.compile(
    r'\b(?:requests\.(?:post|put|patch|delete)\s*\(|'
    r'requests\.request\s*\(\s*[\'"](?:POST|PUT|PATCH|DELETE)[\'"]|'
    r'urlopen\s*\([^)]*\bdata\s*=|'
    r'Request\s*\([^)]*\bdata\s*=|'
    r'httpx\.(?:post|put|patch|delete)\s*\()', re.I)

def _python_network_send(cmd: str) -> FenceDecision:
    """Block a `python -c` literal that makes a POST/PUT/PATCH/DELETE-shaped
    call (requests/urllib/httpx) to a non-local host.

    Extracted via `_PY_C_ARG_RE` (the same quote-respecting match used to
    mask inert `-c` literals above) rather than `_SHELL_SPLIT_RE`'s naive
    per-segment split: a real literal commonly has its own internal `;`
    (`"import requests; requests.post(...)"`), which the segment splitter
    would cut apart from the leading `python -c`, losing the match."""
    for m in _PY_C_ARG_RE.finditer(cmd):
        body = m.group('body')
        if not _PY_NETWORK_SEND_RE.search(body):
            continue
        if any(h in body.lower() for h in _LOCAL_HOSTS):
            continue
        return FenceDecision(True, "python -c makes a network POST/PUT/PATCH/DELETE-shaped "
                                    "call (requests/urllib/httpx) to a non-local host")
    return FenceDecision(False, '')


# ── scp/rsync to a remote host (miss fix, 2026-09-27) ─────────────────────────
_SCP_RSYNC_RE = re.compile(r'\b(scp|rsync)\b', re.I)
# `[user@]host:` where host is 2+ chars, so a Windows drive letter (`C:\...`)
# never matches (single char) and a bare local path never does either (no
# trailing colon).
_REMOTE_TARGET_TOKEN_RE = re.compile(r'^(?:[\w.\-]+@)?([\w.\-]{2,}):(?!\\)')


def _scp_rsync_remote(cmd: str) -> FenceDecision:
    """Block scp/rsync whose target names a remote host — data leaves this
    box the same way a curl/wget upload does, just via a different tool."""
    for seg in _SHELL_SPLIT_RE.split(cmd):
        if not _SCP_RSYNC_RE.search(seg):
            continue
        if any(h in seg.lower() for h in _LOCAL_HOSTS):
            continue
        if re.search(r'rsync://|ssh://', seg, re.I):
            return FenceDecision(True, "rsync to a remote host (data leaves this box)")
        for tok in seg.split():
            if _REMOTE_TARGET_TOKEN_RE.match(tok):
                tool = 'scp' if re.search(r'\bscp\b', seg, re.I) else 'rsync'
                return FenceDecision(True, f"{tool} to a remote host (data leaves this box)")
    return FenceDecision(False, '')


# ── Enabling-construct detection (hardening, 2026-09-12) ─────────────────────
# Dave measured (2026-09-11, fence.classify_bash run directly) that the fence
# regex-matches raw TEXT, so it blocks a SPELLING, not an ACT: `P=push; git $P
# --force`, `T=destroy; terraform $T -auto-approve`, `$G$H --force` (concat'd
# var expansion), `$(printf ...)` as the command word, `xargs` handing a
# blocked verb its argument from stdin, and `base64 -d | bash` all sailed
# through unmatched even though each performs exactly the catastrophic act the
# verb regexes exist to stop. Enumerating more spellings cannot win that race
# — the fix is to block the small, closed set of CONSTRUCTS that let a
# command's identity be decided at runtime instead of read off the line: a
# shell function definition, a decode-then-execute pipeline, xargs handing a
# dangerous verb/interpreter an argument built from stdin, and a variable or
# substitution sitting in COMMAND POSITION (the word the shell would actually
# try to run, or the word right after a verb the fence denies — `git $P` lets
# $P silently BE the subcommand).
#
# Command position only. `echo "$HOME"`, `foo=$(date)`, `content=$(cat f)`,
# and `git commit -m "$(cat msg.txt)"` all put an expansion in ARGUMENT
# position — data the command consumes, never text the shell tries to
# execute — and must keep passing, per the 2026-07-13 / 2026-07-23
# false-positive incidents recorded above. Scoped per shell segment via the
# existing _SHELL_SPLIT_RE, same discipline as _touches_nonlocal_network.

# Both bash spellings, but ALWAYS anchored on the opening brace. Without that
# anchor the `function\s+\w+` half blocked `grep -n function renderChat app.js`
# — searching a JS codebase for the word "function" is one of the most common
# things an agent does here, and it only passed when the pattern happened to be
# quoted (the quote fails the leading-char class), which is an arbitrary line to
# draw. A real definition always has a body.
_FUNC_DEF_RE = re.compile(
    r'(?:^|[\s;&|\n])(?:function\s+\w+\s*(?:\(\s*\))?\s*\{|\w+\s*\(\s*\)\s*\{)', re.I)

_VAR_TOKEN = r'\$\{?\w+\}?|\$\([^()]*\)|`[^`]*`'

_LEADING_ASSIGN_RE = re.compile(
    r'^(?:\s*\w+=(?:"[^"]*"|\'[^\']*\'|\S*)\s+)+')

# A var token immediately followed by `.word` (`$files.Count`) is PowerShell
# MEMBER ACCESS — an expression that reads a property, not an invocation of
# whatever the variable holds — so it must not read as "command position"
# (false-positive fix, 2026-09-28, paired with _PS_ASSIGN_HEAD_RE below: an
# ordinary `$x = Get-Foo; $x.Count` was flagging on the second statement even
# once the assignment itself stopped tripping the head check). KNOWN, ACCEPTED
# GAP: `$cmd.Invoke()` still reads as inert by this same exemption even though
# it DOES execute — same "spelling vs. act" residual-gap class already
# documented for row7/row8 above; not in the fix brief's required set, not
# chased here.
#
# Built from an ATOMIC-MATCH emulation of the `$name`/`${name}` halves of
# _VAR_TOKEN (`(?=(?P<x>\w+))(?P=x)`), not `_VAR_TOKEN` directly: a plain
# `\$\{?\w+\}?` backtracks `\w+` one character short when the `(?!\.\w)`
# lookahead fails on the FULL name, which quietly re-passes on the shortened
# name instead of correctly failing (`$files.Count` was still matching on
# `$file`, one letter short, defeating the member-access exemption above —
# caught by the `$files = Get-ChildItem C:/Users; $files.Count` test case).
# The lookahead+backreference forces the `\w+` capture to stay at its
# longest length with no backtracking, so the exemption actually holds.
_HEAD_EXPANSION_RE = re.compile(
    r'^\s*(?:'
    r'\$\{(?=(?P<_hv1>\w+))(?P=_hv1)\}'   # ${name}
    r'|\$(?=(?P<_hv2>\w+))(?P=_hv2)'      # $name
    r'|\$\([^()]*\)'                      # $(...)
    r'|`[^`]*`'                           # `...`
    r')(?!\.\w)')

# PowerShell assignment (`$x = ...`, `$x += ...`, `$env:NAME = ...`) at the
# START of a segment (false-positive fix, 2026-09-28: Quill flagged that a
# bare `$x = 1; Write-Output $x` was blocked outright — the head-expansion
# check above read the assignment's OWN `$x` as an unresolved command-position
# variable, even though storing a value never runs anything). An assignment's
# LHS is never command position; deliberately excludes `==`/`===` comparisons
# via the trailing negative lookahead so an `if ($x == $y)`-shaped segment
# (not itself in scope here, but adjacent) can't be misread as an assignment.
_PS_ASSIGN_HEAD_RE = re.compile(r'^\s*\$(?:env:)?\w+\s*(?:\+=|=)(?!=)')

# The PowerShell call operator (`&`) and dot-source operator (`.`) put
# whatever follows them in command position exactly like a bare command
# word does — `& $cmd push` RUNS `$cmd`, it does not merely reference it —
# so a variable/expansion right after either must still be caught by the
# head-expansion check below. Requires trailing whitespace so this never
# fires on `./script.sh` (dot immediately followed by `/`, not a space).
_CALL_OP_STRIP_RE = re.compile(r'^\s*(?:&|\.)\s+')

# Verbs the fence already denies by literal spelling — the exact set a hidden
# variable sitting in the SUBCOMMAND slot (`git $P`, `terraform $T`) defeats.
_DENIED_VERBS_RE = (
    r'git|npm|twine|docker|pip|gh|gcloud|aws|az|terraform|kubectl|alembic|'
    r'psql|rm|rmdir|dd|curl|wget'
)
_VERB_THEN_EXPANSION_RE = re.compile(
    rf'\b(?:{_DENIED_VERBS_RE})\s+(?:{_VAR_TOKEN})', re.I)

_XARGS_RE = re.compile(r'\bxargs\b', re.I)
_XARGS_TARGET_RE = re.compile(
    rf'\b(?:{_DENIED_VERBS_RE}|bash|sh|zsh|dash|ksh|pwsh|powershell|'
    r'python\w*|node|perl|ruby|eval|source|iex|invoke-expression)\b', re.I)

_B64_DECODE_MARK = (
    r'base64\s+(?:-d\b|--decode\b)|'
    r'openssl\s+(?:base64|enc)\b[^\n;&|]*-d\b|'
    r'\[?convert\]?\s*::\s*frombase64string'
)
_EXPANSION_TOKEN = r'\$\{?\w+|\$\(|`'

# `eval "$CMD"` and `bash -c "$CMD"` are the purest enabling constructs of all —
# the command that runs is the VALUE of something the fence cannot see, and
# neither was in the measured row set. Only flagged when the interpreted text
# actually carries an expansion: `python -c "print(1)"` is a literal program the
# fence can read, and keeps passing.
_EVAL_EXPANSION_RE = re.compile(
    rf'\b(?:eval|iex|invoke-expression)\b[^\n;&|]*(?:{_EXPANSION_TOKEN})', re.I)
_DASH_C_EXPANSION_RE = re.compile(
    r'\b(?:bash|sh|zsh|dash|ksh|pwsh|powershell|python\w*|node|perl|ruby)\b'
    rf'[^\n;&|]*\s-c\b[^\n;&|]*(?:{_EXPANSION_TOKEN})', re.I)


_B64_TO_INTERPRETER_RE = re.compile(
    rf'(?:{_B64_DECODE_MARK})[^\n;&]*?\|[^\n;&]*?\b'
    r'(?:bash|sh|zsh|dash|ksh|pwsh|powershell|python\w*|node|perl|ruby|'
    r'eval|source|cmd|iex|invoke-expression)\b',
    re.I)


def _enabling_construct(cmd: str) -> FenceDecision:
    """Block the small set of constructs that decide a command's identity at
    RUNTIME instead of on the line the fence can read (see module comment
    above). Complements, does not replace, the verb denylist — a plain `git
    push` still blocks on the literal pattern earlier in classify_bash."""
    if _FUNC_DEF_RE.search(cmd):
        return FenceDecision(True, "shell function definition — the fence "
                                    "cannot see what a later call to it will "
                                    "run (define-then-use crosses the "
                                    "per-call boundary the fence checks at)")
    for pat, why in ((_EVAL_EXPANSION_RE, "eval/iex of an expansion"),
                     (_DASH_C_EXPANSION_RE, "interpreter -c on an expansion")):
        if pat.search(cmd):
            return FenceDecision(True, f"{why} - the program text that runs is "
                                        "a runtime VALUE the fence cannot read",
                                 overridable=False)
    if _B64_TO_INTERPRETER_RE.search(cmd):
        return FenceDecision(True, "base64/decode piped into an interpreter "
                                    "(decode-then-execute hides the command "
                                    "from the verb denylist)")
    for seg in _SHELL_SPLIT_RE.split(cmd):
        if not seg.strip():
            continue
        m = _XARGS_RE.search(seg)
        if m and _XARGS_TARGET_RE.search(seg[m.end():]):
            return FenceDecision(True, "xargs handing a denied verb/"
                                        "interpreter an argument built from "
                                        "stdin (the verb the fence sees is "
                                        "not the verb that runs)")
        stripped = _LEADING_ASSIGN_RE.sub('', seg)
        assignment = _PS_ASSIGN_HEAD_RE.match(stripped)
        if assignment:
            # The LHS `$name =` is not command position for this segment (see
            # _PS_ASSIGN_HEAD_RE above) — but the RIGHT-hand side can still
            # hide an invocation (`$x = & $cmd push`, `$x = git $P`), so it
            # gets the SAME head/verb checks the segment would otherwise get,
            # just scoped to the text after the `=`.
            rhs = stripped[assignment.end():]
            rhs_head = _CALL_OP_STRIP_RE.sub('', rhs, count=1)
            if _HEAD_EXPANSION_RE.match(rhs_head) or _VERB_THEN_EXPANSION_RE.search(seg):
                return FenceDecision(True, "variable/command substitution in "
                                            "command position (the word that "
                                            "actually runs is decided at "
                                            "runtime, not visible on this line)")
            continue
        # A leading `&` or `.` (dot-source) hands command position to
        # whatever follows it — strip it before the head check so `& $cmd
        # push` is judged on `$cmd`, not on the operator in front of it.
        head = _CALL_OP_STRIP_RE.sub('', stripped, count=1)
        if _HEAD_EXPANSION_RE.match(head) or _VERB_THEN_EXPANSION_RE.search(seg):
            return FenceDecision(True, "variable/command substitution in "
                                        "command position (the word that "
                                        "actually runs is decided at "
                                        "runtime, not visible on this line)")
    return FenceDecision(False, '')


_HUMAN_GATE_ROUTE_RE = re.compile(r'local-auth/set|/attend-once(?!/consume)', re.IGNORECASE)


def classify_bash(command: str) -> FenceDecision:
    """Classify a Bash command string. Returns (blocked, reason).

    Classification runs on the inert-prose-MASKED command: commit-message /
    literal-heredoc content is data, not commands (see _mask_inert_prose).
    Command-position text always survives masking, so every shape the fence
    blocked before, it still blocks."""
    if not command or not command.strip():
        return FenceDecision(False, '')
    # The human-proof gate's own routes (Fenn's re-review N2, 2026-09-28): an
    # armed agent setting the first dashboard passcode, or calling the grant,
    # would mint its own "Allow once". Checked on the raw text, before masking,
    # so a heredoc'd URL still counts.
    if _HUMAN_GATE_ROUTE_RE.search(command):
        return FenceDecision(True, "calls the dashboard-passcode or Allow-once "
                                   "grant route (the human-proof gate — "
                                   "human-owned)", overridable=False)
    try:
        cmd = _mask_inert_prose(command.strip())
    except Exception:
        cmd = command.strip()   # masking is best-effort; unmasked = stricter

    # Global-option / disguised-program bypass fix (2026-09-28, see the
    # section above _DELETE_PATTERNS): also check a normalised copy so
    # `git -C x push`, `git.exe push`, `Start-Process git -ArgumentList
    # 'push'...` etc. match the same way `git push` already does. ADDITIVE
    # only — the original `cmd` is still checked unchanged, so nothing that
    # blocked before this fix can stop blocking because of it.
    try:
        normalized = _normalize_for_block_patterns(cmd)
    except Exception:
        normalized = cmd   # normalisation is best-effort; falls back to the original check

    for pat, reason in _BLOCK_PATTERNS:
        if pat.search(cmd) or pat.search(normalized):
            return FenceDecision(True, reason)

    for pat in _DELETE_PATTERNS:
        if pat.search(cmd):
            low = cmd.lower()
            if any(m in low for m in _SCRATCH_MARKERS) and '..' not in cmd:
                break  # scratch-scoped delete → allowed
            return FenceDecision(True, "destructive delete outside scratch (irreversible)")

    net = _touches_nonlocal_network(cmd)
    if net.blocked:
        return net

    py_net = _python_network_send(cmd)
    if py_net.blocked:
        return py_net

    remote_copy = _scp_rsync_remote(cmd)
    if remote_copy.blocked:
        return remote_copy

    enabling = _enabling_construct(cmd)
    if enabling.blocked:
        return enabling

    return FenceDecision(False, '')


# ── Codex apply_patch (MC-975 follow-up, 2026-09-25) ────────────────────────
# Codex edits files with its own `apply_patch` tool, not Write/Edit. Measured
# on codex-cli 0.155.1 with a stdin-dumping probe hook: PreToolUse DOES fire
# for it, as `"tool_name": "apply_patch"`, `"tool_input": {"command":
# "*** Begin Patch\n*** Add File: .claude/settings.local.json\n+{}\n***
# End Patch"}`, paths RELATIVE to the session cwd (also in the payload's `cwd`).
# Before this the fence let both probe writes through, `.claude/` included.
# A patch is turned into one Write-shaped call per target path (Add, Update,
# Delete and Move-to alike), made absolute, and put through the same checks
# a Claude Write gets. A shell-invoked `apply_patch <<EOF` heredoc carries
# the same headers inside a Bash command, so Bash is scanned for them too.
# Over-matching a header is harmless here: a false block only makes the
# agent ask.
_PATCH_TOOL_NAMES = ('apply_patch',)
_PATCH_PATH_RE = re.compile(
    r'^\s*\*\*\*\s*(?:Add File|Update File|Delete File|Move to)\s*:\s*(.+?)\s*$',
    re.MULTILINE | re.IGNORECASE)


def patch_target_paths(tool_input: dict) -> list:
    """Every path an apply_patch body adds, updates, deletes or moves to."""
    ti = tool_input or {}
    body = ti.get('command') or ti.get('input') or ti.get('patch') or ''
    if isinstance(body, (list, tuple)):
        body = '\n'.join(str(x) for x in body)
    return [m.group(1).strip('"\'') for m in _PATCH_PATH_RE.finditer(str(body))]


def as_write_calls(tool_name: str, tool_input: dict,
                   cwd: Optional[str] = None) -> list:
    """[(tool_name, tool_input)] with an apply_patch (or a Bash command that
    carries a patch) expanded into Write calls on absolute paths. Anything
    else is returned unchanged, as the single call it is."""
    name = tool_name or ''
    ti = tool_input or {}
    calls = [] if name in _PATCH_TOOL_NAMES else [(name, ti)]
    if name in _PATCH_TOOL_NAMES or (name == 'Bash' and '*** Begin Patch' in
                                     str(ti.get('command', '') or '')):
        try:
            base = Path(cwd) if cwd else Path.cwd()
        except Exception:
            base = Path('.')
        for raw in patch_target_paths(ti):
            target = Path(raw)
            if not target.is_absolute():
                target = base / target
            calls.append(('Write', {'file_path': str(target)}))
    return calls


def check_install_dir_write(tool_name: str, tool_input: dict,
                             session_cwd: Optional[str] = None) -> FenceDecision:
    """Block a Write/Edit/MultiEdit/NotebookEdit whose target resolves inside
    the running Clayrune install's own source tree (`_INSTALL_DIR`), UNLESS
    this session's own project IS that install dir (a legitimate dev
    checkout — this box included, see mission_control's own project_path).

    Deliberately called UNCONDITIONALLY from main(), before the steward-cycle
    / unattended-arming gate below: that gate decides whether the
    IRREVERSIBILITY backstop (git push, rm -rf, ...) applies to THIS session,
    which is a judgment call for unattended work specifically. Whether an
    agent may edit a DIFFERENT project's install directory is not that kind
    of judgment call — it is a project-boundary rule that has to hold for
    every session the fence runs in, attended or not, the same way a project
    can't read another project's secrets.

    MC launches `claude` with cwd = project_path (steward/core.py), and hooks
    inherit that cwd, so Path.cwd() at call time IS the session's own project
    root — session_cwd exists only so tests can override it without an
    os.chdir dance across the whole suite.
    """
    name = (tool_name or '')
    if name not in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        return FenceDecision(False, '')
    ti = tool_input or {}
    raw = str(ti.get('file_path', '') or ti.get('notebook_path', '') or '')
    if not raw:
        return FenceDecision(False, '')
    try:
        base = Path(session_cwd) if session_cwd else Path.cwd()
        target = Path(raw)
        if not target.is_absolute():
            target = base / target
        target = target.resolve()
        install_dir = _INSTALL_DIR.resolve()
        session_root = base.resolve()
    except Exception:
        return FenceDecision(False, '')  # can't resolve -> best-effort, fail open
    if not (target == install_dir or _is_within(target, install_dir)):
        return FenceDecision(False, '')
    if session_root == install_dir or _is_within(session_root, install_dir):
        return FenceDecision(False, '')  # this session's OWN project is the install dir
    return FenceDecision(True, f"write targets the Clayrune install directory "
                                f"({install_dir}) from a different project — an "
                                f"agent may not edit the running app's own source")


def _vault_home() -> Path:
    """Mirrors mc.secrets_store.clayrune_home() WITHOUT importing it — this
    module is stdlib-only by design (module docstring), and secrets_store
    pulls in the `cryptography` package. tests/test_steward_fence.py pins
    this against the real implementation so the two can't silently drift."""
    override = os.environ.get('CLAYRUNE_HOME')
    if override:
        return Path(override)
    home = (os.environ.get('USERPROFILE') or os.environ.get('HOME')
            or str(Path.home()))
    return Path(home) / '.clayrune'


# The vault's master-key file(s) and its ciphertext store (mc/secrets_store.py:
# key_file_path, store_path, dpapi_mirror_path, wrapped_key_path). Deliberately
# NOT the whole ~/.clayrune directory — that also holds the audit log, the LAN
# passcode hash, and named browser profiles, none of which this specific
# backstop is scoped to (Wren, 2026-09-15, blocker C names the first three;
# secrets.key.wrapped and legacy_key_quarantine/ added under MC 503edfe4 once
# the passphrase lock introduced them as new places the key lives).
_VAULT_FILENAMES = ('secrets.key', 'secrets.json', 'secrets.key.dpapi',
                     'secrets.key.wrapped')

# Retired master-key copies quarantined by set_passphrase()
# (mc.secrets_store._quarantine_legacy_key_material) — a directory of
# timestamped subdirs whose file NAMES vary (they keep the original
# filename, but under an unpredictable <ts>/ path), so this can't be matched
# by a fixed filename list the way the live files above are; it has to be
# matched by directory instead.
_VAULT_QUARANTINE_DIRNAME = 'legacy_key_quarantine'

# The LAN dashboard passcode store (mc/blueprints/local_auth.py:LOCAL_AUTH_PATH,
# wired from server.py as `_DATA_ROOT / 'data' / 'local_auth.json'`). Added
# alongside the vault files under MC 503edfe4 once Wren's review showed WHY
# it belongs here: local_auth.json is no longer "just" the LAN gate's own
# secret — the passcode it stores now doubles as the human-proof gate on
# vault-lock/set and /change (mc/blueprints/secrets_routes.py's
# `_require_human_passcode`), so a same-user agent that could read it would
# have a straight line to the passphrase and recovery key too. Deliberately
# NOT folded into `_VAULT_FILENAMES`/`_vault_home()` — it lives under the
# app's data root (`_INSTALL_DIR` in dev mode), not `~/.clayrune`.
_LOCAL_AUTH_FILENAME = 'local_auth.json'


def _local_auth_data_root() -> Path:
    """Mirrors server.py's `_resolve_dirs()` data_root WITHOUT importing
    server.py — same reasoning as `_vault_home()` avoiding secrets_store.
    `_INSTALL_DIR` already stands in for "this install's own repo root" for
    the dev-mode case (see its docstring); `MC_DATA_DIR` overrides it exactly
    like the real resolver does."""
    override = os.environ.get('MC_DATA_DIR')
    if override:
        return Path(override)
    return _INSTALL_DIR


def _is_vault_filename(raw: str) -> bool:
    return bool(raw) and os.path.basename(raw.replace('\\', '/')) in _VAULT_FILENAMES


def _is_local_auth_filename(raw: str) -> bool:
    return bool(raw) and os.path.basename(raw.replace('\\', '/')) == _LOCAL_AUTH_FILENAME


def _path_resolves_into_local_auth(raw: str, cwd: Optional[Path] = None) -> bool:
    """Same shape as `_path_resolves_into_vault`, resolved against the data
    root instead of `~/.clayrune` — fails toward BLOCK on an unresolvable
    path, same asymmetric-risk bias as the rest of this module."""
    if not _is_local_auth_filename(raw):
        return False
    try:
        data_dir = (_local_auth_data_root() / 'data').resolve()
        target = Path(raw)
        if not target.is_absolute():
            target = (cwd or Path.cwd()) / target
        target = target.resolve()
    except Exception:
        return True
    return target == data_dir / _LOCAL_AUTH_FILENAME or _is_within(target, data_dir)


def _path_resolves_into_vault(raw: str, cwd: Optional[Path] = None) -> bool:
    """True if `raw` is a real filesystem path (Read's file_path, Grep/Glob's
    path) that names one of the vault files, or resolves inside the legacy-key
    quarantine directory — resolved under ~/.clayrune, or, failing that,
    matched by bare filename alone (fails toward BLOCK: a relative
    `secrets.key` the fence can't resolve against the session's real cwd is
    still worth refusing, same asymmetric-risk bias as the rest of this
    module)."""
    is_named_file = _is_vault_filename(raw)
    is_quarantine_mention = bool(raw) and _VAULT_QUARANTINE_DIRNAME in raw.replace('\\', '/')
    if not (is_named_file or is_quarantine_mention):
        return False
    try:
        home = _vault_home().resolve()
        quarantine = home / _VAULT_QUARANTINE_DIRNAME
        target = Path(raw)
        if not target.is_absolute():
            target = (cwd or Path.cwd()) / target
        target = target.resolve()
    except Exception:
        return True
    if is_named_file:
        return target == home or _is_within(target, home)
    return target == quarantine or _is_within(target, quarantine)


# Bash: require a vault filename (or a quarantine-dir mention) AND something
# that suggests an actual READ (a cat/type/Get-Content-shaped verb, or an
# explicit ~/.clayrune mention) in the SAME shell segment (same per-segment
# discipline as _touches_nonlocal_network) — a bare `grep -rn "secrets.key"
# mc/` searching THIS REPO'S OWN SOURCE for the string must keep passing; it
# names the file but reads no bytes of it and never mentions the vault's
# directory.
_VAULT_NAME_RE = re.compile(
    r'secrets\.key\.wrapped|secrets\.key\.dpapi|secrets\.key\b|secrets\.json\b|'
    + re.escape(_VAULT_QUARANTINE_DIRNAME), re.I)
_LOCAL_AUTH_NAME_RE = re.compile(re.escape(_LOCAL_AUTH_FILENAME), re.I)
_VAULT_READ_VERB_RE = re.compile(
    r'\b(cat|type|less|more|head|tail|Get-Content|gc|copy|cp|xxd|od|'
    r'hexdump|base64|python\w*|node|powershell|pwsh|Select-String|'
    r'findstr|strings)\b', re.I)


def _bash_touches_vault_file(cmd: str) -> bool:
    for seg in _SHELL_SPLIT_RE.split(cmd):
        if not _VAULT_NAME_RE.search(seg):
            continue
        if _VAULT_READ_VERB_RE.search(seg) or '.clayrune' in seg.lower():
            return True
    return False


# Any shell mention of the passcode store is refused, no exceptions (Fenn's
# reviews #3-#5, N1). Every round of listing dangerous verbs left a spelling
# out (`printf > store`, `curl -o store`, `-OutFile store`), and the one
# "source search" exception that replaced them was itself read-bypassed by
# `grep -e. local_auth.json other.txt`. To search source for the filename use
# the Grep tool: its content `pattern` is not a path and is never checked.
def _bash_touches_local_auth_file(cmd: str) -> bool:
    return bool(_LOCAL_AUTH_NAME_RE.search(cmd or ''))


def check_vault_file_access(tool_name: str, tool_input: dict,
                             session_cwd: Optional[str] = None) -> FenceDecision:
    """Deny Read/Grep/Glob/Bash access to the secrets vault's master-key
    file(s) (including the wrapped key and its legacy-key quarantine
    directory, MC 503edfe4) and ciphertext store, for EVERY agent session —
    attended or not (Wren, 2026-09-15 security review, blocker C). Called
    UNCONDITIONALLY from main(), same as check_install_dir_write above and
    for the same reason: "an agent may not read the file that opens every
    credential" is a project-boundary-shaped rule, not a judgment call that
    depends on whether a human is reading each tool call.

    Also covers the LAN passcode store (`local_auth.json`, added under MC
    503edfe4's follow-up review): once that passcode is the human-proof gate
    on vault-lock/set and /change, reading it is functionally reading a
    stepping-stone to the vault, not a separate, lower-stakes file.

    HONESTLY SCOPED (docs/SECRETS.md's own access-model note): this blocks
    the tool paths an agent reaches for BY NAME — Read a known path,
    Grep/Glob a pattern, `cat`/`type`/Get-Content in Bash. A same-user
    process can always read a file the server itself reads unattended;
    arbitrary code (a Python one-liner computing an obfuscated path, a
    renamed copy) still gets there. This is the obvious-path backstop, not a
    sandbox — the vault's own documentation already says so and this does
    not pretend otherwise.
    """
    name = tool_name or ''
    ti = tool_input or {}
    cwd = Path(session_cwd) if session_cwd else None
    refusal = FenceDecision(
        True, "reads the secrets vault's key/store file directly — use the "
              "vault API (GET /api/secrets) instead, never the raw file")
    local_auth_refusal = FenceDecision(
        True, "reads or writes the LAN dashboard passcode store directly — that "
              "passcode gates the vault-lock set/change routes, so this is "
              "a stepping-stone to the vault, not a separate file")
    if name == 'Read':
        raw = str(ti.get('file_path', '') or '')
        if _path_resolves_into_vault(raw, cwd):
            return refusal
        if _path_resolves_into_local_auth(raw, cwd):
            return local_auth_refusal
    elif name in ('Grep', 'Glob'):
        raw = str(ti.get('path', '') or '')
        if _path_resolves_into_vault(raw, cwd):
            return refusal
        if _path_resolves_into_local_auth(raw, cwd):
            return local_auth_refusal
        # Grep's own content-search `pattern` is NOT a path — searching this
        # repo's source for the literal text "secrets.key" must not trip
        # this (that is exactly the check the fence itself needed while
        # being written). Only the file-name-filter fields are path-shaped.
        filter_key = 'glob' if name == 'Grep' else 'pattern'
        filter_val = str(ti.get(filter_key, '') or '')
        if _is_vault_filename(filter_val):
            return refusal
        if _is_local_auth_filename(filter_val):
            return local_auth_refusal
    elif name in ('Bash', 'PowerShell'):
        cmd = str(ti.get('command', '') or '')
        if _bash_touches_vault_file(cmd):
            return refusal
        if _bash_touches_local_auth_file(cmd):
            return local_auth_refusal
    # Writes to the passcode store (Fenn's re-review N1, 2026-09-28): the
    # agent need not read the code if it can REPLACE the verifier with one it
    # chose, then pass the passcode-gated "Allow once" grant with it. Same
    # obvious-path scope as the reads above.
    write_paths = []
    if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        write_paths.append(str(ti.get('file_path', '') or ti.get('notebook_path', '') or ''))
    if name in _PATCH_TOOL_NAMES or name in ('Bash', 'PowerShell'):
        try:
            write_paths.extend(patch_target_paths(ti))
        except Exception:
            pass
    for raw in write_paths:
        if _path_resolves_into_local_auth(raw, cwd):
            return FenceDecision(
                True, "writes the LAN dashboard passcode store directly — "
                      "replacing its verifier would let an agent mint the "
                      "code that proves a human")
    return FenceDecision(False, '')


def classify_action(tool_name: str, tool_input: dict) -> FenceDecision:
    """Classify any tool call. Bash is where terminal danger lives; other tools
    default to allow (edits/writes are working-tree-reversible). Extend here if a
    non-Bash irreversible surface appears (e.g. an MCP tool that sends email)."""
    name = (tool_name or '')
    ti = tool_input or {}
    if name in _PATCH_TOOL_NAMES:
        for sub_name, sub_input in as_write_calls(name, ti):
            d = classify_action(sub_name, sub_input)
            if d.blocked:
                return d
        return FenceDecision(False, '')
    if name in ('Bash', 'PowerShell'):
        # PowerShell tool_input carries the command in the same `command`
        # key as Bash (mc/process_guard.py's _SHELL_TOOL_NAMES precedent) and
        # its own cmdlets (Remove-Item, Invoke-WebRequest/-RestMethod, iwr/
        # irm, Publish-Module/-Script) are already in classify_bash's
        # patterns — a live `git push` via the PowerShell tool exited 0
        # before this branch existed (Quill, 2026-09-27) because this
        # function only ever routed 'Bash'.
        return classify_bash(ti.get('command', '') or '')
    # Writing to global config outside the project is out-of-scope for a
    # project steward — block edits/writes targeting ~/.claude or a home dotfile.
    if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = str(ti.get('file_path', '') or ti.get('notebook_path', '') or '')
        low = path.replace('\\', '/').lower()
        if '/.claude/' in low or low.endswith('/.claude'):
            return FenceDecision(True, "editing global ~/.claude config (out of project scope)",
                                 overridable=False)
        # Fence supply chain (MC-914, 2026-08-31): this hook script is re-read
        # from disk fresh on EVERY tool call (invoked as a fresh subprocess per
        # PreToolUse event, no caching) — the exact "hot re-read of a mutable
        # gate" shape MC-914 was opened to close, except the mutable value here
        # is the enforcement code itself rather than an env var. Without this
        # check, a steward session could Edit `steward/fence.py` to neuter the
        # fence and have the very next tool call run the tampered version.
        _fence_names = ('fence.py', 'core.py', '_config.py', '__init__.py')
        if (low.startswith('steward/') or '/steward/' in low) and \
                low.rsplit('/', 1)[-1] in _fence_names:
            return FenceDecision(True, "editing the steward fence's own "
                                       "enforcement code (safety-rail supply "
                                       "chain — human-owned)", overridable=False)
        # Learning-loop supply chain (committee M3, 2026-07-16): proposal
        # frontmatter and the skill-stats signal store are the INPUTS that
        # decide what enters agent loadouts. An unattended agent editing its
        # own provenance/recurrence records is the origin-laundering write
        # path — same fence posture as `.claude/`.
        if ('/data/skills/' in low or low.startswith('data/skills/')
                or low.endswith('_skill_stats.json')
                or low.endswith('_skill_stats_archive.jsonl')):
            return FenceDecision(True, "editing learning-loop artifacts/telemetry "
                                       "(loadout supply chain — human-owned)",
                                 overridable=False)
    # Autonomous web browsing is high blast-radius for an unattended agent: the
    # browser MCP is unrestricted (all sites) with in-page JS execution, so
    # prompt-injecting page content can steer a steward cycle. The steward does
    # not need to browse; block the browser MCP tools (mcp__browser__* /
    # mcp__playwright__*). A cycle that genuinely needs a page is an escalation,
    # not a silent autonomous fetch. Asymmetric-risk: a false block just makes
    # the steward ask.
    if name.startswith('mcp__browser__') or name.startswith('mcp__playwright__'):
        return FenceDecision(True, "autonomous web browsing is out of steward scope "
                                   "(unrestricted browser + in-page JS = prompt-injection "
                                   "blast radius)")
    # Same argument, sharper case: the mail MCP reads Ron's real inbox, so
    # ANYONE who can email him can put text in front of an unattended cycle.
    # AGENT_RULES.md now points cycles at tools/mail-mcp/read_digest.py, which
    # launders the messages through a toolless oneshot() before a tooled
    # session ever sees them — but a rule an agent can decline to follow is
    # not a control. Blocking the raw tools here is what makes the laundered
    # path the ONLY path for a steward. read_digest.py runs via Bash and is
    # unaffected. (docs/UNTRUSTED_INPUT_SURFACE.md finding #2, 2026-09-10.)
    if name.startswith('mcp__mail__'):
        return FenceDecision(True, "read mail through tools/mail-mcp/read_digest.py, which "
                                   "launders it toolless first — the raw mail tools put "
                                   "anyone-who-can-email-you into an unattended context")
    return FenceDecision(False, '')


STEWARD_MARKER = '[Steward cycle]'


def _first_user_text(transcript_path: str) -> str:
    """Return the text of the FIRST user message in a CC transcript jsonl, or ''.
    A steward session's turn-1 message is the build_cycle_task() prompt, which
    starts with the STEWARD_MARKER — so this is a reliable session-type signal."""
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                msg = entry.get('message', entry)
                role = entry.get('type') or msg.get('role') or ''
                if role != 'user':
                    continue
                content = msg.get('content', '')
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get('type') == 'text':
                            return b.get('text', '')
                        if isinstance(b, str):
                            return b
                return ''
    except Exception:
        return ''
    return ''


def _session_is_steward(payload: dict):
    """True/False if we can confirm the session type from the transcript; None if
    unknown (no/unreadable transcript). Steward cycle → first user msg carries the
    marker."""
    tp = payload.get('transcript_path') or payload.get('transcriptPath') or ''
    if not tp:
        return None
    text = _first_user_text(tp)
    if not text:
        return None
    return STEWARD_MARKER in text


# ── Generalized unattended arming (2026-09-14, UNATTENDED_AGENT_PERMISSIONS_AUDIT) ──
# The fence originally enforced ONLY for steward-cycle sessions (the marker
# check above). Every other unattended launch path this MC install has — a
# scheduled task, a workflow agent step, an agent dispatching a helper via the
# HTTP dispatch endpoint, a hivemind worker — shares the steward's exact
# posture (--dangerously-skip-permissions, full tool+MCP fleet, no human
# reading every tool call) in any project where a human already turned the
# steward feature ON for that project (this hook is only ever installed
# there — see steward/core.py:install_fence_to_project). None of those paths
# got this backstop, because the only signal the fence ever checked was the
# literal `[Steward cycle]` marker string.
#
# Detection reuses the MC-923 precedent (mc/secrets_store.py
# detect_unattended_context, same "the harness told us, the agent didn't"
# class of signal the transcript marker already is): the Claude CLI sets
# CLAUDE_CODE_SESSION_ID in every subprocess it spawns, including this hook,
# and MC recorded the session's trigger_type server-side at dispatch time
# (GET /api/session/trigger-type) — ground truth the session itself cannot
# rewrite by anything it types.
#
# Fails OPEN here, the opposite of MC-923's fail-closed: this hook is also
# installed for perfectly ordinary interactive dev sessions in the same
# steward-enabled project (self-gating is the whole point of this module —
# see the top-of-file docstring), and those must NEVER be blocked by an
# unreachable lookup or an unrecognized trigger_type. Only a POSITIVE,
# confirmed match against a known unattended trigger_type arms the fence;
# anything else (manual, unknown, lookup failure) leaves the session exactly
# as unfenced as it was before this change — this is purely additive on top
# of the existing marker check, never a replacement for it.
#
# Gated behind `fence_unattended_enabled` (config default True) so a human
# can flip it off from Settings without a code change if it ever blocks a
# legitimate unattended job — PUT /api/config already refuses an unattended
# caller for every key in _CONFIG_EDITABLE_KEYS (2026-09-10_security.md F5),
# so only a human can turn it back off.

_MC_API_BASE = 'http://127.0.0.1:5199'

# Every trigger_type MC stamps for a launch path with no human reading each
# individual tool call. Deliberately excludes 'manual' — a plain UI chat or an
# agent-to-agent dispatch a human is actively reading.
_UNATTENDED_TRIGGER_TYPES = {
    'schedule', 'workflow', 'dispatch', 'hivemind_orchestrator', 'hivemind_worker',
}


def _session_id_from_env() -> str:
    return (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()


def _lookup_trigger_type(claude_session_id: str) -> Optional[dict]:
    """{'trigger_type': ..., 'fence_unattended_enabled': ...} for this Claude
    session, or None if the server is unreachable or doesn't know the session.
    Split out so tests can monkeypatch this one function instead of standing
    up a live server (same shape as secrets_store._lookup_trigger_type)."""
    try:
        url = (f'{_MC_API_BASE}/api/session/trigger-type'
               f'?claude_session_id={urllib.parse.quote(claude_session_id)}')
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None
    if not data.get('found'):
        return None
    return {
        'trigger_type': str(data.get('trigger_type') or 'manual'),
        'fence_unattended_enabled': bool(data.get('fence_unattended_enabled', True)),
    }


def _should_arm_for_unattended_trigger() -> bool:
    """True only on a positive, confirmed match — see module comment above.
    Only ever ADDS enforcement on top of the existing steward-marker check,
    never removes it."""
    sid = _session_id_from_env()
    if not sid:
        return False
    info = _lookup_trigger_type(sid)
    if not info:
        return False
    if info['trigger_type'] not in _UNATTENDED_TRIGGER_TYPES:
        return False
    return info['fence_unattended_enabled']


# One-shot "Allow once" pass (MC-994 follow-up, 2026-09-28). A human click in
# the chat header (POST .../agent/<sid>/attend-once, human-only) grants ONE
# pass; this hook spends it on the single blocked call it permits. The consume
# route can only SPEND an existing pass, never create one. Fails CLOSED: any
# lookup failure means no pass, so the action stays blocked.
#
# Fenn's review (2026-09-28): the pass is spent only when the WHOLE tool call
# holds exactly one blocked operation and that operation is overridable. The
# block loop in main() stops at the first hit, so judging the pass on that one
# decision let `git push && <patch fence.py>` spend a pass on the push and
# carry the fence edit through with it.
#
# Threat model (both reviews): this is a backstop against a confused or
# prompt-injected agent taking an irreversible step, not an OS sandbox. An
# agent running arbitrary same-user code can reach anything the server can.
_PASS_META_CHARS = set('$`(){};|&<>\n\r')
_PASS_WRAPPER_HEADS = {
    'bash', 'sh', 'zsh', 'dash', 'cmd', 'powershell', 'pwsh', 'python', 'python3',
    'py', 'node', 'perl', 'ruby', 'env', 'xargs', 'iex', 'invoke-expression',
    'eval', 'exec', 'source', 'start-process', 'wsl', 'ssh',
}


def _pass_head_name(tok: str) -> str:
    head = (tok or '').strip('"\'').lower().replace('\\', '/').rsplit('/', 1)[-1]
    return re.sub(r'\.(exe|cmd|bat|ps1)$', '', head)


def _is_plain_single_invocation(cmd: str) -> bool:
    text = (cmd or '').strip()
    if not text or any(c in _PASS_META_CHARS for c in text):
        return False
    low = text.lower()
    if re.search(r'(^|\s)(eval|iex|--next|-exec)(\s|$)', low):
        return False
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not argv:
        return False
    # Judge the head both ways: POSIX quoting strips `"curl"` to `curl`
    # (Fenn's review #4), but also eats Windows backslashes, which the raw
    # first token keeps. Either reading naming a wrapper or transfer tool counts.
    heads = {_pass_head_name(argv[0]), _pass_head_name(text.split()[0])}
    if heads & _PASS_WRAPPER_HEADS:
        return False
    return not heads & _PASS_TRANSFER_HEADS


# No HTTP transfer tool can spend a pass (Fenn's reviews #3-#6, N4). A single
# invocation is not a single request for them: extra URLs, globs, config
# files, redirect-follow and retry each turned one pass into 2-3 POSTs, and
# after an argv allowlist closed those, curl's default config file
# (`.curlrc` with `location` or `retry`) restored replay with nothing on the
# command line. The pass is not bound to a command, so the only bound that
# holds is not covering these tools at all. A blocked external send stays
# blocked; the human can run it themselves.
_PASS_TRANSFER_HEADS = {
    'curl', 'wget', 'http', 'https', 'httpie', 'invoke-webrequest', 'iwr',
    'invoke-restmethod', 'irm',
}


def _blocked_leaves(tool_name: str, tool_input: dict) -> list:
    """Every blocked operation inside one call: patch tools recurse into their
    writes, a chained shell command counts as more than one. Over-counting only
    ever refuses a pass, never grants one."""
    if tool_name in _PATCH_TOOL_NAMES:
        out = []
        for sub_name, sub_input in as_write_calls(tool_name, tool_input or {}):
            if sub_name in _PATCH_TOOL_NAMES:
                d = classify_action(sub_name, sub_input)
                out.extend([d] if d.blocked else [])
            else:
                out.extend(_blocked_leaves(sub_name, sub_input))
        return out
    if tool_name in ('Bash', 'PowerShell'):
        cmd = (tool_input or {}).get('command', '') or ''
        whole = classify_bash(cmd)
        if not whole.blocked:
            return []
        # Only a plain single invocation is passable (Fenn's re-review N4):
        # counting separators let eval, loops, a push nested in $(...) and
        # curl --next through as "one" operation. So the check is positive:
        # no shell metacharacter at all, and not handed to a shell or
        # interpreter. Anything else stays blocked, just never passable.
        if not _is_plain_single_invocation(cmd):
            return [whole, whole]
        return [whole]
    d = classify_action(tool_name, tool_input)
    return [d] if d.blocked else []


def _pass_can_cover(calls) -> bool:
    try:
        leaves = []
        for call_name, call_input in calls:
            leaves.extend(_blocked_leaves(call_name, call_input))
    except Exception:
        return False
    return len(leaves) == 1 and leaves[0].overridable


def _consume_attend_once_pass() -> bool:
    sid = _session_id_from_env()
    if not sid:
        return False
    try:
        req = urllib.request.Request(
            f'{_MC_API_BASE}/api/session/attend-once/consume',
            data=json.dumps({'claude_session_id': sid}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return False
    return data.get('consumed') is True


def main(argv=None) -> int:
    """PreToolUse hook entrypoint. Reads the hook JSON on stdin.

    SELF-GATING: the fence is installed repo-wide (project .claude/settings.json)
    but ENFORCES only for steward-cycle sessions (confirmed by the transcript
    marker) plus, since 2026-09-14, any OTHER session MC recorded as
    unattended (schedule/workflow/dispatch/hivemind — see
    _should_arm_for_unattended_trigger above) — so manual/dev sessions in the
    same project are unaffected.

    Gate, corrected 2026-09-14 (UNATTENDED_AGENT_PERMISSIONS_AUDIT §7 — a real
    bug, found live, not a hypothetical): `confirmed steward (marker=True) →
    always enforce`; everything else (marker=False, i.e. confirmed non-steward,
    OR marker=None, i.e. the transcript is missing/unreadable/has no user text
    yet) → fall through to the trigger_type check and enforce ONLY on a
    positive confirmed match.

    The marker=None case used to enforce outright ("fail-closed on ambiguity,
    since the fence is only ever installed in steward-enabled projects") — that
    assumption broke the moment the hook could be installed into every project
    (not just steward-enabled ones): a brand-new interactive session's very
    FIRST tool call has no transcript file yet, and any transient transcript
    read hiccup has the identical shape. Both used to hard-block that human's
    own `git push`/`rm -rf`/`gh pr merge`. A steward cycle's transcript being
    unreadable is not a hole under the new gate — steward cycles are
    dispatched as ordinary schedule fires (`trigger_type='schedule'`,
    `mc/blueprints/scheduler_routes.py:712/1367` — the `[Steward cycle]`
    prompt marker is additional, not instead of, that stamp), so
    `_should_arm_for_unattended_trigger` still arms it through the
    server-recorded signal even when the marker can't be read at all.

    On a blocked action, exits 2 (stderr reason) — the fail-closed block contract.
    Fails OPEN on any parse error — a broken fence must never wedge the agent.

    MC-975 (2026-09-25), two launcher-supplied argv flags, both used only by
    Clayrune's inline Codex hook (mc/guardrail_hooks.py
    codex_fence_hook_command). Neither can loosen the fence:
      --self-test  exit 2 with a fixed token, before reading stdin. Clayrune
                   runs this before an unattended Codex launch to prove the
                   hook shell, the interpreter and a block round trip all work.
      --armed      Clayrune decided at dispatch that this session is
                   unattended. Codex hooks have no CLAUDE_CODE_SESSION_ID and
                   no Claude-format transcript, so neither signal below can
                   ever arm the fence for a Codex run."""
    argv = list(argv or ())
    if '--self-test' in argv:
        print('CLAYRUNE-FENCE-SELF-TEST-OK: fence.py ran and can signal a block',
              file=sys.stderr)
        return 2
    armed_by_launcher = '--armed' in argv
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # fail open — never wedge the agent on a malformed hook event

    tool_name = payload.get('tool_name') or payload.get('toolName') or ''
    tool_input = payload.get('tool_input') or payload.get('toolInput') or {}

    # Project-boundary guard runs UNCONDITIONALLY, ahead of the steward/
    # unattended gate below — see check_install_dir_write's docstring for why
    # this one check is not a judgment call the way the irreversibility
    # backstop is.
    try:
        calls = as_write_calls(tool_name, tool_input, payload.get('cwd') or None)
    except Exception:
        calls = [(tool_name, tool_input)]
    boundary = FenceDecision(False, '')
    for call_name, call_input in calls:
        try:
            boundary = check_install_dir_write(call_name, call_input)
        except Exception:
            boundary = FenceDecision(False, '')
        if boundary.blocked:
            break
    if boundary.blocked:
        print(f"STEWARD FENCE blocked this action: {boundary.reason}. "
              f"Do NOT retry it against this path.", file=sys.stderr)
        return 2

    # Same unconditional posture as the install-dir guard above — see
    # check_vault_file_access's docstring.
    try:
        vault_access = check_vault_file_access(tool_name, tool_input,
                                               payload.get('cwd') or None)
    except Exception:
        vault_access = FenceDecision(False, '')
    if vault_access.blocked:
        print(f"STEWARD FENCE blocked this action: {vault_access.reason}. "
              f"Do NOT retry it against this path.", file=sys.stderr)
        return 2

    # Confirmed steward (marker=True) always enforces. Everything else
    # (confirmed non-steward OR genuinely unknown) falls through to the
    # generalized trigger_type signal — see the corrected gate above.
    # Only a session armed purely by the server-recorded trigger_type may spend
    # a human's one-shot "Allow once" pass (MC-994 follow-up). A confirmed
    # steward cycle or a launcher-armed (Codex --armed) run never can.
    #
    # Eligibility needs a POSITIVELY confirmed non-steward (marker False): an
    # unreadable transcript (None) still arms through trigger_type below, but
    # must not open the pass to what may be a real steward cycle (Fenn).
    pass_eligible = False
    steward = None if armed_by_launcher else _session_is_steward(payload)
    if not armed_by_launcher and steward is not True:
        if not _should_arm_for_unattended_trigger():
            return 0
        pass_eligible = steward is False

    decision = FenceDecision(False, '')
    for call_name, call_input in calls:
        try:
            decision = classify_action(call_name, call_input)
        except Exception:
            return 0  # fail open
        if decision.blocked:
            break

    if not decision.blocked:
        return 0

    # Supply-chain and global-config edits are never passable, and a call that
    # bundles more than one blocked operation is refused whole: one click, one
    # operation. Those need a genuinely attended session.
    passable = pass_eligible and _pass_can_cover(calls)
    if passable and _consume_attend_once_pass():
        return 0

    if passable:
        how = ('The human can click "Allow once" in the chat header to permit '
               'the next blocked action (one action, expires in 10 minutes). '
               'Do NOT retry it until they say they have.')
    else:
        how = ('Do NOT retry it. Instead post a `DECISION NEEDED:` note to your '
               'charter with the exact command so the human can approve it.')
    msg = (f"STEWARD FENCE blocked this action: {decision.reason}. "
           f"This is irreversible/mutating and you are running unattended. "
           f"{how}")
    # Exit 2 + stderr is the fail-CLOSED block contract: it denies the tool call
    # across all CLI versions (verified against hooks.md — exit 2 blocks even
    # under --dangerously-skip-permissions). JSON permissionDecision is the
    # alternative, but under exit 2 stdout is ignored, and exit-0+JSON would
    # fail OPEN on any CLI that doesn't parse it — wrong posture for a safety
    # backstop. So: exit 2, stderr reason, nothing on stdout.
    print(msg, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
