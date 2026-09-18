#!/usr/bin/env python3
"""Install Clayrune's process guard into every vendor CLI's own hook system.

W2 (docs/VENDOR_AGNOSTIC_PROGRAM.md §3): guardrails are a Clayrune policy,
installed into each vendor CLI's own hook mechanism rather than reimplemented
per vendor. Full per-vendor evidence: docs/GUARDRAIL_PARITY_EVIDENCE.md.

  - Gemini CLI: `~/.gemini/settings.json`, `hooks.BeforeTool`, matcher
    `run_shell_command`. Live-verified: exit code 2 blocks, stderr is the
    denial reason.
  - Qwen Code: `~/.qwen/settings.json`, `hooks.PreToolUse` — same event/shape
    as Claude Code. Requires QwenRuntime to run WITHOUT `--bare` (see
    mc/agent_runtime.py QwenRuntime.build_command's docstring) — `--bare`
    unconditionally disables hooks with no CLI-flag workaround.
  - Claude Code: `~/.claude/settings.json`, `hooks.PreToolUse`. Ron's
    pre-existing entry here (`~/.claude/hooks/process-guard.py`) blocks
    image-name kills only for a hand-maintained process list, not universally
    — SHARED_RULES' "never kill by image name" has no such exception, so this
    installer adds Clayrune's OWN entry (matched/replaced by its `name` tag,
    see `_find_our_group_index`) alongside it. It does not touch, replace or
    remove the pre-existing untagged entry — that is Ron's call, not this
    installer's.
  - Codex CLI: `~/.codex/hooks.json`, `hooks.PreToolUse`, matcher `shell`.
    NOT independently confirmed by loading a real hooks.json (no Codex
    launches — see docs/GUARDRAIL_PARITY_EVIDENCE.md §1 for exactly what was
    and wasn't verified): reconstructed from `codex exec --help`
    (`--dangerously-bypass-hook-trust`) and printable-string extraction from
    the installed `codex.exe` (0.154.0), which independently confirms the
    event name, the exit-code-2-blocks convention, and a `tool_name`/
    `tool_input` payload shape identical to Claude's. Best-evidence, not
    field-tested. Separately, CodexRuntime's own dispatch command needs
    `--dangerously-bypass-hook-trust` (or `-c bypass_hook_trust=true`) added
    for a freshly-written hook to be TRUSTED (and therefore active) in a
    headless run at all — codex.exe's strings show an interactive
    "Hooks need review... Trust all and continue / Continue without
    trusting (hooks won't run)" gate with no non-interactive prompt path.
    That CodexRuntime change is NOT made here (Codex is out of allowance
    until 2026-09-24; changing live dispatch flags with no way to verify
    them is a separate, riskier change) — flagged for whoever runs the
    Sep-24 live proof.

Every vendor points at the SAME guard, `mc/process_guard.py`, invoked with
the SAME Python interpreter this installer itself is running under
(`sys.executable`, captured at install time — never bare `python`, which
depends on the external CLI's own PATH at hook-fire time, not this process's).
No second guard implementation; `mc/process_guard.py`'s `_SHELL_TOOL_NAMES`
covers every vendor's shell-tool naming, so only the hook-config *shape*
(event name, matcher, file location) differs per vendor, and that's what
`VENDOR_CONFIGS` below encodes.

Idempotent by NAME, not by exact command string: each written hook carries
`"name": "clayrune-process-guard"`. Re-running looks up that name within the
target event's array and REPLACES only that group in place (so an interpreter
or repo-root path change updates cleanly instead of accumulating duplicate
stale entries) — every other group in the array, including a pre-existing
hook this installer did not write, is left byte-for-byte untouched.

Dry-run (the default) never writes; `--apply` is required to write.

Failure mode when the written path goes missing or unreadable later (e.g. a
worktree the guard path pointed at gets deleted): live-tested 2026-09-18
against Claude, Gemini and Qwen — all three treat a hook command that cannot
even execute as a DENY (fails closed: the underlying tool call is blocked,
not silently allowed to proceed). This is a byproduct of the interpreter's
own missing-script exit code (2) coinciding with each vendor's own
hook-block convention, not a designed safety net — a broken path fails safe
by blocking every shell call, which is why `--repo-root` (below) exists: to
get the path right rather than lean on that coincidence.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_SCRIPT = REPO_ROOT / 'mc' / 'process_guard.py'
HOOK_NAME = 'clayrune-process-guard'

VENDOR_CONFIGS: Dict[str, Dict[str, str]] = {
    'claude': {
        'settings_rel': '.claude/settings.json',
        'event': 'PreToolUse',
        'matcher': 'Bash|PowerShell',
    },
    'gemini': {
        'settings_rel': '.gemini/settings.json',
        'event': 'BeforeTool',
        'matcher': 'run_shell_command',
    },
    'qwen': {
        'settings_rel': '.qwen/settings.json',
        'event': 'PreToolUse',
        'matcher': 'Bash|PowerShell|run_shell_command',
    },
    'codex': {
        'settings_rel': '.codex/hooks.json',
        'event': 'PreToolUse',
        'matcher': 'shell',
    },
}


def guard_command(guard_script: Path = GUARD_SCRIPT, python_exe: Optional[str] = None) -> str:
    py = python_exe or sys.executable or 'python'
    return f'"{py}" "{guard_script}"'


def _load_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise RuntimeError(
            f'{path} exists but is not valid JSON ({e}) — refusing to touch '
            'a file this installer cannot safely merge') from e
    if not isinstance(data, dict):
        raise RuntimeError(f'{path} is valid JSON but not an object — refusing to touch it')
    return data


def _find_group_index(event_list: Any, marker: str) -> Optional[int]:
    """Index of the group THIS installer previously wrote, or None.

    Identity is the `name` tag on an individual hook entry, never the command
    string or position — a path/interpreter change must update this group in
    place, not be mistaken for a different (or the user's own) hook.
    """
    if not isinstance(event_list, list):
        return None
    for i, entry in enumerate(event_list):
        if not isinstance(entry, dict):
            continue
        for h in entry.get('hooks') or []:
            if isinstance(h, dict) and h.get('name') == marker:
                return i
    return None


def _desired_group(cfg: Dict[str, str], command: str) -> Dict[str, Any]:
    return {
        'matcher': cfg['matcher'],
        'hooks': [{
            'type': 'command',
            'command': command,
            'name': HOOK_NAME,
        }],
    }


def plan_install(vendor: str, home: Path, guard_script: Path = GUARD_SCRIPT,
                  python_exe: Optional[str] = None
                  ) -> Tuple[Path, Dict[str, Any], Dict[str, Any], bool]:
    """Return (settings_path, before, after, changed) — never writes."""
    cfg = VENDOR_CONFIGS[vendor]
    path = home / Path(cfg['settings_rel'])
    before = _load_settings(path)
    after = copy.deepcopy(before)
    command = guard_command(guard_script, python_exe)
    hooks = after.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{path}: top-level 'hooks' is not an object — refusing to touch it")
    event_list = hooks.setdefault(cfg['event'], [])
    if not isinstance(event_list, list):
        raise RuntimeError(
            f"{path}: 'hooks.{cfg['event']}' is not an array — refusing to touch it")

    desired = _desired_group(cfg, command)
    idx = _find_group_index(event_list, HOOK_NAME)
    if idx is None:
        event_list.append(desired)
        changed = True
    else:
        changed = event_list[idx] != desired
        event_list[idx] = desired
    return path, before, after, changed


def diff_text(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """Zero-context diff — deliberate, not a cosmetic choice.

    A non-zero context window pulls in surrounding UNCHANGED lines from the
    user's real settings.json — measured 2026-09-18: on this box that
    included a live API key sitting a few lines above the insertion point.
    Showing a diff must not leak content this installer never reads or
    touches. Zero context still shows every line an install/replace writes,
    since json.dumps re-serializes the whole file — a replace shows both the
    old and new group as remove/add lines with no surrounding context either.
    """
    b = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    a = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    return ''.join(difflib.unified_diff(b, a, fromfile='before', tofile='after', lineterm='\n', n=0))


def install(vendor: str, home: Path, apply: bool, guard_script: Path = GUARD_SCRIPT,
            python_exe: Optional[str] = None) -> Dict[str, Any]:
    path, before, after, changed = plan_install(vendor, home, guard_script, python_exe)
    result: Dict[str, Any] = {'vendor': vendor, 'path': str(path), 'changed': changed, 'diff': ''}
    if not changed:
        return result
    result['diff'] = diff_text(before, after)
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(after, indent=2) + '\n', encoding='utf-8')
    return result


def install_for_boot(home: Optional[Path] = None, guard_script: Path = GUARD_SCRIPT,
                      python_exe: Optional[str] = None,
                      installed_vendors: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Server-startup entry point — see server.py's wiring.

    Applies for real (no dry-run) but ONLY for vendors in `installed_vendors`
    (the caller passes the set whose CLI health_check() found installed —
    writing a hook config for a vendor with no CLI does nothing harmful, but
    it is also not this installer's job to speculatively create config trees
    for tools that aren't there). Never raises on a single vendor's failure —
    one vendor's malformed settings.json (refused by `_load_settings`) must
    not block boot or the other vendors' installs; the failure is returned in
    the result list instead.
    """
    home = home or Path.home()
    vendors = installed_vendors if installed_vendors is not None else sorted(VENDOR_CONFIGS)
    results: List[Dict[str, Any]] = []
    for vendor in vendors:
        if vendor not in VENDOR_CONFIGS:
            continue
        try:
            results.append(install(vendor, home, apply=True,
                                    guard_script=guard_script, python_exe=python_exe))
        except Exception as e:
            results.append({'vendor': vendor, 'error': str(e), 'changed': False})
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vendor', choices=sorted(VENDOR_CONFIGS), action='append',
                         help='Install for this vendor only (repeatable). Default: all.')
    parser.add_argument('--home', default=None,
                         help='Override home directory (isolated/test homes). '
                              'Default: the real user home (Path.home()).')
    parser.add_argument('--repo-root', default=None,
                         help="Repo checkout whose mc/process_guard.py the written hook "
                              "config should point at. Default: this script's own checkout. "
                              "Use this when previewing/writing the REAL install from a "
                              "throwaway worktree — a worktree's process_guard.py is deleted "
                              "when the worktree is, so a hook baked with that path would "
                              "silently stop working (see module docstring: it fails CLOSED, "
                              "blocking every shell call, not open). Point this at the "
                              "permanent (main) checkout instead.")
    parser.add_argument('--python-exe', default=None,
                         help='Python interpreter the written hook command invokes. '
                              'Default: sys.executable of THIS process — pass the server '
                              'process\'s own interpreter (venv / frozen exe) when installing '
                              'on its behalf; bare "python" depends on the external CLI\'s '
                              'own PATH at hook-fire time, not this one.')
    parser.add_argument('--apply', action='store_true',
                         help='Write the change. Default is dry-run: show the diff, write nothing.')
    args = parser.parse_args(argv)

    home = Path(args.home) if args.home else Path.home()
    guard_script = (Path(args.repo_root) / 'mc' / 'process_guard.py') if args.repo_root else GUARD_SCRIPT
    vendors = args.vendor or sorted(VENDOR_CONFIGS)

    for vendor in vendors:
        result = install(vendor, home, apply=args.apply, guard_script=guard_script,
                          python_exe=args.python_exe)
        print(f"== {vendor}: {result['path']} ==")
        if not result['changed']:
            print('  already installed (idempotent no-op) — nothing to write')
            continue
        print(result['diff'], end='')
        print('  [dry-run — pass --apply to write]' if not args.apply else '  written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
