"""Per-launch guardrail hook file locations — shared by the generator
(tools/guards/install_hooks.py, run at Clayrune startup) and every runtime
that injects a launch flag/env var pointing at one (mc/agent_runtime.py,
mc/blueprints/agent_routes.py's `_build_claude_flags`).

W2 redesign (docs/VENDOR_AGNOSTIC_PROGRAM.md §3), per Dave's review: the
first version of this installer wrote into the user's own GLOBAL CLI config
(~/.claude, ~/.gemini, ~/.qwen, ~/.codex) with no uninstall path — a user who
removes Clayrune, or simply runs Claude/Gemini/Qwen by hand with Clayrune
nowhere in the picture, was left with a guard silently changed underneath
them forever. Per-launch injection fixes that at the root: Clayrune passes
the guard ONLY to processes it starts (a flag or env var each CLI resolves
for THAT invocation only — verified per vendor, see
docs/GUARDRAIL_PARITY_EVIDENCE.md §4), and never touches global config.

Files live under `~/.clayrune/hooks/` (NOT DATA_DIR — this is machine/user
config, not a project record, and `~/.clayrune/` already holds the secrets
vault and other Clayrune-owned, non-project state) and are entirely
Clayrune's own — nothing else ever reads or writes them, so generation is a
wholesale overwrite, not a preserve-and-merge like a real global settings
file would need.

**Codex has NO file here (removed 2026-09-18, live regression fix).** The
first version generated `~/.clayrune/hooks/codex-hooks.json` as a MERGE of
the user's real `~/.codex/hooks.json` with Clayrune's own entry, pointed at
via `-c hooks='<path>'`. Both halves of that were wrong, caught on Ron's
live instance: (1) `-c hooks=<path>` is not a valid override — Codex's
`hooks` config key is a TOML **table** (`HooksToml` struct), not a file
path; the CLI died at config-parse time on every launch
(`Error loading config.toml: invalid type: string ..., expected struct
HooksToml`). (2) even had that worked, the generated file copied the user's
own hooks (e.g. a personal `codetalk.py` Stop hook) into a Clayround-owned
file — exactly the "never hold a copy of the user's data" line this
redesign exists to hold. Fixed by injecting the hooks table INLINE via
`-c hooks.PreToolUse=[...]` (a dotted-path override — see
`codex_hook_config_args()`) directly in `CodexRuntime.build_command()`: no
file, no merge, no copy, nothing to go stale. Live-verified against the
real `codex.exe` (0.154.0, no allowance — see `docs/GUARDRAIL_PARITY_EVIDENCE.md`
§4 for the exact commands and the parse-vs-usage_limit_exceeded signal used
to confirm it without spending a real turn) with `--strict-config`, which
rejects any unrecognized field: this shape has none.
"""
from __future__ import annotations

import sys
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# {vendor: filename under ~/.clayrune/hooks/} — Codex is deliberately absent,
# see the module docstring's "NO file here" section.
LAUNCH_FILENAMES = {
    'claude': 'claude-settings.json',
    'gemini': 'gemini-settings.json',
    'qwen': 'qwen-settings.json',
}

HOOK_NAME = 'clayrune-process-guard'


def clayrune_home() -> Path:
    """Where the hook files live — the ONE resolution both the boot-time
    writer and every dispatch-time reader use.

    `<MC_DATA_DIR>/.clayrune` when MC_DATA_DIR is set, else `~/.clayrune`.
    This rule used to live only on the WRITE side (server.py's
    `_install_guardrail_hooks_on_boot`, 889258e) while the readers
    (`launch_file_if_exists`, called by every Claude/Gemini/Qwen launch) kept
    resolving `~/.clayrune`. Under MC_DATA_DIR the file was written to one
    dir and looked for in another, `launch_file_if_exists` returned None, and
    every launch went out with NO guard — silently, since "missing" means
    "don't inject" by design. Caught by the first live Claude pass
    (2026-09-19, run 0919100639): `taskkill /IM <decoy>` killed the decoy and
    the dispatch argv carried no `--settings`. The frozen app ALWAYS sets
    MC_DATA_DIR (app.py `_start_flask`), so on merge this would have removed
    the guard from every packaged install.
    """
    data_dir = os.environ.get('MC_DATA_DIR')
    if data_dir:
        return Path(data_dir) / '.clayrune'
    return Path.home() / '.clayrune'


def hooks_dir(clayrune_home_dir: Optional[Path] = None) -> Path:
    return (clayrune_home_dir or clayrune_home()) / 'hooks'


