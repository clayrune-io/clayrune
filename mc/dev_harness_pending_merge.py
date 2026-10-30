"""Exact pending-index identity; never substitute clean HEAD for a merge (DH-M5)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mc.dev_harness_diff import FreezeError, changes, git_bytes, resolve_commit
from mc.dev_harness_integration_inputs import tracked_identity


def merge_heads(repo: Path) -> list[str]:
    name = git_bytes(repo, ['rev-parse', '--git-path', 'MERGE_HEAD']).decode('utf-8').strip()
    path = Path(name) if Path(name).is_absolute() else repo / name
    if path.exists():
        refs = path.read_text(encoding='ascii').split()
    else:
        words = os.environ.get('GIT_REFLOG_ACTION', '').split()
        if words[:1] not in (['merge'], ['pull']):
            return []
        refs = []
        for word in words[1:]:
            if word.startswith('-'):
                continue
            try:
                refs.append(resolve_commit(repo, word))
            except FreezeError:
                continue
    return list(dict.fromkeys(resolve_commit(repo, ref) for ref in refs))


def freeze_pending(repo: Path) -> dict[str, Any] | None:
    heads = merge_heads(repo)
    if not heads:
        return None
    if len(heads) != 1:
        raise FreezeError('ambiguous_pending_parents')
    b, h = resolve_commit(repo, 'HEAD'), heads[0]
    if git_bytes(repo, ['ls-files', '--unmerged', '-z']):
        raise FreezeError('pending_conflicts')
    # Pending T contains only index bytes; unrelated untracked runtime writes
    # cannot change it. Git itself refuses an untracked overwrite collision.
    if git_bytes(repo, ['diff', '--name-only', '-z', '--']):
        raise FreezeError('pending_worktree_differs')
    tree = git_bytes(repo, ['write-tree']).decode('ascii').strip()
    bases = git_bytes(repo, ['merge-base', '--all', b, h]).decode('ascii').split()
    if len(bases) != 1:
        raise FreezeError('ambiguous_merge_base')
    auto = git_bytes(repo, ['merge-tree', '--write-tree', b, h]).decode('ascii').splitlines()[0]
    if tree != auto:
        raise FreezeError('pending_resolution_unsupported')
    identity = tracked_identity(repo)
    result = {'kind': 'pending_merge', 'B': b, 'H': h, 'M': bases[0], 'T': tree,
              'auto_tree': auto, 'working_manifest': identity['manifest'],
              'branch_changes': changes(repo, bases[0], h), 'integration_changes': changes(repo, b, tree)}
    if tracked_identity(repo) != identity or merge_heads(repo) != heads:
        raise FreezeError('inputs_changed')
    return result


def pending_pins(repo: Path) -> dict[str, Any] | None:
    """Git-only inactive-hook lease pins; never a native execution descriptor."""
    heads = merge_heads(repo)
    if not heads:
        return None
    if len(heads) != 1:
        raise FreezeError('ambiguous_pending_parents')
    return {'B': resolve_commit(repo, 'HEAD'), 'H': heads[0],
            'T': git_bytes(repo, ['write-tree']).decode('ascii').strip()}


def revalidate(repo: Path, frozen: dict[str, Any]) -> None:
    if freeze_pending(repo) != frozen:
        raise FreezeError('inputs_changed')
