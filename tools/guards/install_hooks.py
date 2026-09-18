#!/usr/bin/env python3
"""Install Clayrune's process guard into Gemini's and Qwen's own hook systems.

W2 (docs/VENDOR_AGNOSTIC_PROGRAM.md §3): guardrails are a Clayrune policy,
installed into each vendor CLI's own hook mechanism rather than reimplemented
per vendor. Claude Code already runs under one (`~/.claude/settings.json`'s
PreToolUse hook) — this installer covers the two vendors that had none:

  - Gemini CLI: `~/.gemini/settings.json`, `hooks.BeforeTool`, matcher against
    the tool name `run_shell_command` (confirmed via the CLI's own bundled
    docs, `@google/gemini-cli/bundle/docs/hooks/reference.md`: exit code 2
    blocks the tool call, `stderr` is the reason sent back to the agent).
  - Qwen Code: `~/.qwen/settings.json`, `hooks.PreToolUse` — same event names
    and hook-definition shape as Claude Code (confirmed by reading the
    bundled settings schema, `@qwen-code/qwen-code`'s `chunk-P6XVYPWA.js`,
    `HOOK_DEFINITION_ITEMS`); matcher covers both `Bash`/`PowerShell` (in
    case a future qwen-code build names its shell tool the Claude way) and
    `run_shell_command` (its actual, live-verified name today, since
    qwen-code bundles gemini-cli's own shell tool).

Codex is NOT covered: `codex --help` lists no `hooks` subcommand (0.154.0),
and there is no separate hooks-config doc shipped with the CLI. Treated as a
vendor gap until Codex ships one, not silently worked around.

Every vendor points at the SAME guard, `mc/process_guard.py`, run directly as
`python "<repo>/mc/process_guard.py"` — the identical invocation shape
Claude's own (pre-existing, untouched) hook config already uses. No second
guard implementation. `mc/process_guard.py`'s `hook_main` already recognizes
both tool-name conventions (`_SHELL_TOOL_NAMES`), so nothing vendor-specific
lives in the guard itself — only the hook-config *shape* differs per vendor,
and that's what this installer encodes.

Idempotent: re-running never duplicates our own entry (matched by exact
`command` string) and never touches, reorders or removes any hook a user
already configured — it only appends one new entry to the relevant event's
array. Dry-run (the default) never writes; `--apply` is required to write.
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
}


def guard_command(guard_script: Path = GUARD_SCRIPT) -> str:
    return f'python "{guard_script}"'


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


def _has_our_hook(event_entries: Any, command: str) -> bool:
    if not isinstance(event_entries, list):
        return False
    for entry in event_entries:
        if not isinstance(entry, dict):
            continue
        for h in entry.get('hooks') or []:
            if isinstance(h, dict) and h.get('command') == command:
                return True
    return False


def plan_install(vendor: str, home: Path,
                  guard_script: Path = GUARD_SCRIPT) -> Tuple[Path, Dict[str, Any], Dict[str, Any], bool]:
    """Return (settings_path, before, after, changed) — never writes."""
    cfg = VENDOR_CONFIGS[vendor]
    path = home / Path(cfg['settings_rel'])
    before = _load_settings(path)
    after = copy.deepcopy(before)
    command = guard_command(guard_script)
    hooks = after.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{path}: top-level 'hooks' is not an object — refusing to touch it")
    event_list = hooks.setdefault(cfg['event'], [])
    if _has_our_hook(event_list, command):
        return path, before, after, False
    if not isinstance(event_list, list):
        raise RuntimeError(
            f"{path}: 'hooks.{cfg['event']}' is not an array — refusing to touch it")
    event_list.append({
        'matcher': cfg['matcher'],
        'hooks': [{
            'type': 'command',
            'command': command,
            'name': HOOK_NAME,
        }],
    })
    return path, before, after, True


def diff_text(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """Zero-context diff — deliberate, not a cosmetic choice.

    This installer only ever appends a new hook entry (see plan_install), so
    every real diff is a pure insertion; zero context still shows every line
    it would write. A non-zero context window pulls in surrounding UNCHANGED
    lines from the user's real settings.json — measured 2026-09-18: on this
    box that included a live API key sitting a few lines above the insertion
    point. Showing a diff must not leak content this installer never reads or
    touches.
    """
    b = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    a = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    return ''.join(difflib.unified_diff(b, a, fromfile='before', tofile='after', lineterm='\n', n=0))


def install(vendor: str, home: Path, apply: bool,
            guard_script: Path = GUARD_SCRIPT) -> Dict[str, Any]:
    path, before, after, changed = plan_install(vendor, home, guard_script)
    result: Dict[str, Any] = {'vendor': vendor, 'path': str(path), 'changed': changed, 'diff': ''}
    if not changed:
        return result
    result['diff'] = diff_text(before, after)
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(after, indent=2) + '\n', encoding='utf-8')
    return result


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
                              "silently stop working. Point this at the permanent (main) "
                              "checkout instead.")
    parser.add_argument('--apply', action='store_true',
                         help='Write the change. Default is dry-run: show the diff, write nothing.')
    args = parser.parse_args(argv)

    home = Path(args.home) if args.home else Path.home()
    guard_script = (Path(args.repo_root) / 'mc' / 'process_guard.py') if args.repo_root else GUARD_SCRIPT
    vendors = args.vendor or sorted(VENDOR_CONFIGS)

    for vendor in vendors:
        result = install(vendor, home, apply=args.apply, guard_script=guard_script)
        print(f"== {vendor}: {result['path']} ==")
        if not result['changed']:
            print('  already installed (idempotent no-op) — nothing to write')
            continue
        print(result['diff'], end='')
        print('  [dry-run — pass --apply to write]' if not args.apply else '  written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