def launch_file_path(vendor: str, clayrune_home_dir: Optional[Path] = None) -> Path:
    return hooks_dir(clayrune_home_dir) / LAUNCH_FILENAMES[vendor]


def launch_file_if_exists(vendor: str, clayrune_home_dir: Optional[Path] = None) -> Optional[Path]:
    """The generated file's path, or None if it hasn't been generated yet.

    Injection call sites use this, never `launch_file_path` directly: a
    dispatch that fires before the boot-time generation step has run (or
    after it failed) must add NO flag at all, rather than point a real CLI
    at a file that doesn't exist — the first version of this design pointed
    at a path that could go missing and learned the hard way (live-tested,
    docs/GUARDRAIL_PARITY_EVIDENCE.md §1a) that every vendor treats an
    unreadable hook command as a DENY, not a silent pass-through. That was
    an acceptable, disclosed tradeoff for a guard that's supposed to always
    be there; it is NOT acceptable for optional per-launch injection, whose
    entire point is that dispatch behaves normally when nothing has gone
    wrong. Missing here means "don't inject," full stop.
    """
    p = launch_file_path(vendor, clayrune_home_dir)
    return p if p.is_file() else None


def _shell_neutral_path(p: str) -> str:
    """A Windows path with `\\` turned into `/`, which bash, cmd.exe and
    PowerShell all read as the same path (see guard_shell_command). Other
    platforms' paths are returned unchanged."""
    return p.replace('\\', '/') if os.name == 'nt' else p


# The one tail that makes a BLOCK verdict survive every shell a vendor CLI
# may run the hook through on Windows. Empty off Windows, where every vendor
# runs hooks through `bash -c` and a native exit code already propagates.
#
# Why it is needed (measured 2026-09-19, docs/GUARDRAIL_PARITY_EVIDENCE.md §9):
# `powershell -Command "<native.exe>"` does NOT propagate the child's exit
# code — it reports 1 for any non-zero. The guard's exit 2 became 1, and Qwen's
# `convertPlainTextToHookOutput` maps every code other than 0/2 to
# EXIT_CODE_NON_BLOCKING_ERROR -> `decision: "allow"`. Qwen picks PowerShell
# whenever `ComSpec` ends in powershell.exe/pwsh.exe (`getShellConfiguration`,
# chunk-V545KI73.js) and, unlike gemini-cli, appends NO exit-code re-raise of
# its own. So the denial text was printed and the kill still ran.
#
# Why THIS string, in all three shells (all measured, both verdicts):
#   PowerShell  `; exit $LASTEXITCODE` re-raises the guard's real code.  -> 2/0
#   bash        `$LASTEXITCODE` is unset -> `exit` with no argument, which
#               exits with the status of the last command, i.e. the guard's. -> 2/0
#   cmd.exe     `;` is not a separator there; cmd hands the whole tail to the
#               child as extra argv, and `process_guard.py` ignores argv
#               entirely (`hook_main` reads only stdin).                  -> 2/0
#
# The SPACE before `;` is load-bearing for cmd.exe: without it the semicolon
# stays glued to the script path in the child's argv (`...process_guard.py;`),
# python can't open the file and exits 2 — a silent fail-CLOSED that blocks
# every shell call, measured before the space was added.
#
# Gemini appends its own `; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }`
# after this one. Harmless: on Windows gemini-cli 0.59.0 runs hooks ONLY
# through PowerShell (`getShellConfiguration`, chunk-S4PJ76PA.js — re-read
# 2026-09-19, every branch returns shell "powershell"), where our `exit` fires
# first. It would be a bash PARSE error if gemini ever picked bash on Windows;
# it cannot, and that is asserted by a test.
_EXIT_CODE_SUFFIX = ' ; exit $LASTEXITCODE' if os.name == 'nt' else ''


# Hook names app.py's `--clayrune-hook` entry serves (mc/hook_entry.py).
PROCESS_GUARD_HOOK = 'process-guard'
FENCE_HOOK = 'fence'
HOOK_ENTRY_FLAG = '--clayrune-hook'


