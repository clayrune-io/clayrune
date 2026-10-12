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
import base64
import hashlib
import json
import os
import posixpath
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


# Which deletes count as scratch-scoped: see "Per-target scratch judgement for
# deletes" above classify_bash (a marker anywhere in the text no longer does).

# Hosts that count as "local" — the steward's own reporting/API calls target
# these and must pass the fence.
_LOCAL_HOSTS = ('localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]')

# Building blocks for the Windows/macOS disk entries in _BLOCK_PATTERNS.
_PROG_PRE = r'(?<![\w.\-])'
_PROG_END = r'''(?:\.exe)?['"]?'''
_ARG_SEP = r'''['"]?\s+['"]?'''
# Command position: start of string, after `; & | ( {` or a backtick or a
# newline, after `-c` / `-Command` / `/c` / `/k` (a shell inside a shell), or
# after sudo/doas/nohup/xargs; then an optional opening quote. Used where the
# bare word is also ordinary prose or an ordinary argument (`reg copy`, `ri`,
# `erase`, `rd`), so `echo "use reg copy"` and `git commit -am "erase old rows"`
# stay allowed while `x; ri y`, `gci | ri` and `powershell -c "ri y"` do not.
_CMD_POS = (r'''(?:^|[;&|({`\n\r]|\s-c(?:ommand)?\s|\s/{1,2}[ck]\s'''
            r'''|(?<![\w.\-])(?:sudo|doas|nohup|xargs)\s)\s*['"]?''')

# Irreversible command shapes → BLOCK. Each entry: (compiled regex, human reason).
# Matched case-insensitively against the WHOLE command string (so chained
# commands like `cd x && git push` are still caught).
_BLOCK_PATTERNS = [
    (re.compile(r'\bgit\s+push\b', re.I),
     "git push writes to a shared remote (irreversible once others fetch)"),
    # `--hard` anywhere among reset's operands (`git reset -q --hard`, `--quiet
    # --hard HEAD~1`); a global option before `reset` is stripped by the
    # normalised pass (`git -C x reset -q --hard`).
    # A backslash-newline continuation counts as a separator.
    (re.compile(r'\bgit\s+reset\b(?:(?:[ \t]*\\\r?\n[ \t]*|[ \t]+)[^\s;&|]+)*?'
                r'(?:[ \t]*\\\r?\n[ \t]*|\s+)[\'"]?--hard\b', re.I),
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
    # dd with an `if=` or `of=` operand in EITHER order (`dd of=/dev/sda
    # if=/dev/zero`, `dd bs=1M of=/dev/sda`): the line above only saw `if=`
    # first. `dd --help` / `dd --version` carry no operand and stay allowed.
    (re.compile(rf'{_PROG_PRE}dd{_PROG_END}(?:[ \t]+[^\s;&|]+)*?[ \t]+[\'"]?(?:if|of)=', re.I),
     "disk-destructive operation (dd)"),
    # Windows / macOS disk, volume and backup destruction (2026-10-03, Quill):
    # the line above is Unix-only, so Format-Volume, diskpart, `format D:`,
    # diskutil eraseDisk, vssadmin delete shadows and `reg delete` were all
    # ALLOW. Program names are matched as whole tokens (_PROG_PRE / _PROG_END:
    # not glued to a word, `.`, or `-` on the left; optional .exe and closing
    # quote on the right) so `Format-Table`, `git log --format=`,
    # `'{}'.format(1)` and `diskpart-notes.md` never match. Every entry needs
    # a DESTRUCTIVE verb after the program, so the read-only forms (Get-Disk,
    # diskutil list/info, vssadmin list shadows, wmic shadowcopy list, wbadmin
    # get versions, reg query, cipher /?) stay allowed. The `Start-Process`,
    # `&` call-operator and quoted-path spellings reach these through
    # _normalize_for_block_patterns and _ARG_SEP, same as git push.
    (re.compile(r'\b(?:Format-Volume|Clear-Disk|Remove-Partition|Initialize-Disk|'
                r'Remove-VirtualDisk|Remove-StoragePool)\b', re.I),
     "disk-destructive operation (PowerShell storage cmdlet)"),
    (re.compile(rf'{_PROG_PRE}diskpart{_PROG_END}(?=\s|[;&|)]|$)', re.I),
     "disk-destructive operation (diskpart)"),
    # `format.com`: drive-letter argument (`format D:`, `format /fs:ntfs D:`)
    # or one of its own switches. The `(?<!dotnet )` keeps `dotnet format D:\`
    # (a project path, not a drive) allowed.
    (re.compile(rf'{_PROG_PRE}(?<!dotnet )format(?:\.com)?[\'"]?'
                r'(?:\s+[^\s;&|]+)*?\s+[\'"]?(?:[A-Za-z]:[\\/]?(?=\s|$|[;&|)"\'])|/(?:fs:|q\b|y\b|x\b))',
                re.I),
     "disk-destructive operation (format)"),
    (re.compile(rf'{_PROG_PRE}diskutil{_PROG_END}{_ARG_SEP}(?:eraseDisk|eraseVolume|secureErase|'
                rf'zeroDisk|randomDisk|reformat|partitionDisk|repartitionDisk|mergePartitions|'
                rf'apfs{_ARG_SEP}(?:deleteContainer|deleteVolume|eraseVolume))\b', re.I),
     "disk-destructive operation (diskutil)"),
    (re.compile(rf'{_PROG_PRE}cipher{_PROG_END}(?:\s+[^\s;&|]+)*?\s+[\'"]?/w\b', re.I),
     "disk-destructive operation (cipher /w wipes free space)"),
    (re.compile(rf'{_PROG_PRE}wmic{_PROG_END}(?=\s)[^;&|\n]*?'
                r'(?:\bshadowcopy\b[^;&|\n]*?\bdelete\b|\bcall\s+format\b)', re.I),
     "disk-destructive operation (wmic shadowcopy delete / volume format)"),
    # Backups and restore points: deleting them is irreversible.
    (re.compile(rf'{_PROG_PRE}vssadmin{_PROG_END}{_ARG_SEP}'
                rf'(?:delete{_ARG_SEP}shadow(?:s|storage)|resize{_ARG_SEP}shadowstorage)\b', re.I),
     "shadow-copy deletion (irreversible)"),
    (re.compile(r'\bWin32_ShadowCopy\b[\s\S]*?(?:\bRemove-CimInstance\b|\bRemove-WmiObject\b|'
                r'\.Delete\s*\()', re.I),
     "shadow-copy deletion (irreversible)"),
    (re.compile(rf'{_PROG_PRE}wbadmin{_PROG_END}{_ARG_SEP}delete{_ARG_SEP}'
                r'(?:catalog|backup|systemstatebackup)\b', re.I),
     "backup deletion (irreversible)"),
    (re.compile(rf'{_PROG_PRE}tmutil{_PROG_END}{_ARG_SEP}(?:delete|deletelocalsnapshots)\b', re.I),
     "Time Machine backup deletion (irreversible)"),
    # Registry deletion: reg delete, and Remove-/Clear-ItemProperty plus the
    # `rp`/`clp` aliases when aimed at a registry path. `reg add`/`reg import`
    # are config writes, deliberately not here. `reg restore` (overwrites a
    # key from a hive file) and `reg copy` (overwrites the destination key) are
    # irreversible the same way; they are matched in command position only,
    # because "reg copy" is also an everyday phrase.
    (re.compile(rf'{_PROG_PRE}reg{_PROG_END}{_ARG_SEP}delete\b', re.I),
     "registry deletion (irreversible)"),
    (re.compile(rf'{_CMD_POS}(?:[^\s\'";&|]*[\\/])?reg{_PROG_END}{_ARG_SEP}(?:restore|copy)\b', re.I),
     "registry overwrite (reg restore / reg copy replaces keys, irreversible)"),
    (re.compile(r'\b(?:Remove-ItemProperty|Clear-ItemProperty)\b|'
                rf'{_PROG_PRE}(?:rp|clp)\s[^;&|\n]*?\b(?:HK[A-Z]{{1,3}}:|Registry::)', re.I),
     "registry value deletion (irreversible)"),
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
    # PowerShell's other Remove-Item aliases: `ri`, `erase`, and `rd` without
    # /s (`rd -Recurse -Force`). Command position only (see _CMD_POS), bare or
    # with arguments, so the pipe form (`gci x | ri -Recurse`) is covered and
    # `git rebase -i`, `print`, `uri`, `erase_all(` and prose are not.
    re.compile(_CMD_POS + r'''(?:ri|erase|rd)(?=[\s;&|)}'"]|$)''', re.I),
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


# ── curl data-argument masking (MC-1037, 2026-10-02; reworked 2026-10-03) ────
# A backlog POST whose JSON text merely MENTIONED pushing was blocked in
# clayrune_cloud: `curl -s -X POST .../backlog -d '{"text":"... git push ..."}'`
# (the apostrophe spelt '"'"'). curl never executes its arguments, so the value
# after -d / --data* / --json is data, the same class as a commit-message body.
#
# Unlike the regex masks below, this one must not trust a regex's idea of
# "inside quotes": `echo ' -d "'; <cmd>; echo '"'` hands a regex a span the
# shell RUNS. So it lexes the command with its own quote state machine and
# masks a value only when every one of these holds; any doubt returns the
# command UNCHANGED, which still blocks:
#   • every quote pairs, and nothing in the text makes bash and PowerShell pair
#     quotes differently (an unquoted backslash, backtick or #; $' or $"; $( or
#     a process substitution; a here-doc; a curly quote, which PowerShell reads as a quote; inside "..."
#     a backslash, $ or backtick). The Bash tool and Codex's PowerShell shell
#     both reach this function, so it has to be right for both;
#   • curl is the FIRST word of its own shell segment. `bash -c "curl ..."`,
#     `eval curl ...`, `sudo curl ...`, `$(curl ...)` keep being scanned whole;
#   • curl's argv is WALKED with an option-arity table, never searched: a known
#     no-argument flag is skipped, a known value option consumes the next word,
#     and anything else (an unknown option, `--`, -K/--config, --next/-:, a
#     glued short value like -XPOST, any --opt=value, an option value that
#     starts with `-`) leaves the command unchanged. A search for "-d" cannot
#     tell the option from an operand: in `curl -o -d '--request=POST' URL`
#     -d is -o's file name and `--request=POST` is a live option (Fenn,
#     2026-10-03), and masking it hid a real POST from the network check;
#   • every URL in that curl segment is Clayrune's own API
#     (http://localhost:5199 or 127.0.0.1:5199) at a route in
#     _CURL_PROSE_ROUTE_RE, the routes whose body is prose. A POSITIVE list:
#     /api/project/<id>/agent/<sid>/job runs its body as a shell command, and
#     /api/project/<id>/agent/dispatch hands its `task` to a child agent as an
#     instruction (Fenn, MC-1037 r3: `"source":"ui"` in the body even lifts the
#     child's fence), so neither is prose. The next such route is added the
#     day someone writes it, so everything not
#     named (other hosts, terminal, secrets/exec, jobs, schedules, any unknown
#     route) is not masked;
#   • the value is nothing but quoted pieces ('..', "..", or a mix like
#     '..'"'"'..'), so no unquoted text or expansion can ride along.
_CURL_DATA_OPTS = frozenset({'-d', '--data', '--data-raw', '--data-binary',
                             '--data-ascii', '--data-urlencode', '--json'})
_CURL_NOARG_SHORT = frozenset('sSLivfkgI46N')
_CURL_VALUE_SHORT = frozenset('XHouAebcwm')
_CURL_NOARG_LONG = frozenset({
    '--silent', '--show-error', '--location', '--include', '--verbose', '--fail',
    '--fail-with-body', '--insecure', '--globoff', '--head', '--no-buffer',
    '--compressed', '--http1.1', '--ipv4', '--ipv6'})
_CURL_VALUE_LONG = frozenset({
    '--request', '--header', '--output', '--user', '--user-agent', '--referer',
    '--cookie', '--cookie-jar', '--write-out', '--max-time', '--connect-timeout',
    '--retry', '--retry-delay', '--retry-max-time', '--url'})
_CURL_HEADS = frozenset({'curl', 'curl.exe'})
_CURLY_QUOTES = '‘’‚‛“”„'
# One path segment: never `.`/`..` (curl collapses them before sending) and
# never a `%` (an encoded dot is still a different route to a later reader).
_CURL_SEG = r'[A-Za-z0-9_][A-Za-z0-9_.-]*'
_CURL_PROSE_URL_RE = re.compile(
    r'^(?i:https?://(?:localhost|127\.0\.0\.1)):5199(?P<path>/[^?#]*)?(?:\?[\w=&.,%-]*)?$')
_CURL_PROSE_ROUTE_RE = re.compile(
    r'^/api/(?:'
    rf'project/{_CURL_SEG}/(?:backlog(?:/{_CURL_SEG}){{0,3}}'
    rf'|memory/positions(?:/{_CURL_SEG})?|memory/mints(?:/{_CURL_SEG})+)'
    rf'|floor/figure/{_CURL_SEG}/name'
    r')/?$')


def _lex_shell_segments(cmd: str):
    """[[(start, end, [(quote, text), ...]), ...], ...] — shell segments, each a
    list of words, each word its start/end offsets plus its pieces. quote is
    "'", '"' or '' (unquoted). None when the text cannot be lexed identically
    by bash and PowerShell (see the section comment)."""
    if any(c in cmd for c in _CURLY_QUOTES):
        return None
    segments: list = []
    words: list = []
    parts: list = []
    wstart = -1
    i, n = 0, len(cmd)

    def end_word(end):
        nonlocal parts, wstart
        if parts:
            words.append((wstart, end, parts))
        parts, wstart = [], -1

    def end_segment():
        nonlocal words
        if words:
            segments.append(words)
        words = []

    while i < n:
        c = cmd[i]
        if c in ' \t\r':
            end_word(i)
            i += 1
        elif c in '\n;|()' or (c == '&' and not (
                (i > 0 and cmd[i - 1] in '<>') or cmd[i + 1:i + 2] == '>')):
            end_word(i)
            end_segment()
            i += 1
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            if not parts:
                wstart = i
            parts.append(("'", cmd[i + 1:j]))
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and cmd[j] != '"':
                if cmd[j] in '\\$`':
                    return None
                j += 1
            if j >= n:
                return None
            if not parts:
                wstart = i
            parts.append(('"', cmd[i + 1:j]))
            i = j + 1
        else:
            if c in '\\`#' or (c == '$' and cmd[i + 1:i + 2] in ("'", '"', '(')) \
                    or cmd[i:i + 2] in ('<<', '<(', '>('):
                return None
            if not parts:
                wstart = i
            if parts and parts[-1][0] == '':
                parts[-1] = ('', parts[-1][1] + c)
            else:
                parts.append(('', c))
            i += 1
    end_word(n)
    end_segment()
    return segments


def _curl_prose_data_words(words):
    """The data-value words of ONE curl segment (`words[0]` is curl) that may be
    masked, or None when the segment cannot be PROVEN to talk only to a prose
    route of Clayrune's own API. Walks the argv; see the section comment."""
    def literal(word):
        # What the word spells, or None when part of it could expand: an
        # unquoted piece with a $, ~ or glob character is not a fixed string.
        for quote, text in word[2]:
            if not quote and any(c in text for c in '$~*?[]{}!'):
                return None
        return ''.join(text for _, text in word[2])

    def value_of(k):
        # The word after an option, as text; None when it is missing, could
        # expand, or starts with `-` (curl would take it as the value, but a
        # reader cannot tell it from the next option).
        if k >= len(words):
            return None
        text = literal(words[k])
        return None if text is None or text.startswith('-') else text

    urls, data = [], []
    k = 1
    while k < len(words):
        text = literal(words[k])
        if text is None:
            return None
        if not any(q for q, _ in words[k][2]) and re.match(r'\d*[<>]', text):
            k += 1                            # a redirection: `2>&1`, `>/dev/null`
            continue
        if not text.startswith('-') or text == '-':
            urls.append(text)
            k += 1
            continue
        if any(q for q, _ in words[k][2]):
            return None                       # a quoted option: not read
        if text.startswith('--'):
            name, is_data = text, text in _CURL_DATA_OPTS
            takes_value = name in _CURL_VALUE_LONG or is_data
            skip = name in _CURL_NOARG_LONG
        else:
            flags = text[1:]
            name, is_data = text, text == '-d'
            takes_value = len(flags) == 1 and (flags in _CURL_VALUE_SHORT or is_data)
            skip = all(c in _CURL_NOARG_SHORT for c in flags)
        if skip:
            k += 1
        elif takes_value:
            value = value_of(k + 1)
            if value is None:
                return None
            if is_data:
                if not all(q for q, _ in words[k + 1][2]):
                    return None               # unquoted text rides along
                data.append(words[k + 1])
            elif name == '--url':
                urls.append(value)
            k += 2
        else:
            return None
    if not urls or not data:
        return None
    for url in urls:
        m = _CURL_PROSE_URL_RE.match(url)
        if not m or not _CURL_PROSE_ROUTE_RE.match(m.group('path') or ''):
            return None
    return data


def _mask_curl_data_args(cmd: str) -> str:
    """Replace the quoted value of a top-level curl's -d/--data*/--json with the
    inert placeholder. Returns `cmd` unchanged unless every condition in the
    section comment above holds. Never raises."""
    try:
        if 'curl' not in cmd.lower():
            return cmd
        segments = _lex_shell_segments(cmd)
        if not segments:
            return cmd
        spans = []
        for words in segments:
            head = words[0][2]
            if len(head) != 1 or head[0][0] != '' or \
                    head[0][1].lower().rsplit('/', 1)[-1] not in _CURL_HEADS:
                continue
            for val in _curl_prose_data_words(words) or ():
                spans.append((val[0], val[1]))
        out = cmd
        for start, end in sorted(spans, reverse=True):
            out = f"{out[:start]}'{_MASK_TOKEN}'{out[end:]}"
        return out
    except Exception:
        return cmd


def _mask_inert_prose(cmd: str, mask_python_c: bool = True) -> str:
    """Replace provably-inert data spans with a placeholder. Best-effort and
    conservative: any span we cannot PROVE inert is left untouched (fails
    toward blocking, never toward allowing). `mask_python_c=False` keeps a
    `python -c` body visible, for the one check (_mod_plant_guard) that must
    read what such a body writes."""
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

    # Runs FIRST, on the original text: its quote pairing is its own (below),
    # not inherited from the regex masks that follow.
    out = _mask_curl_data_args(cmd)
    out = _MSG_ARG_RE.sub(_msg_repl, out)
    out = _GREP_PATTERN_RE.sub(_grep_repl, out)
    if mask_python_c:
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
    '--proxy-header', '--proxy-user', '--proxy-cacert', '--proxy-cert',
    '--proxy-key', '--proxy-pass', '--preproxy', '--noproxy', '--socks4',
    '--socks4a', '--socks5', '--socks5-hostname', '--unix-socket',
    '--abstract-unix-socket', '--aws-sigv4', '--ciphers', '--dns-servers',
    '--doh-url', '--engine', '--expect100-timeout', '--keepalive-time',
    '--local-port', '--login-options', '--mail-auth', '--mail-from',
    '--mail-rcpt', '--netrc-file', '--pinnedpubkey', '--proto',
    '--proto-default', '--proto-redir', '--pubkey', '--quote',
    '--request-target', '--service-name', '--speed-limit', '--speed-time',
    '--tls-max', '--variable', '--url-query', '--etag-save', '--etag-compare',
    '--capath', '--crlfile', '--cert-type', '--key-type', '--pass',
    '--time-cond', '--continue-at', '--telnet-option', '--alt-svc', '--hsts',
    '--parallel-max', '--create-file-mode', '--ftp-port', '--delegation',
    '--happy-eyeballs-timeout-ms', '--ip-tos', '--trace-config',
}
# Known value-less curl options. A long option in NEITHER table has unknown
# arity, so the next token might be its value; a group holding one may not
# downgrade a visible send to a read (review #10 N16).
_CURL_LONG_FLAG = {
    '--silent', '--show-error', '--fail', '--fail-with-body', '--fail-early',
    '--compressed', '--insecure', '--include', '--verbose', '--http1.0',
    '--http1.1', '--http2', '--http2-prior-knowledge', '--http3', '--globoff',
    '--get', '--no-get', '--head', '--no-progress-meter', '--progress-bar',
    '--remote-name', '--remote-name-all', '--remote-header-name', '--no-buffer',
    '--ipv4', '--ipv6', '--location', '--location-trusted', '--create-dirs',
    '--path-as-is', '--no-keepalive', '--tcp-nodelay', '--raw', '--ssl-reqd',
    '--netrc', '--netrc-optional', '--anyauth', '--basic', '--digest',
    '--ntlm', '--negotiate', '--post301', '--post302', '--post303',
    '--junk-session-cookies', '--styled-output', '--no-styled-output',
    '--tlsv1', '--tlsv1.2', '--tlsv1.3', '--disable', '--parallel',
    '--retry-all-errors', '--retry-connrefused', '--no-sessionid', '--next',
}
_CURL_SHORT_FLAG = set('sSfkivIgGjlnNOR0123469#JLpqZMV')
_CURL_BODY_LONG = {'--data', '--data-raw', '--data-binary', '--data-urlencode',
                   '--data-ascii', '--json'}
_CURL_UPLOAD_LONG = {'--form', '--form-string', '--upload-file'}


def _net_tokens(seg: str) -> list:
    r"""Split a segment into argv the way a shell does, keeping quoted
    whitespace INSIDE its argument (review #9 N13: splitting first and
    stripping quotes after turned `--header="X: --get"` into a real
    `--get`). Single quotes are literal; inside double quotes `\"` and `\\`
    are escapes. Outside quotes a backslash escapes only whitespace, a
    quote or another backslash; any other backslash is kept, so a Windows
    path (`C:\tools\curl.exe`) still names its tool. An unterminated quote runs
    to the end of the segment.

    Unquoted redirections (`2>&1`, `2>/dev/null`, `> out.json`, `<in`, a
    trailing `&`) are the shell's, not the tool's: they and their target
    word are dropped (MC-1013, 2026-09-30). Kept as argv they read as extra
    non-local destinations, so a localhost POST with `2>&1` was refused.
    A QUOTED `>` stays an argument, and the target of a real redirect is a
    file the shell opens, never a URL the tool sends to."""
    toks, cur, have, skip = [], [], False, False
    i, n = 0, len(seg)
    while i < n:
        ch = seg[i]
        if ch.isspace():
            if have:
                if not skip:
                    toks.append(''.join(cur))
                skip = False
                cur, have = [], False
            i += 1
            continue
        if ch in '<>' or (ch == '&' and not have):
            if ch == '&' and not seg.startswith('>', i + 1):
                i += 1  # background `&`: a separator, not an argument
                continue
            fd = ''.join(cur)
            if have and not skip and not fd.isdigit():
                toks.append(fd)  # `word>file`: the word is still an argument
            skip = False
            cur, have = [], False
            i += 1 if ch != '&' else 2
            while i < n and seg[i] in '<>':
                i += 1
            if i < n and seg[i] == '&':  # `>&1`, `>&-`: a descriptor, no target word
                i += 1
                while i < n and (seg[i].isdigit() or seg[i] == '-'):
                    i += 1
                continue
            skip = True  # the next word is the redirect's target
            continue
        have = True
        if ch == "'":
            j = seg.find("'", i + 1)
            j = n if j < 0 else j
            cur.append(seg[i + 1:j])
            i = j + 1
            continue
        if ch == '"':
            i += 1
            while i < n and seg[i] != '"':
                if seg[i] == '\\' and i + 1 < n and seg[i + 1] in '"\\':
                    i += 1
                cur.append(seg[i])
                i += 1
            i += 1
            continue
        if ch == '\\' and i + 1 < n and (seg[i + 1].isspace() or seg[i + 1] in '"\'\\'):
            cur.append(seg[i + 1])  # `X:\ --get` is one argument (review #10 N17)
            i += 2
            continue
        cur.append(ch)
        i += 1
    if have and not skip:
        toks.append(''.join(cur))
    return toks


_LOCAL_HOSTNAMES = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}
# Options that send a request somewhere other than its URL's host, so a
# local URL no longer proves a local destination.
_CURL_REROUTE = {'--proxy', '--preproxy', '--socks4', '--socks4a', '--socks5',
                 '--socks5-hostname', '--resolve', '--connect-to', '-x'}
