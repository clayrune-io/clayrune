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
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional


class FenceDecision(NamedTuple):
    blocked: bool
    reason: str


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
    (re.compile(r'\bgh\s+api\b.*-X\s*(POST|PUT|PATCH|DELETE)', re.I),
     "GitHub API mutation"),
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


def _mask_inert_prose(cmd: str) -> str:
    """Replace provably-inert data spans with a placeholder. Best-effort and
    conservative: any span we cannot PROVE inert is left untouched (fails
    toward blocking, never toward allowing)."""
    def _msg_repl(m):
        if m.group('q') == '"' and ('$(' in m.group('body') or '`' in m.group('body')):
            return m.group(0)          # substitution could execute — leave it
        return f"{m.group(1)}{m.group('q')}{_MASK_TOKEN}{m.group('q')}"

    def _block_repl(m):
        # If the consuming line mentions an interpreter, the "data" may be
        # executed (bash <<'EOF') — leave the whole span for the patterns.
        if _INTERPRETER_RE.search(m.group('head')):
            return m.group(0)
        return f"{m.group('head')}{_MASK_TOKEN}\n{m.group('term')}"

    out = _MSG_ARG_RE.sub(_msg_repl, cmd)
    out = _HEREDOC_RE.sub(_block_repl, out)
    out = _PS_HERESTRING_RE.sub(_block_repl, out)
    return out


_SHELL_SPLIT_RE = re.compile(r'&&|\|\||[|;\n]')


_NET_TOOL_RE = re.compile(
    r'\b(curl|wget|http|invoke-webrequest|invoke-restmethod|iwr)\b', re.I)


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
        mutating = (
            bool(re.search(r'-X\s*(POST|PUT|PATCH|DELETE)', seg, re.I))
            or bool(re.search(r'(^|\s)(--data\b|--data-raw\b|-d\b|--upload-file\b|-T\b|-F\b|--form\b)', seg))
            or bool(re.search(r'-Method\s+(POST|PUT|PATCH|DELETE)', seg, re.I))
        )
        if not mutating:
            continue
        if any(h in seg.lower() for h in _LOCAL_HOSTS):
            continue  # steward's own API calls
        return FenceDecision(True, "external network send (mutating HTTP to a non-local host)")
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

_HEAD_EXPANSION_RE = re.compile(rf'^\s*(?:{_VAR_TOKEN})')

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
                                        "a runtime VALUE the fence cannot read")
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
        if _HEAD_EXPANSION_RE.match(stripped) or _VERB_THEN_EXPANSION_RE.search(seg):
            return FenceDecision(True, "variable/command substitution in "
                                        "command position (the word that "
                                        "actually runs is decided at "
                                        "runtime, not visible on this line)")
    return FenceDecision(False, '')


def classify_bash(command: str) -> FenceDecision:
    """Classify a Bash command string. Returns (blocked, reason).

    Classification runs on the inert-prose-MASKED command: commit-message /
    literal-heredoc content is data, not commands (see _mask_inert_prose).
    Command-position text always survives masking, so every shape the fence
    blocked before, it still blocks."""
    if not command or not command.strip():
        return FenceDecision(False, '')
    try:
        cmd = _mask_inert_prose(command.strip())
    except Exception:
        cmd = command.strip()   # masking is best-effort; unmasked = stricter

    for pat, reason in _BLOCK_PATTERNS:
        if pat.search(cmd):
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


def _bash_touches_local_auth_file(cmd: str) -> bool:
    for seg in _SHELL_SPLIT_RE.split(cmd):
        if not _LOCAL_AUTH_NAME_RE.search(seg):
            continue
        seg_norm = seg.replace('\\', '/').lower()
        if _VAULT_READ_VERB_RE.search(seg) or 'data/local_auth.json' in seg_norm:
            return True
    return False


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
        True, "reads the LAN dashboard passcode store directly — that "
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
    elif name == 'Bash':
        cmd = str(ti.get('command', '') or '')
        if _bash_touches_vault_file(cmd):
            return refusal
        if _bash_touches_local_auth_file(cmd):
            return local_auth_refusal
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
    if name == 'Bash':
        return classify_bash(ti.get('command', '') or '')
    # Writing to global config outside the project is out-of-scope for a
    # project steward — block edits/writes targeting ~/.claude or a home dotfile.
    if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = str(ti.get('file_path', '') or ti.get('notebook_path', '') or '')
        low = path.replace('\\', '/').lower()
        if '/.claude/' in low or low.endswith('/.claude'):
            return FenceDecision(True, "editing global ~/.claude config (out of project scope)")
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
                                       "chain — human-owned)")
        # Learning-loop supply chain (committee M3, 2026-07-16): proposal
        # frontmatter and the skill-stats signal store are the INPUTS that
        # decide what enters agent loadouts. An unattended agent editing its
        # own provenance/recurrence records is the origin-laundering write
        # path — same fence posture as `.claude/`.
        if ('/data/skills/' in low or low.startswith('data/skills/')
                or low.endswith('_skill_stats.json')
                or low.endswith('_skill_stats_archive.jsonl')):
            return FenceDecision(True, "editing learning-loop artifacts/telemetry "
                                       "(loadout supply chain — human-owned)")
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
        vault_access = check_vault_file_access(tool_name, tool_input)
    except Exception:
        vault_access = FenceDecision(False, '')
    if vault_access.blocked:
        print(f"STEWARD FENCE blocked this action: {vault_access.reason}. "
              f"Do NOT retry it against this path.", file=sys.stderr)
        return 2

    # Confirmed steward (marker=True) always enforces. Everything else
    # (confirmed non-steward OR genuinely unknown) falls through to the
    # generalized trigger_type signal — see the corrected gate above.
    if not armed_by_launcher and _session_is_steward(payload) is not True:
        if not _should_arm_for_unattended_trigger():
            return 0

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

    msg = (f"STEWARD FENCE blocked this action: {decision.reason}. "
           f"This is irreversible/mutating and you are running unattended. "
           f"Do NOT retry it. Instead post a `DECISION NEEDED:` note to your "
           f"charter with the exact command so the human can approve it.")
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
