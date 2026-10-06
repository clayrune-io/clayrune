"""Find the projects a user already works on in Claude Code (backlog ba3b73f9).

A new Clayrune install starts with an empty project grid even though
`~/.claude/projects/` already records every folder the user has run `claude` in.
Skills, MCP servers and settings need no import (Clayrune reads `~/.claude`
directly); projects are the cold part. This module turns that directory into a
DRY-RUN list of candidates. It registers nothing and never writes to `~/.claude`.

For each `~/.claude/projects/<encoded>/` it reads the real folder from the
`cwd` field of the newest transcript rather than decoding the directory name
(the encoding turns every non-alphanumeric into `-`, so `my-app` and `my_app`
and `my/app` are indistinguishable once encoded).

A candidate is kept only when its folder
  * still exists on disk,
  * is not already a registered project,
  * is not a Clayrune agent worktree (`.clayrune/agents/<id>`),
  * is not the Clayrune install itself (the same refusal "add project" makes),
  * is not the home directory or a filesystem root (the folder `claude` was
    launched from when it was used as a general chat, never a project).
`scan()` returns what it dropped as counts, so the UI can say "3 folders no
longer exist" instead of silently hiding them.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# A transcript line can carry a pasted image (megabytes). The `cwd` of a session
# is on its first message lines, so only the head of the file is ever read.
_HEAD_BYTES = 256 * 1024
# How many of a folder's newest transcripts to try before giving up on a cwd.
_MAX_TRANSCRIPTS_TRIED = 5
_CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
# Cap on the list returned to the browser; `total` still reports the full count.
MAX_CANDIDATES = 300


def default_claude_home() -> Path:
    """Claude Code's transcript store, via the one definition the runtime uses."""
    from mc.agent_runtime import _CLAUDE_HOME  # lazy: keep this module leaf-level
    return _CLAUDE_HOME


def _encode(path: str) -> str:
    """Claude Code's directory naming: every non-alphanumeric becomes `-`."""
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def _norm(path: str) -> str:
    """Comparison key for a folder: resolved, case-folded on Windows."""
    try:
        p = str(Path(path).resolve())
    except Exception:
        p = path
    p = p.rstrip('\\/') or p
    return p.lower() if os.name == 'nt' else p


def _cwds_in_head(transcript: Path) -> list:
    """Distinct `cwd` values in the first _HEAD_BYTES of a transcript, in order."""
    try:
        with open(transcript, 'rb') as fh:
            head = fh.read(_HEAD_BYTES).decode('utf-8', errors='replace')
    except OSError:
        return []
    seen: list = []
    for m in _CWD_RE.finditer(head):
        try:
            val = json.loads('"' + m.group(1) + '"')
        except ValueError:
            continue
        if isinstance(val, str) and val and val not in seen:
            seen.append(val)
    return seen


def _folder_cwd(dir_name: str, transcripts_newest_first: list) -> Optional[str]:
    """The folder a transcript dir belongs to, from the newest transcript that
    names one. A session can `cd` after it starts, so when several cwds appear
    the one whose encoding IS the directory name wins (that is the folder
    `claude` was launched in); otherwise the first one seen."""
    for t in transcripts_newest_first[:_MAX_TRANSCRIPTS_TRIED]:
        cwds = _cwds_in_head(t)
        if not cwds:
            continue
        for c in cwds:
            if _encode(c) == dir_name:
                return c
        return cwds[0]
    return None


def is_agent_worktree(path: str) -> bool:
    """`.clayrune/agents/<id>` anywhere in the path (either slash style)."""
    parts = [p.lower() for p in re.split(r'[\\/]+', path) if p]
    return any(parts[i] == '.clayrune' and parts[i + 1] == 'agents'
               for i in range(len(parts) - 1))


def _is_dir_root_or_home(path: str, home: Path) -> bool:
    key = _norm(path)
    if key == _norm(str(home)):
        return True
    try:
        p = Path(path).resolve()
    except Exception:
        return False
    return p == p.parent  # a filesystem root (C:\ or /)


def _slug(name: str) -> str:
    """Same rule as the add-project form's autoSlug()."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _unique_id(base: str, taken: set) -> str:
    base = base or 'project'
    cand, n = base, 2
    while cand in taken:
        cand = f'{base}_{n}'
        n += 1
    taken.add(cand)
    return cand


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def scan(*, claude_home: Optional[Path] = None,
         registered_paths: Iterable[str] = (),
         registered_ids: Iterable[str] = (),
         app_dir: Optional[Path] = None,
         home: Optional[Path] = None,
         allow_install_dir: bool = False) -> dict:
    """Dry run. Returns
    `{'candidates': [...newest first...], 'total': N, 'truncated': bool,
      'skipped': {'missing': n, 'registered': n, 'worktree': n,
                  'install_dir': n, 'home_or_root': n, 'no_cwd': n}}`.
    Each candidate: `path`, `name`, `id`, `last_activity` (ISO, UTC),
    `session_count`. Never raises on a bad directory: an unreadable one is
    counted under `no_cwd` and skipped."""
    from mc.core import path_is_within  # lazy: same reason as default_claude_home

    root = Path(claude_home) if claude_home is not None else default_claude_home()
    home = Path(home) if home is not None else Path.home()
    skipped = {'missing': 0, 'registered': 0, 'worktree': 0,
               'install_dir': 0, 'home_or_root': 0, 'no_cwd': 0}
    registered = {_norm(p) for p in registered_paths if p}
    taken_ids = set(registered_ids)
    # folder key -> candidate (two transcript dirs can name one folder: a path
    # reached by two spellings, or a symlink).
    found: dict = {}

    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        dirs = []

    for d in dirs:
        try:
            transcripts = [f for f in d.glob('*.jsonl') if f.is_file()]
            stamped = sorted(((f.stat().st_mtime, f) for f in transcripts),
                             key=lambda t: t[0], reverse=True)
        except OSError:
            skipped['no_cwd'] += 1
            continue
        if not stamped:
            continue  # no sessions recorded: nothing to import
        cwd = _folder_cwd(d.name, [f for _, f in stamped])
        if not cwd:
            skipped['no_cwd'] += 1
            continue
        if is_agent_worktree(cwd):
            skipped['worktree'] += 1
            continue
        if not os.path.isdir(cwd):
            skipped['missing'] += 1
            continue
        if _is_dir_root_or_home(cwd, home):
            skipped['home_or_root'] += 1
            continue
        if app_dir is not None and not allow_install_dir and path_is_within(cwd, app_dir):
            skipped['install_dir'] += 1
            continue
        key = _norm(cwd)
        if key in registered:
            skipped['registered'] += 1
            continue
        newest = stamped[0][0]
        prior = found.get(key)
        if prior:
            prior['session_count'] += len(stamped)
            prior['_ts'] = max(prior['_ts'], newest)
            continue
        found[key] = {'path': cwd, 'name': Path(cwd).name or cwd,
                      'session_count': len(stamped), '_ts': newest}

    ordered = sorted(found.values(), key=lambda c: c['_ts'], reverse=True)
    total = len(ordered)
    out = []
    for c in ordered[:MAX_CANDIDATES]:
        out.append({'path': c['path'], 'name': c['name'],
                    'id': _unique_id(_slug(c['name']), taken_ids),
                    'last_activity': _iso(c['_ts']),
                    'session_count': c['session_count']})
    return {'candidates': out, 'total': total,
            'truncated': total > len(out), 'skipped': skipped}
