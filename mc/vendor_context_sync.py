"""Mirror CLAUDE.md into a non-Claude runtime's own context filename.

Codex/Gemini/Qwen each load their OWN project-context file natively
(`RuntimeCapabilities.context_file_name` in mc/agent_runtime.py: AGENTS.md,
GEMINI.md, QWEN.md) instead of CLAUDE.md, which only Claude reads. Nothing
kept those mirrors in sync, so AGENTS.md drifted stale for weeks -- missing
the vault and unattended-backlog BINDING sections Ron added to CLAUDE.md --
until Dave hand-copied it again on 2026-09-25. This module regenerates the
mirror before a non-Claude session starts (mc/blueprints/agent_routes.py
`_dispatch_via_runtime`), keyed off a content hash so it is a cheap no-op
once the mirror is current.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable, Optional

MARKER_PREFIX = '<!-- clayrune:generated-from CLAUDE.md sha256='
MARKER_SUFFIX = ' -- edit CLAUDE.md, not this file -->'

# CLAUDE.md's own H1, as shipped in this repo's project-notes header. Swapped
# for a vendor-labelled title in the mirror; every other line is verbatim.
_H1_RE = re.compile(r'^# Clayrune — Claude Code project notes', re.MULTILINE)

_VENDOR_LABELS = {
    'AGENTS.md': 'Codex',
    'GEMINI.md': 'Gemini',
    'QWEN.md': 'Qwen',
}


def _vendor_label(context_file_name: str) -> str:
    return _VENDOR_LABELS.get(context_file_name, context_file_name[:-3])


def _rewritten_body(source_text: str, vendor_label: str) -> str:
    return _H1_RE.sub(f'# Clayrune — {vendor_label} project notes', source_text, count=1)


def sync_vendor_context_file(project_root: str, context_file_name: Optional[str],
                              *, log: Optional[Callable[[str], None]] = None) -> None:
    """Ensure `<project_root>/<context_file_name>` mirrors `CLAUDE.md`.

    No-op when `context_file_name` is falsy, not a `.md` name, or `CLAUDE.md`
    itself (the `.aider.conf.yml`/`None` runtimes and Claude's own flag-based
    injection never reach this). No-op when the project has no `CLAUDE.md`.

    Regeneration is keyed off `CLAUDE.md`'s sha256, stamped as the first line
    of the mirror -- unchanged hash is a read + compare, no write. A mirror
    with no marker line is treated as hand-authored and left untouched
    (logged once), UNLESS its body already equals CLAUDE.md's body with only
    the H1 rewritten -- that is this repo's one-time unmarked AGENTS.md
    (Dave's manual mirror, 2026-09-25), safe to adopt.
    """
    if not context_file_name or not context_file_name.endswith('.md'):
        return
    if context_file_name == 'CLAUDE.md':
        return
    root = Path(project_root)
    source = root / 'CLAUDE.md'
    if not source.is_file():
        return
    try:
        source_text = source.read_text(encoding='utf-8')
    except OSError as e:
        if log:
            log(f'[vendor-context-sync] read CLAUDE.md failed: {e}')
        return

    vendor_label = _vendor_label(context_file_name)
    expected_body = _rewritten_body(source_text, vendor_label)
    source_hash = hashlib.sha256(source_text.encode('utf-8')).hexdigest()

    target = root / context_file_name
    if target.is_file():
        try:
            target_text = target.read_text(encoding='utf-8')
        except OSError as e:
            if log:
                log(f'[vendor-context-sync] read {context_file_name} failed: {e}')
            return
        first_line = target_text.split('\n', 1)[0]
        if first_line.startswith(MARKER_PREFIX):
            existing_hash = (first_line[len(MARKER_PREFIX):-len(MARKER_SUFFIX)]
                              if first_line.endswith(MARKER_SUFFIX) else '')
            if existing_hash == source_hash:
                return  # already current
        elif target_text != expected_body:
            if log:
                log(f'[vendor-context-sync] {context_file_name} has no '
                    f'generated-from marker and differs from CLAUDE.md -- '
                    f'leaving it alone (hand-authored)')
            return
        # else: unmarked but byte-identical to CLAUDE.md's body -- one-time
        # adoption falls through to the write below, which attaches the marker.

    from mc.core import _atomic_write_text
    new_text = f'{MARKER_PREFIX}{source_hash}{MARKER_SUFFIX}\n{expected_body}'
    _atomic_write_text(target, new_text)
