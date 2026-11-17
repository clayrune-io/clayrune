"""Do not let the vendor context mirror make a worktree read as dirty (MC-1179 AP43).

Before a Codex, Gemini or Qwen session starts, `vendor_context_sync` writes
AGENTS.md / GEMINI.md / QWEN.md into the session's run directory, which for an
isolated session is its worktree. A project that does not ignore those names
then sees an untracked file (or, where it tracks a generated copy, a modified
one) in `git status`, so the worktree reads dirty and does not land or get
cleaned up although the agent has done nothing.

The rule, and only this rule: a mirror entry is ignored when the file is
  * untracked and carries the sync's own first-line marker, or
  * tracked, modified in the work tree only, and equal to what the sync would
    write from the worktree's current CLAUDE.md (compared the way the sync
    reads it, as decoded text).
Anything else naming those files, including an untracked file with no marker
(an agent's or a person's own work) or a staged change, still counts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Tuple

from mc.vendor_context_sync import MARKER_PREFIX, expected_mirror_text

MIRROR_NAMES = ('AGENTS.md', 'GEMINI.md', 'QWEN.md')

# `git status --porcelain=v2` entry states. v2 is used because project_sync.git_run
# strips its output, which eats the leading space of v1's " M" on the first line
# and makes "modified in the work tree" indistinguishable from "staged".
UNTRACKED = '?'
WORKTREE_MODIFIED = '.M'
STATUS_ARGS = ['status', '--porcelain=v2']

GitRun = Callable[..., Tuple[bool, str]]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, ValueError):
        return None


def _is_mirror_noise(wt: Path, status: str, name: str) -> bool:
    text = _read(wt / name)
    if text is None:
        return False
    if status == UNTRACKED:
        return text.startswith(MARKER_PREFIX)
    if status != WORKTREE_MODIFIED:
        return False
    source = _read(wt / 'CLAUDE.md')
    return source is not None and text == expected_mirror_text(source, name)


def _entry(line: str) -> Tuple[str, str]:
    """(state, path) of one porcelain v2 line; ('', '') for any other kind."""
    if line.startswith('? '):
        return UNTRACKED, line[2:]
    if line.startswith('1 '):
        parts = line.split(' ', 8)
        if len(parts) == 9:
            return parts[1], parts[8]
    return '', ''


def remaining_dirty(wt: str | Path, porcelain_v2: str) -> str:
    """`git status --porcelain=v2` output without the mirror noise."""
    root = Path(wt)
    kept = []
    for line in porcelain_v2.splitlines():
        status, name = _entry(line)
        if name in MIRROR_NAMES and _is_mirror_noise(root, status, name):
            continue
        kept.append(line)
    return '\n'.join(kept)


def dirty_ignoring_mirror(wt: str | Path, git_run: GitRun) -> bool:
    """`project_sync._dirty` for a worktree, minus the vendor mirror noise."""
    ok, out = git_run(str(wt), STATUS_ARGS)
    return ok and bool(remaining_dirty(wt, out).strip())
