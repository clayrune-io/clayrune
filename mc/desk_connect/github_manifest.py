"""Bounded content inventory for a staged and approved repository; no repo code runs."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from collections.abc import Iterator
from mc.desk_connect.mcp_errors import ActivationError

def entries(directory: str, skip: set[str] | None = None) -> Iterator[Path]:
    """Walk without following links; prune recorded output before descending into it."""
    root = Path(directory)
    if root.is_symlink() or getattr(root, 'is_junction', lambda: False)() or not root.is_dir():
        raise ActivationError('The staged folder is missing or unsafe.', 'repository_changed', 409)
    pending = [(root, 0)]
    while pending:
        folder, depth = pending.pop()
        if depth > 64:
            raise ActivationError('The repository is too deep to check safely.', 'unsafe_repository', 400)
        with os.scandir(folder) as children:
            for child in children:
                p = Path(child.path)
                rel = p.relative_to(root)
                if rel.parts[0] == '.git' or rel.as_posix() == '.meta.json' or (skip and rel.as_posix() in skip):
                    continue
                yield p
                if child.is_dir(follow_symlinks=False) and not getattr(p, 'is_junction', lambda: False)():
                    pending.append((p, depth + 1))

def inventory(directory: str, *, skip: set[str] | None = None) -> dict[str, str]:
    root = Path(directory)
    files: dict[str, str] = {}
    total = 0
    count = 0
    for p in entries(directory, skip):
        count += 1
        if count > 20000:
            raise ActivationError('The repository is too large to check safely.', 'repository_too_large', 400)
        rel = p.relative_to(root)
        if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
            raise ActivationError('Links in a repository cannot be approved here.', 'unsafe_repository', 400)
        if not p.is_file():
            continue
        total += p.stat().st_size
        if total > 100_000_000 or len(files) >= 10000:
            raise ActivationError('The repository is too large to check safely.', 'repository_too_large', 400)
        files[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files
