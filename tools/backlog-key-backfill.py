#!/usr/bin/env python3
"""Assign backlog keys (MC-01) to every item in every project, in one pass.

The server already backfills lazily — `_ensure_backlog_numbers` runs on the
full `/backlog` GET — but lazily means "the first time someone opens that
project's backlog tab". A project nobody has opened since the feature landed
shows no keys at all, and the cross-project backlog view renders items from
several projects at once, so it goes half-keyed. This tool closes that gap in
one shot.

It calls the SAME `_ensure_backlog_numbers` the server does rather than
reimplementing the rules. A backfill script that derives keys its own way is
how two sources of truth start, and the first divergence would silently re-key
items — the one thing the design promises never happens.

Safe to re-run: the operation is idempotent by construction (a project that
already has a key keeps it; an item that already has a num keeps it).

    python tools/backlog-key-backfill.py            # report only, writes nothing
    python tools/backlog-key-backfill.py --apply    # write, after a backup
    python tools/backlog-key-backfill.py --apply --no-backup

STOP THE SERVER FIRST, or accept the race: this writes whole project records,
and so does the running server. A concurrent write on the same project loses
whichever finished first. --apply takes a timestamped backup of data/projects/
by default for exactly that reason.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mc.blueprints import project_routes as pr  # noqa: E402


def _resolve_data_dir(override: str | None) -> Path:
    """Where the project records actually live.

    `project_routes.DATA_DIR` is None until server.py calls wire(), and
    importing server for a batch script starts schedulers and reapers we do not
    want. Resolve it the same way server.py does instead: MC_DATA_DIR if set,
    else <repo root>/data/projects.

    The override matters more than it looks — this file can be run from a git
    WORKTREE, whose own data/projects is an empty shadow of the real one. A
    silent backfill of nothing looks exactly like success.
    """
    if override:
        return Path(override).expanduser().resolve()
    import os
    root = Path(os.environ['MC_DATA_DIR']) if os.environ.get('MC_DATA_DIR') else REPO_ROOT
    return root / 'data' / 'projects'


def _project_files():
    """Every real project record. Mirrors load_projects()' sidecar exclusion —
    a `*_agent_log.json` treated as a project would get a backlog key and a
    malformed record, and DATA_DIR pollution 500s the restart endpoints."""
    for f in sorted(pr.DATA_DIR.glob('*.json')):
        if f.name.endswith(pr.EXCLUDED_SIDECAR_SUFFIXES):
            continue
        yield f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true',
                    help='write the changes (default is a dry run)')
    ap.add_argument('--no-backup', action='store_true',
                    help='skip the data/projects backup that --apply takes')
    ap.add_argument('--data-dir', default=None,
                    help='project records dir (default: <repo>/data/projects, '
                         'or $MC_DATA_DIR/data/projects). Pass the MAIN '
                         'checkout when running from a worktree.')
    args = ap.parse_args()

    pr.DATA_DIR = _resolve_data_dir(args.data_dir)
    if not pr.DATA_DIR.exists():
        print(f'data dir not found: {pr.DATA_DIR}', file=sys.stderr)
        return 2
    print(f'data dir: {pr.DATA_DIR}\n')

    # Load everything FIRST. Key derivation is collision-aware, and it can only
    # avoid a collision against keys it can see — assigning one project at a
    # time off disk would let two projects both claim MC before either is saved.
    records = []
    for f in _project_files():
        try:
            rec = json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'  ! skipping {f.name}: unreadable ({e})')
            continue
        if not isinstance(rec, dict):
            # Same guard load_projects() uses. DATA_DIR is supposed to hold
            # nothing but project records, but a stray list lands here often
            # enough that the server checks too — see the DATA_DIR pollution
            # rule in CLAUDE.md.
            print(f'  ! skipping {f.name}: not a project record '
                  f'({type(rec).__name__}) — this file does not belong in DATA_DIR')
            continue
        records.append((f, rec))
    if not records:
        print('no project records found.')
        return 0

    taken = {str(p.get('backlog_key')).upper() for _, p in records if p.get('backlog_key')}

    if args.apply and not args.no_backup:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        dest = pr.DATA_DIR.parent / f'projects_backup_{stamp}'
        dest.mkdir(parents=True, exist_ok=True)
        # Copy the records we might rewrite, file by file — NOT copytree.
        # DATA_DIR accumulates strays (this machine had a zero-byte file
        # literally named `nul`, a Windows reserved device name), and a single
        # uncopyable entry aborts a whole-tree copy — taking the backup, and
        # therefore the backfill, down with it.
        for _f, _ in records:
            shutil.copy2(_f, dest / _f.name)
        print(f'backup: {dest} ({len(records)} records)\n')

    total_items = total_new = 0
    rows = []
    for f, p in records:
        pid = p.get('id') or f.stem
        backlog = p.get('backlog') or []
        before_keyed = sum(1 for i in backlog if i.get('key'))

        # Derive here rather than letting _ensure_backlog_key hit the store:
        # it reads sibling keys off DISK, which in a dry run is a set that
        # never grows, so every unkeyed project would derive against the same
        # snapshot and two could pick the same prefix.
        if not p.get('backlog_key'):
            p['backlog_key'] = pr._derive_backlog_key(p.get('name') or pid, taken)
        taken.add(str(p['backlog_key']).upper())

        changed = pr._ensure_backlog_numbers(p)
        after_keyed = sum(1 for i in backlog if i.get('key'))
        new = after_keyed - before_keyed
        total_items += len(backlog)
        total_new += new

        rows.append((p['backlog_key'], pid, len(backlog), new, changed))
        if changed and args.apply:
            f.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding='utf-8')

    w = max((len(r[0]) for r in rows), default=3)
    print(f'{"KEY".ljust(w)}  {"PROJECT".ljust(26)}  ITEMS   NEW  RANGE')
    for key, pid, n, new, changed in rows:
        rng = f'{key}-01 … {pr._format_backlog_key(key, n)}' if n else '—'
        print(f'{key.ljust(w)}  {pid[:26].ljust(26)}  {n:>5}  {new:>4}  {rng}')

    print(f'\n{len(rows)} projects, {total_items} items, {total_new} newly keyed.')
    if not args.apply:
        print('DRY RUN — nothing written. Re-run with --apply.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
