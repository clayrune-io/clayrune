#!/usr/bin/env python3
"""Standing-position review — the deterministic half of Dave phase 3.

The reviewing agent runs this to find out WHAT to check and to log what it
found. Deciding whether a condition has come true needs a model; deciding which
conditions are due does not, and a model doing that job would quietly review a
different set every night.

    # what is due, across every project (or one)
    python tools/position-review.py brief
    python tools/position-review.py brief --project mission_control

    # log an outcome after checking one
    python tools/position-review.py record --project mission_control \
        --file position_obsidian.md --tripped \
        --finding "Obsidian shipped an API that would replace _mem_link_key"

    # what is currently flagged and waiting on a human
    python tools/position-review.py flags

`record` prints `newly_flagged: true` exactly once per trip. That is the signal
to email Ron — a position that tripped last week and still trips today is not
news, and re-raising it nightly is how a channel stops being read.

The reviewer NEVER edits a position. It writes only its own sidecar. Changing a
ruling that steers every other agent is a human's call; see the authority guard
in `CLAUDE.md`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# NEVER `import server` here (MC-1179 TL30): it starts the tunnel autostart
# thread, whose orphan reaper kills the running server's connector. The records
# are read directly and mc.memory is wired by the server-free harness the
# memory-eval probes use (see tools/memory-eval/_harness.py).
sys.path.insert(0, str(REPO_ROOT / 'tools' / 'memory-eval'))
import _harness                                  # noqa: E402

_harness.wire(project_id='_position_review')
from mc import memory as _mem                    # noqa: E402
from mc import positions_review as pr            # noqa: E402
from mc.blueprints.project_routes import EXCLUDED_SIDECAR_SUFFIXES  # noqa: E402


def _projects_dir():
    root = Path(os.environ.get('MC_DATA_DIR') or REPO_ROOT)
    return root / 'data' / 'projects'


def load_project(pid):
    """One project record, or None (missing, unreadable or not an object)."""
    if not pid or Path(pid).name != pid:
        return None
    f = _projects_dir() / f'{pid}.json'
    if f.name.endswith(EXCLUDED_SIDECAR_SUFFIXES):
        return None
    try:
        rec = json.loads(f.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    rec.setdefault('id', pid)
    rec.setdefault('project_path', '')
    return rec


def load_projects():
    out = []
    for f in sorted(_projects_dir().glob('*.json')):
        if f.name.endswith(EXCLUDED_SIDECAR_SUFFIXES):
            continue
        rec = load_project(f.stem)
        if rec:
            out.append(rec)
    return out


def _projects(pid):
    if pid:
        p = load_project(pid)
        if not p:
            print(f'no such project: {pid}', file=sys.stderr)
            raise SystemExit(2)
        return [p]
    # Only projects that actually hold positions — a brief listing twenty
    # projects with nothing to review is a brief nobody finishes reading.
    out = []
    for p in load_projects():
        try:
            if _mem.list_positions(p):
                out.append(p)
        except Exception:
            continue
    return out


def cmd_brief(args):
    parts, total = [], 0
    for p in _projects(args.project):
        due = pr.positions_due(p, interval_days=args.interval)
        total += len(due)
        if due or args.project:
            parts.append(pr.render_brief(p, interval_days=args.interval))
    if not total:
        print('Nothing due. No positions have come round for review.')
        return 0
    print('\n\n'.join(parts))
    return 0


def cmd_record(args):
    p = load_project(args.project)
    if not p:
        print(f'no such project: {args.project}', file=sys.stderr)
        return 2
    rec = next((r for r in _mem.list_positions(p) if r.get('file') == args.file), None)
    if not rec:
        print(f'no such position in {args.project}: {args.file}', file=sys.stderr)
        return 2
    entry = pr.record_review(p, rec, tripped=args.tripped, finding=args.finding)
    print(json.dumps({'subject': rec.get('subject'), **entry}, indent=2))
    return 0


def cmd_flags(args):
    rows = []
    for p in _projects(args.project):
        for r in pr.open_flags(p):
            rows.append({'project': p.get('id'), 'file': r.get('file'),
                         'subject': r.get('subject'), 'verdict': r.get('verdict'),
                         'expires_when': r.get('expires_when'),
                         'finding': (r.get('_review') or {}).get('finding'),
                         'flagged_at': (r.get('_review') or {}).get('flagged_at'),
                         'contested': bool((r.get('_review') or {}).get('contested'))})
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print('No open flags. Every reviewed position still holds.')
    else:
        for r in rows:
            print(f"[{r['project']}] {r['subject']}"
                 + ('  (CONTESTED — latest review says it no longer holds)'
                    if r['contested'] else ''))
            print(f"    trigger : {r['expires_when'] or '(none recorded)'}")
            print(f"    finding : {r['finding']}")
            print(f"    since   : {(r['flagged_at'] or '')[:10]}   ({r['file']})")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('brief', help='what is due for review')
    b.add_argument('--project')
    b.add_argument('--interval', type=int, default=pr.DEFAULT_INTERVAL_DAYS,
                   help='days a position rests between reviews')
    b.set_defaults(fn=cmd_brief)

    r = sub.add_parser('record', help='log the outcome of checking one position')
    r.add_argument('--project', required=True)
    r.add_argument('--file', required=True, help='position_*.md filename')
    r.add_argument('--tripped', action='store_true',
                   help='the expiry condition appears to have come true')
    r.add_argument('--finding', default='', help='what you checked and what you saw')
    r.set_defaults(fn=cmd_record)

    f = sub.add_parser('flags', help='positions flagged and waiting on a human')
    f.add_argument('--project')
    f.add_argument('--json', action='store_true')
    f.set_defaults(fn=cmd_flags)

    args = ap.parse_args(argv)
    rc = args.fn(args)
    _harness.assert_no_server()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
