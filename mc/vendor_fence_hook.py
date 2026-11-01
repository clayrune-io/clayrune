"""Translate Gemini/Qwen hook payloads into the human-owned steward fence.

Loaded only by an unattended per-launch settings file. No vendor/global
configuration is written. The fence itself remains the policy authority.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, Sequence

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def normalize_payload(payload: Dict[str, Any], *, powershell: bool = False) -> Dict[str, Any]:
    """Preserve call identity/cwd and map native filesystem/shell tool names."""
    name = payload.get('tool_name') or payload.get('toolName')
    args = payload.get('tool_input', payload.get('toolInput', {}))
    if not isinstance(name, str) or not name or not isinstance(args, dict):
        raise ValueError('invalid tool name/input in fence hook event')
    aliases = {
        'run_shell_command': 'PowerShell' if powershell else 'Bash',
        'shell': 'PowerShell' if powershell else 'Bash',
        'write_file': 'Write', 'edit_file': 'Edit', 'replace': 'Edit',
        'read_file': 'Read', 'read_many_files': 'Read',
        'glob': 'Glob', 'grep_search': 'Grep', 'search_file_content': 'Grep',
    }
    out = dict(payload)
    out['tool_name'] = aliases.get(name, name)
    out['tool_input'] = dict(args)
    if out['tool_name'] in ('Read', 'Write', 'Edit'):
        # Both vendors use file_path for their native file tools. Some older
        # Qwen aliases use path; the fence expects Claude's file_path key.
        out['tool_input']['file_path'] = (args.get('file_path') or args.get('path')
                                          or args.get('absolute_path') or '')
    if name == 'read_many_files':
        # A single synthetic Read would miss all but one path. Refuse this
        # bulk variant until it can be expanded without losing any target.
        raise ValueError('read_many_files has no verified fence translation; use read_file')
    return out


def main(argv: Sequence[str] = ()) -> int:
    """Return 2 with stderr on load/translation failures, never fail open."""
    original = sys.stdin
    try:
        from steward import fence
        if '--self-test' in argv:
            return fence.main(['--armed', '--self-test'])
        raw = json.load(original)
        if not isinstance(raw, dict):
            raise ValueError('fence event must be an object')
        payload = normalize_payload(raw, powershell='--powershell' in argv)
        sys.stdin = io.StringIO(json.dumps(payload))
        rc = fence.main(['--armed'])
        if rc not in (0, 2):
            raise RuntimeError(f'fence returned unexpected exit {rc}')
        return rc
    except Exception as exc:
        print(f'STEWARD FENCE could not run; blocking fail-closed: {exc}', file=sys.stderr)
        return 2
    finally:
        sys.stdin = original


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
