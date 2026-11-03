"""PreToolUse boundary for Desk research. No project code or settings execute."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


def checked_path(root: Path, value: str, *, file: bool = True) -> Path:
    from mc.desk_product_context import _safe_parts
    root = root.resolve(strict=True)
    path = Path(value)
    path = path if path.is_absolute() else root / path
    for candidate in (path.absolute(), path.resolve(strict=True)):
        if not candidate.is_relative_to(root):
            raise ValueError('path leaves the project folder')
        rel = candidate.relative_to(root)
        _safe_parts(rel)
        if any(p.lower().startswith('.env') or p.lower() in ('.git', '.hg', '.svn') for p in rel.parts):
            raise ValueError('private project metadata or environment file')
        if rel != Path('.'):
            _not_ignored(root, rel)
    resolved = path.resolve(strict=True)
    if file and not resolved.is_file():
        raise ValueError('Read/Grep needs one regular file; use Glob to find names first')
    return resolved


def _not_ignored(root: Path, rel: Path) -> None:
    try:
        _check_ignored(root, rel)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('ignore policy unavailable') from exc


def _check_ignored(root: Path, rel: Path) -> None:
    flags: dict[str, Any] = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    cmd = ['git', '-C', str(root), 'check-ignore', '--no-index', '-q', '--', rel.as_posix()]
    result = subprocess.run(cmd, capture_output=True, timeout=10, **flags)
    if result.returncode == 128 and not any((p / '.git').exists() for p in (root, *root.parents)):
        # Non-Git projects still get .gitignore rules without writing to them.
        with tempfile.TemporaryDirectory(prefix='desk-ignore-') as tmp:
            subprocess.run(['git', 'init', '--bare', '-q', tmp], check=True,
                           capture_output=True, timeout=10, **flags)
            result = subprocess.run(['git', '--git-dir', tmp, '--work-tree', str(root),
                                     'check-ignore', '--no-index', '-q', '--', rel.as_posix()],
                                    cwd=root, capture_output=True, timeout=10, **flags)
    if result.returncode != 1:
        raise ValueError('gitignored path or ignore policy unavailable')


def decide(root: Path | None, event: dict) -> dict:
    name, args = event.get('tool_name'), event.get('tool_input') or {}
    updated = dict(args)
    try:
        if name in ('WebSearch', 'WebFetch'):
            if name == 'WebFetch' and not str(args.get('url', '')).startswith(('https://', 'http://')):
                raise ValueError('only HTTP(S) web pages may be fetched')
        elif name in ('Read', 'Grep', 'Glob') and root is not None:
            value = args.get('file_path') if name == 'Read' else args.get('path', str(root))
            if not isinstance(value, str) or not value:
                raise ValueError('supply a project path')
            target = checked_path(root, value, file=name != 'Glob')
            updated['file_path' if name == 'Read' else 'path'] = str(target)
            if name == 'Glob':
                pattern = str(args.get('pattern', ''))
                if '..' in pattern or pattern.startswith(('/', '\\')) or ':' in pattern:
                    raise ValueError('Glob pattern must be relative to its project path')
        else:
            raise ValueError('tool is not offered for Desk research')
        decision, reason = 'allow', 'Read-only Desk research'
    except Exception as exc:
        decision, reason = 'deny', f'Desk read guard: {exc}'
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                   'permissionDecision': decision,
                                   'updatedInput': updated,
                                   'permissionDecisionReason': reason}}


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        args = sys.argv[1:] if argv is None else argv
        root = Path(args[0]) if args and args[0] else None
        print(json.dumps(decide(root, json.load(sys.stdin))))
        return 0
    except Exception as exc:
        print(f'Desk read guard failed: {type(exc).__name__}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
