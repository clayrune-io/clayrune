#!/usr/bin/env python3
"""Report dangling `[[wikilinks]]`, slug drift, and orphan notes in a memory vault.

The read floor traverses one wikilink hop off every BM25 hit
(`mc.memory._mem_expand_links`), so a link that resolves is a retrieval edge and
a link that doesn't is dead weight. Obsidian renders a broken link red on sight;
nothing here does, which is how 6 of them survived unnoticed until 2026-08-09.
This is that missing red.

Usage:
    python tools/memory-link-check.py                     # this project's vault
    python tools/memory-link-check.py --project <id>
    python tools/memory-link-check.py --dir <path>
    python tools/memory-link-check.py --strict            # exit 1 if dangling

Read-only. Never edits a note.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mc.memory import _mem_link_key, _mem_link_targets  # noqa: E402

_INDEX_FILES = {'MEMORY.md', 'MEMORY_ARCHIVE.md'}


def _default_dir(project_id):
    """Resolve a project's memory dir the same way the server does."""
    import json
    import os
    from mc import memory as _mem
    root = pathlib.Path(os.environ.get('MC_DATA_DIR')
                        or pathlib.Path(__file__).resolve().parents[1])
    pfile = root / 'data' / 'projects' / f'{project_id or "mission_control"}.json'
    if not pfile.exists():
        raise SystemExit(f'no such project record: {pfile}')
    proj = json.loads(pfile.read_text(encoding='utf-8'))
    # mc.memory's path constants are late-bound by server.wire(); this script
    # never boots the server, so bind the two it needs.
    _mem.MEMORY_DIR = root / 'data' / 'memory'
    _mem.CLAUDE_HOME = pathlib.Path.home() / '.claude' / 'projects'
    return _mem._get_memory_path(proj).parent


def scan(mem_dir):
    """Return (files, links, name_fields) for a vault directory."""
    files = sorted(p for p in mem_dir.glob('*.md'))
    links, names = {}, {}
    for p in files:
        txt = p.read_text(encoding='utf-8', errors='replace')
        links[p.name] = _mem_link_targets(txt)
        m = re.search(r'^name:\s*(.+)$', txt, re.M)
        if m:
            names[p.name] = m.group(1).strip()
    return files, links, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=None)
    ap.add_argument('--dir', default=None)
    ap.add_argument('--strict', action='store_true',
                    help='exit 1 when a dangling link exists')
    a = ap.parse_args()

    mem_dir = pathlib.Path(a.dir) if a.dir else _default_dir(a.project)
    if not mem_dir.is_dir():
        raise SystemExit(f'not a directory: {mem_dir}')
    files, links, names = scan(mem_dir)

    by_key = {}
    for p in files:
        if p.name in _INDEX_FILES:
            continue
        by_key.setdefault(_mem_link_key(p.stem), []).append(p.name)

    collisions = {k: v for k, v in by_key.items() if len(v) > 1}
    dangling, resolved = {}, 0
    inbound = {p.name: 0 for p in files if p.name not in _INDEX_FILES}
    for src, tgts in links.items():
        for t in tgts:
            hit = by_key.get(_mem_link_key(t))
            if not hit:
                dangling.setdefault(t, []).append(src)
            else:
                resolved += 1
                inbound[hit[0]] = inbound.get(hit[0], 0) + 1

    drift = {f: n for f, n in names.items()
             if _mem_link_key(n) != _mem_link_key(pathlib.Path(f).stem)}
    orphans = sorted(f for f, c in inbound.items() if c == 0)

    total = resolved + sum(len(v) for v in dangling.values())
    print(f'vault: {mem_dir}')
    print(f'notes: {len(inbound)}   links: {total} '
          f'({resolved} resolved, {sum(len(v) for v in dangling.values())} dangling)')

    if collisions:
        print(f'\nSLUG COLLISIONS ({len(collisions)}) — two notes share one link key:')
        for k, v in sorted(collisions.items()):
            print(f'  {k}: {", ".join(v)}')

    if dangling:
        print(f'\nDANGLING ({len(dangling)} unique targets):')
        for t, srcs in sorted(dangling.items()):
            print(f'  [[{t}]]  <- {", ".join(sorted(set(srcs)))}')

    if drift:
        print(f'\nFRONTMATTER name: != filename stem ({len(drift)}):')
        for f, n in sorted(drift.items()):
            print(f'  {f:<52} name={n!r}')

    print(f'\nORPHANS — no inbound link ({len(orphans)}/{len(inbound)}):')
    for f in orphans:
        print(f'  {f}')

    if a.strict and (dangling or collisions):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
