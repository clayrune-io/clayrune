"""Bounded content inventory for a staged and approved repository; no repo code runs."""
from __future__ import annotations
import hashlib
from pathlib import Path
from mc.desk_connect.mcp_errors import ActivationError

def inventory(directory: str) -> dict[str, str]:
    root = Path(directory)
    if root.is_symlink() or getattr(root, 'is_junction', lambda: False)() or not root.is_dir():
        raise ActivationError('The staged folder is missing or unsafe.', 'repository_changed', 409)
    files: dict[str, str] = {}
    total = 0
    for p in root.rglob('*'):
        rel = p.relative_to(root)
        if rel.parts[0] == '.git' or str(rel) == '.meta.json':
            continue
        if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
            raise ActivationError('Links in a repository cannot be approved here.', 'unsafe_repository', 400)
        if not p.is_file():
            continue
        total += p.stat().st_size
        if total > 100_000_000 or len(files) >= 10000:
            raise ActivationError('The repository is too large to check safely.', 'repository_too_large', 400)
        files[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files
