"""MC-964 T5 — what makes a delivered memory unit a Step D re-probe trigger.

Step D (`agent_routes._allowance_conflict_block` / `_newer_memory_unit_exists`)
re-probes a standing vendor-exhaustion record when a memory unit NEWER than
the record names that vendor. "Newer" used to mean one thing: a bracketed
`[YYYY-MM-DD]` in the hit's snippet, which is how an ARCHIVE line is dated.
A standing topic note is dated differently — `project_codex_allowance_topped_
up.md` writes its date as `**2026-09-17**` in the body, and its frontmatter
carries no date at all — so the one note written for the Codex incident could
never trigger the re-probe that would have broken it (Bram's diagnosis, T5,
journal b2d85e51).

This module reads a topic note's FRONTMATTER (never its prose) for two
signals, both opt-in by whoever wrote the note:

  * `holds_while: allowance(<vendor>[, <vendor>])` — the note declares that it
    is about that vendor's allowance state, so it counts for that vendor even
    if the snippet the read floor happened to cut never says the name. The
    existing grammar spelling `allowance_exhausted(<vendor>) >= 1` is accepted
    too, so one spelling cannot silently fail to count.
  * dated metadata — `updated:` / `modified:` / `decided:` / `date:`, at the
    top level or nested under `metadata:` (the vault's notes use both). The
    latest one is the note's date. A note that declares `holds_while` and has
    no dated metadata falls back to its file's mtime: an author who wrote the
    declaration has asserted relevance, and the last time the file changed is
    real evidence of when it was last true. A note that does NOT declare
    anything gets no mtime fallback — a bare mention is still not a trigger.

Pure functions plus one bounded file read; nothing here probes, records or
refuses. The caller keeps the "newer than the record" comparison and the
30s/vendor `heal()` throttle that bounds repeats. Flag:
`allowance_conflict_note_metadata_enabled` (default on; off restores the
bracketed-date-only behaviour exactly).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Set, Tuple

from mc import state
from mc.core import _log

CONFIG_KEY = 'allowance_conflict_note_metadata_enabled'

_FRONTMATTER_RE = re.compile(r'\A\s*---\s*\n(.*?)\n---\s*(?:\n|\Z)', re.DOTALL)
_HOLDS_WHILE_LINE_RE = re.compile(r'^[ \t]*holds_while:[ \t]*(.+?)[ \t]*$',
                                  re.MULTILINE)
_ALLOWANCE_ARG_RE = re.compile(r'allowance(?:_exhausted)?\(([^()]*)\)',
                               re.IGNORECASE)
_DATE_KEYS = ('updated', 'modified', 'decided', 'date')
_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')

# Containers that are not standing topic notes.
_NOT_NOTES = ('MEMORY.md', 'MEMORY_ARCHIVE.md', 'SESSION_LOG.md')


def enabled(config: Optional[dict] = None) -> bool:
    cfg = state.CONFIG if config is None else config
    return bool(cfg.get(CONFIG_KEY, True))


def _frontmatter(text: str) -> str:
    m = _FRONTMATTER_RE.match(text or '')
    return m.group(1) if m else ''


def declared_vendors(text: str) -> Set[str]:
    """Vendors a note's frontmatter declares via `holds_while: allowance(...)`."""
    out: Set[str] = set()
    for line in _HOLDS_WHILE_LINE_RE.findall(_frontmatter(text)):
        for arg in _ALLOWANCE_ARG_RE.findall(line):
            out.update(v.strip().lower() for v in arg.split(',') if v.strip())
    return out


def metadata_date(text: str) -> Optional[date]:
    """The latest `updated`/`modified`/`decided`/`date` in the frontmatter, or
    None. Matches the key at any indent so `metadata:` children count."""
    fm = _frontmatter(text)
    found = []
    for key in _DATE_KEYS:
        for m in re.finditer(rf'^[ \t]*{key}:[ \t]*["\']?(.*?)["\']?[ \t]*$',
                             fm, re.MULTILINE):
            dm = _DATE_RE.search(m.group(1))
            if not dm:
                continue
            try:
                found.append(datetime.strptime(dm.group(1), '%Y-%m-%d').date())
            except ValueError:
                continue
    return max(found) if found else None


def _read_note(mem_dir, filename) -> Optional[Tuple[str, float]]:
    """(text, mtime) of a topic note in `mem_dir`, or None. Refuses any name
    that is not a bare `*.md` filename, so a hit can never walk out of the
    memory dir."""
    name = str(filename or '')
    if (not name.endswith('.md') or name in _NOT_NOTES
            or '#' in name or '/' in name or '\\' in name or name.startswith('.')):
        return None
    try:
        path = Path(mem_dir) / name
        return path.read_text(encoding='utf-8', errors='replace'), path.stat().st_mtime
    except OSError:
        return None
    except Exception as e:
        _log(f'[allowance-conflict] note read failed for {name!r}: {e}')
        return None


def note_signals(hit: dict, mem_dir) -> Tuple[Set[str], Optional[date]]:
    """(declared vendors, date) for a delivered hit, or (set(), None) when the
    hit is not a readable topic note, the dir is unknown, or the flag is off.
    """
    if not mem_dir or not enabled():
        return set(), None
    if hit.get('cls') not in (None, 'topic'):
        return set(), None
    got = _read_note(mem_dir, hit.get('file'))
    if got is None:
        return set(), None
    text, mtime = got
    vendors = declared_vendors(text)
    when = metadata_date(text)
    if when is None and vendors:
        when = datetime.fromtimestamp(mtime, tz=timezone.utc).date()
    return vendors, when
