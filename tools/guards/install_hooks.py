#!/usr/bin/env python3
"""Generate Clayrune's per-launch guardrail hook files (W2, redesigned).

**This does NOT touch any vendor CLI's global config** (`~/.claude`,
`~/.gemini`, `~/.qwen`, `~/.codex`). It writes small, Clayrune-owned files
under `~/.clayrune/hooks/` (see `mc/guardrail_hooks.py` for the exact paths),
which `mc/agent_runtime.py` and `mc/blueprints/agent_routes.py`'s
`_build_claude_flags` point a CLI at ONLY on the launches Clayrune itself
starts, via a flag/env var each CLI resolves fresh per invocation:

  - Claude Code: `--settings <file>` — CLI help: "load additional settings
    from" (additive), and explicitly still applies under
    `--dangerously-skip-permissions`. NOT one of the `--setting-sources`
    (user/project/local) a project could exclude.
  - Gemini CLI: `GEMINI_CLI_SYSTEM_SETTINGS_PATH` env var — the CLI's own
    docs (`bundle/docs/reference/configuration.md`) name this as the
    override for its "System settings file", the highest-precedence layer,
    above project settings.
  - Qwen Code: `QWEN_CODE_SYSTEM_SETTINGS_PATH` env var — same mechanism,
    confirmed present in the bundled CLI (`chunk-IDS7MSUP.js`).
  - Codex CLI: `-c hooks="<file>"` plus `-c bypass_hook_trust=true` (the
    interactive hook-trust gate has no non-interactive prompt path — see
    docs/GUARDRAIL_PARITY_EVIDENCE.md §1). NOT independently confirmed live
    (no Codex launches); the codex file below is generated as a MERGE with
    the user's real hooks.json specifically because that CLI's own merge-vs-
    replace behavior for this override is unverified — Claude/Gemini/Qwen's
    IS verified additive, so their generated files are guard-only.

Live-verified, 2026-09-18, each with an isolated test home carrying a
harmless marker-writing "user" hook plus the per-launch mechanism pointed at
`mc/process_guard.py`: for Claude, Gemini and Qwen, BOTH hooks fired for the
same `taskkill /IM notepad.exe` attempt (the user's marker was written AND
Clayrune's guard blocked the command) — proving the per-launch layer adds to,
never replaces, whatever the user has configured for themselves.

Regenerated idempotently (this script always overwrites its own files
wholesale — they are 100% Clayrune-owned, so there is nothing to preserve),
normally called once at Clayrune startup (`server.py`'s
`_install_guardrail_hooks_on_boot`), gated per vendor on
`health_check().installed` exactly as before.

Superseded design (kept only as history in git, not in this file): writing
directly into `~/.claude/settings.json` etc. That silently changed how a
user's OWN, Clayrune-independent CLI use behaved, with no uninstall path.
Nothing here writes to those files anymore.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mc import guardrail_hooks as _gh  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_SCRIPT = REPO_ROOT / 'mc' / 'process_guard.py'
HOOK_NAME = 'clayrune-process-guard'

# Per-vendor hook-file SHAPE (event name + matcher) — the DESTINATION and
# whether the vendor's real global config gets read (for merging) lives in
# mc/guardrail_hooks.py / _real_codex_hooks_path below, not here.
VENDOR_CONFIGS: Dict[str, Dict[str, str]] = {
    'claude': {'event': 'PreToolUse', 'matcher': 'Bash|PowerShell'},
    'gemini': {'event': 'BeforeTool', 'matcher': 'run_shell_command'},
    'qwen': {'event': 'PreToolUse', 'matcher': 'Bash|PowerShell|run_shell_command'},
    'codex': {'event': 'PreToolUse', 'matcher': 'shell'},
}

# Vendors whose per-launch override is verified additive — their generated
# file needs only Clayrune's own entry, nothing read from the real install.
_GUARD_ONLY_VENDORS = {'claude', 'gemini', 'qwen'}
# Vendors whose override semantics are unverified — merge with the real file
# ourselves so correctness doesn't depend on an unconfirmed CLI behavior.
_MERGE_VENDORS = {'codex'}
assert _GUARD_ONLY_VENDORS | _MERGE_VENDORS == set(VENDOR_CONFIGS), \
    'every vendor must be classified as guard-only or merge'


def guard_command(guard_script: Path = GUARD_SCRIPT, python_exe: Optional[str] = None) -> str:
    """Command string a vendor's hook runner executes.

    Quoting the interpreter path unconditionally (`"<py>" "<script>"`) was
    the first version of this function and broke SILENTLY in production:
    live-tested 2026-09-18 against a real dispatch, Gemini's CLI (the
    Windows `.cmd` launcher `resolve_binary()` finds) executes hook commands
    through a shell that parses two adjacent quoted tokens as a syntax
    error ("UnexpectedToken") — the hook then failed to even run, and
    Gemini treated that failure as an ALLOW, not a deny (unlike a genuinely
    MISSING script, which every vendor treats as a deny — see
    docs/GUARDRAIL_PARITY_EVIDENCE.md §1a). `taskkill /IM notepad.exe` went
    through and killed a live test process before this was caught. Claude
    and Qwen were re-verified unaffected by the same quoted format, so this
    was Gemini-specific, not a two-vendor coincidence.

    Fix: quote the interpreter ONLY when its path actually contains a space
    (the one case an unquoted bareword can't handle) — live re-verified
    against Gemini's real `.cmd` launcher, a real taskkill, blocked
    correctly. An interpreter path WITH a space is a known, disclosed gap:
    no single quoting form is confirmed safe across every vendor's hook
    shell in that case, and none of the vendors tested here need it (their
    resolved interpreters have no spaces).
    """
    py = python_exe or sys.executable or 'python'
    py_token = f'"{py}"' if ' ' in py else py
    return f'{py_token} "{guard_script}"'


def _desired_group(cfg: Dict[str, str], command: str) -> Dict[str, Any]:
    return {
        'matcher': cfg['matcher'],
        'hooks': [{'type': 'command', 'command': command, 'name': HOOK_NAME}],
    }


def _find_group_index(event_list: Any, marker: str) -> Optional[int]:
    """Index of the group carrying `marker` on one of its hooks, or None."""
    if not isinstance(event_list, list):
        return None
    for i, entry in enumerate(event_list):
        if not isinstance(entry, dict):
            continue
        for h in entry.get('hooks') or []:
            if isinstance(h, dict) and h.get('name') == marker:
                return i
    return None


def _real_codex_hooks_path(home: Path) -> Path:
    return home / '.codex' / 'hooks.json'


def _load_json_object(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise RuntimeError(f'{path} exists but is not valid JSON ({e}) — refusing to read it') from e
    if not isinstance(data, dict):
        raise RuntimeError(f'{path} is valid JSON but not an object — refusing to read it')
    return data


def plan_generate(vendor: str, guard_script: Path = GUARD_SCRIPT,
                   python_exe: Optional[str] = None,
                   real_home: Optional[Path] = None,
                   clayrune_home: Optional[Path] = None
                   ) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """Return (before, after, changed) for the GENERATED launch file.

    `before` is what's on disk at the destination NOW (for the diff/idempotency
    check) — for a guard-only vendor that's simply the previous generation;
    for codex it's the previously-generated MERGED file, so a diff still shows
    only what actually changes on regeneration, not the user's whole real file
    every time.
    """
    cfg = VENDOR_CONFIGS[vendor]
    command = guard_command(guard_script, python_exe)
    dest = _gh.launch_file_path(vendor, clayrune_home)
    before = _load_json_object(dest)

    if vendor in _MERGE_VENDORS:
        real_home = real_home or Path.home()
        after = copy.deepcopy(_load_json_object(_real_codex_hooks_path(real_home)))
    else:
        # Guard-only vendors regenerate from scratch each time — no reason to
        # carry forward a stale prior shape, and there is nothing else in
        # this file to preserve (it's 100% Clayrune's own).
        after = {}

    hooks = after.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise RuntimeError(f"unexpected non-object 'hooks' key while generating {vendor}'s launch file")
    event_list = hooks.setdefault(cfg['event'], [])
    if not isinstance(event_list, list):
        raise RuntimeError(f"unexpected non-array 'hooks.{cfg['event']}' while generating {vendor}'s launch file")

    desired = _desired_group(cfg, command)
    idx = _find_group_index(event_list, HOOK_NAME)
    if idx is None:
        event_list.append(desired)
    else:
        event_list[idx] = desired

    changed = before != after
    return before, after, changed


def diff_text(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """Zero-context diff — see docs/GUARDRAIL_PARITY_EVIDENCE.md for why: a
    wider window pulled a live API key out of a real settings file in
    testing, back when this wrote into global config. Kept even though the
    generated files are Clayrune-owned now, since Codex's is a merge of the
    user's real hooks.json and could still carry content this script never
    means to display.
    """
    b = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    a = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    return ''.join(difflib.unified_diff(b, a, fromfile='before', tofile='after', lineterm='\n', n=0))


def generate(vendor: str, apply: bool, guard_script: Path = GUARD_SCRIPT,
             python_exe: Optional[str] = None, real_home: Optional[Path] = None,
             clayrune_home: Optional[Path] = None) -> Dict[str, Any]:
    before, after, changed = plan_generate(vendor, guard_script, python_exe, real_home, clayrune_home)
    dest = _gh.launch_file_path(vendor, clayrune_home)
    result: Dict[str, Any] = {'vendor': vendor, 'path': str(dest), 'changed': changed, 'diff': ''}
    if not changed:
        return result
    result['diff'] = diff_text(before, after)
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(after, indent=2) + '\n', encoding='utf-8')
    return result


def generate_for_boot(clayrune_home: Optional[Path] = None, guard_script: Path = GUARD_SCRIPT,
                       python_exe: Optional[str] = None,
                       installed_vendors: Optional[List[str]] = None,
                       real_home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Server-startup entry point — see server.py's wiring.

    Generates (applies for real) ONLY for vendors in `installed_vendors`.
    Never raises on a single vendor's failure (e.g. a malformed real
    ~/.codex/hooks.json for the codex merge) — one vendor's problem must not
    block boot or the other vendors' generation.
    """
    vendors = installed_vendors if installed_vendors is not None else sorted(VENDOR_CONFIGS)
    results: List[Dict[str, Any]] = []
    for vendor in vendors:
        if vendor not in VENDOR_CONFIGS:
            continue
        try:
            results.append(generate(vendor, apply=True, guard_script=guard_script,
                                     python_exe=python_exe, real_home=real_home,
                                     clayrune_home=clayrune_home))
        except Exception as e:
            results.append({'vendor': vendor, 'error': str(e), 'changed': False})
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vendor', choices=sorted(VENDOR_CONFIGS), action='append',
                         help='Generate for this vendor only (repeatable). Default: all.')
    parser.add_argument('--real-home', default=None,
                         help="Home directory to read the user's REAL config from, for the "
                              "codex merge only (claude/gemini/qwen never read the real "
                              "install at all). Default: Path.home().")
    parser.add_argument('--clayrune-home', default=None,
                         help='Where to write the generated files, under <this>/hooks/. '
                              'Default: ~/.clayrune (isolated/test homes override this).')
    parser.add_argument('--repo-root', default=None,
                         help="Repo checkout whose mc/process_guard.py the generated hook "
                              "command should point at. Default: this script's own checkout.")
    parser.add_argument('--python-exe', default=None,
                         help='Python interpreter the generated hook command invokes. '
                              'Default: sys.executable of THIS process.')
    parser.add_argument('--apply', action='store_true',
                         help='Write the change. Default is dry-run: show the diff, write nothing.')
    args = parser.parse_args(argv)

    real_home = Path(args.real_home) if args.real_home else None
    clayrune_home = Path(args.clayrune_home) if args.clayrune_home else None
    guard_script = (Path(args.repo_root) / 'mc' / 'process_guard.py') if args.repo_root else GUARD_SCRIPT
    vendors = args.vendor or sorted(VENDOR_CONFIGS)

    for vendor in vendors:
        result = generate(vendor, apply=args.apply, guard_script=guard_script,
                           python_exe=args.python_exe, real_home=real_home,
                           clayrune_home=clayrune_home)
        print(f"== {vendor}: {result['path']} ==")
        if not result['changed']:
            print('  already up to date — nothing to write')
            continue
        print(result['diff'], end='')
        print('  [dry-run — pass --apply to write]' if not args.apply else '  written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