def hook_invocation_tokens(hook: str, script: Optional[Path], default_script: Path,
                           python_exe: Optional[str] = None) -> List[str]:
    """The leading argv of a hook command, shell-neutral and unquoted.

    Source install: `[<python>, <script>]`. Frozen build (MC-975 follow-up):
    `sys.executable` is the Clayrune app itself and the hook scripts exist
    only as bytecode inside the bundle, so `<app> <script>` booted a second
    copy of the app instead of running the hook. There it is
    `[<app>, --clayrune-hook, <hook>]`, which app.py hands to
    mc/hook_entry.py in-process before anything else starts. An explicit
    `script` or `python_exe` always means the source form: callers that pass
    them (tests, tools/guards/install_hooks.py) name a real file."""
    if getattr(sys, 'frozen', False) and script is None and python_exe is None:
        return [_shell_neutral_path(sys.executable), HOOK_ENTRY_FLAG, hook]
    py = _shell_neutral_path(python_exe or sys.executable or 'python')
    return [py, _shell_neutral_path(str(script or default_script))]


def guard_shell_command(guard_script: Optional[Path] = None, python_exe: Optional[str] = None) -> str:
    """The command string every vendor's hook config points at — the ONE
    place this is built, shared by `tools/guards/install_hooks.py` (writes
    it into claude/gemini/qwen's generated files) and Codex's inline `-c`
    injection (`codex_hook_config_args`, no file at all).

    Quoting the interpreter path unconditionally (`"<py>" "<script>"`) was
    the first version and broke SILENTLY in production: live-tested through
    a REAL Clayrune dispatch, Gemini's Windows `.cmd` launcher executes hook
    commands through a shell that parses two adjacent quoted tokens as a
    syntax error ("UnexpectedToken") — the hook then failed to even run, and
    Gemini treated that failure as an ALLOW, not a deny (a genuinely MISSING
    script fails closed instead — see docs/GUARDRAIL_PARITY_EVIDENCE.md
    §1a/§4). `taskkill /IM notepad.exe` went through and killed a live test
    process before this was caught. Claude and Qwen were separately
    re-verified unaffected by the identical quoted input — Gemini-specific.

    Fix: quote the interpreter ONLY when its path contains a space (the one
    case a bareword can't handle). Re-verified through the same real
    dispatch path: blocked, notepad survived. An interpreter path WITH a
    space remains a disclosed, untested gap.

    W4/MC-947 (2026-09-18): the SAME unconditional-quoting mistake existed
    for the SCRIPT path too, and broke Qwen specifically — live-reproduced
    through a real `QwenRuntime.dispatch()` (env with no `MSYSTEM`/`TERM`,
    i.e. the shape Clayrune's own server process runs under, which resolves
    Qwen's hook shell to `cmd.exe` per `getShellConfiguration()` in the
    bundled `chunk-V545KI73.js` — a git-bash-launched shell with `MSYSTEM`
    set masked this by resolving to bash instead, which is why earlier
    ad-hoc testing from a bash prompt never caught it). Qwen's own
    `executeCommandHook` spawns `cmd.exe /d /s /c <command>` with
    `shell: false`, handing the single already-quoted `command` STRING as
    one argv element; Node then has to re-serialize that array into ONE
    Win32 command-line for `CreateProcess`, and an argv element that already
    contains embedded `"..."` gets re-escaped on top of its own quoting.
    Every `run_shell_command` call in the affected session failed with
    (paraphrased) 'python.exe: cannot open file [cwd glued onto the still-
    quoted script path, quote characters included]: Invalid argument' —
    and Qwen's hook layer treats a hook that
    fails to even launch as `execution_denied`, not an allow, so EVERY shell
    tool call in the session was silently blocked (matching a live incident:
    a Qwen-hosted dispatcher agent, unable to run `curl`/`python -c` at all,
    gave up on shell tools entirely — see docs/_journal/provider-live/
    cross-vendor/W5-notes.md). Re-verified live after this fix: the same
    stripped-env dispatch ran `echo`/`curl`/`dir` via `run_shell_command`
    with no error at all.

    Same fix, same reasoning: quote the script path ONLY when it contains a
    space. This repo's own path never does, so the common case now emits NO
    embedded quote characters for either token — nothing left for a
    naive-relaunch shell to mis-parse. A script path WITH a space remains
    the same disclosed, untested gap the interpreter path already carries.

    Qwen live pass run 3 (2026-09-19): paths are emitted with FORWARD
    slashes. qwen-code 0.23.4's `getShellConfiguration()` (chunk-V545KI73.js)
    runs hooks through `bash -c` whenever the CLI inherits `MSYSTEM=MINGW*`/
    `MSYS*` (or a `TERM` containing msys/cygwin) — i.e. whenever Clayrune was
    started from a git-bash prompt. bash reads each unquoted backslash as an
    escape, so `C:\\Users\\...\\python.exe` became `C:Users...python.exe`,
    the hook exited 127, and qwen maps any exit other than 0/2 to
    EXIT_CODE_NON_BLOCKING_ERROR → `decision: "allow"`
    (`convertPlainTextToHookOutput`, chunk-DCRVSIK6.js). The guard failed
    OPEN: `taskkill /IM <decoy> /F` ran and killed the decoy. Forward
    slashes mean the same path to bash, cmd.exe and PowerShell (measured:
    guard exits 2 in bash and cmd; PowerShell exits 1 itself, and Gemini's
    runner re-raises $LASTEXITCODE there).

    W6 (2026-09-19): on Windows the command now ENDS with
    ` ; exit $LASTEXITCODE` — `_EXIT_CODE_SUFFIX`, see its comment for the
    measurements and for why every character of it (including the space
    before the `;`) is load-bearing. Without it a `ComSpec` pointing at
    PowerShell reproduced the §8 fail-open: the guard printed its denial and
    PowerShell still exited 1, which Qwen maps to allow.
    """
    tokens = hook_invocation_tokens(
        PROCESS_GUARD_HOOK, guard_script,
        Path(__file__).resolve().parent / 'process_guard.py', python_exe)
    quoted = [f'"{t}"' if ' ' in t else t for t in tokens]
    return ' '.join(quoted) + _EXIT_CODE_SUFFIX