_DEST_LIKE_RE = re.compile(r'^(\[[0-9A-Fa-f:.]+\]|[\w-]+(\.[\w-]+)+|[\w-]+:\d+)(:\d+)?([/?#].*)?$')


def _is_local_url(url: str) -> bool:
    """Locality from the parsed HOSTNAME, never from text anywhere in the
    command (review #12 N19: `-H "X-Source: localhost"` exempted an
    external POST, and so did `localhost.example.com`)."""
    try:
        host = urllib.parse.urlsplit(url if '://' in url else 'http://' + url).hostname
    except ValueError:
        return False
    return host in _LOCAL_HOSTNAMES


def _all_local(targets: list) -> bool:
    return bool(targets) and all(_is_local_url(u) for u in targets)


def _curl_mutates(args: list) -> bool:
    """Each `--next` / `:` starts a new transfer with its own method and
    data (reviews #8-#9, N10), so each transfer is judged on its own and
    any mutating one makes the invocation a send. The separator is found
    while walking options, never by scanning raw tokens: `-s:` ends a
    transfer, but `--data --next` is the data value `--next` (N14).
    True means a NON-LOCAL send: a transfer is exempt only when every URL
    of THAT transfer is a local hostname and nothing reroutes it (N19)."""
    state = {'method': None, 'body': False, 'upload': False, 'get': False,
             'verb_seen': False, 'unknown': False, 'urls': [],
             'rerouted': False}

    def sends() -> bool:
        if state['unknown'] and (state['body'] or state['upload']
                                 or state['verb_seen']):
            pass  # an option of unknown arity: no read downgrade
        elif state['method'] is not None:
            if state['method'].upper() not in _MUTATING_VERBS:
                return False
        elif not (state['upload'] or (state['body'] and not state['get'])):
            return False
        return state['rerouted'] or not _all_local(state['urls'])

    def reset() -> None:
        state.update(method=None, body=False, upload=False, get=False,
                     verb_seen=False, unknown=False, urls=[], rerouted=False)

    def set_method(val: str) -> None:
        state['method'] = val
        if val.upper() in _MUTATING_VERBS:
            state['verb_seen'] = True

    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == '--next':
            if sends():
                return True
            reset()
            continue
        if tok == '--no-get':
            state['get'] = False
            continue
        if tok.startswith('--') and len(tok) > 2:
            name, eq, val = tok.partition('=')
            if name in _CURL_LONG_ARG and not eq:
                val = args[i] if i < len(args) else ''
                i += 1
            elif name not in _CURL_LONG_ARG and name not in _CURL_LONG_FLAG:
                state['unknown'] = True
            if name in _CURL_REROUTE:
                state['rerouted'] = True
            if name == '--url':
                state['urls'].append(val)
            elif name == '--request':
                set_method(val)
            elif name in _CURL_BODY_LONG:
                state['body'] = True
            elif name in _CURL_UPLOAD_LONG:
                state['upload'] = True
            elif name == '--get':
                state['get'] = True
            continue
        if tok.startswith('-') and len(tok) > 1:
            letters = tok[1:]
            for j, ch in enumerate(letters):
                if ch == ':':
                    if sends():
                        return True
                    reset()
                    continue
                if ch == 'G':
                    state['get'] = True
                if ch not in _CURL_SHORT_ARG:
                    if ch not in _CURL_SHORT_FLAG:
                        state['unknown'] = True
                    continue
                val = letters[j + 1:]
                if not val:
                    val = args[i] if i < len(args) else ''
                    i += 1
                if ch == 'x':
                    state['rerouted'] = True
                if ch == 'X':
                    set_method(val)
                elif ch == 'd':
                    state['body'] = True
                elif ch in 'FT':
                    state['upload'] = True
                break
            continue
        state['urls'].append(tok)
    return sends()


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


_HTTPIE_VALUE_OPTS = {
    '--auth', '-a', '--auth-type', '-A', '--timeout', '--output', '-o',
    '--session', '--session-read-only', '--verify', '--cert', '--cert-key',
    '--cert-key-pass', '--proxy', '--print', '-p', '--pretty', '--style', '-s',
    '--format-options', '--max-redirects', '--max-headers', '--boundary',
    '--ssl', '--ciphers', '--default-scheme', '--response-charset',
    '--response-mime', '--raw', '--history-print', '-P',
}
# Earliest separator wins; `==` is a query parameter and `:` a header, the
# rest are body items (review #8 N12).
_HTTPIE_ITEM_RE = re.compile(r'^(?:[^=:@\\]|\\.)*?(==|:=@|=@|:=|=|@|:)')


def _httpie_mutates(args: list) -> bool:
    pos, form = [], False
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok.startswith('-') and len(tok) > 1:
            name, eq, _ = tok.partition('=')
            if name in ('--raw',) or tok in ('-f', '--form', '--multipart'):
                form = True
            if name in _HTTPIE_VALUE_OPTS and not eq:
                i += 1  # its value is not the method (review #8 N11)
            continue
        pos.append(tok)
    method = None
    if len(pos) >= 2 and re.fullmatch(r'[A-Za-z]+', pos[0]):
        method, pos = pos[0], pos[1:]
    if method is not None:
        return method.upper() in _MUTATING_VERBS
    if form:
        return True
    for item in pos[1:]:
        m = _HTTPIE_ITEM_RE.match(item)
        if m and m.group(1) not in ('==', ':'):
            return True
    return False


# KNOWN GAPS (accepted 2026-09-28, MC-994, after 13 second-vendor reviews).
# This detector stops ACCIDENTAL external sends by a well-meaning unattended
# agent using an ordinary curl/wget/httpie/iwr spelling. It is not an evasion
# barrier (2026-09-12 position): any unknown executable (python -c requests,
# node, a script file) already sends unchecked by design. Remaining misses are
# unusual argument spellings of the four named tools (per-tool argument roles
# are approximated, not fully parsed). A new CLASS of miss still blocks a
# merge; another spelling of these does not. Reviews: _scratch/mc994-review3-*.
def _segment_mutates(seg: str) -> bool:
    """True when this shell segment runs a named HTTP tool that sends a
    mutating request. Parsed from argv with each tool's own option rules
    (Fenn's reviews #6-#7, N6-N8): an option counts as a method only when it
    IS the tool's method option, so `--output`, `-o post.json` or
    `-OutFile delete.txt` never read as sends, and curl short clusters
    (`-sdfixture`) are walked the way curl walks them. These are the tools'
    documented forms; the 2026-09-12 position against chasing evasions
    still stands. A quoted argument that itself names a tool (`bash -c
    "curl -X POST ...; curl -X GET ..."`) is a program: it is split at its
    OWN operators and each command judged alone (review #11 N18), so a
    later GET cannot overwrite an earlier POST's method. The local-host
    exemption is judged per command too, so a nested localhost call
    cannot exempt a nested external one. True means a NON-LOCAL send."""
    toks = _net_tokens(seg)
    for idx, tok in enumerate(toks):
        m = _NET_HEAD_RE.search(tok)
        if not m:
            if _NET_TOOL_RE.search(tok) and ' ' in tok and any(
                    _segment_mutates(inner) for inner in _net_segments(tok)):
                return True
            continue
        head = m.group(1).lower()
        args = toks[idx + 1:]
        if head == 'curl':
            return _curl_mutates(args)
        if head == 'wget':
            mutates = _wget_mutates(args)
        elif head in ('http', 'https'):
            mutates = _httpie_mutates(args)
        else:
            mutates = _ps_web_mutates(args)
        if not mutates:
            return False
        # Own API calls are exempt only when every destination-shaped
        # argument is a local hostname (review #12 N19). A filename that
        # looks like a host fails closed.
        # PowerShell binds a value with a colon (`-Uri:URL`); that value is
        # a candidate destination too (review #13 N19), or the real URI
        # vanishes and a local-looking body decides.
        cands = [a.split(':', 1)[1] if a.startswith('-') and not a.startswith('--')
                 and ':' in a else a for a in args]
        dests = [a for a in cands if not a.startswith('-')
                 and ('://' in a or _DEST_LIKE_RE.match(a))]
        return not _all_local(dests)
    return False


