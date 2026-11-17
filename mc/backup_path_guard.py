"""Where a backup archive is allowed to write (MC-1179 pass 07, P27).

An archive is someone else's file on the cross-machine import path, so its
member names and the ``dest`` its manifest records are untrusted input. These
helpers are pure (no I/O beyond ``Path.resolve``) and return a refusal string
or ``None``; ``mc/backup.py`` raises ``BackupError`` from the string so the
whole import or restore fails before the first write, naming the member.

Importable without ``mc.backup`` (which imports this module).
"""
from __future__ import annotations

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
