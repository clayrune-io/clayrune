"""Where a Claude tool-free transform (oneshot / stream_text / describe_image) runs.

`ClaudeRuntime.TRANSFORM_ISOLATION` strips tools, MCP, plugins, skills and hooks,
but the CLI still discovers CLAUDE.md files by walking UP from the cwd and still
injects its auto-memory instructions. Measured 2026-10-06 (claude 2.1.x, haiku,
prompt "Reply OK", same isolated argv, total prompt tokens):

    cwd = project root                      14,042
    cwd = a dir inside any repo             13,420   (ancestor CLAUDE.md walk)
    cwd = a dir with no CLAUDE.md above it   6,687
    + CLAUDE_CODE_DISABLE_AUTO_MEMORY=1      4,279   (floor)

So a transform is paid ~7.4k tokens for project context it cannot use (it has no
tools, and every brief travels in the prompt), and the other ~2.4k for memory
instructions for a tool it does not have. This module owns both fixes: an empty
cwd that is not under any repo, and the env that turns off auto-memory and
CLAUDE.md discovery. `--bare` would do it in one flag but demands an
ANTHROPIC_API_KEY, which breaks OAuth sign-in.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

NEUTRAL_DIRNAME = 'transform_cwd'

# Both names are real CLI switches (each changed the measured prompt size).
# CLAUDE_MDS is belt-and-braces: the neutral dir has none above it today, but a
# CLAUDE.md dropped into the user's home would otherwise ride every transform.
TRANSFORM_ENV: Dict[str, str] = {
    'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1',
    'CLAUDE_CODE_DISABLE_CLAUDE_MDS': '1',
}

_cached: Optional[str] = None


def neutral_cwd() -> str:
    """An empty directory under ~/.clayrune/, created on demand. Never inside a
    repo, so the CLI's upward CLAUDE.md walk finds nothing. Falls back to the
    home dir (the pre-existing default) if it cannot be created."""
    global _cached
    if _cached and Path(_cached).is_dir():
        return _cached
    path = Path.home() / '.clayrune' / NEUTRAL_DIRNAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return str(Path.home())
    _cached = str(path)
    return _cached


def transform_env() -> Dict[str, str]:
    """The parent environment plus TRANSFORM_ENV, for subprocess `env=`."""
    env = dict(os.environ)
    env.update(TRANSFORM_ENV)
    return env