def _net_segments(cmd: str) -> list:
    """Split at `&&`, `||`, `|`, `;` and newline only OUTSIDE quotes and
    escapes (review #10 N15: `-H "Content-Type: a/json; charset=utf-8"`
    cut curl off from its `--data`). Local to the network check; the
    shared _SHELL_SPLIT_RE keeps its other callers' behaviour."""
    segs, cur, quote = [], [], ''
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == '\\' and quote == '"' and i + 1 < n:
                cur.append(cmd[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ''
            cur.append(ch)
            i += 1
            continue
        if ch in '"\'':
            quote = ch
        elif ch == '\\' and i + 1 < n:
            cur.append(cmd[i:i + 2])
            i += 2
            continue
        elif cmd.startswith(('&&', '||'), i):
            segs.append(''.join(cur))
            cur = []
            i += 2
            continue
        elif ch in '|;\n':
            segs.append(''.join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    segs.append(''.join(cur))
    return segs


def _join_line_continuations(cmd: str) -> str:
    """Join shell line continuations before the network check, the way the
    shell does (MC-1013, 2026-09-30). Unattended agents write multi-line
    `curl ... \\<newline> -d '...'`; _net_tokens kept the newline as its own
    token, _curl_mutates read it as a non-local destination, and every such
    call to Clayrune's own API was refused (hivemind workers could not hand
    off). Bash: `\\<newline>` is REMOVED outside single quotes, not replaced
    by a space, so `cu\\<newline>rl` is judged as the `curl` bash runs.
    PowerShell: a backtick-newline outside quotes is whitespace."""
    out, quote = [], ''
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        nl = 2 if cmd.startswith('\r\n', i + 1) else 1 if cmd.startswith('\n', i + 1) else 0
        if quote == "'":
            if ch == "'":
                quote = ''
        elif ch == '\\' and cmd.startswith('\\', i + 1):
            out.append('\\\\')
            i += 2
            continue
        elif ch == '\\' and nl:
            i += 1 + nl
            continue
        elif ch == '`' and nl and not quote:
            out.append(' ')
            i += 1 + nl
            continue
        elif ch == quote:
            quote = ''
        elif ch in '"\'' and not quote:
            quote = ch
        out.append(ch)
        i += 1
    return ''.join(out)


# MC-1056: ONE browser route is open to unattended callers, the digest route (read-digest,
# under the browser API prefix). It reads a page only for a profile a human switched on,
# on a domain the human listed, and answers through a toolless model call; the caller never
# receives page text (mc/blueprints/browser_agent_read_routes.py). Everything else the
# check below names (launch, read, input, navigate) stays blocked, and `read` still
# matches the plain read route itself. The exemption is EXACT: after `read-digest` the next
# character must end the path (end of segment, quote, space, `?`, `#`), so
# `read-digest/../launch` (curl squashes dot segments before it sends), `read-digestX`
# and `read-digest.json` are all still blocked.
_BROWSER_API_RE = re.compile(
    r'/api/browser/(?:launch|input|navigate|read(?!-digest(?:$|[\s?#"\'`)])))', re.I)


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
    for seg in _net_segments(_join_line_continuations(cmd)):
        if not _NET_TOOL_RE.search(seg):
            continue
        if _BROWSER_API_RE.search(seg):
            return FenceDecision(True, "autonomous web browsing is out of steward scope - "
                                       "the browser HTTP API is the same capability as the "
                                       "browser MCP tools, which are blocked")
        if not _segment_mutates(seg):
            continue
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


# ── Agent shell-install guard (MC-1042, split from MC-1022, 2026-10-03) ─────
# Add-on installs go through a passcode + tap card (`POST /api/addons/requests`,
# approved by the human). An armed agent with Bash could still run `winget
# install`, `apt install`, `npm i -g`, `pip install requests` itself and never
# raise a card. This refuses those for ARMED sessions only (the fence does not
# arm for an attended chat; accepted in docs/ADDON_INSTALLS_SPEC.md row 4c).
#
# ALLOW / DENY RULE. Global or system installs are refused; installs that land
# inside the project stay allowed so builders and test runs keep working.
#   DENIED always: winget/choco/scoop/brew/apt/apt-get/aptitude/dnf/yum/
#     microdnf/pacman (install and upgrade verbs; `apt update`, `winget list`,
#     `brew search`, `pacman -Ss` are not installs), pipx install, uv tool
#     install, cargo install (unless --root is a local dir), go install,
#     PowerShell Install-Module/-Script/-Package/-PSResource.
#   npm/pnpm/yarn: `-g`/`--global`/`--location=global` and `yarn global add`
#     are denied; `npm install`/`npm ci`/`pnpm add` (node_modules in the
#     project) stay allowed, as does `--prefix <local dir>`.
#   pip (pip, pip3, python -m pip, py -m pip, uv pip): `--user` and
#     `--break-system-packages` are denied. Otherwise ALLOWED when the install
#     is project-bound: `--target <local dir>`; a venv-shaped executable path
#     inside the project (`.venv/bin/pip`, `.venv\Scripts\python -m pip`); an
#     earlier `source .venv/bin/activate` in the same command; or an install
#     that names only things the project declares (`-r requirements.txt`,
#     `-e .`, `pip install .[dev]`, no package names, no URLs). A named
#     index package outside a venv (`pip install requests`) is DENIED.
# "Local" = a relative path that does not climb out with `..`, or an absolute
# path inside the hook's cwd.
# KNOWN GAPS, accepted: command text only. `pip install -r req.txt` is allowed
# even though an agent can write req.txt first; a plain `npm install` is not
# checked for which directory it runs in; `cd` is not tracked; installers that
# are not package managers (msiexec, `curl | sh`, setup.exe) and `powershell
# -EncodedCommand` are out of scope here. Wrappers peeled: sudo/doas/env/
# nohup/time/xargs/wsl/start, bash/sh/zsh/su/pwsh/powershell/cmd -c/-Command/
# /c, eval/iex, Start-Process, and `&&` `;` `|` `$( )` chains. No full shell
# parsing, so a hidden-in-a-variable verb is still _enabling_construct's job.
_INSTALL_ADDON_POINTER = (
    "Installs go through the add-on card, which the human approves with their "
    "passcode: POST http://127.0.0.1:5199/api/addons/requests with "
    '{"addon_id": "<catalogue id>", "reason": "<why>"} (it installs nothing '
    "itself). Something outside the catalogue is a request to the human in "
    "your final message. Project-local installs stay allowed: npm install, "
    "pip install -r/-e ., a venv in the worktree.")

_INSTALL_VERBS = {
    'winget': {'install', 'add', 'upgrade', 'update'},
    'choco': {'install', 'upgrade'},
    'chocolatey': {'install', 'upgrade'},
    'scoop': {'install', 'update'},
    'brew': {'install', 'reinstall', 'upgrade'},
    'apt': {'install', 'reinstall', 'upgrade', 'dist-upgrade', 'full-upgrade'},
    'apt-get': {'install', 'reinstall', 'upgrade', 'dist-upgrade', 'full-upgrade'},
    'aptitude': {'install', 'reinstall', 'upgrade', 'safe-upgrade', 'full-upgrade'},
    'dnf': {'install', 'reinstall', 'upgrade', 'update', 'localinstall', 'groupinstall'},
    'yum': {'install', 'reinstall', 'upgrade', 'update', 'localinstall', 'groupinstall'},
    'microdnf': {'install', 'reinstall', 'upgrade', 'update'},
    'pipx': {'install', 'upgrade', 'upgrade-all', 'reinstall', 'reinstall-all'},
    'go': {'install'},
}
_INSTALL_ALWAYS = frozenset({'cinst', 'cup', 'install-module', 'install-script',
                             'install-package', 'install-psresource'})
_NODE_INSTALL_VERBS = frozenset({'install', 'i', 'in', 'add', 'update', 'up', 'upgrade'})
_INSTALL_PIP_RE = re.compile(r'^pip\d*(?:\.\d+)?$')
_INSTALL_PY_RE = re.compile(r'^(?:python\d*(?:\.\d+)?|py|pypy\d*)$')
# Value-taking pip options, so their values are not read as package names.
_PIP_VALUE_OPTS = frozenset({
    '-r', '--requirement', '-e', '--editable', '-c', '--constraint', '-t',
    '--target', '--prefix', '--root', '-i', '--index-url', '--extra-index-url',
    '-f', '--find-links', '--python', '--platform', '--python-version',
    '--implementation', '--abi', '--cache-dir', '--src', '--upgrade-strategy',
    '--log', '--proxy', '--retries', '--timeout', '--cert', '--client-cert',
    '--trusted-host', '--progress-bar', '--only-binary', '--no-binary',
    '--config-settings', '-C', '--global-option', '--install-option', '-b',
    '--report', '--root-user-action', '--keyring-provider', '--exists-action'})
_WRAPPER_VALUE_OPTS = {
    'sudo': {'-u', '-g', '-h', '-p', '-C', '-D', '-R', '-r', '-t', '-T', '-U'},
    'doas': {'-u', '-C'}, 'env': {'-u', '-C', '-S'}, 'nice': {'-n'},
    'xargs': {'-n', '-I', '-L', '-P', '-s', '-d', '-E', '-a'},
    'wsl': {'-d', '--distribution', '-u', '--user', '--cd'},
    'nohup': set(), 'time': set(), 'command': set(), 'exec': set(),
    'stdbuf': {'-i', '-o', '-e'}, 'builtin': set(), 'call': set(), 'start': set(),
}
_SHELL_DASH_C = frozenset({'bash', 'sh', 'zsh', 'dash', 'ksh', 'fish', 'su'})
_PS_COMMAND_RE = re.compile(r'^-c(?:o(?:m(?:m(?:a(?:n(?:d)?)?)?)?)?)?$', re.I)
_ACTIVATE_WORD_RE = re.compile(r'[\\/]activate(?:\.bat|\.ps1)?$', re.I)
_WIN_DRIVE_RE = re.compile(r'^[A-Za-z]:[\\/]')


def _install_split(cmd: str, parens: bool = True) -> list:
    """[[word, ...], ...]: shell segments of dequoted words. Splits on ; & |
    newline ( ) and backtick outside quotes (not on ( ) and backtick when
    `parens` is False, so `Set-Content -Path (Join-Path $HOME .claude)` stays
    one segment); a backslash is a literal (Windows paths). Not a shell
    parser, only enough to find a command's first words."""
    segs: list = []
    words: list = []
    cur: list = []
    has_word = False
    quote = ''

    def end_word():
        nonlocal cur, has_word
        if has_word:
            words.append(''.join(cur))
        cur, has_word = [], False

    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == quote:
                quote = ''
            elif ch == '\\' and quote == '"' and cmd[i + 1:i + 2] == '"':
                cur.append('"')
                i += 1
            else:
                cur.append(ch)
        elif ch in '"\'':
            quote, has_word = ch, True
        elif ch in ' \t\r':
            end_word()
        elif ch in (';\n|&()`' if parens else ';\n|&'):
            end_word()
            if words:
                segs.append(words)
            words = []
        else:
            cur.append(ch)
            has_word = True
        i += 1
    end_word()
    if words:
        segs.append(words)
    return segs


def _install_prog(word: str) -> str:
    base = re.split(r'[\\/]', word)[-1].lower()
    return _PROG_EXT_RE.sub('', base)


def _install_path_is_local(raw: str, cwd: Optional[str]) -> bool:
    """A relative path that stays under the cwd, or an absolute path inside it."""
    raw = (raw or '').strip()
    if not raw or raw[0] in '~$%' or '$' in raw or '%' in raw:
        return False
    if raw.startswith('\\\\') or raw.startswith('//') or raw.startswith('-'):
        return False
    if re.match(r'^[A-Za-z][\w+.-]+:', raw):
        return False                                 # a URL (https:, git+ssh:), not a path
    try:
        base = Path(cwd or os.getcwd()).resolve()
        absolute = raw.startswith('/') or bool(_WIN_DRIVE_RE.match(raw))
        if absolute:
            return _is_within(Path(raw).resolve(), base)
        parts = raw.replace('\\', '/').split('/')
        depth = 0
        for part in parts:
            if part in ('', '.'):
                continue
            depth += -1 if part == '..' else 1
            if depth < 0:
                return False
        return True
    except Exception:
        return False


def _install_opt_value(args: list, *names: str) -> Optional[str]:
    """Value of `--opt value` / `--opt=value` for any of `names`, else None."""
    for k, w in enumerate(args):
        for name in names:
            if w == name and k + 1 < len(args):
                return args[k + 1]
            if w.startswith(name + '=') and name.startswith('--'):
                return w[len(name) + 1:]
    return None


def _install_is_global_flag(args: list) -> bool:
    for k, w in enumerate(args):
        if w in ('--global', '-g') or re.match(r'^-[A-Za-z]*g[A-Za-z]*$', w):
            return True
        if w == '--location=global' or (w == '--location' and args[k + 1:k + 2] == ['global']):
            return True
    return False


def _install_pip_label(args: list, cwd: Optional[str], venv: bool,
                       exe_local_venv: bool) -> Optional[str]:
    """Label when this `pip ... install ...` is refused, else None. `args` are
    the words after the program (and after `-m pip` / `uv pip`)."""
    if 'install' not in args:
        return None
    if '--user' in args or '--break-system-packages' in args:
        return "pip install --user / --break-system-packages"
    for opt in ('--prefix', '--root'):
        val = _install_opt_value(args, opt)
        if val is not None and not _install_path_is_local(val, cwd):
            return f"pip install {opt} outside the project"
    target = _install_opt_value(args, '--target', '-t')
    if target is not None and _install_path_is_local(target, cwd):
        return None
    if venv or exe_local_venv:
        return None
    # Outside a venv pip writes to the shared interpreter: only what the
    # project itself declares may go in.
    declared = ('-r', '--requirement', '-e', '--editable', '-c', '--constraint')
    positional: list = []
    skip = False
    for k, w in enumerate(args):
        if skip:
            skip = False
            continue
        glued = re.match(r'^-([rec])(.+)$', w)
        if w.startswith('--') and '=' in w:
            name, _, val = w.partition('=')
            if name in declared and not _install_path_is_local(val, cwd):
                return "pip install from a non-local requirement file or URL"
            continue
        if glued and not w.startswith('--'):
            if not _install_path_is_local(glued.group(2), cwd):
                return "pip install from a non-local requirement file or URL"
            continue
        if w in declared:
            val = args[k + 1] if k + 1 < len(args) else ''
            if not _install_path_is_local(val, cwd):
                return "pip install from a non-local requirement file or URL"
            skip = True
            continue
        if w in _PIP_VALUE_OPTS:
            skip = True
            continue
        if w.startswith('-') or w == 'install' or re.match(r'^\d*[<>]', w):
            continue
        positional.append(w)
    for w in positional:
        path = re.sub(r'\[[^\]]*\]$', '', w)
        if path and (re.match(r'^\.{1,2}(?:[\\/].*)?$', path)
                     and _install_path_is_local(path, cwd)):
            continue
        return "pip install of a package by name outside a venv"
    return None


def _install_exe_is_local_venv(word: str, cwd: Optional[str]) -> bool:
    """`.venv/bin/pip`, `.\\venv\\Scripts\\python.exe`, or an absolute one
    inside the cwd: `<local dir>/(bin|Scripts)/<exe>`."""
    parts = word.replace('\\', '/').split('/')
    if len(parts) < 3 or parts[-2] not in ('bin', 'Scripts', 'scripts'):
        return False
    return _install_path_is_local('/'.join(parts[:-2]) or '/', cwd)


def _install_segment_label(words: list, cwd: Optional[str], venv: bool,
                           depth: int) -> Optional[str]:
    """Label if one segment's command is a refused install. Peels wrappers."""
    k = 0
    while k < len(words):
        w = words[k]
        head = _install_prog(w)
        if (re.match(r'^\w+=', w) and not w.startswith('-')) \
                or w in ('{', '}', '!', 'then', 'do', 'else'):
            k += 1                                        # FOO=bar prog; { prog; }; then prog
            continue
        if head in _WRAPPER_VALUE_OPTS:
            vals = _WRAPPER_VALUE_OPTS[head]
            k += 1
            while k < len(words):
                nxt = words[k]
                if head == 'start' or head == 'call':
                    if re.match(r'^/[A-Za-z]+$', nxt):
                        k += 1
                        continue
                    break
                if nxt == '--':
                    k += 1
                    break
                if nxt.startswith('-') and nxt != '-':
                    k += 2 if nxt in vals else 1
                    continue
                if head == 'env' and re.match(r'^\w+=', nxt):
                    k += 1
                    continue
                break
            continue
        break
    else:
        return None
    word = words[k]
    prog = _install_prog(word)
    rest = words[k + 1:]
    if depth < 4:
        # A shell or eval that carries a command string: judge that string.
        inner = None
        if prog in _SHELL_DASH_C:
            for j, a in enumerate(rest):
                if re.match(r'^-[A-Za-z]*c[A-Za-z]*$', a) and j + 1 < len(rest):
                    inner = rest[j + 1]
                    break
        elif prog in ('pwsh', 'powershell'):
            for j, a in enumerate(rest):
                if _PS_COMMAND_RE.match(a):
                    inner = ' '.join(rest[j + 1:])
                    break
        elif prog == 'cmd':
            for j, a in enumerate(rest):
                if a.lower() in ('/c', '/k'):
                    inner = ' '.join(rest[j + 1:])
                    break
        elif prog in ('eval', 'iex', 'invoke-expression'):
            inner = ' '.join(rest)
        if inner is not None:
            return _install_label(inner, cwd, depth + 1, venv)
    if prog in _INSTALL_ALWAYS:
        return prog
    verbs = _INSTALL_VERBS.get(prog)
    if verbs is not None:
        hit = next((a for a in rest if a.lower() in verbs), None)
        if hit:
            return f"{prog} {hit.lower()}"
        return None
    if prog == 'pacman':
        for a in rest:
            if a == '--sync' or re.match(r'^-[A-Za-z]*S[A-Za-z]*$', a):
                if not re.search(r'[silgcp]', a.replace('S', '', 1)):
                    return "pacman -S"   # -Ss/-Si/-Sl/-Sg/-Sc/-Sp read or clean, the rest install
            if a == '--upgrade' or re.match(r'^-[A-Za-z]*U[A-Za-z]*$', a):
                return "pacman -U"
        return None
    if prog == 'cargo':
        first = next((a for a in rest if not a.startswith('-') and not a.startswith('+')), '')
        if first != 'install':
            return None
        root = _install_opt_value(rest, '--root')
        return None if root is not None and _install_path_is_local(root, cwd) \
            else "cargo install"
    if prog in ('npm', 'pnpm', 'yarn'):
        if prog == 'yarn':
            if 'global' in rest and any(a in ('add', 'upgrade') for a in rest):
                return "yarn global add"
            return None
        verb = next((a.lower() for a in rest if a.lower() in _NODE_INSTALL_VERBS), None)
        if verb is None:
            return None
        if _install_is_global_flag(rest):
            return f"{prog} {verb} -g"
        pre = _install_opt_value(rest, '--prefix')
        if pre is not None and not _install_path_is_local(pre, cwd):
            return f"{prog} {verb} --prefix outside the project"
        return None
    if prog == 'uv':
        first = next((a for a in rest if not a.startswith('-')), '')
        if first == 'tool' and 'install' in rest:
            return "uv tool install"
        if first == 'pip' and 'install' in rest:
            # uv refuses to install outside a venv unless told --system.
            if '--system' in rest or '--break-system-packages' in rest:
                return "uv pip install --system"
        return None
    if _INSTALL_PIP_RE.match(prog):
        return _install_pip_label(rest, cwd, venv, _install_exe_is_local_venv(word, cwd))
    if _INSTALL_PY_RE.match(prog):
        for j in range(len(rest) - 1):
            if rest[j] == '-m' and rest[j + 1] == 'pip':
                return _install_pip_label(rest[j + 2:], cwd, venv,
                                          _install_exe_is_local_venv(word, cwd))
    return None


def _install_label(cmd: str, cwd: Optional[str], depth: int = 0,
                   venv: bool = False) -> Optional[str]:
    """First refused install in `cmd` (a whole command line), or None."""
    cmd = _unwrap_start_process(_join_line_continuations(cmd))
    for words in _install_split(cmd):
        if any(_ACTIVATE_WORD_RE.search(w) and _install_path_is_local(w, cwd)
               for w in words):
            venv = True                  # `source .venv/bin/activate && pip ...`
            continue
        label = _install_segment_label(words, cwd, venv, depth)
        if label:
            return label
    return None


def _agent_shell_install(cmd: str, cwd: Optional[str] = None) -> FenceDecision:
    """Block an armed agent's own system-level install (see the section
    comment). Overridable: a human's "Allow once" may still cover one."""
    try:
        label = _install_label(cmd, cwd)
    except Exception:
        return FenceDecision(False, '')      # never wedge the agent on a parse bug
    if not label:
        return FenceDecision(False, '')
    return FenceDecision(True, f"{label} is an agent-run system-level install. "
                               f"{_INSTALL_ADDON_POINTER}")


# ── Mod / plugin planting guard (2026-10-03) ────────────────────────────────
# Claude Code 2.1.287+ auto-loads a folder under ~/.claude/skills/<name> (or a
# project's .claude/skills/<name>) as a plugin when it holds
# .claude-plugin/plugin.json and hooks/hooks.json, and `claude -p` always loads
# fresh. A plugin's tool.call hook sits ABOVE settings PreToolUse, so it can
# answer a call without next() and this fence never runs; $.process / $.fs act
# outside tool calls entirely. classify_action already refuses Write/Edit
# under /.claude/, but a SHELL write there was allowed, so an armed agent could
# plant a mod that unfences every later run. For armed sessions this refuses,
# never passably (a human-owned supply chain, like the Write rule):
#   - a shell write, copy, move, mkdir, redirect, tee, in-place edit, download
#     or extract whose target is a `.claude` path (home or project), whether
#     spelled as a command, as `bash -c` / `pwsh -Command` / `-EncodedCommand`,
#     as Start-Process, or as the code of python/node/perl/ruby -c, a
#     heredoc to an interpreter, or a .NET call;
#   - taking one away (2026-10-12): `mv`/`Move-Item`/`git mv`, `rm`/`rmdir`/`del`/
#     `Remove-Item`/`git rm`/`find -delete`, or a program body that deletes, whose
#     SOURCE is the `.claude` directory itself or a settings file directly in it
#     (a moved settings.json drops the fence's own hook registration). A copy OUT
#     stays allowed, and so does moving a subfolder out (`mv ~/.claude/skills/old
#     _scratch/old`, pinned by tests/test_fence_mod_plant.py);
#   - any CLAUDE_CODE_PLUGIN_*DIR* variable being SET (`CLAUDE_CODE_PLUGIN_DIRS`
#     plugin folders, `_DIR_WATCH`, `_SEED_DIR`, `_CACHE_DIR`), and the
#     `--plugin-dir` / `--plugin-url` flags;
#   - `claude plugin install|i|enable|update|init|new` and
#     `claude plugin marketplace add|update`.
# Reading `.claude` and prose that merely mentions it stay allowed: a redirect
# or write verb must AIM at the path, and -m messages, grep patterns and
# quoted heredoc bodies are masked first (a `python -c` body is the one span
# kept visible, see _mask_inert_prose).
# Own-memory topic files: the Write-tool exception (_is_own_memory_topic_file)
# does NOT extend to the shell. The shell has no project_root to judge it by
# and tests/test_fence_memory_dir.py pins "Bash is untouched by the memory
# allowance"; an agent writes its notes with the Write tool.
# KNOWN GAPS, accepted (command text only): a script file that writes .claude
# (`python _scratch/plant.py`), a path built from a variable or glob, a
# relative write whose cwd the hook payload does not show to be inside
# `.claude`, `git apply`/`stash apply`/`reset --hard` (or a `checkout`/`restore`
# with no pathspec naming .claude) materialising a tracked .claude file, and
# `claude plugin eval|test` running a plugin that is already
# on disk. awk and sed write with no -i and no redirect word, the target sits
# in their quoted program text (_mp_script_writes); gaps there: an awk path
# passed as a variable (`-v f=.claude/x '{print > f}'`), `-f script`, sed `e`,
# a perl pipe open. In-place editors and output-file options/operands are
# named too (_mp_named_output_writes); gaps: `vim -R` made writable (`set noro`,
# a `-S` script or commands on stdin), uniq/xxd/split written as `IN OUT`
# through a variable, and the same shape in tools not named (zip, like
# `tar -c`, makes an archive, not a loadable file). Other writers by name
# (_mp_tool_writes): dos2unix & co, sponge, `yq -i`, gunzip/`gzip -d` & co (they
# write the de-suffixed file beside the archive), certutil -decode/-encode, and
# `git checkout|restore|checkout-index` whose pathspec names .claude.
# Living in fence.py rather than a new module on purpose:
# the hook is a standalone stdlib script, and only these four basenames are
# protected from agent edits (see the `_fence_names` check in classify_action).
_MP_CLAUDE_RE = re.compile(r'(?<![\w-])\.claude(?![\w.-])', re.I)
_MP_WORKTREE_RE = re.compile(r'^/worktrees(?:/|$)', re.I)
_MP_ENV_NAME = r'CLAUDE_CODE_PLUGIN_\w*DIR\w*'
_MP_ENV_RE = re.compile(
    r'(?:\$env:|\benv:|\bsetx\s+|\bsetenv\s+|SetEnvironmentVariable\W{0,3}'
    r'|environ\W{0,3}|process\.env\W{0,3}|putenv\W{0,3})\s*' + _MP_ENV_NAME
    + r'|' + _MP_ENV_NAME + r'''['"\]]?\s*[:+]?=''', re.I)
_MP_PLUGIN_FLAGS = ('--plugin-dir', '--plugin-url')
_MP_PLUGIN_VERBS = frozenset({'install', 'i', 'enable', 'update', 'init', 'new'})
_MP_PROSE_HEADS = frozenset({'echo', 'printf', 'grep', 'egrep', 'fgrep', 'rg',
                             'write-output', 'write-host', 'select-string'})
_MP_CD = frozenset({'cd', 'chdir', 'pushd', 'set-location', 'sl', 'push-location'})
# Target is the LAST operand (cp a b, ln -s t link) unless -t / -Destination.
_MP_DEST_LAST = frozenset({
    'cp', 'copy', 'mv', 'move', 'ln', 'install', 'rsync', 'scp', 'xcopy',
    'robocopy', 'copy-item', 'cpi', 'move-item', 'mi'})
# Any operand aimed at .claude counts.
_MP_ANY_ARG = frozenset({
    'mkdir', 'md', 'new-item', 'ni', 'touch', 'tee', 'tee-object', 'set-content',
    'sc', 'add-content', 'ac', 'out-file', 'export-clixml', 'expand-archive',
    'unzip', '7z', '7za', '7zr', '7zz', 'tar', 'bsdtar', 'truncate', 'sed', 'gsed',
    'perl', 'ruby', 'patch', 'rename-item', 'rni', 'ren', 'rename', 'mklink', 'dd',
    'sponge', 'yq'})
# Taking .claude (or its settings) AWAY (2026-10-12): `mv .claude .claude.bak` and
# `rm -rf .claude` aim at no destination, so the checks above never saw them, yet
# a moved settings.json drops the fence's own hook registration and a moved
# .claude drops every mod. For these verbs the SOURCE operand counts, not only
# the destination, but only when it IS the `.claude` directory or a settings file
# directly in it (_MpTarget.removed). A copy OUT (`cp .claude/settings.json
# /tmp/x`) still passes.
_MP_MOVE = frozenset({'mv', 'move', 'move-item', 'mi'})
_MP_DELETE = frozenset({'rm', 'rmdir', 'unlink', 'shred', 'del', 'erase', 'rd',
                        'remove-item', 'ri'})
_MP_FIND_EXEC = frozenset({'-exec', '-execdir', '-ok', '-okdir'})
_MP_CLAUDE_DIR_END_RE = re.compile(r'(?:^|/)\.claude$', re.I)
_MP_CLAUDE_SETTINGS_RE = re.compile(r'(?:^|/)\.claude/settings[^/]*$', re.I)
_MP_INTERP_RE = re.compile(
    r'^(?:python\d*(?:\.\d+)?|py|pypy\d*|node|nodejs|deno|bun|perl|ruby|php|lua|'
    r'pwsh|powershell)$')
_MP_NET_OUT = frozenset({'-o', '--output', '--output-dir', '-O', '--output-document',
                         '-P', '--directory-prefix', '-outfile', '-destination'})
_MP_PS_ENCODED_RE = re.compile(r'^-e(?:c|nc\w*)?$', re.I)
_MP_HEREDOC_RE = re.compile(
    r'''(?P<head>[^\n]*)<<-?[ \t]*(?P<q>['"]?)(?P<tag>\w+)(?P=q)[^\n]*\n'''
    r'''(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?=\n|$)''', re.S)
# What a program body does to a path: open for write, mkdir, copy, rename,
# symlink, archive extract, shell-out; fs.*; Ruby/Perl file helpers; the
# PowerShell cmdlets and .NET statics. Both this AND a `.claude` path must be
# in the same string (a code word, a heredoc body) before it counts. Perl's
# two-argument open (`open(F, ">f")`, `open F, ">>f"`, `+>`/`+<`) carries the
# mode inside the path string, with or without parentheses; a `<` read stays out.
# `fileinput ... inplace=True` (or True as the second positional argument)
# rewrites the files it is given (a `.claude` file named in the same code).
_MP_WRITE_API_RE = re.compile(
    r'''\bopen\s*\([^)]*,\s*(?:mode\s*=\s*)?['"]?[rwxabt+<]*[wxa+>][rwxabt+<>]*['"]?\s*[,)]'''
    r'''|\.open\s*\(\s*['"]?[rwxabt+]*[wxa+][rwxabt+]*['"]?\s*[,)]'''
    r'''|\bopen\b\s*\(?[^,;)]*,\s*['"]\s*(?:\+?>|\+<)'''
    r'''|\bfileinput\b[\s\S]*?\binplace\s*=(?!\s*(?:False|0|None)\b)'''
    r'''|\bfileinput\s*\.\s*(?:input|FileInput)\s*\(\s*(?:\[[^\]]*\]|\([^)]*\)|[^,()\s][^,()]*)'''
    r'''\s*,\s*(?:True|1)\s*[,)]'''
    r'''|\.(?:write_text|write_bytes|mkdir|touch|symlink_to|hardlink_to|rename)\s*\('''
    r'''|\bshutil\s*\.\s*(?:copy\w*|move|make_archive|unpack_archive)\b'''
    r'''|\bos\s*\.\s*(?:makedirs|mkdir|rename|replace|symlink|link|system|popen)\b'''
    r'''|\b(?:zipfile|tarfile|subprocess|child_process)\b'''
    r'''|(?:\bfs(?:\.promises)?|\bfs['"]?\))\s*\.\s*(?:write\w*|append\w*|mkdir\w*|copy\w*|cp\w*'''
    r'''|rename\w*|symlink\w*|link\w*|createWriteStream)\b'''
    r'''|\bFile\s*\.\s*(?:write|binwrite|open|rename|symlink)\b|\bFileUtils\b'''
    r'''|\bFile::(?:Path|Copy)\b|\b(?:make_path|mkpath)\b'''
    r'''|\b(?:Set-Content|Add-Content|Out-File|New-Item|Copy-Item|Move-Item'''
    r'''|Rename-Item|Expand-Archive)\b'''
    r'''|\[(?:System\.)?IO\.(?:File|Directory)\]\s*::\s*'''
    r'''(?:Write\w*|Append\w*|Copy|Move|Create\w*|Open\w*|Replace)''', re.I)
# What a program body does to take a path away. Only the `.claude` rule adds
# these to the write list (_MpTarget.write_api): moves and renames are already
# above, deletes are not.
_MP_DELETE_API_RE = re.compile(
    r'''\bshutil\s*\.\s*rmtree\b|\bos\s*\.\s*(?:remove|unlink|rmdir|removedirs)\b'''
    r'''|\.(?:unlink|rmdir)\s*\(|\b(?:rmtree|remove_tree)\b'''
    r'''|(?:\bfs(?:\.promises)?|\bfs['"]?\))\s*\.\s*(?:rm\w*|unlink\w*)\b'''
    r'''|\bFile\s*\.\s*(?:delete|unlink)\b|\bDir\s*\.\s*(?:rmdir|unlink|delete)\b'''
    r'''|\bRemove-Item\b|\[(?:System\.)?IO\.(?:File|Directory)\]\s*::\s*Delete''', re.I)
_MP_WRITE_OR_DELETE_API_RE = re.compile(
    _MP_WRITE_API_RE.pattern + '|' + _MP_DELETE_API_RE.pattern, re.I)


def _mp_hit(text: str, skip_worktrees: bool = False) -> bool:
    """True when `text` names a `.claude` path component. With skip_worktrees,
    a component that only leads into `.claude/worktrees/` does not count (Claude
    Code's own agent worktrees live there; used for the hook cwd only)."""
    norm = (text or '').replace('\\', '/')
    for m in _MP_CLAUDE_RE.finditer(norm):
        if not (skip_worktrees and _MP_WORKTREE_RE.match(norm[m.end():])):
            return True
    return False


class _MpTarget:
    """Which paths the shell-write scan (_mp_scan) is hunting for. This base
    class IS the `.claude` rule; _MpPathTarget below aims the same parser at
    other places. `ctx` is the target's own "where is the shell now" state: a
    bool for `.claude` (inside a `cd .claude`), a path string or None for the
    path targets."""
    into = 'a .claude directory'
    under = '.claude'
    plugin_rules = True       # CLAUDE_CODE_PLUGIN_* / `claude plugin` checks: .claude only
    git_paths = False         # `git checkout|restore|apply|mv` naming a path
    # A .NET static call (`[IO.File]::WriteAllText(...)`) is only looked for
    # AFTER a command's first word. A bare one, which is how the PowerShell tool
    # usually spells it, IS the first word; the .claude rule has always missed
    # that and is left as it was.
    dotnet_head = False
    git_arg_may_be_ref = False    # True: also define exists(text, ctx), see _MpInstall
    removes = True            # moving / deleting the target itself counts (see _MP_MOVE)
    write_api = _MP_WRITE_OR_DELETE_API_RE    # what in a program body counts as acting on it
    gone = 'a .claude directory or its settings file'

    def start(self, cwd):
        return _mp_hit(cwd or '', skip_worktrees=True)

    def cd(self, ctx, target):
        return True if _mp_hit(target) else ctx

    def hit(self, text, ctx):
        """`text` (one path-ish string) names the target."""
        return _mp_hit(text)

    def word(self, w, ctx):
        """`hit`, plus: inside the target's own directory a bare relative word counts.
        An option with its value attached (`7z -o.claude/x`, `tar -C.claude`) counts."""
        if _mp_hit(w) or _mp_hit(re.sub(r'^-[A-Za-z]', '', w)):
            return True
        return bool(ctx and w and not w.startswith('-') and w[0] not in '/~$%\\'
                    and not _WIN_DRIVE_RE.match(w))

    def removed(self, w, ctx):
        """`w` is something a move or delete verb would take away: the `.claude`
        directory itself (or `.claude/*`) or a settings file directly in it. A
        subfolder is not (`mv ~/.claude/skills/old _scratch/old` is pinned as
        allowed by tests/test_fence_mod_plant.py), nor are Claude Code's agent
        worktrees below it. Inside a `cd .claude`, `.`, `*` and a bare `settings*`
        count. `..` is folded first, so `.claude/worktrees/../settings.json` is
        the settings file."""
        w = w or ''
        if w.startswith('-'):
            m = _MP_FLAG_VALUE_RE.match(w)       # -Path:.claude\settings.json
            if not m:
                return False
            w = m.group(1)
        norm = posixpath.normpath(w.replace('\\', '/')) if w else ''
        norm = re.sub(r'(?:/+\*{1,2}|/+\.)+$', '', norm)
        if _MP_CLAUDE_DIR_END_RE.search(norm) or _MP_CLAUDE_SETTINGS_RE.search(norm):
            return True
        return bool(ctx and (norm in ('.', '*', '**') or re.match(r'^settings[^/]*$', norm)))

    def code(self, code):
        """A program body (a `-c` string, a heredoc) names the target."""
        return _mp_hit(code)

    def dir_dest(self, dest, sources, ctx):
        """`cp SRC DIR` where the file it creates inside DIR is the target."""
        return False

    def command(self, prog, rest):
        """A reason the one command `prog rest...` plants the target by itself
        (no path involved), for a target without the plugin rules; else None."""
        return None


_MP_CLAUDE = _MpTarget()


def _mp_args_hit(args, in_claude, tgt: Optional[_MpTarget] = None) -> bool:
    """Any word names the target (for `.claude`: or, inside a `cd .claude`, is a relative path)."""
    tgt = tgt or _MP_CLAUDE
    return any(tgt.word(w, in_claude) for w in args)


def _mp_code_plants(code: str, tgt: Optional[_MpTarget] = None) -> bool:
    tgt = tgt or _MP_CLAUDE
    return tgt.code(code) and bool(tgt.write_api.search(code))


def _mp_positional(args) -> list:
    return [w for w in args if w and not w.startswith('-')
            and not re.match(r'^\d*>', w)]


def _mp_dest(prog: str, rest: list) -> Optional[str]:
    for k, w in enumerate(rest):
        if w in ('-t', '--target-directory') and k + 1 < len(rest):
            return rest[k + 1]
        if w.startswith('--target-directory='):
            return w.split('=', 1)[1]
        m = re.match(r'^-dest\w*[:=]?(.*)$', w, re.I)
        if m and prog in ('copy-item', 'cpi', 'move-item', 'mi'):
            return m.group(1) or (rest[k + 1] if k + 1 < len(rest) else None)
    if prog in ('robocopy', 'xcopy'):
        rest = [w for w in rest if not re.match(r'^/[A-Za-z?]+(?::.*)?$', w)]
    pos = _mp_positional(rest)
    return pos[-1] if pos else None


def _mp_any_arg_applies(prog: str, rest: list) -> bool:
    """tar only writes when it extracts, sed/perl/ruby only when they edit in place."""
    if prog in ('tar', 'bsdtar'):
        return any(re.match(r'^-[A-Za-z]*x', w) or re.match(r'^x[A-Za-z]*$', w)
                   or w in ('--extract', '--get') for w in rest)
    if prog in ('sed', 'gsed', 'perl'):
        return any(w == '--in-place' or w.startswith('--in-place=')
                   or re.match(r'^-[A-Za-z]*i', w)
                   or (prog == 'perl' and ('$^I' in w or '$INPLACE_EDIT' in w))
                   for w in rest)
    if prog == 'yq':                         # only -i / --inplace writes the file
        return any(w in ('--inplace', '--in-place') or re.match(r'^-[A-Za-z]*i[A-Za-z]*$', w)
                   for w in rest)
    if prog == 'ruby':                       # -i[ext], -pi, -lpi; not -Ilib
        return any(re.match(r'^-[acdlnpsSvwx0-9]*i', w) for w in rest)
    return True


_MP_AWK = frozenset({'awk', 'gawk', 'mawk', 'nawk'})
_MP_SED = frozenset({'sed', 'gsed'})
# sed `w FILE` / `W FILE` (a command, after an optional address) and the s///w
# flag. A letter before the w only counts inside an s-flag run (`s/a/b/gw F`),
# so `s/show .claude/x/` is not a write.
_MP_SED_W_RE = re.compile(r'[wW][ \t]*([^\n]*)')
_MP_SED_FLAG_CTX_RE = re.compile(r'[^\w\s][gpiImMe]*$')


def _mp_awk_writes(text: str) -> bool:
    """An awk program text that can write or run something: a `>`/`>>` after a
    print/printf (before one, `>` is a comparison), a pipe (`print | "cmd"`,
    `"cmd" | getline`, `|&`), or system()."""
    i = text.find('print')
    return bool((i >= 0 and '>' in text[i:])
                or re.search(r'(?<!\|)\|(?!\|)', text)
                or re.search(r'\bsystem\s*\(', text))


def _mp_sed_writes_claude(text: str) -> bool:
    """A sed script with a `w`/`W` command or s///w flag whose file is .claude."""
    for m in _MP_SED_W_RE.finditer(text):
        pre = text[:m.start()]
        if pre and pre[-1].isalpha() and not _MP_SED_FLAG_CTX_RE.search(pre):
            continue
        if _mp_hit(m.group(1)):
            return True
    return False


def _mp_script_writes(prog: str, rest: list) -> Optional[str]:
    """awk and sed write without -i or a redirect word: the target sits inside
    the quoted program text, so each word is judged on its own (a .claude path
    that is only an INPUT operand is a separate word and is not a write)."""
    if prog in _MP_AWK:
        if any(_mp_hit(w) and _mp_awk_writes(w) for w in rest):
            return f"`{prog}` program that writes or runs a command aimed at .claude"
    elif prog in _MP_SED:
        if any(_mp_sed_writes_claude(w) for w in rest):
            return f"`{prog}` w command writing into a .claude directory"
    return None


# Writers that are not writers by name. In-place modes: gawk `-i inplace`, ruby
# `-i`, ex/vi/vim/nvim run from a script (`-es -c wq`, or commands on stdin; only
# -R and `view` read). Output as an option or an operand: sort/shuf/iconv -o,
# openssl -out, `uniq IN OUT`, `xxd -r IN OUT`, `split IN PREFIX`. Each is judged
# on the word that is the written file, so a .claude path that is only an INPUT
# (`sort .claude/x`, `uniq .claude/x`, `openssl enc -in .claude/x`) is not a write.
_MP_OUT_OPTS = {
    'sort': ('-o', '--output'), 'shuf': ('-o', '--output'),
    'iconv': ('-o', '--output'),
    'openssl': ('-out', '-keyout', '-writerand'),
    'gpg': ('-o', '--output'), 'gpg2': ('-o', '--output'),
    'zstd': ('-o', '--output'), 'unzstd': ('-o', '--output'), 'csplit': ('-f', '--prefix'),
}
# prog -> options that take a value, so that value is not mistaken for an operand.
_MP_OUT_OPERAND_OPTS = {
    'uniq': ('-f', '-s', '-w', '--skip-fields', '--skip-chars', '--check-chars'),
    'xxd': ('-s', '-c', '-l', '-g', '-o', '-n', '-seek', '-cols', '-len',
            '-groupsize', '-name'),
    'split': ('-l', '-b', '-n', '-C', '-a', '-t', '--lines', '--bytes', '--number',
              '--line-bytes', '--suffix-length', '--separator', '--filter',
              '--additional-suffix'),
}
_MP_AWK_VALUED = frozenset({'-F', '-v', '-f', '-i', '-l', '-e', '-E', '-W',
                            '--include', '--load', '--file', '--source', '--exec',
                            '--assign', '--field-separator'})
_MP_EDITORS = frozenset({'ex', 'vi', 'vim', 'nvim', 'view', 'rview', 'ed', 'red'})
# `vim -R` / `view` refuse a plain `w`; a forced write of the current file does not.
_MP_VIM_FORCE_RE = re.compile(
    r'(?:^|[\s:|%,;.$+\d])(?:w(?:r(?:i(?:t(?:e)?)?)?)?|wq|x(?:it?)?|up(?:d(?:a(?:t(?:e)?)?)?)?)!'
    r'(?=\s*(?:$|\|))', re.I)


def _mp_operands(rest: list, valued) -> tuple:
    """(positional words, number of `< file` input redirects). A lone `-` is
    stdin and an operand; the value of an option in `valued` and the word after a
    bare redirect operator are not."""
    ops, redirects, skip = [], 0, False
    for w in rest:
        if skip:
            skip = False
        elif w in valued:
            skip = True
        elif re.match(r'^(?:\d*|&)[<>]+$', w):
            skip = True
            redirects += w.lstrip('0123456789&').startswith('<')
        elif re.match(r'^(?:\d*|&)[<>]+.', w):
            redirects += w.lstrip('0123456789&').startswith('<')
        elif w == '-' or (w and not w.startswith('-')):
            ops.append(w)
    return ops, redirects


def _mp_out_opt_hit(prog: str, rest: list, in_claude: bool) -> bool:
    """`-o FILE`, `-oFILE`, a cluster ending in o (`-ro FILE`), `--output FILE`,
    `--output=FILE`, `-out FILE`: the option's value names .claude."""
    flags = _MP_OUT_OPTS[prog]
    shorts = [f[1] for f in flags if len(f) == 2 and f[0] == '-']   # -o, -f
    for k, w in enumerate(rest):
        nxt = rest[k + 1:k + 2]
        if w in flags and nxt and _mp_args_hit(nxt, in_claude):
            return True
        if w.startswith('--'):
            flag, _, val = w.partition('=')
            if val and flag in flags and _mp_args_hit([val], in_claude):
                return True
        else:
            for c in shorts:
                if re.match(r'^-[A-Za-z]*' + c + r'$', w) and nxt and _mp_args_hit(nxt, in_claude):
                    return True
                if re.match(r'^-' + c + r'.', w) and _mp_args_hit([w[2:]], in_claude):
                    return True
    return False


def _mp_awk_inplace(rest: list) -> bool:
    """gawk's inplace library: `-i inplace`, `-iinplace`, `--include=inplace`,
    or an `@include "inplace"` line in the program text."""
    for k, w in enumerate(rest):
        if w in ('-i', '--include'):
            val = rest[k + 1] if k + 1 < len(rest) else ''
        elif w.startswith('--include='):
            val = w[len('--include='):]
        elif w.startswith('-i'):
            val = w[2:]
        else:
            if re.search(r'''@include\s*["']?[^"'\s]*inplace''', w):
                return True
            continue
        if re.match(r'''["']?(?:[^"'/\\]*[/\\])?inplace\b''', val):
            return True
    return False


def _mp_named_output_writes(prog: str, rest: list, in_claude: bool) -> Optional[str]:
    if prog in _MP_OUT_OPTS:
        if _mp_out_opt_hit(prog, rest, in_claude):
            return f"`{prog}` writing its output file into a .claude directory"
    elif prog in _MP_OUT_OPERAND_OPTS:
        if prog == 'xxd' and not any(
                w == '-revert' or re.match(r'^-[A-Za-z]{0,3}r[A-Za-z]{0,3}$', w)
                for w in rest):
            return None                      # a plain hexdump is not a rebuild
        ops, redirected = _mp_operands(rest, _MP_OUT_OPERAND_OPTS[prog])
        out = 1 - redirected                 # IN OUT; `< IN` leaves only OUT
        if 0 <= out < len(ops) and _mp_args_hit([ops[out]], in_claude):
            return f"`{prog}` writing its output operand into a .claude directory"
    elif prog in _MP_AWK:
        if _mp_awk_inplace(rest):
            ops, _ = _mp_operands(rest, _MP_AWK_VALUED)
            if not any(re.match(r'^-(?:[feE]|-file|-source|-exec)', w) for w in rest):
                ops = ops[1:]                # the first operand is the program
            if _mp_args_hit(ops, in_claude):
                return f"`{prog}` editing a .claude file in place"
    elif prog in _MP_EDITORS:
        if prog in ('view', 'rview') or any(re.match(r'^-[A-Za-z]*R', w) for w in rest):
            # read-only: only a command that names .claude (`-c 'w! .claude/x'`)
            cmds = [rest[k + 1] for k, w in enumerate(rest[:-1])
                    if w in ('-c', '--cmd', '-S')] + [w for w in rest if w.startswith('+')]
            if _mp_args_hit(cmds, False):
                return f"`{prog}` command writing into a .claude directory"
            if any(_MP_VIM_FORCE_RE.search(c) for c in cmds) and _mp_args_hit(rest, in_claude):
                return f"`{prog}` forcing a write (w!) of a .claude file"
        elif _mp_args_hit(rest, in_claude):
            return f"`{prog}` editing a .claude file (scripted ex/vim/ed writes it)"
    return None


# End-of-line converters rewrite their operands in place; `-n IN OUT` (newfile
# mode) writes only OUT, `-o` goes back to in-place, `-O`/`-i`/`-h`/`-V` write
# nothing (stdout, info, help).
_MP_EOL_TOOLS = frozenset({'dos2unix', 'unix2dos', 'mac2unix', 'unix2mac', 'todos', 'fromdos'})
_MP_EOL_MODES = frozenset({'ascii', 'iso', '7bit'})
# Decompressors write the de-suffixed file beside the archive (`gunzip x.gz` -> x)
# unless -c / -t / -l; the second set decompresses only with -d.
_MP_UNPACK = frozenset({'gunzip', 'bunzip2', 'unxz', 'unlzma', 'uncompress', 'unzstd'})
_MP_UNPACK_D = frozenset({'gzip', 'bzip2', 'xz', 'lzma', 'zstd'})
_MP_CERTUTIL_OUT = frozenset({'decode', 'decodehex', 'encode', 'encodehex', 'urlcache'})
_MP_GIT_VALUE_OPTS = frozenset({'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path'})
_MP_PATHSPEC_EXCLUDE_RE = re.compile(r'^:(?:[!^]|\([^)]*\bexclude\b)', re.I)


def _mp_unredirected(rest: list) -> list:
    """The words without redirect operators and their targets (`< IN`, `> OUT`)."""
    out, skip = [], False
    for w in rest:
        if skip:
            skip = False
        elif re.match(r'^(?:\d*|&)[<>]+$', w):
            skip = True
        elif not re.match(r'^(?:\d*|&)[<>]+.', w):
            out.append(w)
    return out


def _mp_eol_writes(rest: list, in_claude: bool) -> bool:
    new_mode, pending, targets = False, None, []
    for w in _mp_unredirected(rest):
        if w.startswith('--'):
            if w in ('--to-stdout', '--help', '--version') or w.startswith('--info'):
                return False
            if w == '--newfile':
                new_mode, pending = True, None
            elif w == '--oldfile':
                new_mode, pending = False, None
        elif re.match(r'^-[A-Za-z]+$', w):
            letters = w[1:]
            if letters in _MP_EOL_MODES:
                continue
            if 'O' in letters or letters in ('h', 'V') or letters.startswith('i'):
                return False
            if letters.endswith('n'):
                new_mode, pending = True, None
            elif letters.endswith('o'):
                new_mode, pending = False, None
        elif w and not w.startswith('-'):
            if not new_mode:
                targets.append(w)
            elif pending is None:
                pending = w                      # the input of an `-n IN OUT` pair
            else:
                targets.append(w)
                pending = None
    return _mp_args_hit(targets, in_claude)


def _mp_unpack_writes(prog: str, rest: list, in_claude: bool) -> bool:
    if prog in _MP_UNPACK_D and not any(
            w in ('--decompress', '--uncompress') or re.match(r'^-[A-Za-z]*d[A-Za-z]*$', w)
            for w in rest):
        return False                         # compressing is not a plant
    rest = _mp_unredirected(rest)
    for w in rest:
        if w in ('--stdout', '--to-stdout', '--test', '--list'):
            return False
        if re.match(r'^-[A-Za-z0-9]*[ctl][A-Za-z0-9]*$', w):
            return False                     # -c stdout, -t test, -l list
        if prog in ('zstd', 'unzstd') and (w.startswith('-o') or w.startswith('--output')):
            return False                     # an explicit output is judged as one (_MP_OUT_OPTS)
    return _mp_args_hit(_mp_positional(rest), in_claude)


def _mp_certutil_writes(rest: list, in_claude: bool) -> bool:
    if not {w.lower().lstrip('-/') for w in rest if w[:1] in '-/'} & _MP_CERTUTIL_OUT:
        return False
    pos = _mp_positional(rest)
    return bool(pos) and _mp_args_hit([pos[-1]], in_claude)


def _mp_git_restores(rest: list, in_claude: bool) -> Optional[str]:
    """`git checkout|restore|checkout-index` writes the work tree from a tree it
    is told to read (a stash, an older commit): that plants a tracked .claude
    file. Judged by its pathspec: the words after `--`, or a word that names
    .claude. `git checkout <branch>` and `restore --staged` (index only) stay."""
    args, k, sub = list(rest), 0, None
    while k < len(args):
        w = args[k]
        if w in _MP_GIT_VALUE_OPTS:
            if w == '-C' and k + 1 < len(args) and _mp_hit(args[k + 1], skip_worktrees=True):
                in_claude = True
            k += 2
        elif w.startswith('-'):
            k += 1
        else:
            sub, k = w.lower(), k + 1
            break
    if sub not in ('checkout', 'restore', 'checkout-index'):
        return None
    after = args[k:]
    if sub == 'restore':
        staged = any(w == '--staged' or re.match(r'^-[A-Za-z]*S[A-Za-z]*$', w) for w in after)
        worktree = any(w == '--worktree' or re.match(r'^-[A-Za-z]*W[A-Za-z]*$', w) for w in after)
        if staged and not worktree:
            return None
    dashdash = after.index('--') if '--' in after else -1
    skip, specs = False, []
    for i, w in enumerate(after):
        if skip:
            skip = False
        elif w in ('-s', '--source', '--pathspec-from-file'):
            skip = True
        elif i == dashdash or w.startswith('-') or w.startswith('--pathspec-from-file') \
                or _MP_PATHSPEC_EXCLUDE_RE.match(w):
            continue
        elif sub == 'restore' or (dashdash >= 0 and i > dashdash):
            specs.append(w)                  # a pathspec: relative names count in a .claude cwd
        elif _mp_hit(w):
            return f"`git {sub}` restoring a .claude path from a tree"
    if _mp_args_hit(specs, in_claude):
        return f"`git {sub}` restoring a .claude path from a tree"
    return None


def _mp_tool_writes(prog: str, rest: list, in_claude: bool) -> Optional[str]:
    if prog in _MP_EOL_TOOLS:
        if _mp_eol_writes(rest, in_claude):
            return f"`{prog}` rewriting a .claude file in place"
    elif prog in _MP_UNPACK or prog in _MP_UNPACK_D:
        if _mp_unpack_writes(prog, rest, in_claude):
            return f"`{prog}` writing the decompressed file beside a .claude archive"
    elif prog == 'certutil':
        if _mp_certutil_writes(rest, in_claude):
            return "`certutil` writing its output file into a .claude directory"
    elif prog == 'git':
        return _mp_git_restores(rest, in_claude)
    return None


# A static [IO.File]/[IO.Directory] call, its arguments, and which one is the
# path that gets written (Copy/Move: the destination, Replace: destination and
# backup, Write/Append/Create/Open: the first).
_MP_NET_CALL_RE = re.compile(r'\[(?:System\.)?IO\.(?:File|Directory)\]\s*::\s*(\w+)\s*\(', re.I)


def _mp_dotnet_writes(text: str, in_claude: bool) -> bool:
    for m in _MP_NET_CALL_RE.finditer(text):
        name = m.group(1).lower()
        if name in ('copy', 'move'):
            idx = (1,)
        elif name == 'replace':
            idx = (1, 2)
        elif name.startswith(('write', 'append', 'create', 'open')) \
                and name not in ('openread', 'opentext'):
            idx = (0,)
        else:
            continue
        args, depth, cur = [], 0, ''
        for ch in text[m.end():]:
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
                if depth < 0:
                    break
            if ch == ',' and depth == 0:
                args.append(cur)
                cur = ''
            else:
                cur += ch
        args.append(cur)
        if _mp_args_hit([args[i].strip() for i in idx if i < len(args)], in_claude):
            return True
    return False


def _mp_net_output_hit(rest: list, tgt: '_MpTarget', ctx) -> bool:
    for k, w in enumerate(rest):
        low = w.lower()
        if low in _MP_NET_OUT and k + 1 < len(rest) and tgt.hit(rest[k + 1], ctx):
            return True
        m = re.match(r'^(--?[a-z-]+)[:=](.+)$', low)
        if m and m.group(1) in _MP_NET_OUT and tgt.hit(m.group(2), ctx):
            return True
        if re.match(r'^-o.', w) and not w.startswith('--') and tgt.hit(w[2:], ctx):
            return True
        if re.match(r'^-[A-Za-z]*[oO]$', w) and k + 1 < len(rest) and tgt.hit(rest[k + 1], ctx):
            return True                      # a flag cluster ending in -o: -sSfLo FILE
    return False


def _mp_segment(words: list, in_claude, depth: int, tgt: '_MpTarget'):
    """(reason or None, in_claude) for one shell segment of dequoted words.
    `in_claude` is the target's own cwd state (a bool for `.claude`, see
    _MpTarget.start); `tgt` decides which paths count."""
    words = list(words)
    if words:
        words[0] = re.sub(r'^(?:\$?\(+|\{+)', '', words[0])
    for k, w in enumerate(words):
        m = re.match(r'^(?:\d*|&)>>?(?![>&])(.*)$', w)
        if m:
            target = m.group(1) or (words[k + 1] if k + 1 < len(words) else '')
            if target and tgt.word(target, in_claude):
                return f"a shell redirect into {tgt.into}", in_claude
    k = 0
    while k < len(words):
        w = words[k]
        head = _install_prog(w)
        if (re.match(r'^\w+=', w) and not w.startswith('-')) \
                or w in ('{', '}', '!', 'then', 'do', 'else', ''):
            k += 1
            continue
        if head in _WRAPPER_VALUE_OPTS:
            vals = _WRAPPER_VALUE_OPTS[head]
            k += 1
            while k < len(words):
                nxt = words[k]
                if head in ('start', 'call'):
                    if re.match(r'^/[A-Za-z]+$', nxt):
                        k += 1
                        continue
                    break
                if nxt == '--':
                    k += 1
                    break
                if nxt.startswith('-') and nxt != '-':
                    k += 2 if nxt in vals else 1
                    continue
                if head == 'env' and re.match(r'^\w+=', nxt):
                    k += 1
                    continue
                break
            continue
        break
    else:
        return None, in_claude
    prog = _install_prog(words[k])
    rest = words[k + 1:]
    if depth < 4:
        inner = None
        if prog in _SHELL_DASH_C:
            for j, a in enumerate(rest):
                if re.match(r'^-[A-Za-z]*c[A-Za-z]*$', a) and j + 1 < len(rest):
                    inner = rest[j + 1]
                    break
        elif prog in ('pwsh', 'powershell'):
            for j, a in enumerate(rest):
                if _PS_COMMAND_RE.match(a):
                    inner = ' '.join(rest[j + 1:])
                    break
                if _MP_PS_ENCODED_RE.match(a) and j + 1 < len(rest):
                    try:
                        inner = base64.b64decode(rest[j + 1]).decode('utf-16-le')
                    except Exception:
                        inner = None
                    break
        elif prog == 'cmd':
            for j, a in enumerate(rest):
                if a.lower() in ('/c', '/k'):
                    inner = ' '.join(rest[j + 1:])
                    break
        elif prog in ('eval', 'iex', 'invoke-expression'):
            inner = ' '.join(rest)
        if inner is not None:
            why = _mp_scan(inner, depth + 1, in_claude, tgt)
            if why:
                return why, in_claude
    if prog in _MP_CD:
        target = next((a for a in rest if not a.startswith('-')), '')
        if target:
            in_claude = tgt.cd(in_claude, target)
        return None, in_claude
    if tgt.plugin_rules:
        if prog not in _MP_PROSE_HEADS:
            if any(w == f or w.startswith(f + '=') for w in rest for f in _MP_PLUGIN_FLAGS):
                return "the --plugin-dir / --plugin-url flags (load a plugin outside the install)", in_claude
        for i, w in enumerate(words):
            if _install_prog(w) != 'claude':
                continue
            sub = [a.lower() for a in words[i + 1:] if not a.startswith('-')]
            for j, a in enumerate(sub):
                if a in ('plugin', 'plugins') and j + 1 < len(sub):
                    verb = sub[j + 1]
                    if verb in _MP_PLUGIN_VERBS or (
                            verb == 'marketplace' and sub[j + 2:j + 3] in (['add'], ['update'])):
                        return f"`claude plugin {verb}` (installs or scaffolds a plugin)", in_claude
                    break
    else:
        why = tgt.command(prog, rest)
        if why:
            return why, in_claude
    why = None
    if prog in _MP_DEST_LAST:
        dest = _mp_dest(prog, rest)
        if dest and (tgt.word(dest, in_claude) or tgt.dir_dest(
                dest, [p for p in _mp_positional(rest) if p != dest], in_claude)):
            why = f"`{prog}` into {tgt.into}"
        elif tgt.removes and prog in _MP_MOVE and any(tgt.removed(a, in_claude) for a in rest):
            why = f"`{prog}` moving {tgt.gone} away"
    elif prog == 'git':
        sub = [a for a in rest if not a.startswith('-')]
        if 'clone' in sub:
            pos = _mp_positional(rest[rest.index('clone') + 1:]) if 'clone' in rest else []
            if pos and tgt.word(pos[-1], in_claude):
                why = f"`git clone` into {tgt.into}"
        elif sub[:2] == ['worktree', 'add'] and len(sub) > 2 and tgt.word(sub[2], in_claude):
            why = f"`git worktree add` into {tgt.into}"
        if not why and tgt.git_paths:
            why = _mp_git_paths(rest, in_claude, tgt)
        if not why and tgt.removes:
            why = _mp_git_removes(rest, in_claude, tgt)
    elif tgt.removes and prog in _MP_DELETE:
        if any(tgt.removed(a, in_claude) for a in rest):
            why = f"`{prog}` deleting {tgt.gone}"
    elif tgt.removes and prog == 'find':
        # `find .claude -delete`, `find .claude -exec rm {} +`
        if ('-delete' in rest or any(
                a in _MP_FIND_EXEC and rest[j + 1].lower() in _MP_MOVE | _MP_DELETE
                for j, a in enumerate(rest[:-1])))                 and any(tgt.removed(a, in_claude) for a in rest):
            why = f"`find` deleting {tgt.gone}"
    elif prog in _MP_ANY_ARG and _mp_any_arg_applies(prog, rest):
        if prog == 'dd':
            if any(a.lower().startswith('of=') and tgt.hit(a[3:], in_claude) for a in rest):
                why = f"`dd of=` into {tgt.into}"
        elif _mp_args_hit([w for w in rest if prog != 'yq' or not re.search(r'\s', w)], in_claude, tgt):
            why = f"`{prog}` aimed at {tgt.into}"  # yq: an expression is not a file
    elif prog in ('curl', 'wget', 'iwr', 'invoke-webrequest', 'irm', 'invoke-restmethod',
                  'start-bitstransfer') and _mp_net_output_hit(rest, tgt, in_claude):
        why = f"`{prog}` saving a download into {tgt.into}"
    if not why and type(tgt) is _MpTarget:      # modw writers name .claude only
        why = (_mp_script_writes(prog, rest) or _mp_named_output_writes(prog, rest, in_claude)
               or _mp_tool_writes(prog, rest, in_claude))
    if why:
        return why, in_claude
    if _MP_INTERP_RE.match(prog):
        for a in rest:
            if _mp_code_plants(a, tgt):
                return f"`{prog}` code that writes under {tgt.under}", in_claude
    for a in (words[k:] if tgt.dotnet_head else rest):
        if '::' in a and _mp_code_plants(a, tgt):
            return f"a .NET file call that writes under {tgt.under}", in_claude
    # the call can START the segment ([IO.File]::Copy(a, b); [void][IO.File]::...)
    # and its path can be a later word, so judge the written argument of the call
    if type(tgt) is _MpTarget and any('::' in w for w in words[k:]) \
            and _mp_dotnet_writes(' '.join(words[k:]), in_claude):
        return "a .NET file call that writes under .claude", in_claude
    return None, in_claude


def _mp_scan(cmd: str, depth: int = 0, in_claude=False,
             tgt: Optional['_MpTarget'] = None) -> Optional[str]:
    tgt = tgt or _MP_CLAUDE
    cmd = _unwrap_start_process(_join_line_continuations(cmd))
    for words in _install_split(cmd, parens=False):
        why, in_claude = _mp_segment(words, in_claude, depth, tgt)
        if why:
            return why
    return None


def _mp_scan_command(scan: str, cwd: Optional[str], tgt: '_MpTarget') -> Optional[str]:
    """Reason a (prose-masked) command writes to `tgt`, else None: interpreter
    heredoc bodies first, then the shell segments. May raise; callers wrap."""
    for m in _MP_HEREDOC_RE.finditer(scan):
        if _INTERPRETER_RE.search(m.group('head')) and (
                _mp_code_plants(m.group('body'), tgt)
                or _mp_scan(m.group('body'), 1, tgt.start(None), tgt)):
            return f"an interpreter script that writes under {tgt.under}"
    return _mp_scan(scan, 0, tgt.start(cwd), tgt)


def _mod_plant_guard(command: str, cwd: Optional[str] = None) -> FenceDecision:
    """Block an armed agent planting a Claude Code mod or plugin (see the
    section comment). Never passable: a human owns what Claude loads."""
    try:
        scan = _mask_inert_prose(command.strip(), mask_python_c=False)
    except Exception:
        scan = command.strip()
    why = None
    try:
        if _MP_ENV_RE.search(scan):
            why = "setting a CLAUDE_CODE_PLUGIN_*DIR* variable (loads plugin folders)"
        if not why:
            why = _mp_scan_command(scan, cwd, _MP_CLAUDE)
    except Exception:
        return FenceDecision(False, '')      # never wedge the agent on a parse bug
    if not why:
        return FenceDecision(False, '')
    return FenceDecision(True, f"{why} — Claude Code auto-loads a plugin or mod "
                               f"from .claude and its tool.call hook runs above "
                               f"this fence, so what lives there and what plugin "
                               f"folders load is human-owned (supply chain)",
                         overridable=False)


# ── Shell writes to the fence's own files and the install tree (2026-10-06) ──
# classify_action refuses a Write/Edit/MultiEdit/NotebookEdit onto the four
# fence files, data/skills (and the _skill_stats files) and mc/addons, and
# check_install_dir_write refuses one into the running install from another
# project. Both only ever looked at those four tools, so the SAME file went
# through the shell: `echo x > steward/fence.py`, `cp`, `sed -i`,
# `Set-Content`, `git checkout -- steward/fence.py` all exited 0 against the
# live hook (Quill, 2026-10-06), and a worktree copy of the fence was rewritten
# that way twice. This points the mod-plant parser (_mp_scan: redirect, tee,
# cp/mv, Copy-Item/Move-Item, sed/perl -i, Set-Content/Out-File/Add-Content,
# python/node/perl -c and heredoc bodies, .NET writes, bash -c / pwsh -Command
# / Start-Process wrapping) at those same targets, through a target predicate
# instead of a copy of the parser. No new targets: what the Write rules
# protect, and the install folder with the Write guard's own-project
# exemption.
#
# ARMED ONLY. classify_bash is reached from main() only for an armed session,
# so the shell rule for the fence's files is armed-only exactly like the Write
# rule is. The Write guard for the install folder runs attended too
# (unconditionally); the shell version deliberately does not, because an
# attended human-driven session legitimately copies into, edits and rebuilds
# the install from another project, and diagnostic one-liners naming it are
# common there. Whether to enforce it attended is a human decision
# (docs/patches/fence-shell-self-protect.README.md).
#
# Path SHAPE decides, as it does for the Write tool: any `.../steward/<fence
# file>`, `.../data/skills/...`, `.../mc/addons/...` counts wherever it lives.
# A scratch copy of the fence under a folder named `steward` is refused here
# the way the Write tool already refuses it; build fence patches in a scratch
# dir NOT named steward (`fence_work/fence.py`) and rewrite the diff headers.
#
# Also covered: `git [-C dir] checkout|restore|mv` naming such a path (a
# `restore --staged` that leaves the work tree alone is allowed), `git apply
# --include=<path>`, and a unified diff carried IN the command (`git apply -
# <<EOF`, `patch <<EOF`) whose headers name one.
# KNOWN GAPS, accepted (command text only), the .claude rule's plus: a script
# file that writes the path (`python _scratch/edit.py`), a path built from a
# variable, a glob or os.path.join, `git apply x.patch` / `patch < x.diff`
# whose patch FILE carries the target, `git checkout|reset --hard|merge|pull|
# stash pop` that rewrites the file without naming it, and an `-c` body that
# only READS a protected path but also uses a write-capable or shell-out API
# (the co-occurrence test cannot tell direction).
_MP_FENCE_NAMES = ('fence.py', 'core.py', '_config.py', '__init__.py')
# Target paths inside a program body: the Write rules' shapes as a search.
_MP_PROT_CODE_RE = re.compile(
    r'(?<![\w.-])(?:'
    r'steward[\\/]+(?:[^\s\'"`]*?[\\/]+)?(?:fence|core|_config|__init__)\.py(?![\w-]|\.\w)'
    r'|data[\\/]+skills[\\/]'
    r'|mc[\\/]+addons[\\/]'
    r'|\.clayrune[\\/]+addons(?![\w-])'
    r'|_skill_stats\.json(?![\w-])|_skill_stats_archive\.jsonl(?![\w-])'
    r')', re.I)
_MP_FLAG_VALUE_RE = re.compile(r'^--?[A-Za-z][\w-]*[:=](.+)$')
_MP_DIFF_HDR_RE = re.compile(r'^(?:\+\+\+|---|diff --git)[ \t]+(?:"?a/)?(\S+)', re.M)
_MP_GIT_APPLY_RE = re.compile(
    r'\bgit(?:\.exe)?\s+(?:(?:-C|-c)\s+\S+\s+|--?\S+\s+)*apply\b'
    r'|(?:^|[;&|(\s])patch\b', re.M)
_MP_GIT_GLOBAL_VALUE = frozenset({'-c', '--git-dir', '--namespace', '--exec-path',
                                  '--super-prefix', '--config-env'})
_MP_GIT_SUB_VALUE = {
    'checkout': frozenset({'-b', '-B', '--orphan', '--conflict', '--pathspec-from-file'}),
    'restore': frozenset({'-s', '--source', '--conflict', '--pathspec-from-file'})}


def _shell_addons_hit(raw: str, cwd: Optional[str]) -> bool:
    """`raw` resolves inside the add-ons root. Unlike _path_resolves_into_addons
    it fails toward ALLOW on an unresolvable word: a shell word is not always a
    path, and refusing every odd word would wedge ordinary commands."""
    low = raw.replace('\\', '/').lower()
    if re.search(r'(?:^|/)\.clayrune/addons(?:/|$)', low):
        return True
    try:
        root = _addons_root().resolve()
        target = Path(os.path.expanduser(os.path.expandvars(raw)))
        if not target.is_absolute():
            target = (Path(cwd) if cwd else Path.cwd()) / target
        target = target.resolve()
    except Exception:
        return False
    return target == root or _is_within(target, root)


def _protected_path_kind(raw: str, cwd: Optional[str] = None) -> Optional[str]:
    """'fence' | 'skills' | 'addons' when `raw` is a path classify_action's
    Write rules protect, else None. Same shapes, same order; pinned against
    classify_action by tests/test_fence_shell_self_protect.py."""
    low = raw.replace('\\', '/').lower()
    if (low.startswith('steward/') or '/steward/' in low) and \
            low.rsplit('/', 1)[-1] in _MP_FENCE_NAMES:
        return 'fence'
    if ('/data/skills/' in low or low.startswith('data/skills/')
            or low.endswith('_skill_stats.json')
            or low.endswith('_skill_stats_archive.jsonl')):
        return 'skills'
    if low.startswith('mc/addons/') or '/mc/addons/' in low or _shell_addons_hit(raw, cwd):
        return 'addons'
    return None


def _mp_flag_word(w: str) -> str:
    """The path part of a word: itself, or the value of `-Path:x` / `--include=x`;
    '' for a bare flag."""
    if not w.startswith('-'):
        return w
    m = _MP_FLAG_VALUE_RE.match(w)
    return m.group(1) if m else ''


def _mp_cd_ctx(ctx, target: str):
    """Where the shell is after `cd target` (posix-style string; None = unknown)."""
    t = (target or '').replace('\\', '/')
    if not t or t == '-':
        return ctx
    if '$' in t or '%' in t:
        return None
    if t.startswith(('/', '~')) or _WIN_DRIVE_RE.match(t) or not ctx:
        new = t
    else:
        new = ctx.rstrip('/') + '/' + t
    return posixpath.normpath(new)


def _mp_norm_code(code: str) -> str:
    return re.sub(r'/+', '/', code.replace('\\', '/')).lower()


class _MpPathTarget(_MpTarget):
    """A target decided by path shape or resolution, not by a `.claude` name."""
    plugin_rules = False
    git_paths = True
    dotnet_head = True
    removes = False           # the Write rules these mirror do not cover a move/delete
    write_api = _MP_WRITE_API_RE

    def start(self, cwd):
        return _mp_cd_ctx(None, cwd) if cwd else None

    def cd(self, ctx, target):
        return _mp_cd_ctx(ctx, target)

    def word(self, w, ctx):
        return self.hit(w, ctx)

    def dir_dest(self, dest, sources, ctx):
        d = (dest or '').rstrip('/\\')
        return bool(d) and any(
            self.hit(d + '/' + (re.split(r'[\\/]', s)[-1] or 'x'), ctx) for s in sources)


class _MpProtected(_MpPathTarget):
    """The fence's own files, data/skills and mc/addons (classify_action's rules)."""
    into = "a fence-protected path (the fence's own code, data/skills or mc/addons)"
    under = 'a fence-protected path'

    def hit(self, text, ctx):
        w = _mp_flag_word(text or '')
        if not w:
            return False
        cwd = ctx if isinstance(ctx, str) else None
        if _protected_path_kind(w, cwd):
            return True
        if cwd and w[0] not in '/~$%\\' and not _WIN_DRIVE_RE.match(w):
            return _protected_path_kind(cwd.rstrip('/') + '/' + w, cwd) is not None
        return False

    def code(self, code):
        if _MP_PROT_CODE_RE.search(code):
            return True
        try:
            return _mp_norm_code(str(_addons_root())) in _mp_norm_code(code)
        except Exception:
            return False


class _MpInstall(_MpPathTarget):
    """Anything inside the running Clayrune install (`_INSTALL_DIR`)."""
    into = 'the Clayrune install directory'
    under = 'the Clayrune install directory'

    # `git checkout NAME` with NAME a branch resolves "inside the install" as a
    # relative word too, so there only `--` or an existing file counts.
    git_arg_may_be_ref = True

    def _resolve(self, text, ctx) -> Optional[Path]:
        w = _mp_flag_word(text or '')
        if not w:
            return None
        try:
            target = Path(os.path.expanduser(os.path.expandvars(w)))
            if not target.is_absolute():
                base = Path(os.path.expanduser(ctx)) if isinstance(ctx, str) and ctx else Path.cwd()
                target = base / target
            return target.resolve()
        except Exception:
            return None

    def hit(self, text, ctx):
        target = self._resolve(text, ctx)
        if target is None:
            return False
        try:
            install_dir = _INSTALL_DIR.resolve()
        except Exception:
            return False
        return target == install_dir or _is_within(target, install_dir)

    def exists(self, text, ctx):
        target = self._resolve(text, ctx)
        return target is not None and target.exists()

    def code(self, code):
        norm = _mp_norm_code(code)
        try:
            return any(_mp_norm_code(str(p)) in norm
                       for p in {_INSTALL_DIR, _INSTALL_DIR.resolve()})
        except Exception:
            return False


_MP_PROTECTED = _MpProtected()
_MP_INSTALL = _MpInstall()


def _mp_git_split(rest: list, ctx, tgt: _MpTarget):
    """(ctx, subcommand, its args) of `git [global opts] sub args...`; subcommand
    is None when there is none. `-C dir` / `--work-tree` move ctx like a `cd`."""
    i, n = 0, len(rest)
    while i < n and rest[i].startswith('-'):
        opt = rest[i]
        if opt in ('-C', '--work-tree') and i + 1 < n:
            ctx = tgt.cd(ctx, rest[i + 1])
            i += 2
        elif opt.startswith('--work-tree='):
            ctx = tgt.cd(ctx, opt.split('=', 1)[1])
            i += 1
        elif opt in _MP_GIT_GLOBAL_VALUE:
            i += 2
        else:
            i += 1
    if i >= n:
        return ctx, None, []
    return ctx, rest[i].lower(), rest[i + 1:]


def _mp_git_removes(rest: list, ctx, tgt: _MpTarget) -> Optional[str]:
    """Reason `git mv|rm ...` takes a path of `tgt` out of the work tree, else
    None. `git rm --cached` only drops it from the index."""
    ctx, sub, args = _mp_git_split(rest, ctx, tgt)
    if sub not in ('mv', 'rm') or (sub == 'rm' and '--cached' in args):
        return None
    if any(tgt.removed(a, ctx) for a in args if not a.startswith('-')):
        return f"`git {sub}` taking {tgt.gone} out of the work tree"
    return None


def _mp_git_paths(rest: list, ctx, tgt: _MpTarget) -> Optional[str]:
    """Reason `git [global opts] checkout|restore|apply|mv ...` writes a path of
    `tgt`, else None."""
    ctx, sub, args = _mp_git_split(rest, ctx, tgt)
    if sub is None:
        return None
    what = f"a file inside {tgt.into}"
    if sub in ('checkout', 'restore'):
        if sub == 'restore' and any(a in ('--staged', '-S') for a in args) \
                and not any(a in ('--worktree', '-W') for a in args):
            return None                      # index only, the work tree is untouched
        skip, paths, k, after_dd = _MP_GIT_SUB_VALUE[sub], [], 0, sub == 'restore'
        while k < len(args):
            if args[k] == '--':
                after_dd = True
            elif args[k] in skip:
                k += 2
                continue
            elif not args[k].startswith('-'):
                paths.append((args[k], after_dd))
            k += 1
        if any(tgt.hit(p, ctx) and (dd or not tgt.git_arg_may_be_ref or tgt.exists(p, ctx))
               for p, dd in paths):
            return f"`git {sub}` restoring {what}"
    elif sub == 'apply':
        for k, a in enumerate(args):
            val = args[k + 1] if a == '--include' and k + 1 < len(args) else (
                a.split('=', 1)[1] if a.startswith('--include=') else '')
            if val and tgt.hit(val, ctx):
                return f"`git apply --include` of {what}"
    elif sub == 'mv':
        pos = [a for a in args if not a.startswith('-')]
        if len(pos) >= 2 and tgt.hit(pos[-1], ctx):
            return f"`git mv` onto {what}"
    return None


def _mp_patch_body_hit(command: str, cwd: Optional[str], tgt: _MpTarget) -> bool:
    """A unified diff carried in the command itself (`git apply - <<EOF`,
    `patch <<EOF`) names a path of `tgt`. A patch FILE is not read (accepted)."""
    if not _MP_GIT_APPLY_RE.search(command) \
            or re.search(r'--(?:check|stat|numstat|summary)\b', command):
        return False
    ctx = tgt.start(cwd)
    return any(tgt.hit(m.group(1), ctx) for m in _MP_DIFF_HDR_RE.finditer(command))


def _mp_cwd_in_install(cwd: Optional[str]) -> bool:
    """The session's own project IS the install (check_install_dir_write's
    exemption); an unresolvable cwd also counts, it fails open the same way."""
    try:
        session = (Path(cwd) if cwd else Path.cwd()).resolve()
        install_dir = _INSTALL_DIR.resolve()
    except Exception:
        return True
    return session == install_dir or _is_within(session, install_dir)


def _shell_self_protect_guard(command: str, cwd: Optional[str] = None) -> FenceDecision:
    """Block an armed agent writing, through the shell, a path the Write rules
    protect, or (from another project) the install tree. Never passable, like
    the Write rules. Fails open on a parse bug."""
    try:
        scan = _mask_inert_prose(command.strip(), mask_python_c=False)
    except Exception:
        scan = command.strip()
    try:
        why = _mp_scan_command(scan, cwd, _MP_PROTECTED)
        if not why and _mp_patch_body_hit(command, cwd, _MP_PROTECTED):
            why = "a patch applied to a fence-protected path"
        if why:
            return FenceDecision(True, f"{why} — the fence's own enforcement code "
                                       f"(steward/fence.py, core.py, _config.py, "
                                       f"__init__.py), data/skills and mc/addons are "
                                       f"human-owned (safety-rail and loadout supply "
                                       f"chain; the Write tool refuses the same paths)",
                                 overridable=False)
        if not _mp_cwd_in_install(cwd):
            why = _mp_scan_command(scan, cwd, _MP_INSTALL)
            if why:
                return FenceDecision(True, f"{why} ({_INSTALL_DIR}) from a different "
                                           f"project — an agent may not edit the running "
                                           f"app's own source (the Write tool refuses the "
                                           f"same)",
                                     overridable=False)
    except Exception:
        return FenceDecision(False, '')      # never wedge the agent on a parse bug
    return FenceDecision(False, '')


# ── MCP-server / git-hook / shell-profile planting guard (2026-10-04) ───────
# Sibling of the mod-plant guard above, same hole one layer over. agent_runtime
# launches `claude` with the whole MCP fleet (~/.claude.json, the project's
# .mcp.json) unless a session pins --strict-mcp-config, and no `mcp__<server>__*`
# tool call a planted server answers is worth anything to this fence: the
# server decides what its tools do and the fence sees a tool name. A git hook
# or a shell profile is the same shape: code that runs later, outside any tool
# call this hook ever sees. For armed sessions this refuses, never passably
# (what Claude loads and what runs around it is a human-owned supply chain):
#   - `claude mcp add|add-json|add-from-claude-desktop|add-*` (any scope, any
#     option order, also via npx / sudo / bash -c / pwsh -Command / & / Start-
#     Process, which _mp_segment already unwraps). `mcp list|get|remove` stay;
#   - a shell write, copy, move, redirect, tee, in-place edit, download or
#     interpreter-code write aimed at ~/.claude.json (also $HOME/, %USERPROFILE%\,
#     C:\Users\*\) or any `.mcp.json`; and the Write/Edit/MultiEdit tools on the
#     same files (classify_action's /.claude/ rule misses `.claude.json`: it is
#     a file beside the directory, not under it);
#   - a write under `.git/hooks/` (shell and Write/Edit), `git config
#     core.hooksPath <value>` (any scope; `--get`, a bare key, `--unset` stay)
#     and `git -c core.hooksPath=...`;
#   - a write to a shell profile: ~/.bashrc .bash_profile .bash_login .profile
#     .zshrc .zprofile .zshenv .zlogin, the PowerShell profile files
#     (Documents\PowerShell and Documents\WindowsPowerShell
#     Microsoft.PowerShell_profile.ps1, profile.ps1) and `$PROFILE` as a
#     target.
# Reuses _mp_segment/_mp_scan through a target (_MpConfig, a _MpTarget like the
# `.claude` and fence-file rules), so the aim-not-mention rule and the
# prose masking are the mod-plant guard's: reading (`cat ~/.claude.json`,
# `grep mcpServers .mcp.json`, `ls .git/hooks`, `source ~/.bashrc`, `. $PROFILE`),
# `git diff|add .mcp.json` and a message that merely names a path stay allowed.
# KNOWN GAPS, accepted (command text only): a script file that does the write
# (`bash x.sh`, `python plant.py`), a path built from a variable or glob, a
# relative write whose cwd the hook payload does not show to be inside .git/hooks,
# `git checkout|restore|apply|stash pop` materialising a tracked .mcp.json or
# hook, `sponge`/`jq -i`-style tools not in the mod-plant verb lists, a bare
# repo's `<name>.git/hooks`, GIT_CONFIG_* env vars and a hand edit of
# .git/config or ~/.gitconfig, `claude --mcp-config <file>` (one session only),
# `claude config set`, and .env / ~/.codex/config.toml (left out on purpose).
_CP_WHAT = 'an MCP config, git hooks or shell profile path'
_CP_PATHS = (r'\.(?:claude|mcp)\.json',
             r'\.git/(?:modules/.+?/)?hooks',
             r'\.(?:bashrc|bash_profile|bash_login|profile|zshrc|zprofile|zshenv|zlogin)',
             r'microsoft\.\w+_profile\.ps1',
             r'(?:windows)?powershell/(?:\d+/)?profile\.ps1',
             r'\$profile(?:\.(?:currentuser|alluser)\w*)?')


def _cp_re(before: str, after: str):
    return re.compile('|'.join(before + p + after for p in _CP_PATHS), re.I)


# A word handed to a command is a path only when the name ends the word and
# does not follow a space (`Set-Content n.md "edit .mcp.json by hand"` is prose);
# code (a `python -c` body, a heredoc) holds the name amid other text.
_CP_ARG_RE = _cp_re(r'(?<![\w\s-])', r'(?![\w.\s-])')
_CP_CODE_RE = _cp_re(r'(?<![\w-])', r'(?![\w.-])')
_CP_CLAUDE_PROGS = frozenset({'claude', 'claude-code'})
_CP_LAUNCHERS = frozenset({'npx', 'bunx', 'pnpx', 'uvx', 'pipx', 'npm', 'pnpm', 'yarn',
                           'bun', 'corepack', 'volta', 'mise', 'fnm'})
_CP_MCP_ADD_RE = re.compile(r'^add(?:-[\w-]+)?$')
_CP_GIT_CONFIG_READS = frozenset({
    '--get', '--get-all', '--get-regexp', '--get-urlmatch', '--list', '-l',
    '--unset', '--unset-all', '--remove-section', '--rename-section'})
_CP_HOOKSPATH_RE = re.compile(r'^core\.hookspath(?:=|$)', re.I)
# `git -c core.hooksPath=/dev/null commit` runs NO hook: the same effect as
# --no-verify, which this guard leaves alone. Measured on 93,676 past shell
# calls, every real `git -c core.hooksPath=` was this one (3 of 3).
# `git config core.hooksPath /dev/null` persists the same "no hooks", so the
# config form gets the same exception (it disables hooks, it plants none).
_CP_NO_HOOKS = frozenset({'/dev/null', 'nul'})


def _cp_hit(text: str) -> bool:
    """True when the word `text` is an MCP config file, a `.git/hooks` path or a
    shell profile (see the section comment)."""
    return bool(_CP_ARG_RE.search((text or '').replace('\\', '/')))


def _cp_code_hit(text: str) -> bool:
    """_cp_hit for a span of code, where the path sits amid other text."""
    return bool(_CP_CODE_RE.search((text or '').replace('\\', '/')))


def _cp_command(prog: str, rest: list) -> Optional[str]:
    """Reason when this one command is `claude mcp add*` or sets git's
    core.hooksPath. `prog`/`rest` are the program and its words after wrappers
    (sudo, env, ...) were skipped. A prose head (echo, grep, ...) names, never runs."""
    if prog in _MP_PROSE_HEADS:
        return None
    after = None
    if prog in _CP_CLAUDE_PROGS:
        after = rest
    elif prog in _CP_LAUNCHERS:
        for j, w in enumerate(rest):
            if _install_prog(w) in _CP_CLAUDE_PROGS:
                after = rest[j + 1:]
                break
    if after is not None:
        sub = [a.lower() for a in after if not a.startswith('-')]
        if 'mcp' in sub:
            j = sub.index('mcp')
            if j + 1 < len(sub) and _CP_MCP_ADD_RE.match(sub[j + 1]):
                return (f"`claude mcp {sub[j + 1]}` (registers an MCP server whose "
                        f"tool calls never reach this fence)")
    if prog == 'git':
        for k, w in enumerate(rest):
            spec = None
            if w in ('-c', '--config', '--config-env'):
                spec = rest[k + 1] if k + 1 < len(rest) else ''
            elif w.startswith('--config'):
                spec = w.split('=', 1)[1] if '=' in w else None
            elif w.startswith('-c'):
                spec = w[2:]
            if spec is not None and _CP_HOOKSPATH_RE.match(spec):
                value = spec.split('=', 1)[1].strip().lower() if '=' in spec else ''
                if value in _CP_NO_HOOKS and 'env' not in w:
                    continue         # one-shot "run no hooks", the --no-verify of config
                return "`git -c core.hooksPath=...` (points git at a hooks directory)"
        sub_i = None
        k = 0
        while k < len(rest):
            w = rest[k]
            if w in ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix',
                     '--config-env', '--exec-path'):
                k += 2
            elif w.startswith('-'):
                k += 1
            else:
                sub_i = k
                break
        if sub_i is not None and rest[sub_i].lower() == 'config':
            args = rest[sub_i + 1:]
            if not any(a in _CP_GIT_CONFIG_READS for a in args):
                pos = [a for a in args if a and not a.startswith('-')]
                for j, a in enumerate(pos):
                    if a.lower() == 'core.hookspath':
                        vals = [v.strip().lower() for v in pos[j + 1:]]
                        if vals and not all(v in _CP_NO_HOOKS for v in vals):
                            return "`git config core.hooksPath <value>` (points git at a hooks directory)"
                        break
    return None


class _MpConfig(_MpTarget):
    """MCP config files, `.git/hooks` and shell profiles (_cp_hit's paths) as the
    shell-write scan's target. Plugin checks give way to _cp_command; no
    move/delete rule, and the write list is the plain one."""
    into = _CP_WHAT
    under = _CP_WHAT
    plugin_rules = False
    removes = False
    write_api = _MP_WRITE_API_RE

    def start(self, cwd):
        return _cp_hit(cwd or '')

    def cd(self, ctx, target):
        return True if _cp_hit(target) else ctx

    def hit(self, text, ctx):
        return _cp_hit(text)

    def word(self, w, ctx):
        if _cp_hit(w):
            return True
        return bool(ctx and w and not w.startswith('-') and w[0] not in '/~$%\\'
                    and not _WIN_DRIVE_RE.match(w))

    def code(self, code):
        return _cp_code_hit(code)

    def command(self, prog, rest):
        return _cp_command(prog, rest)


_MP_CONFIG = _MpConfig()


def _config_plant_guard(command: str, cwd: Optional[str] = None) -> FenceDecision:
    """Block an armed agent planting an MCP server, a git hook or a shell
    profile line (see the section comment). Never passable."""
    try:
        scan = _mask_inert_prose(command.strip(), mask_python_c=False)
    except Exception:
        scan = command.strip()
    try:
        why = _mp_scan_command(scan, cwd, _MP_CONFIG)
    except Exception:
        return FenceDecision(False, '')      # never wedge the agent on a parse bug
    if not why:
        return FenceDecision(False, '')
    return FenceDecision(True, f"{why} — a planted MCP server's tools are never "
                               f"seen by this fence, and a git hook or shell "
                               f"profile runs outside every tool call, so what "
                               f"Claude loads and what runs around it is "
                               f"human-owned (supply chain)",
                         overridable=False)


# ── Per-target scratch judgement for deletes (Quill, 2026-10-07 §2) ──────────
# The delete exemption used to be `any(marker in command) and '..' not in
# command`: a scratch marker ANYWHERE in the text let every recursive delete in
# it through. `rm -rf ~ /tmp/build`, `rm -rf "$HOME"  # /tmp/`, `rm -rf "$HOME";
# ls /tmp/` and `Remove-Item -Recurse -Force <Documents>, <Temp>\x` all passed,
# and so did `C:/work/scratchpad-app`, whose NAME merely contains a marker.
# Present since the MVP commit (24785676).
#
# Now every delete is lexed and every target judged on its own:
#   • the command is split into segments. A `#` comment never reaches a
#     target. A segment that matches a delete pattern must itself BE the delete
#     (rm / rmdir / Remove-Item / ri / del / erase / rd, after a sudo-style
#     wrapper), a `bash -c` / `powershell -Command` / `cmd /c` around one
#     (judged recursively), or the `gci <paths> | ri` pipeline. Anything else
#     (xargs, eval, find -exec, a script block) refuses;
#   • each target is read the way EACH shell that may run it reads it (the
#     Bash tool name also carries Codex's PowerShell on Windows), expanding ~,
#     $HOME, $env:X, %X% and an allowlist of environment variables. A variable
#     the command itself mentions any other way (an assignment), any other
#     expansion, a substitution, a brace or an escape the two shells disagree
#     on refuses;
#   • a reading is scratch when its REAL path (8.3 names, junctions and
#     symlinks resolved) lies strictly inside a temp root (/tmp, <drive>:/tmp,
#     <drive>:/Users/<u>/AppData/Local/Temp, $TEMP, $TMP, $TMPDIR, the
#     interpreter's temp dir) or at/under a folder named exactly `_scratch` or
#     `scratchpad`;
#   • a target passes when one reading is scratch and every other reading is
#     scratch or names nothing that exists (bash's reading of an unquoted
#     `C:\Users\x\AppData\Local\Temp\y` is a no-op name in the cwd);
#   • ONE failing target refuses the whole command, and so does anything this
#     section cannot judge: it fails closed to the pre-exemption verdict.
# Known limits: after a `cd` in the same command a relative target is judged
# by its folder names only; `mv <dir> /tmp/y && rm -rf /tmp/y` is a move into
# scratch, which no delete check can see; a link made in the same command
# refuses outright because its target cannot be resolved yet.
class _Unjudgeable(Exception):
    """A delete this section cannot read with certainty (→ refuse)."""


_SCRATCH_DIR_NAMES = ('_scratch', 'scratchpad')
_SCRATCH_ENV_VARS = frozenset({'HOME', 'USERPROFILE', 'TEMP', 'TMP', 'TMPDIR',
                               'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH',
                               'SYSTEMROOT', 'SYSTEMDRIVE', 'USERNAME'})
_DEL_HEADS = frozenset({'rm', 'rmdir', 'remove-item', 'ri', 'del', 'erase', 'rd'})
_DEL_CMD_HEADS = frozenset({'rmdir', 'rd', 'del', 'erase'})
_DEL_WRAPPERS = frozenset({'sudo', 'doas', 'nohup', 'time', 'command', 'builtin', 'exec'})
_DEL_LISTERS = frozenset({'get-childitem', 'gci', 'ls', 'dir', 'get-item', 'gi'})
_DEL_FOREACH = frozenset({'%', 'foreach-object', 'foreach'})
_DEL_CD = frozenset({'cd', 'chdir', 'pushd', 'popd', 'set-location', 'sl',
                     'push-location', 'pop-location'})
_DEL_POSIX_SHELLS = frozenset({'bash', 'sh', 'zsh', 'dash', 'ksh'})
_DEL_PS_SHELLS = frozenset({'powershell', 'pwsh'})
_DEL_PS_NOARG_OPTS = frozenset({'-noprofile', '-nop', '-noninteractive', '-noni',
                                '-nologo', '-nol'})
_DEL_PS_VALUE_OPTS = frozenset({'-executionpolicy', '-ep', '-ex'})
_DEL_PIPED_REFS = frozenset({'$_', '$psitem', '$_.fullname', '$psitem.fullname',
                             '$_.pspath', '$psitem.pspath'})
_DEL_ACTION_PARAMS = frozenset({'erroraction', 'ea', 'warningaction', 'wa',
                                'informationaction', 'infa', 'progressaction', 'proga'})
_DEL_ACTION_PREFS = frozenset({'silentlycontinue', 'stop', 'continue', 'ignore',
                               'inquire', 'suspend', 'break'})
# A link made in the same command cannot be resolved before it exists.
_DEL_LINK_RE = re.compile(
    r'(?<![\w.\-])(?:ln|mklink|junction|subst|mount)(?:\.exe)?(?![\w.\-])'
    r'|\b(?:SymbolicLink|Junction|HardLink|CreateSymbolicLink|symlink)\b', re.I)
_DEL_VAR_RE = re.compile(r'\$(?:\{(?P<b>(?:env:)?[A-Za-z_]\w*)\}|(?P<auto>_(?!\w)|PSItem\b)'
                         r'|(?P<n>(?:env:)?[A-Za-z_]\w*))', re.I)


def _del_lex(text: str) -> list:
    """Segments of `text`: [{'sep', 'end', 'words', 'text'}], each word
    (start, end, [(quote, raw), ...]) with quote "'", '"' or '' (unquoted).
    Raises _Unjudgeable on any construct bash and PowerShell read differently
    or that hides a command (substitution, here-doc, backtick, `''`)."""
    segs: list = []
    words: list = []
    parts: list = []
    st = {'wstart': -1, 'seg_start': 0, 'sep': '', 'redirect': False}
    n = len(text)

    def end_word(end):
        nonlocal parts
        if parts:
            if st['redirect']:
                st['redirect'] = False   # a redirection's target, not an argument
            else:
                words.append((st['wstart'], end, parts))
        parts = []
        st['wstart'] = -1

    def end_seg(at, term, nxt):
        nonlocal words
        end_word(at)
        if st['redirect']:
            raise _Unjudgeable('redirection with no target')
        segs.append({'sep': st['sep'], 'end': term, 'words': words,
                     'text': text[st['seg_start']:at]})
        words = []
        st['sep'] = term
        st['seg_start'] = nxt

    def add(q, raw, at):
        if not parts:
            st['wstart'] = at
        if q == '' and parts and parts[-1][0] == '':
            parts[-1] = ('', parts[-1][1] + raw)
        else:
            parts.append((q, raw))

    i = 0
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if c in _CURLY_QUOTES or c == '`':
            raise _Unjudgeable('backtick or curly quote')
        if c in ' \t\r':
            end_word(i)
            i += 1
        elif two in ('&&', '||'):
            end_seg(i, two, i + 2)
            i += 2
        elif c == '&' and text[i + 1:i + 2] == '>':
            end_word(i)
            i += 2 + (text[i + 2:i + 3] == '>')
            st['redirect'] = True
        elif c in '\n;|(){}&':
            if c in '{}' and parts:
                raise _Unjudgeable('brace inside a word')
            end_seg(i, c, i + 1)
            i += 1
        elif c in '<>':
            if two in ('<<', '<(', '>(', '<#'):
                raise _Unjudgeable('here-doc, process substitution or block comment')
            if parts:
                if len(parts) == 1 and parts[0][0] == '' and re.fullmatch(r'\d+|\*', parts[0][1]):
                    parts = []          # `2>` / `*>`: the fd belongs to the operator
                    st['wstart'] = -1
                else:
                    raise _Unjudgeable('redirection glued to a word')
            j = i + 1 + (text[i + 1:i + 2] == '>')
            m = re.match(r'&(?:\d+|-)', text[j:])
            if m:
                i = j + m.end()         # `2>&1`: duplicates a descriptor, no target
            else:
                st['redirect'] = True
                i = j
        elif c == '#' and not parts:
            nl = text.find('\n', i)
            i = n if nl < 0 else nl     # a comment: never an argument
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0 or text[j + 1:j + 2] == "'":
                raise _Unjudgeable("unpaired quote or ''")
            add("'", text[i + 1:j], i)
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                ch = text[j]
                if ch == '\\' and text[j + 1:j + 2] in ('$', '`', '"', '\\', '\n', '\r'):
                    raise _Unjudgeable('escape inside double quotes')
                if ch == '$' and text[j + 1:j + 2] == '(':
                    raise _Unjudgeable('substitution')
                if ch == '$' and text[j + 1:j + 2] == '{':
                    m = re.match(r'\$\{(?:env:)?[A-Za-z_]\w*\}', text[j:])
                    if not m:
                        raise _Unjudgeable('parameter expansion')
                    j += m.end()
                    continue
                j += 1
            if j >= n or text[j + 1:j + 2] == '"':
                raise _Unjudgeable('unpaired quote or ""')
            add('"', text[i + 1:j], i)
            i = j + 1
        elif c == '$':
            nx = text[i + 1:i + 2]
            if nx in ('(', "'", '"'):
                raise _Unjudgeable('substitution or ANSI-C quote')
            if nx == '{':
                m = re.match(r'\$\{(?:env:)?[A-Za-z_]\w*\}', text[i:])
                if not m:
                    raise _Unjudgeable('parameter expansion')
                add('', m.group(0), i)
                i += m.end()
            else:
                add('', c, i)
                i += 1
        elif c == '@' and not parts and text[i + 1:i + 2] in ('(', '{', "'", '"'):
            raise _Unjudgeable('PowerShell array, hashtable or here-string')
        elif c == '\\':
            nx = text[i + 1:i + 2]
            if not nx or nx in '\'" \t\r\n;|&#$()<>{}`':
                raise _Unjudgeable('backslash the two shells read differently')
            add('', c + nx, i)
            i += 2
        else:
            add('', c, i)
            i += 1
    end_seg(n, '', n)
    return segs


def _del_home() -> str:
    return os.path.expanduser('~')


def _del_env(name: str, shell: str, raw: str) -> str:
    """The value of an allowlisted variable, or _Unjudgeable."""
    up = name.upper()
    if up not in _SCRATCH_ENV_VARS or (shell == 'bash' and name != up):
        raise _Unjudgeable(f'variable {name} is not on the allowlist')
    n = re.escape(name)
    # Every mention of the name must be a plain read: `TEMP=~`, `export TEMP`,
    # `$env:TEMP = ...`, `Set-Item env:TEMP` all make the hook's value a guess.
    # (`...\Local\Temp` in a path is a folder, not the variable.)
    total = len(re.findall(rf'(?<!\w)(?<!local/)(?<!local\\){n}(?!\w)', raw, re.I))
    reads = len(re.findall(rf'(?:\$\{{(?:env:)?{n}\}}|\$(?:env:)?{n}(?!\w)|%{n}%)'
                           rf'(?!\s*[-+*/.?]?=(?!=))', raw, re.I))
    if total > reads:
        raise _Unjudgeable(f'{name} is assigned or mentioned in the command')
    if up == 'HOME' and shell == 'ps':
        return _del_home()               # PowerShell's $HOME is the profile dir
    val = os.environ.get(up) or os.environ.get(name)
    if not val and up in ('HOME', 'USERPROFILE'):
        val = _del_home()
    if not val:
        raise _Unjudgeable(f'{name} is not set')
    return val


def _del_expand(raw: str, shell: str, quoted: bool, cmd_raw: str) -> str:
    """Expand the variables in one piece of a word for `shell`."""
    if shell == 'cmd':
        if any(ch in raw for ch in '^!'):
            raise _Unjudgeable('cmd escape or delayed expansion')
        if '%' in re.sub(r'%[A-Za-z_]\w*%', '', raw):
            raise _Unjudgeable('stray % in a cmd word')
        return re.sub(r'%([A-Za-z_]\w*)%',
                      lambda m: _del_env(m.group(1), shell, cmd_raw), raw)
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == '\\' and shell == 'bash' and not quoted:
            out.append(raw[i + 1:i + 2])     # bash drops an unquoted backslash
            i += 2
            continue
        if ch != '$':
            out.append(ch)
            i += 1
            continue
        m = _DEL_VAR_RE.match(raw, i)
        if not m or m.group('auto'):
            raise _Unjudgeable('unreadable $ expansion')
        name = m.group('b') or m.group('n')
        if name.lower().startswith('env:'):
            if shell != 'ps':
                raise _Unjudgeable('$env: outside PowerShell')
            name = name[4:]
        elif shell == 'ps' and name.upper() != 'HOME':
            raise _Unjudgeable(f'PowerShell variable ${name}')
        out.append(_del_env(name, shell, cmd_raw))
        i = m.end()
        if shell == 'ps' and not quoted and raw[i:i + 1] in ('.', '[', '('):
            raise _Unjudgeable('PowerShell member access on a variable')
    return ''.join(out)


_DEL_TILDE = '\x00HOME\x00'


def _del_word_values(pieces: list, shell: str, cmd_raw: str) -> list:
    """What `shell` passes for one word: a list (PowerShell splits on unquoted
    commas; bash and cmd give one value)."""
    if shell == 'cmd' and any(q == "'" for q, _ in pieces):
        raise _Unjudgeable("cmd does not treat ' as a quote")
    vals = ['']
    for k, (q, raw) in enumerate(pieces):
        if q == "'":
            vals[-1] += raw
            continue
        quoted = q == '"'
        if k == 0 and not quoted and raw.startswith('~') and shell == 'bash':
            rest = raw[1:]
            if rest and not rest.startswith('/'):
                raise _Unjudgeable('~user or ~+ expansion')
            if not rest and len(pieces) > 1:
                raise _Unjudgeable('~ followed by a quoted part')
            raw = _DEL_TILDE + rest
        chunks = raw.split(',') if (shell == 'ps' and not quoted) else [raw]
        for ci, chunk in enumerate(chunks):
            if ci:
                vals.append('')
            if chunk.startswith(_DEL_TILDE):
                vals[-1] += _del_home() + _del_expand(chunk[len(_DEL_TILDE):], shell,
                                                      quoted, cmd_raw)
            else:
                vals[-1] += _del_expand(chunk, shell, quoted, cmd_raw)
    if shell == 'ps':
        # `a, b` lexes as `a,` and `b`: an empty end is the list continuing
        # into the next word (_del_args checks that word), an empty middle is not.
        if len(vals) > 1 and not vals[-1]:
            vals.pop()
        if len(vals) > 1 and not vals[0]:
            vals.pop(0)
        if any(not v for v in vals) and len(vals) > 1:
            raise _Unjudgeable('empty element in a PowerShell list')
        # The FileSystem provider resolves a leading ~ in ANY path string,
        # quoted or not, first in a list or not.
        out = []
        for v in vals:
            if v == '~' or v.startswith(('~/', '~\\')):
                v = _del_home() + v[1:]
            elif v.startswith('~'):
                raise _Unjudgeable('~ followed by a name')
            out.append(v)
        vals = out
    return vals


def _del_word_value(pieces: list, shell: str, cmd_raw: str) -> str:
    vals = _del_word_values(pieces, shell, cmd_raw)
    if len(vals) != 1:
        raise _Unjudgeable('a list where one value was expected')
    return vals[0]


def _del_cmp_form(p: str) -> str:
    """Comparable form: forward slashes, no `.`/empty components, no trailing
    slash; Windows: trailing dots/spaces dropped per component, lowercased."""
    nt = os.name == 'nt'
    s = p.replace('\\', '/')
    lead = '/' if s.startswith('/') else ''
    comps = []
    for c in s.split('/'):
        if nt and c != '.':
            c = c.rstrip(' .')
        if c in ('', '.'):
            continue
        comps.append(c)
    out = lead + '/'.join(comps)
    return out.lower() if nt else out


def _del_temp_roots() -> list:
    import tempfile
    home = _del_cmp_form(os.path.realpath(_del_home()))
    cands = [os.environ.get(k) for k in ('TEMP', 'TMP', 'TMPDIR')]
    cands.append(tempfile.gettempdir())
    if os.environ.get('LOCALAPPDATA'):
        cands.append(os.path.join(os.environ['LOCALAPPDATA'], 'Temp'))
    roots = set()
    for c in cands:
        if not c:
            continue
        for v in (c, os.path.realpath(c)):
            r = _del_cmp_form(v)
            # A temp variable pointed at a drive root, or at home or above it,
            # is not a scratch root.
            if r.count('/') < 2 or r == home or home.startswith(r + '/'):
                continue
            roots.add(r)
    return sorted(roots)


_DEL_TEMP_TEXT_RE = re.compile(
    r'^(?:[a-z]:)?(?:/private)?/tmp/.'
    r'|^[a-z]:/users/[^/*?\[\]]+/appdata/local/temp/.', re.I)


def _del_is_scratch(real: str) -> bool:
    nt = os.name == 'nt'
    if any((c.lower() if nt else c) in _SCRATCH_DIR_NAMES for c in real.split('/')):
        return True
    if _DEL_TEMP_TEXT_RE.match(real):
        return True
    return any(real.startswith(r + '/') for r in _del_temp_roots())


def _del_path_state(value: str, shell: str, cwd: Optional[str], cd_seen: bool) -> str:
    """'scratch', 'dud' (names nothing that exists) or 'bad' for one reading."""
    nt = os.name == 'nt'
    s = value.replace('\\', '/')
    if not s.strip() or s.startswith('//'):
        return 'bad'                     # empty, UNC or device path
    if nt and shell == 'bash':
        m = re.match(r'^/([a-zA-Z])(?=/|$)', s)
        if m:
            s = m.group(1) + ':' + s[2:]  # MSYS /c/x → C:/x
    for c in s.split('/'):
        c2 = c.rstrip(' .') if nt else c
        if c2 == '' and c not in ('', '.'):
            return 'bad'                 # `..` (Windows: also `...`, `.. `)
        if not nt and c == '..':
            return 'bad'
    glob = any(ch in s for ch in '*?[')
    base = (cwd or os.getcwd()).replace('\\', '/').rstrip('/')
    drive_rel = re.match(r'^([a-zA-Z]):(?!/)', s)
    if drive_rel:
        # `C:x` is relative to the cwd on drive C: known only when that is ours.
        if base[:2].lower() != s[:2].lower():
            cd_seen = True
        s = s[2:]
    absolute = s.startswith('/') or bool(re.match(r'^[a-zA-Z]:/', s))
    if not absolute:
        if cd_seen:
            # The base folder is unknown: scratch only by a scratch-named folder.
            names = [c.lower() if nt else c for c in s.split('/')]
            return 'scratch' if any(c in _SCRATCH_DIR_NAMES for c in names) else 'bad'
        s = base + '/' + s
    fs = s
    if nt and shell == 'bash' and re.match(r'^/tmp(?:/|$)', s):
        import tempfile
        fs = tempfile.gettempdir().replace('\\', '/') + s[4:]   # MSYS /tmp
    try:
        real = _del_cmp_form(os.path.realpath(fs))
    except (OSError, ValueError):
        return 'bad'
    if _del_is_scratch(real):
        return 'scratch'
    try:
        if not glob and not os.path.lexists(fs):
            return 'dud'
    except (OSError, ValueError):
        pass
    return 'bad'


def _del_target_ok(pieces: list, shells: tuple, cwd, cd_seen, cmd_raw) -> bool:
    scratch = False
    for sh in shells:
        for v in _del_word_values(pieces, sh, cmd_raw):
            state = _del_path_state(v, sh, cwd, cd_seen)
            if state == 'bad':
                return False
            scratch = scratch or state == 'scratch'
    return scratch


def _del_head(words: list, shells: tuple, cmd_raw: str):
    """(index, lowercase program name) of a segment's command word after any
    sudo-style wrapper, or None when the shells would not agree on it."""
    i = 0
    while i < len(words):
        try:
            vals = {_del_word_value(words[i][2], sh, cmd_raw) for sh in shells}
        except _Unjudgeable:
            return None
        if len(vals) != 1:
            return None
        v = vals.pop()
        name = re.split(r'[\\/]', v)[-1].lower()
        if name.endswith('.exe'):
            name = name[:-4]
        if name in _DEL_WRAPPERS and i + 1 < len(words):
            i += 1
            continue
        return i, name
    return None


def _del_args(words: list, cmd_style: bool) -> tuple:
    """(targets, piped_refs) among a delete's or lister's arguments."""
    targets, piped = [], 0
    end_opts = skip_action = list_open = False
    for w in words:
        pieces = w[2]
        q0, lit0 = pieces[0]
        plain = lit0 if (len(pieces) == 1 and q0 == '') else None
        if list_open and lit0.startswith('-'):
            raise _Unjudgeable('a PowerShell list running into an option')
        list_open = pieces[-1][0] == '' and pieces[-1][1].endswith(',')
        if skip_action:
            skip_action = False
            if plain is not None and plain.lower() in _DEL_ACTION_PREFS:
                continue
        if plain is not None and plain.lower() in _DEL_PIPED_REFS:
            piped += 1
            continue
        if not end_opts and lit0.startswith('-'):
            if q0 != '' or len(pieces) > 1:
                raise _Unjudgeable('quoted option')
            if lit0 == '--':
                end_opts = True
                continue
            name, colon, val = lit0[1:].partition(':')
            if colon:
                if val.lower() not in _DEL_ACTION_PREFS | {'$true', '$false'}:
                    raise _Unjudgeable(f'option value {lit0}')
                continue
            if name.lower() in _DEL_ACTION_PARAMS:
                skip_action = True
            continue
        if cmd_style and plain is not None and plain.startswith('/'):
            continue                     # cmd's `rd /s /q`
        targets.append(pieces)
    if list_open:
        raise _Unjudgeable('a PowerShell list with no last element')
    return targets, piped


def _del_pipeline_ok(segs, k, shells, cwd, cd_seen, cmd_raw, piped_refs) -> bool:
    """`gci <scratch paths> | ri` and `gci <scratch paths> | % { ri $_ }`."""
    seg = segs[k]
    j = k - 1
    if seg['sep'] == '{':
        if seg['end'] != '}' or j < 0:
            return False
        fe = segs[j]
        fe_head = _del_head(fe['words'], shells, cmd_raw)
        if (len(fe['words']) != 1 or not fe_head or fe_head[1] not in _DEL_FOREACH
                or fe['sep'] != '|' or fe['end'] != '{'):
            return False
        j -= 1
    elif seg['sep'] == '|':
        if piped_refs:
            return False                 # $_ outside a ForEach block
    else:
        return False
    if j < 0:
        return False
    head_seg = segs[j]
    if head_seg['sep'] in ('|', '{') or head_seg['end'] != '|':
        return False
    head = _del_head(head_seg['words'], shells, cmd_raw)
    if not head or head[1] not in _DEL_LISTERS or head[0] != 0:
        return False
    targets, piped = _del_args(head_seg['words'][1:], False)
    if piped or not targets:
        return False
    return all(_del_target_ok(t, shells, cwd, cd_seen, cmd_raw) for t in targets)


def _del_nested_ok(seg, idx, name, shells, cwd, cd_seen, cmd_raw, depth) -> bool:
    """`bash -c '<delete>'`, `powershell -Command <delete>`, `cmd /c <delete>`:
    the inner text must be the same whichever outer shell runs it."""
    if seg['sep'] == '|' or seg['end'] in ('(', '{'):
        return False
    lists = {tuple(_del_word_value(w[2], sh, cmd_raw) for w in seg['words'][idx + 1:])
             for sh in shells}
    if len(lists) != 1:
        return False
    ws = list(lists.pop())
    if name in _DEL_POSIX_SHELLS:
        if len(ws) != 2 or ws[0] != '-c':
            return False
        return _del_judge(ws[1], ('bash',), cwd, cd_seen, cmd_raw, depth + 1)
    if name in _DEL_PS_SHELLS:
        while ws and ws[0].lower() in _DEL_PS_NOARG_OPTS | _DEL_PS_VALUE_OPTS:
            ws = ws[2:] if ws[0].lower() in _DEL_PS_VALUE_OPTS else ws[1:]
        if len(ws) < 2 or ws[0].lower() not in ('-command', '-c'):
            return False
        return _del_judge(' '.join(ws[1:]), ('ps',), cwd, cd_seen, cmd_raw, depth + 1)
    if name == 'cmd':
        if len(ws) < 2 or ws[0].lower() not in ('/c', '//c', '/k', '//k'):
            return False
        inner = ' '.join(ws[1:])
        # cmd also splits arguments on these; the lexer would not.
        if any(ch in inner for ch in ';,=()\'^!'):
            return False
        return _del_judge(inner, ('cmd',), cwd, cd_seen, cmd_raw, depth + 1)
    return False


def _del_judge(text, shells, cwd, cd_seen, cmd_raw, depth=0) -> bool:
    """True only when every delete in `text` targets scratch (see the section
    comment) and at least one delete was found. Raises _Unjudgeable."""
    if depth > 3 or _DEL_LINK_RE.search(text):
        return False
    segs = _del_lex(text)
    heads = [_del_head(s['words'], shells, cmd_raw) if s['words'] else None for s in segs]
    if any(s['words'] and (h is None or h[1] in _DEL_CD) for s, h in zip(segs, heads)):
        cd_seen = True                   # or a command word we could not read
    judged = 0
    for k, seg in enumerate(segs):
        if not any(p.search(seg['text']) for p in _DELETE_PATTERNS):
            continue
        head = heads[k]
        if head is None:
            return False
        idx, name = head
        if name in _DEL_HEADS:
            cmd_style = shells == ('cmd',) and name in _DEL_CMD_HEADS
            targets, piped = _del_args(seg['words'][idx + 1:], cmd_style)
            if piped or not targets:
                if targets or idx != 0 or not _del_pipeline_ok(
                        segs, k, shells, cwd, cd_seen, cmd_raw, piped):
                    return False
            else:
                if seg['sep'] == '|' or seg['end'] in ('(', '{'):
                    return False
                if not all(_del_target_ok(t, shells, cwd, cd_seen, cmd_raw)
                           for t in targets):
                    return False
        elif name in _DEL_POSIX_SHELLS | _DEL_PS_SHELLS | {'cmd'}:
            if not _del_nested_ok(seg, idx, name, shells, cwd, cd_seen, cmd_raw, depth):
                return False
        else:
            return False                 # xargs, eval, find -exec, prose ...
        judged += 1
    return judged > 0


def _deletes_all_scratch(cmd: str, raw: str, cwd: Optional[str],
                         shell: Optional[str] = None) -> bool:
    """The delete exemption: True only when every delete in `cmd` (the masked
    command) is scratch-scoped. `raw` is the unmasked text, searched for
    assignments; `shell` is 'powershell' for the PowerShell tool, else None
    (bash AND PowerShell readings, since Codex sends PowerShell as Bash)."""
    shells = ('ps',) if shell == 'powershell' else ('bash', 'ps')
    try:
        return _del_judge(cmd, shells, cwd, False, raw)
    except (_Unjudgeable, IndexError, ValueError):
        return False


_HUMAN_GATE_ROUTE_RE = re.compile(r'local-auth/set|/attend-once(?!/consume)', re.IGNORECASE)


def classify_bash(command: str, cwd: Optional[str] = None,
                  shell: Optional[str] = None) -> FenceDecision:
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
    # Git-planted code (2026-10-12), see the section above the Codex apply_patch
    # block: judged on the raw text and ahead of the pattern lists, like the
    # guard below, and never passable.
    git_plant = _git_plant_guard(command, cwd)
    if git_plant.blocked:
        return git_plant
    # Judged on the raw text, not the masked `cmd` below (a `python -c` body is
    # masked there), and ahead of the pattern lists so a never-passable plant
    # is not reported as an overridable disk or delete block.
    mod_plant = _mod_plant_guard(command, cwd)
    if mod_plant.blocked:
        return mod_plant
    # Same parser, aimed at the fence's own files and the install tree (see
    # "Shell writes to the fence's own files"). Never passable either.
    self_protect = _shell_self_protect_guard(command, cwd)
    if self_protect.blocked:
        return self_protect
    config_plant = _config_plant_guard(command, cwd)
    if config_plant.blocked:
        return config_plant
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

    # git's silent discards (2026-10-12), see the section above the Codex
    # apply_patch block. Overridable, like the git reset --hard pattern above.
    git_discard = _git_discard_guard(cmd, cwd)
    if git_discard.blocked:
        return git_discard

    if any(pat.search(cmd) for pat in _DELETE_PATTERNS):
        # Every target of every delete judged on its own (Quill, 2026-10-07;
        # see the section above _HUMAN_GATE_ROUTE_RE). `shell` narrows the
        # readings to PowerShell's for the PowerShell tool.
        if not _deletes_all_scratch(cmd, command, cwd, shell):
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

    installs = _agent_shell_install(cmd, cwd)
    if installs.blocked:
        return installs

    return FenceDecision(False, '')


# ── Git-planted code and silent working-tree discards (2026-10-12) ──────────
# Measured on master d5748ef5 with the public skillkeel/tamper-cases corpus
# (hook level, --armed, exit 0 = allowed):
#   git config core.fsmonitor '...'           git -c core.fsmonitor='sh -c id' status
#   git config core.sshCommand '...'          git config alias.st '!sh -c id'
#   git -c alias.x='!id' x                    git config filter.x.smudge id
#   git config --local diff.external /tmp/d   printf '[core]\n\tfsmonitor = id\n' >> sub/.git/config
#   mv .gitx sub/.git                         cp -r /tmp/g sub/.git
#   git checkout -- .                         (git reset --hard and git clean -f were refused)
# Each plants code git runs LATER (on the next `git status`, `git commit`, a
# diff, a fetch, a human's next pager), outside any tool call this hook sees,
# or throws away uncommitted work. Two guards, one walker:
#
# _git_plant_guard (armed only; never passable, like the mod-plant guard):
#   - `git config [--global|--system|--local|--file F|--worktree] KEY VALUE`,
#     `git config set KEY VALUE` and `git -c KEY=VALUE <cmd>` / `--config-env`,
#     where KEY is a setting that makes git run a program. Refused keys:
#       core.fsmonitor core.sshCommand core.gitProxy core.askPass core.hooksPath
#       core.pager pager.* core.editor sequence.editor diff.external
#       diff.*.textconv diff.*.command filter.*.smudge|clean|process
#       merge.*.driver mergetool|difftool|browser|man.*.cmd|path
#       credential[.*].helper gpg[.*].program remote.*.uploadpack|receivepack|
#       uploadarchive uploadpack.packObjectsHook trailer.*.cmd include.path
#       includeIf.*.path init.templateDir, alias.* and submodule.*.update when
#       the value starts with `!`.
#     Values that cannot run anything stay allowed: an empty value; core.fsmonitor
#     true|false (git's built-in daemon, the common perf toggle); core.hooksPath
#     /dev/null (the config spelling of --no-verify; 3 of 3 real uses on 93,676
#     past shell calls, per fix/fence-mcp-plant); core.editor / sequence.editor
#     true|false|:|cat (the "never open an editor" idiom); core.pager / pager.*
#     cat|less|more|true|false|: (pager.* also yes|no|on|off|0|1); credential.helper
#     store|cache|manager|manager-core|osxkeychain|wincred|libsecret; an alias that
#     is a plain git alias. Reads and removals are never touched: `git config
#     KEY`, --get*, --list, --unset*, --remove-section, --rename-section, --edit.
#   - a shell write (redirect, tee, cp/mv/ln/install/rsync, sed -i, Set-Content,
#     tar -x, dd of=, curl -o, interpreter code or heredoc) aimed at a
#     `.git/config`, `.git/config.worktree`, `.git/hooks/*`, `.git/modules/*/
#     {config,hooks}`, `.git/worktrees/*/config.worktree`, `~/.gitconfig`,
#     `~/.config/git/config` or `/etc/gitconfig`; and cp/mv/ln/install/rsync/
#     redirect onto a path whose last component is `.git` (a copied or linked
#     `.git` directory, or a `.git` file holding `gitdir: <elsewhere>`, brings its
#     own config). Inside a `cd .git` / `cd .git/hooks` a relative `config` or
#     `hooks/x` counts too. The Write/Edit/MultiEdit/NotebookEdit tools get the
#     same path test in classify_action.
#
# _git_discard_guard (armed only; overridable, exactly like `git reset --hard`,
# which already accepts that an agent cannot undo its own edit):
#   `git checkout -- PATH...` / `git checkout TREE -- PATH`, `git checkout .`
#   (or any lone argument that is a glob, a `:` pathspec or an existing file),
#   `git checkout -f|--force`, `git restore PATH...` and `--worktree` (not
#   `--staged` alone: that only unstages), `git switch -f|--force|
#   --discard-changes` and `git checkout-index -f` (the plumbing under
#   `checkout -- .`). Branch switching (`git checkout BRANCH`, `-b`, `-B`, `-`,
#   `git switch BRANCH`, `-c`, `-C`) stays allowed: `-B`/`-C` move a branch
#   pointer (the old tip stays in the reflog) and never touch uncommitted work.
#
# KNOWN GAPS, accepted (command text only): a script that does the write
# (`bash x.sh`, `python plant.py`), a path or key built from a variable,
# GIT_CONFIG_KEY_n / GIT_CONFIG_PARAMETERS / GIT_SSH_COMMAND / GIT_EXTERNAL_DIFF
# environment spellings, `git init|clone --template`, a bare repo's
# `<name>.git/config` (only a component named exactly `.git` counts),
# `xargs`/`find -exec` carrying the write, and `git read-tree --reset -u`.
_GP_WHAT = 'a git config, git hook or .git directory'
_GP_EXEC_KEY_RE = re.compile(
    r'^(?:core\.(?:sshcommand|gitproxy|askpass|hookspath|fsmonitor|pager|editor)'
    r'|pager\..+|sequence\.editor|diff\.external|diff\..+\.(?:textconv|command)'
    r'|filter\..+\.(?:smudge|clean|process)|merge\..+\.driver'
    r'|(?:mergetool|difftool|browser|man)\..+\.(?:cmd|path)'
    r'|credential\.(?:.+\.)?helper|gpg\.(?:.+\.)?program'
    r'|remote\..+\.(?:uploadpack|receivepack|uploadarchive)'
    r'|uploadpack\.packobjectshook|trailer\..+\.cmd'
    r'|include\.path|includeif\..+\.path|init\.templatedir'
    r'|alias\..+|submodule\..+\.update)$')
_GP_BOOLS = frozenset({'true', 'false', 'yes', 'no', 'on', 'off', '0', '1'})
_GP_NOOP_EDITORS = frozenset({'true', 'false', ':', 'cat'})
_GP_SAFE_PAGERS = frozenset({'cat', 'less', 'more', 'true', 'false', ':'})
_GP_SAFE_HELPERS = frozenset({'store', 'cache', 'manager', 'manager-core',
                              'osxkeychain', 'wincred', 'libsecret'})
_GP_META_RE = re.compile(r'[;&|$`()<>\\\n]')
_GP_GIT_VALUE_OPTS = frozenset({'-C', '--git-dir', '--work-tree', '--namespace',
                                '--super-prefix'})
_GP_CFG_NOWRITE = frozenset({'--get', '--get-all', '--get-regexp', '--get-urlmatch',
                             '--list', '-l', '--unset', '--unset-all',
                             '--remove-section', '--rename-section', '--edit', '-e'})
_GP_CFG_VALUE_OPTS = frozenset({'-f', '--file', '--blob', '-t', '--type', '--default',
                                '--comment'})
_GP_CFG_READ_SUBS = frozenset({'get', 'list', 'unset', 'edit', 'remove-section',
                               'rename-section'})
# A word handed to a writer: .git/config, .git/hooks/..., a global git config.
_GP_PATH_RE = re.compile(
    r'(?:^|/)\.git/(?:modules/.+?/|worktrees/[^/]+/)?(?:config(?:\.worktree)?$|hooks(?:/|$))'
    r'|(?:^|/)\.gitconfig$|(?:^|/)\.config/git/config$|^/etc/gitconfig$', re.I)
# The same inside a span of code (a `python -c` body, a heredoc).
_GP_CODE_RE = re.compile(
    r'\.git/(?:modules/.+?/|worktrees/[^/]+/)?(?:config\b|hooks\b)|\.gitconfig\b'
    r'|\.config/git/config\b', re.I)
_GP_DOTGIT_RE = re.compile(r'(?:^|/)\.git/?$', re.I)
_GP_NET_PROGS = frozenset({'curl', 'wget', 'iwr', 'invoke-webrequest', 'irm',
                           'invoke-restmethod', 'start-bitstransfer'})


def _gp_target_hit(word: str, in_git: str = '', dotgit: bool = False) -> bool:
    """True when `word` names a git config / hook path. `dotgit` also counts a
    path ending in `.git` (a destination that would BE a git directory or
    gitdir file); `in_git` ('git' or 'hooks') is a `cd` into such a directory,
    where a relative `config` / `hooks/x` / any name does."""
    norm = (word or '').replace('\\', '/')
    if _GP_PATH_RE.search(norm) or (dotgit and _GP_DOTGIT_RE.search(norm)):
        return True
    if in_git and word and not word.startswith(('-', '/', '~', '$', '%')) \
            and not _WIN_DRIVE_RE.match(word):
        rel = norm[2:] if norm.startswith('./') else norm
        if in_git == 'hooks' or re.match(r'(?:config(?:\.worktree)?$|hooks(?:/|$))', rel, re.I):
            return True
    return False


def _gp_code_plants(code: str) -> bool:
    return bool(_GP_CODE_RE.search((code or '').replace('\\', '/'))) \
        and bool(_MP_WRITE_API_RE.search(code))


def _gp_dir_state(text: str) -> str:
    """'hooks' / 'git' when `text` is a path that ends in .git/hooks or .git."""
    norm = (text or '').replace('\\', '/').rstrip('/')
    if re.search(r'(?:^|/)\.git/hooks$', norm, re.I):
        return 'hooks'
    return 'git' if re.search(r'(?:^|/)\.git$', norm, re.I) else ''


def _gp_value_runs(key: str, value: str) -> bool:
    """True when setting `key` to `value` leaves git something to execute."""
    v = (value or '').strip()
    if not v:
        return False
    low = v.lower()
    if key.startswith('alias.') or (key.startswith('submodule.') and key.endswith('.update')):
        return v.startswith('!')
    if key == 'core.fsmonitor':
        return low not in _GP_BOOLS
    if key == 'core.hookspath':
        return low not in ('/dev/null', 'nul')
    plain = not _GP_META_RE.search(v)
    first = low.split(None, 1)[0]
    if key in ('core.editor', 'sequence.editor'):
        return not (plain and first in _GP_NOOP_EDITORS)
    if key == 'core.pager' or key.startswith('pager.'):
        return not (plain and first in _GP_SAFE_PAGERS) \
            and not (key != 'core.pager' and low in _GP_BOOLS)
    if re.match(r'^credential\.(?:.+\.)?helper$', key):
        return not (plain and first in _GP_SAFE_HELPERS)
    return True


def _gp_exec_setting(key: str, value: str) -> Optional[str]:
    key = (key or '').strip().lower()
    if _GP_EXEC_KEY_RE.match(key) and _gp_value_runs(key, value):
        return f"git config `{key}` set to a value git executes"
    return None


def _gp_git_split(rest: list):
    """(config specs, subcommand, its args, -C dirs) for the words after `git`.
    A spec is (key, value, from_env) from `-c k=v` / `--config-env k=ENVVAR`."""
    specs, cdirs, k = [], [], 0
    while k < len(rest):
        w = rest[k]
        if w == '-c' and k + 1 < len(rest):
            key, _, val = rest[k + 1].partition('=')
            specs.append((key, val, False))
            k += 2
        elif w == '--config-env' and k + 1 < len(rest):
            key, _, val = rest[k + 1].partition('=')
            specs.append((key, '$' + val, True))
            k += 2
        elif w.startswith('--config-env='):
            key, _, val = w.split('=', 1)[1].partition('=')
            specs.append((key, '$' + val, True))
            k += 1
        elif w == '-C' and k + 1 < len(rest):
            cdirs.append(rest[k + 1])
            k += 2
        elif w in _GP_GIT_VALUE_OPTS:
            k += 2
        elif w.startswith('-'):
            k += 1
        else:
            return specs, w.lower(), rest[k + 1:], cdirs
    return specs, '', [], cdirs


def _gp_config_sets(args: list) -> list:
    """[(key, value)] a `git config ...` call writes; [] for a read, --list,
    --unset*, --remove-section, --rename-section or --edit."""
    if any(a in _GP_CFG_NOWRITE for a in args):
        return []
    pos, k, end = [], 0, False
    while k < len(args):
        a = args[k]
        if end or a == '-' or not a.startswith('-'):
            pos.append(a)
        elif a == '--':
            end = True
        elif a in _GP_CFG_VALUE_OPTS:
            k += 1
        k += 1
    if pos[:1] == ['set']:
        pos = pos[1:]
    elif pos[:1] and pos[0].lower() in _GP_CFG_READ_SUBS:
        return []
    return [(pos[0], pos[1])] if len(pos) >= 2 else []


def _gp_parse(args: list, long_val=frozenset(), short_val=frozenset()):
    """(flags, positionals, words after `--`, saw `--`) for a git subcommand's
    args. A flag is `--name` or `-c` (one per letter of a short cluster, which
    stops at a letter that takes a value: `-bfix` is -b with value `fix`)."""
    flags, pos, k = set(), [], 0
    while k < len(args):
        a = args[k]
        if a == '--':
            return flags, pos, args[k + 1:], True
        if a.startswith('--'):
            name, eq, _ = a.partition('=')
            flags.add(name)
            if name in long_val and not eq:
                k += 1
        elif a.startswith('-') and len(a) > 1:
            for i, ch in enumerate(a[1:]):
                flags.add('-' + ch)
                if ch in short_val:
                    if i == len(a) - 2:
                        k += 1
                    break
        else:
            pos.append(a)
        k += 1
    return flags, pos, [], False


def _gp_is_pathspec(word: str, cdirs: list, cwd: Optional[str]) -> bool:
    """A lone `git checkout WORD` argument that is a path, not a branch: `.`, a
    glob, a `:` pathspec, or something that exists in the working tree."""
    if word == '.' or word.startswith(':') or any(c in word for c in '*?['):
        return True
    try:
        base = cwd or os.getcwd()
        for d in cdirs:
            base = os.path.join(base, d)
        return os.path.lexists(os.path.join(base, word))
    except Exception:
        return False


def _gp_discard(sub: str, args: list, cdirs: list, cwd: Optional[str]) -> Optional[str]:
    if sub == 'checkout':
        flags, pos, tail, dd = _gp_parse(args, {'--orphan', '--conflict'}, set('bBU'))
        if flags & {'-f', '--force'}:
            return "`git checkout -f` discards working-tree changes"
        if tail or '--pathspec-from-file' in flags:
            return "`git checkout -- PATH` overwrites working-tree files from the index or a commit"
        if flags & {'-b', '-B', '--orphan'}:
            return None                       # new branch: a lone argument is a start point
        if len(pos) >= 2 or (len(pos) == 1 and _gp_is_pathspec(pos[0], cdirs, cwd)):
            return "`git checkout PATH` overwrites working-tree files from the index or a commit"
    elif sub == 'restore':
        flags, pos, tail, dd = _gp_parse(args, {'--source', '--pathspec-from-file',
                                                '--conflict'}, set('sU'))
        if (pos or tail or '--pathspec-from-file' in flags) \
                and not (flags & {'-S', '--staged'} and not flags & {'-W', '--worktree'}):
            return "`git restore` overwrites working-tree files (only --staged alone is safe)"
    elif sub == 'switch':
        flags, pos, tail, dd = _gp_parse(
            args, {'--conflict', '--orphan', '--create', '--force-create'}, set('cC'))
        if flags & {'-f', '--force', '--discard-changes'}:
            return "`git switch -f` / `--discard-changes` discards working-tree changes"
    elif sub == 'checkout-index':
        flags, pos, tail, dd = _gp_parse(args, {'--prefix', '--stage'})
        if flags & {'-f', '--force'}:
            return "`git checkout-index -f` overwrites working-tree files from the index"
    return None


def _gp_plant(words: list, prog: str, rest: list, in_git: str) -> Optional[str]:
    """Reason when this segment plants code through git (see the section comment)."""
    for k, w in enumerate(words):
        m = re.match(r'^(?:\d*|&)>>?(?![>&])(.*)$', w)
        if m:
            target = m.group(1) or (words[k + 1] if k + 1 < len(words) else '')
            if target and _gp_target_hit(target, in_git, True):
                return f"a shell redirect into {_GP_WHAT}"
    if prog == 'git':
        specs, sub, args, _ = _gp_git_split(rest)
        for key, val, _env in specs:
            why = _gp_exec_setting(key, val)
            if why:
                return why
        if sub == 'config':
            for key, val in _gp_config_sets(args):
                why = _gp_exec_setting(key, val)
                if why:
                    return why
        return None
    if prog in _MP_DEST_LAST:
        dest = _mp_dest(prog, rest)
        if dest and _gp_target_hit(dest, in_git, True):
            return f"`{prog}` into {_GP_WHAT}"
    elif prog in _MP_ANY_ARG and _mp_any_arg_applies(prog, rest):
        if prog == 'dd':
            if any(a.lower().startswith('of=') and _gp_target_hit(a[3:], in_git) for a in rest):
                return f"`dd of=` into {_GP_WHAT}"
        elif any(_gp_target_hit(a, in_git) for a in rest):
            return f"`{prog}` aimed at {_GP_WHAT}"
    elif prog in _GP_NET_PROGS:
        for k, w in enumerate(rest):
            m = re.match(r'^(--?[A-Za-z-]+)[:=](.+)$', w)
            if w.lower() in _MP_NET_OUT and k + 1 < len(rest):
                tgt = rest[k + 1]
            elif m and m.group(1).lower() in _MP_NET_OUT:
                tgt = m.group(2)
            elif re.match(r'^-o.', w) and not w.startswith('--'):
                tgt = w[2:]
            elif re.match(r'^-[A-Za-z]*[oO]$', w) and k + 1 < len(rest):
                tgt = rest[k + 1]
            else:
                continue
            if _gp_target_hit(tgt, in_git):
                return f"`{prog}` saving a download into {_GP_WHAT}"
    if _MP_INTERP_RE.match(prog) and any(_gp_code_plants(a) for a in rest):
        return f"`{prog}` code that writes {_GP_WHAT}"
    if any('::' in a and _gp_code_plants(a) for a in words):
        return f"a .NET file call that writes {_GP_WHAT}"
    return None


def _gp_scan(cmd: str, visit, depth: int = 0, in_git: str = '') -> Optional[str]:
    """Walk the shell segments of `cmd`; `visit(words, prog, rest, in_git)` gives
    a reason or None for each command, wrappers (sudo, env, ...) skipped and
    `bash -c` / `pwsh -Command` / `cmd /c` / `eval` bodies scanned in turn.
    The unwrapping follows _mp_segment, which this leaves untouched."""
    cmd = _unwrap_start_process(_join_line_continuations(cmd))
    for words in _install_split(cmd, parens=False):
        words = list(words)
        if words:
            words[0] = re.sub(r'^(?:\$?\(+|\{+)', '', words[0])
        k = 0
        while k < len(words):
            w = words[k]
            head = _install_prog(w)
            if (re.match(r'^\w+=', w) and not w.startswith('-')) \
                    or w in ('{', '}', '!', 'then', 'do', 'else', ''):
                k += 1
                continue
            if head in _WRAPPER_VALUE_OPTS:
                vals = _WRAPPER_VALUE_OPTS[head]
                k += 1
                while k < len(words):
                    nxt = words[k]
                    if head in ('start', 'call'):
                        if re.match(r'^/[A-Za-z]+$', nxt):
                            k += 1
                            continue
                        break
                    if nxt == '--':
                        k += 1
                        break
                    if nxt.startswith('-') and nxt != '-':
                        k += 2 if nxt in vals else 1
                        continue
                    if head == 'env' and re.match(r'^\w+=', nxt):
                        k += 1
                        continue
                    break
                continue
            break
        else:
            continue
        prog, rest = _install_prog(words[k]), words[k + 1:]
        if depth < 4:
            inner = None
            if prog in _SHELL_DASH_C:
                for j, a in enumerate(rest):
                    if re.match(r'^-[A-Za-z]*c[A-Za-z]*$', a) and j + 1 < len(rest):
                        inner = rest[j + 1]
                        break
            elif prog in ('pwsh', 'powershell'):
                for j, a in enumerate(rest):
                    if _PS_COMMAND_RE.match(a):
                        inner = ' '.join(rest[j + 1:])
                        break
                    if _MP_PS_ENCODED_RE.match(a) and j + 1 < len(rest):
                        try:
                            inner = base64.b64decode(rest[j + 1]).decode('utf-16-le')
                        except Exception:
                            inner = None
                        break
            elif prog == 'cmd':
                for j, a in enumerate(rest):
                    if a.lower() in ('/c', '/k'):
                        inner = ' '.join(rest[j + 1:])
                        break
            elif prog in ('eval', 'iex', 'invoke-expression'):
                inner = ' '.join(rest)
            if inner is not None:
                why = _gp_scan(inner, visit, depth + 1, in_git)
                if why:
                    return why
        if prog in _MP_CD:
            target = next((a for a in rest if not a.startswith('-')), '')
            in_git = _gp_dir_state(target) or (
                'hooks' if in_git == 'git' and target.rstrip('/\\') == 'hooks' else '')
            continue
        why = visit(words, prog, rest, in_git)
        if why:
            return why
    return None


def _git_plant_guard(command: str, cwd: Optional[str] = None) -> FenceDecision:
    """Block an armed agent planting code through git config or a .git
    directory (see the section comment). Never passable."""
    try:
        scan = _mask_inert_prose(command.strip(), mask_python_c=False)
    except Exception:
        scan = command.strip()
    start = _gp_dir_state(cwd or '')
    why = None
    try:
        for m in _MP_HEREDOC_RE.finditer(scan):
            if _INTERPRETER_RE.search(m.group('head')) and (
                    _gp_code_plants(m.group('body'))
                    or _gp_scan(m.group('body'), _gp_plant, 1, start)):
                why = f"an interpreter script that writes {_GP_WHAT}"
                break
        if not why:
            why = _gp_scan(scan, _gp_plant, 0, start)
    except Exception:
        return FenceDecision(False, '')      # never wedge the agent on a parse bug
    if not why:
        return FenceDecision(False, '')
    return FenceDecision(True, f"{why}; git runs a configured program or a hook "
                               f"later (next status, commit, diff, fetch), outside "
                               f"every tool call this fence sees, so git's config "
                               f"and hooks are human-owned (supply chain)",
                         overridable=False)


def _git_discard_guard(command: str, cwd: Optional[str] = None) -> FenceDecision:
    """Block an armed agent discarding working-tree changes through git
    checkout / restore / switch, the same effect `git reset --hard` is refused
    for. Overridable, like reset --hard."""
    def visit(words, prog, rest, in_git):
        if prog != 'git':
            return None
        _specs, sub, args, cdirs = _gp_git_split(rest)
        return _gp_discard(sub, args, cdirs, cwd)
    try:
        why = _gp_scan(command, visit)
    except Exception:
        return FenceDecision(False, '')      # never wedge the agent on a parse bug
    if not why:
        return FenceDecision(False, '')
    return FenceDecision(True, f"{why}; git reset --hard is refused for the same "
                               f"reason (uncommitted work is not recoverable)")


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


def _addons_root() -> Path:
    """Mirrors mc.addons.manifest.addons_root() WITHOUT importing it (stdlib-only,
    same reasoning as `_vault_home()`); tests/test_addons_fence.py pins the two."""
    override = os.environ.get('CLAYRUNE_ADDONS_DIR')
    return Path(override) if override else _vault_home() / 'addons'


def _path_resolves_into_addons(raw: str, cwd: Optional[Path] = None) -> bool:
    """True if `raw` resolves inside the add-ons root. Fails toward BLOCK on an
    unresolvable path, same bias as `_path_resolves_into_vault`."""
    if not raw:
        return False
    try:
        root = _addons_root().resolve()
        target = Path(raw)
        if not target.is_absolute():
            target = (Path(cwd) if cwd else Path.cwd()) / target
        target = target.resolve()
    except Exception:
        return True
    return target == root or _is_within(target, root)


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


# ── Own-project memory topic files (MC-1037, Ron 2026-10-02: "Yes he should") ─
# The `.claude` rule below exists to stop self-installed skills and global
# config edits. A project's memory TOPIC files are neither, and Clayrune
# already writes memory for unattended sessions server-side; refusing the
# agent's own notes only made it stop and ask. So a fenced session may Write /
# Edit / MultiEdit / apply_patch `*.md` files directly inside ITS OWN project's
# memory dir, and nothing else under `.claude`.
#
# "Its own project" is the project root the SERVER recorded for the session at
# launch, handed in as `project_root` (main() asks GET /api/session/trigger-type,
# keyed by the CLAUDE_CODE_SESSION_ID the CLI puts in the hook's environment, the
# same ground-truth channel that arms the fence). It is NEVER the hook's cwd
# or anything in tool_input: a session's cwd moves (`cd` into a nested project,
# an added directory, a made-up `<dir>/.clayrune/agents/x`), and ownership that
# followed it would hand the agent another project's memory (Fenn, 2026-10-03).
# No project_root (no session id, an unreachable server, a launch that does not
# supply one, such as the Codex hook, which carries no session id) means the
# exception is OFF and the `.claude` rule blocks as before. The dir is then
# derived the way the server does (mc.memory._native_memory_path, pinned
# against the real one by tests/test_fence_memory_dir.py), including its
# underscore-to-dash alternate, which maps from that same root.
#
# Allowed: <memory dir>/<name>.md, where the RESOLVED target (symlinks and
# junctions followed, `..` collapsed, 8.3 names expanded) sits directly in
# the RESOLVED memory dir. Everything the server maintains, a subdirectory,
# another project's memory, a name with a trailing dot or space, an NTFS
# stream, an 8.3-looking alias, or anything we cannot resolve stays blocked.
_MEMORY_SERVER_FILES = ('memory.md', 'memory_archive.md', 'session_log.md')
_SHORT_NAME_ALIAS_RE = re.compile(r'~\d')


def _claude_projects_root() -> Path:
    """~/.claude/projects — mirrors server.py's CLAUDE_HOME, resolving the home
    dir like _vault_home() does. Split out so tests can point it at a tmp dir."""
    home = (os.environ.get('USERPROFILE') or os.environ.get('HOME')
            or str(Path.home()))
    return Path(home) / '.claude' / 'projects'


def _own_memory_dir(project_root: Optional[str] = None) -> Optional[Path]:
    """The memory dir of the project rooted at `project_root` (the server's
    registered path), picked the way mc.memory._native_memory_path picks it
    (including its underscore→dash alternate, which is the real one for a path
    like `...\\_claude\\...`). None for no root or a relative one."""
    if not project_root or not isinstance(project_root, str):
        return None
    try:
        raw_root = Path(project_root)
        if not raw_root.is_absolute():
            return None
        root = raw_root.resolve()
    except Exception:
        return None
    encoded = str(root).replace(':', '-').replace('\\', '-').replace('/', '-')
    if not encoded:
        return None
    base = _claude_projects_root()
    mem = base / encoded / 'memory' / 'MEMORY.md'
    alt_encoded = encoded.replace('_', '-')
    if alt_encoded != encoded:
        alt = base / alt_encoded / 'memory' / 'MEMORY.md'
        if alt.exists() and mem.exists():
            if alt.stat().st_mtime > mem.stat().st_mtime:
                return alt.parent
        elif alt.exists():
            return alt.parent
    return mem.parent


def _is_own_memory_topic_file(raw: str, cwd: Optional[str] = None,
                              project_root: Optional[str] = None) -> bool:
    """True only for <the session's project memory dir>/<name>.md. `cwd` only
    anchors a RELATIVE `raw`; ownership comes from `project_root` alone. Fails
    closed: any doubt, any error, is False and the `.claude` rule blocks as
    before."""
    try:
        if not raw or '\x00' in raw:
            return False
        stripped = raw.strip()
        if stripped.startswith(('\\\\?\\', '\\\\.\\', '//?/', '//./')):
            return False
        mem = _own_memory_dir(project_root)
        if mem is None:
            return False
        target = Path(stripped)
        if not target.is_absolute():
            target = (Path(cwd) if cwd else Path.cwd()) / target
        mem_real = mem.resolve()
        # The memory dir itself must not be a link: a junction at
        # projects/<own>/memory pointing at another project's memory would
        # make "own dir" mean "theirs". Only the projects root may be a link.
        if os.path.normcase(str(mem_real)) != os.path.normcase(
                str(_claude_projects_root().resolve() / mem.parent.name / mem.name)):
            return False
        target_real = target.resolve()
        name = target_real.name
        low = name.lower()
        if not low.endswith('.md') or low == '.md' or low in _MEMORY_SERVER_FILES:
            return False
        if ':' in name or name != name.rstrip(' .') or _SHORT_NAME_ALIAS_RE.search(name):
            return False
        return os.path.normcase(str(target_real.parent)) == os.path.normcase(str(mem_real))
    except Exception:
        return False


def _anchor_path(raw: str, cwd: Optional[str] = None) -> str:
    """`raw` joined onto `cwd` (the hook payload's, else this process's) when it
    is a relative path; anything else comes back unchanged. The path rules in
    classify_action look for `/.claude/`, `/steward/` and so on, so a relative
    `.claude/settings.json` has to be anchored before they can see the slash.
    Joined, not normalised: every old match on `raw` is still a match."""
    if not raw or raw.startswith(('/', '\\', '~', '$', '%')) or _WIN_DRIVE_RE.match(raw):
        return raw
    try:
        base = cwd or os.getcwd()
    except Exception:
        return raw
    return base.rstrip('/\\') + '/' + raw


def classify_action(tool_name: str, tool_input: dict,
                    cwd: Optional[str] = None,
                    project_root: Optional[str] = None) -> FenceDecision:
    """Classify any tool call. Bash is where terminal danger lives; other tools
    default to allow (edits/writes are working-tree-reversible). Extend here if a
    non-Bash irreversible surface appears (e.g. an MCP tool that sends email).

    `cwd` is the hook payload's cwd (None = this process's); it anchors relative
    paths and nothing else. `project_root` is the session's project as the SERVER
    recorded it (see "Own-project memory topic files"); None switches the
    memory-dir exception off."""
    name = (tool_name or '')
    ti = tool_input or {}
    if name in _PATCH_TOOL_NAMES:
        for sub_name, sub_input in as_write_calls(name, ti, cwd):
            d = classify_action(sub_name, sub_input, cwd, project_root)
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
        return classify_bash(ti.get('command', '') or '', cwd,
                             shell='powershell' if name == 'PowerShell' else None)
    # Writing to global config outside the project is out-of-scope for a
    # project steward — block edits/writes targeting ~/.claude or a home dotfile.
    if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = _anchor_path(
            str(ti.get('file_path', '') or ti.get('notebook_path', '') or ''), cwd)
        low = path.replace('\\', '/').lower()
        if ('/.claude/' in low or low.endswith('/.claude')) \
                and not _is_own_memory_topic_file(path, cwd, project_root):
            return FenceDecision(True, "editing global ~/.claude config (out of project scope)",
                                 overridable=False)
        # MCP server config, git hooks, shell profiles (2026-10-04): files, not
        # directories under .claude, so the rule above misses ~/.claude.json
        # and a project's .mcp.json. Same posture, see _config_plant_guard.
        if _cp_hit(path):
            return FenceDecision(True, "editing an MCP server config, git hook or "
                                       "shell profile (what Claude loads and what "
                                       "runs outside a tool call — human-owned "
                                       "supply chain)", overridable=False)
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
        # Add-on supply chain (MC-1022): the manifest and the installed
        # binaries are what `mc.addons.resolve()` hands to every consumer, and
        # `mc/addons/` holds the pinned catalogue hashes. Same posture as
        # `data/skills/`: an agent that could write either could substitute
        # the binary the app then runs.
        if (low.startswith('mc/addons/') or '/mc/addons/' in low
                or _path_resolves_into_addons(path, cwd)):
            return FenceDecision(True, "editing the add-on catalogue, manifest or "
                                       "installed binaries (executable supply "
                                       "chain — human-owned)", overridable=False)
        # Git config and hooks (2026-10-12): files git runs code from later, see
        # _git_plant_guard. The shell half refuses the same paths.
        if _gp_target_hit(path, dotgit=True):
            return FenceDecision(True, "editing a git config, git hook or .git file "
                                       "(git runs what it names outside any tool "
                                       "call this fence sees; human-owned)",
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
    # The marker must OPEN a line (the scheduler may prepend a time header, so
    # not only the first). A bare substring test armed any chat whose first
    # message merely QUOTED the marker: a handed-off conversation carrying a
    # standing position about this very file was fenced as a steward cycle,
    # and steward sessions are never offered Allow once (2026-10-03). Measured
    # then: all 8 recent transcripts containing the marker were quotes.
    return _STEWARD_MARKER_LINE.search(text) is not None


_STEWARD_MARKER_LINE = re.compile(r'(?m)^[ 	]*' + re.escape(STEWARD_MARKER))


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
        # The registered project path of a LIVE session; absent otherwise.
        'project_path': data.get('project_path'),
    }


def _launcher_project_root() -> Optional[str]:
    """The project root the server recorded for THIS session, or None. The
    only input to the memory-dir exception (see "Own-project memory topic
    files"): None means the exception is off. Never raises."""
    try:
        sid = _session_id_from_env()
        if not sid:
            return None
        root = (_lookup_trigger_type(sid) or {}).get('project_path')
        return root if isinstance(root, str) and root.strip() else None
    except Exception:
        return None


# Set by mc/launch_marker.py on every Claude child the server launches for an
# unattended trigger_type (backlog dc480ad3). Consulted ONLY when the trigger-type
# lookup returns nothing (server down / restarting, or session unknown): the
# lookup used to be the sole arming signal, so a dispatched child ran unfenced
# for exactly as long as the server was unreachable. A successful lookup still
# wins — a human "I am here" re-stamp to 'manual' must keep unfencing the chat.
_LAUNCH_MARKER_ENV = 'CLAYRUNE_LAUNCHED_UNATTENDED'


def _launched_unattended() -> bool:
    return (os.environ.get(_LAUNCH_MARKER_ENV) or '').strip() == '1'


def _should_arm_for_unattended_trigger() -> bool:
    """True only on a positive, confirmed match — see module comment above.
    Only ever ADDS enforcement on top of the existing steward-marker check,
    never removes it. When the server cannot answer, the per-launch marker
    (`_LAUNCH_MARKER_ENV`) stands in for the lookup."""
    sid = _session_id_from_env()
    if not sid:
        return _launched_unattended()
    info = _lookup_trigger_type(sid)
    if not info:
        return _launched_unattended()
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


def _blocked_leaves(tool_name: str, tool_input: dict,
                    cwd: Optional[str] = None,
                    project_root: Optional[str] = None) -> list:
    """Every blocked operation inside one call: patch tools recurse into their
    writes, a chained shell command counts as more than one. Over-counting only
    ever refuses a pass, never grants one."""
    if tool_name in _PATCH_TOOL_NAMES:
        out = []
        for sub_name, sub_input in as_write_calls(tool_name, tool_input or {}, cwd):
            if sub_name in _PATCH_TOOL_NAMES:
                d = classify_action(sub_name, sub_input, cwd, project_root)
                out.extend([d] if d.blocked else [])
            else:
                out.extend(_blocked_leaves(sub_name, sub_input, cwd, project_root))
        return out
    if tool_name in ('Bash', 'PowerShell'):
        cmd = (tool_input or {}).get('command', '') or ''
        whole = classify_bash(cmd, cwd)
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
    d = classify_action(tool_name, tool_input, cwd, project_root)
    return [d] if d.blocked else []


def _pass_can_cover(calls, cwd: Optional[str] = None,
                    project_root: Optional[str] = None) -> bool:
    try:
        leaves = []
        for call_name, call_input in calls:
            leaves.extend(_blocked_leaves(call_name, call_input, cwd, project_root))
    except Exception:
        return False
    return len(leaves) == 1 and leaves[0].overridable


# MC-1055: a Clayrune-launched session registers this hook twice (project
# settings AND the per-launch --settings file) whenever the two commands are
# not byte-identical, so ONE tool call reaches this script twice. Each run
# used to spend a pass on its own: the first spent it and allowed, the second
# found none and blocked, and Claude Code blocks on any exit 2. The call's
# identity goes to the consume route so a repeat of the SAME call is answered
# without spending a second pass; a different call, or the same id with
# different content, still finds no pass. main() sets this before judging.
_current_call_identity = None


def _tool_call_identity(payload, tool_name, tool_input):
    """(tool_use_id, sha256 of what the call does), or None when the payload
    carries no id (older CLI, Codex): the server then spends one pass per
    request exactly as before."""
    tool_use_id = payload.get('tool_use_id') or payload.get('toolUseId')
    if not isinstance(tool_use_id, str) or not tool_use_id.strip():
        return None
    try:
        blob = json.dumps([tool_name, tool_input], sort_keys=True,
                          separators=(',', ':'), default=str)
    except Exception:
        return None
    return tool_use_id.strip(), hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _consume_attend_once_pass() -> bool:
    sid = _session_id_from_env()
    if not sid:
        return False
    body = {'claude_session_id': sid}
    if _current_call_identity:
        body['tool_use_id'], body['action_digest'] = _current_call_identity
    try:
        req = urllib.request.Request(
            f'{_MC_API_BASE}/api/session/attend-once/consume',
            data=json.dumps(body).encode('utf-8'),
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
    global _current_call_identity
    _current_call_identity = _tool_call_identity(payload, tool_name, tool_input)
    # Only a call that names something under `.claude` can use the memory-dir
    # exception, so only that call pays for the server lookup.
    project_root = _launcher_project_root() if '.claude' in raw.lower() else None

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
            decision = classify_action(call_name, call_input,
                                       payload.get('cwd') or None, project_root)
        except Exception:
            return 0  # fail open
        if decision.blocked:
            break

    if not decision.blocked:
        return 0

    # Supply-chain and global-config edits are never passable, and a call that
    # bundles more than one blocked operation is refused whole: one click, one
    # operation. Those need a genuinely attended session.
    passable = pass_eligible and _pass_can_cover(
        calls, payload.get('cwd') or None, project_root)
    if passable and _consume_attend_once_pass():
        return 0

    if passable:
        how = ('The human can click "Allow once" in the chat header to permit '
               'the next blocked action (one action, expires in 10 minutes). '
               'Do NOT retry it until they say they have.')
    else:
        how = ('No one-time chat unlock can cover this action. '
               'It needs a fresh chat started by the human. Do NOT retry it '
               'in this guarded chat. Put the exact command in your final '
               'message so the human can run it from a fresh chat.')
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