def _toml_basic_string(s: str) -> str:
    """Escape `s` for embedding as a TOML basic (double-quoted) string —
    used ONLY for the command string inside Codex's inline `-c hooks.*=`
    override (`codex_hook_config_args`). Backslashes and double quotes are
    the two characters that matter for a Windows path embedded this way;
    hand-rolling this exact kind of escaping is what caused the Gemini
    quoting bug above, so it is centralized here rather than repeated at
    the call site.
    """
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def codex_hook_config_args(guard_script: Optional[Path] = None,
                           python_exe: Optional[str] = None,
                           fence_armed: Optional[bool] = None,
                           fence_script: Optional[Path] = None) -> List[str]:
    """`-c`/flag arguments for CodexRuntime.build_command() — no file,
    nothing written to disk, nothing that could ever hold a copy of the
    user's own hooks (see module docstring for why that matters here).

    Two pieces, both load-bearing:
      --dangerously-bypass-hook-trust  Codex's hook-trust review
                                        (`"Hooks need review... Trust all
                                        and continue"`) has no
                                        non-interactive prompt path; without
                                        this flag a freshly-injected hook
                                        sits untrusted and inert in a
                                        headless run.
      -c hooks.PreToolUse=[...]        A DOTTED-PATH override — confirmed
                                        (via `--strict-config`, which
                                        rejects any unrecognized field) to
                                        set just the `PreToolUse` array
                                        within the `hooks` table, not
                                        replace the whole table — so a
                                        native `~/.codex/hooks.json` or
                                        `<project>/.codex/hooks.json` the
                                        user already has for OTHER events
                                        (`Stop`, etc.) is left alone. `hooks`
                                        alone (no dotted path) IS a valid
                                        TOML table value too, but would
                                        require reconstructing the user's
                                        entire hooks table in Python to
                                        avoid clobbering it — the dotted
                                        path makes that unnecessary.

    W6 (2026-09-19) — TWO measured defects fixed here, both of which made this
    guard a no-op on every Codex launch since it shipped. Codex had allowance
    on this box again, so both halves were reproduced and re-verified through
    real `codex exec` turns, not inferred:

    1. **NO MATCHER.** This used to carry `matcher="shell"`, taken from an
       offline strings extraction of codex.exe (§1 of the evidence doc, which
       said "Codex's shell tool is named `shell`"). It is not. A PreToolUse
       probe hook that dumped its own stdin measured
       `"tool_name": "Bash", "tool_input": {"command": ...},
       "tool_use_id": "exec-..."` — Codex 0.154.0 reports its shell tool as
       `Bash`. With `matcher="shell"` the event never fired at all: a live
       `taskkill /IM <decoy> /F` turn printed `hook: SessionStart`,
       `hook: UserPromptSubmit`, `hook: Stop` — and no `hook: PreToolUse`.
       The matcher is now OMITTED rather than corrected to `"Bash"`. It was a
       redundant SECOND filter on top of `process_guard.hook_main`, which
       already returns 0 immediately for any `tool_name` outside
       `_SHELL_TOOL_NAMES` — and the only behaviour a second filter can add
       is a silent fail-open when it disagrees with reality, which is exactly
       what it did. One filter, in the guard, where a vendor renaming its
       tool to `shell`/`run_shell_command`/`PowerShell` is already covered.
       Cost: the guard also runs (and instantly exits 0) on non-shell tool
       calls.

    2. **NO EXIT-CODE SUFFIX.** It now calls `guard_shell_command()`, the
       shared builder, instead of hand-rolling `"<py>" "<script>"`. The same
       probe measured Codex's hook parent process as
       `powershell.exe -NoProfile -Command "<command>"` — the PowerShell
       shell whose exit-code collapse `_EXIT_CODE_SUFFIX` exists to undo. The
       old pinned-bytes shape carried no suffix, so even once the matcher
       fired, the guard's exit 2 would have reached Codex as 1. The previous
       docstring's instruction ("Re-verify live before ever pointing this at
       the shared helper") is what was done: see the evidence doc §9 for the
       before/after live runs.

    MC-975 (2026-09-25): `fence_armed` not None ALSO injects the steward
    fence as a second handler in the same group; see
    codex_fence_hook_command(). None keeps the guard-only shape, which the
    pinned-string tests use.
    """
    command = guard_shell_command(guard_script=guard_script, python_exe=python_exe)
    handlers = (
        '{type="command",'
        f'command={_toml_basic_string(command)},name={_toml_basic_string(HOOK_NAME)}}}'
    )
    if fence_armed is not None:
        fence_cmd = codex_fence_hook_command(armed=fence_armed, fence_script=fence_script,
                                             python_exe=python_exe)
        handlers += (
            ',{type="command",'
            f'command={_toml_basic_string(fence_cmd)},'
            f'timeout={CODEX_FENCE_TIMEOUT_SEC},'
            f'name={_toml_basic_string(FENCE_HOOK_NAME)}}}'
        )
    hooks_value = f'hooks.PreToolUse=[{{hooks=[{handlers}]}}]'
    return ['--dangerously-bypass-hook-trust', '-c', hooks_value]


