"""Private M5 integration inputs; dirty Preview/ordinary freezing stay separate."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mc.dev_harness_diff import FreezeError, _freeze_candidate, git_bytes, manifest, resolve_commit


def tracked_identity(repo: Path) -> dict[str, Any]:
    """HEAD, full index entries and tracked working bytes, never runtime files."""
    return {'head': resolve_commit(repo, 'HEAD'), 'manifest': manifest(repo, include_untracked=False)}


def tracked_dirty(repo: Path) -> bool:
    return bool(git_bytes(repo, ['status', '--porcelain', '-z', '--untracked-files=no']))


def require_tracked_clean(repo: Path) -> None:
    if tracked_dirty(repo):
        raise FreezeError('dirty_integration_input')


def freeze_integration(repo: Path, target: str, incoming: str) -> dict[str, Any]:
    before = tracked_identity(repo)
    frozen = _freeze_candidate(repo, target, incoming, include_untracked=False)
    if tracked_identity(repo) != before:
        raise FreezeError('inputs_changed')
    # This descriptor is created internally, never accepted from HTTP/CLI.
    return {**frozen, 'working_identity': before}


def source_identity(repo: Path, frozen: dict[str, Any]) -> dict[str, Any]:
    if frozen['kind'] == 'pending_merge' or (frozen['kind'] == 'integration' and 'working_identity' in frozen):
        return tracked_identity(repo)
    return manifest(repo)
