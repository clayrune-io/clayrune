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
from pathlib import Path
from typing import List, Optional

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
    """
    guard_script = guard_script or (Path(__file__).resolve().parent / 'process_guard.py')
    py = python_exe or sys.executable or 'python'
    py_token = f'"{py}"' if ' ' in py else py
    script_str = str(guard_script)
    script_token = f'"{script_str}"' if ' ' in script_str else script_str
    return f'{py_token} {script_token}'


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
                           python_exe: Optional[str] = None) -> List[str]:
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

    Deliberately does NOT call `guard_shell_command()` — builds its own
    unconditionally-quoted `"<py>" "<script>"` string inline instead. W4/
    MC-947 (2026-09-18) made `guard_shell_command()` quote the script path
    ONLY when it contains a space (fixing a real Qwen breakage — see that
    function's docstring), but the byte-exact QUOTED shape this function
    produces is the one actually live-verified against real `codex.exe`
    (0.154.0) with `--strict-config` — Codex was out of allowance to re-
    verify a changed shape at the time of that fix, so this stays pinned to
    the proven bytes rather than silently drifting with an unrelated
    vendor's fix. Re-verify live before ever pointing this at the shared
    helper.
    """
    py = python_exe or sys.executable or 'python'
    script = str(guard_script or (Path(__file__).resolve().parent / 'process_guard.py'))
    py_token = f'"{py}"' if ' ' in py else py
    command = f'{py_token} "{script}"'
    hooks_value = (
        'hooks.PreToolUse=[{matcher="shell",hooks=[{type="command",'
        f'command={_toml_basic_string(command)},name={_toml_basic_string(HOOK_NAME)}}}]}}]'
    )
    return ['--dangerously-bypass-hook-trust', '-c', hooks_value]