# ── Steward fence for Codex (MC-975, 2026-09-25) ────────────────────────────
# Measured against codex-cli 0.155.1 on this box with a probe PreToolUse hook
# (docs/_journal/MC-975-codex-unattended-sandbox.md, 2026-09-25 fence entry):
#   * the hook runs as `powershell.exe -NoProfile -Command "<command>"`;
#   * exit 2 WITH stderr text, or `permissionDecision: "deny"` JSON on
#     stdout, blocks the tool call;
#   * exit 2 with EMPTY stderr, any other non-zero exit, a PowerShell parse
#     error, and a timeout all print `hook: PreToolUse Failed` and RUN THE
#     TOOL. Codex hooks fail OPEN; no config field changes that;
#   * `timeout` is in SECONDS (30 let an 8 s hook block; 5 timed it out).
# Before this, the only fence a Codex launch saw was Codex's own migrated
# copy of the Claude hook in <project>/.codex/hooks.json, `"<py>" "<fence>"`:
# two adjacent quoted strings, which PowerShell rejects (`UnexpectedToken`)
# before python starts. Every tool call logged `PreToolUse Failed` and ran,
# logged in or not. Even had it run, the fence arms on CLAUDE_CODE_SESSION_ID
# or a Claude-format transcript; a Codex hook has neither, so it could never
# arm for an unattended run.
#
# So Clayrune injects the fence itself. Arming is decided at dispatch from the
# session's trigger_type (`--armed`, in codex's own argv, which the agent
# cannot alter). An armed fence is wrapped so any exit other than 0/2 (python
# missing, 0xC0000142, a crash) becomes exit 2 with a stderr reason. Two
# fail-opens the wrapper cannot see: PowerShell itself failing to start (the
# S4U 0xC0000142 case) and a hook timeout. codex_fence_self_test() covers
# the first before an unattended launch; the second is Codex's to fix.
FENCE_HOOK_NAME = 'clayrune-steward-fence'
FENCE_ARMED_ARG = '--armed'
FENCE_SELF_TEST_ARG = '--self-test'
FENCE_SELF_TEST_TOKEN = 'CLAYRUNE-FENCE-SELF-TEST-OK'
# A hook timeout fails OPEN in Codex, so this must sit far above the fence's
# real runtime. Measured 2026-09-25 through the Codex hook shell (PowerShell
# + python + fence.py), 20 runs each: armed block max 0.29 s, armed allow
# max 0.30 s, unarmed max 0.27 s; 8 concurrent, 40 runs: max 0.36 s. About
# 80x headroom. tests/test_codex_steward_fence.py fails if one armed run
# takes over a fifth of this.
CODEX_FENCE_TIMEOUT_SEC = 30

