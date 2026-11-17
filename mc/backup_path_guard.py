"""Where a backup archive is allowed to write (MC-1179 pass 07, P27).

An archive is someone else's file on the cross-machine import path, so its
member names and the ``dest`` its manifest records are untrusted input. These
helpers are pure (no I/O beyond ``Path.resolve``) and return a refusal string
or ``None``; ``mc/backup.py`` raises ``BackupError`` from the string so the
whole import or restore fails before the first write, naming the member.

Importable without ``mc.backup`` (which imports this module).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterable, Optional

_DRIVE = re.compile(r'^[A-Za-z]:')


def member_name_refusal(name: object) -> Optional[str]:
    """Reason an archive member name is unsafe, or None. Segments are what
    matter: ``a..b.txt`` is an ordinary file name, ``a/../b`` is not. Names
    this module writes are always forward-slash relative paths."""
    if not isinstance(name, str) or not name:
        return 'member name is empty or not a string'
    if '\x00' in name:
        return 'member name holds a NUL character'
    if '\\' in name:
        return 'member name holds a backslash'
    if name.startswith('/'):
        return 'member name starts with a slash'
    if _DRIVE.match(name):
        return 'member name starts with a drive'
    if '..' in name.split('/'):
        return "member name holds a '..' segment"
    return None


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.resolve()))


def is_within(dest: Path, root: Path) -> bool:
    """True if ``dest`` resolves to ``root`` itself or somewhere under it.
    Both sides are resolved, so ``..`` segments and symlinks/junctions are
    followed before the comparison (and compared case-folded on Windows)."""
    try:
        d, r = _norm(Path(dest)), _norm(Path(root))
        return os.path.commonpath([d, r]) == r
    except (ValueError, OSError):
        return False  # different drives, or a path that cannot be resolved


def dest_refusal(member: str, dest: Path, roots: Iterable[Path]) -> Optional[str]:
    """Reason ``dest`` is outside every allowed root, or None."""
    roots = list(roots)
    if any(is_within(dest, r) for r in roots):
        return None
    return f'{member}: destination {dest} is outside every location a backup may write to'


def archive_project_paths(manifest: dict, read_member) -> list[Path]:
    """Project paths named by the project records inside the archive itself.

    A full restore after the records were lost must still be able to put a
    project's unprotected files back, and the only thing that knows where that
    project lived is its own record in the archive. Only a member that is
    exactly ``records/projects/<id>.json`` for an id the manifest lists
    (the rule ``_validate_records_write`` applies), whose name passes
    ``member_name_refusal`` and whose sha256 matches the manifest, is parsed;
    its ``project_path`` counts only if it is a non-empty absolute path that
    is not a filesystem root. ``read_member(name)`` returns the member bytes.
    Anything that fails a check is skipped, never an error: the member simply
    adds no root, and the restore's own checks refuse what depended on it."""
    ids = {p.get('id') for p in manifest.get('projects', []) if isinstance(p, dict)}
    out: list[Path] = []
    for name, meta in (manifest.get('files') or {}).items():
        if not isinstance(meta, dict) or member_name_refusal(name):
            continue
        if not (name.startswith('records/projects/') and name.endswith('.json')):
            continue
        pid = name[len('records/projects/'):-len('.json')]
        if not pid or '/' in pid or pid not in ids:
            continue
        try:
            data = read_member(name)
            if hashlib.sha256(data).hexdigest() != meta.get('sha256'):
                continue
            rec = json.loads(data.decode('utf-8'))
        except (KeyError, ValueError, OSError):
            continue
        pp = rec.get('project_path') if isinstance(rec, dict) else None
        if not isinstance(pp, str) or not pp.strip() or '\x00' in pp:
            continue
        p = Path(pp)
        if not p.is_absolute() or p == p.parent:
            continue
        out.append(p)
    return out