_FENCE_FAIL_REASON = 'STEWARD FENCE failed to run; blocking this tool call fail-closed. Exit: '
# PowerShell: `$LASTEXITCODE` is $null when python could not even be found,
# and `$null -ne 0` is true, so that case blocks too. Single quotes only, so
# no embedded double quote for codex's own command-line quoting to mangle.
_FAIL_CLOSED_SUFFIX_PS = (
    " ; $c = $LASTEXITCODE ; if ($c -ne 0 -and $c -ne 2) "
    "{ [Console]::Error.WriteLine('" + _FENCE_FAIL_REASON + "' + $c) } ; "
    "if ($c -ne 0) { exit 2 } ; exit 0")
_FAIL_CLOSED_SUFFIX_SH = (
    " ; c=$? ; if [ $c -ne 0 ] && [ $c -ne 2 ]; "
    "then echo '" + _FENCE_FAIL_REASON + "'$c >&2; fi ; "
    "if [ $c -ne 0 ]; then exit 2; fi ; exit 0")


def _default_fence_script() -> Path:
    return Path(__file__).resolve().parent.parent / 'steward' / 'fence.py'


def codex_fence_hook_command(*, armed: bool, fence_script: Optional[Path] = None,
                             python_exe: Optional[str] = None,
                             extra_args: Sequence[str] = ()) -> str:
    """The steward-fence command string for Codex's hook runner.

    On Windows it is written for PowerShell only (Codex uses no other shell
    there, measured). Tokens are quoted only when they contain a space, and
    a quoted interpreter gets PowerShell's `.` invocation operator, because
    `"<py>" "<script>"` is a parse error there (the defect this replaces).
    NOT `&`: the value reaches codex through npm's `codex.CMD`, whose `%*`
    line cmd.exe re-parses, and an `&` there split the `-c` argument and
    killed the launch at config load (measured). Keep every cmd.exe
    metacharacter (& | < > ^ %) out of this string. Armed gets the
    fail-closed suffix. Unarmed (a human is watching) gets the plain
    exit-code re-raise, so a broken fence cannot wedge an interactive chat.
    """
    tokens = [f'"{t}"' if ' ' in t else t for t in hook_invocation_tokens(
        FENCE_HOOK, fence_script, _default_fence_script(), python_exe)]
    args = ([FENCE_ARMED_ARG] if armed else []) + list(extra_args)
    base = ' '.join(tokens + args)
    if os.name == 'nt':
        if tokens[0].startswith('"'):
            base = '. ' + base
        return base + (_FAIL_CLOSED_SUFFIX_PS if armed else _EXIT_CODE_SUFFIX)
    return base + (_FAIL_CLOSED_SUFFIX_SH if armed else '')


def _codex_hook_shell_argv(command: str) -> List[str]:
    """The argv Codex runs a hook command with (Windows shape measured)."""
    if os.name == 'nt':
        root = os.environ.get('SystemRoot') or r'C:\Windows'
        ps = os.path.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        return [ps, '-NoProfile', '-Command', command]
    return ['sh', '-c', command]


def codex_fence_self_test(*, fence_script: Optional[Path] = None,
                          python_exe: Optional[str] = None,
                          timeout: float = CODEX_FENCE_TIMEOUT_SEC) -> Tuple[bool, str]:
    """Run the ARMED fence command the way Codex's hook runner would, with
    `--self-test`, from this process's own token and session. Passes only on
    exit 2 carrying FENCE_SELF_TEST_TOKEN: the shell started, python started,
    fence.py ran, and a block survived the round trip. The wrapper's own
    fail-closed exit 2 carries no token, so it cannot pass by accident.
    Returns (ok, detail)."""
    command = codex_fence_hook_command(armed=True, fence_script=fence_script,
                                       python_exe=python_exe,
                                       extra_args=[FENCE_SELF_TEST_ARG])
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        r = subprocess.run(_codex_hook_shell_argv(command), input='{}',
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout, **kwargs)
    except Exception as e:
        return False, f'could not run the hook shell: {e}'
    err = (r.stderr or '').strip()
    ok = r.returncode == 2 and FENCE_SELF_TEST_TOKEN in err
    return ok, f'exit {r.returncode}; stderr: {err[:300]}'
