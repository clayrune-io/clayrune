#!/usr/bin/env python
"""Put the faces back on agent-log rows that lost them (MC-946).

A chat's persona lives ONLY on its agent-log row. When a truncated log read
back as empty, the startup transcript backfill replaced the real rows with
synthesized ones that carry no `character` at all -- so every conversation
with a hired agent rendered as the project's default agent instead.

The transcripts survived, and they still contain the persona marker MC
injected ("Your name is Dave. Use it when you introduce yourself or sign
off"). This reads that back and stamps the resolved {name, scope} onto rows
that have no character.

DRY RUN BY DEFAULT -- prints what it would change and writes nothing. Pass
--apply to write. Idempotent: a row that already has a character is never
touched, so re-running only picks up what it missed last time.

    python tools/repair-agent-log-personas.py                  # all projects, dry run
    python tools/repair-agent-log-personas.py --project mission_control
    python tools/repair-agent-log-personas.py --project mission_control --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mc import memory as _memory              # noqa: E402
from mc.atomic_json import write_json_atomic  # noqa: E402


def data_dir() -> Path:
    """Same resolution server.py uses: MC_DATA_DIR, else the repo."""
    root = Path(os.environ.get('MC_DATA_DIR') or REPO_ROOT)
    return root / 'data' / 'projects'


def project_path_for(projects_dir: Path, project_id: str) -> str:
    rec = projects_dir / f'{project_id}.json'
    try:
        return json.loads(rec.read_text(encoding='utf-8')).get('project_path', '')
    except Exception as e:
        print(f'  ! cannot read {rec.name}: {e}')
        return ''


def repair(projects_dir: Path, project_id: str, apply: bool) -> Counter:
    counts: Counter = Counter()
    log_file = projects_dir / f'{project_id}_agent_log.json'
    try:
        log = json.loads(log_file.read_text(encoding='utf-8'))
    except Exception as e:
        # The same rule the backfill now follows: a log we could not read is
        # never rewritten. Repairing rows we cannot see would drop them.
        print(f'  ! {log_file.name} does not parse ({e}) -- SKIPPING, not rewriting')
        counts['unreadable'] += 1
        return counts
    if not isinstance(log, list):
        print(f'  ! {log_file.name} is not a list -- skipping')
        counts['unreadable'] += 1
        return counts

    pp = project_path_for(projects_dir, project_id)
    if not pp:
        print('  ! no project_path on the record -- cannot locate transcripts')
        return counts

    changed = 0
    for row in log:
        if not isinstance(row, dict) or row.get('character'):
            continue
        csid = (row.get('claude_session_id') or '').strip()
        if not csid:
            counts['no-session-id'] += 1
            continue
        ref = _memory.persona_ref_for_session(pp, csid)
        if not ref:
            counts['no-persona'] += 1
            continue
        row['character'] = ref
        counts[f"{ref['scope']}:{ref['name']}"] += 1
        changed += 1

    if changed and apply:
        write_json_atomic(log_file, log, indent=2, ensure_ascii=False)
        print(f'  wrote {log_file.name} ({changed} row(s) stamped)')
    elif changed:
        print(f'  would stamp {changed} row(s) -- DRY RUN, nothing written '
              f'(pass --apply)')
    else:
        print('  nothing to stamp')
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', action='append', dest='projects',
                    help='project id (repeatable). Default: every project with a log.')
    ap.add_argument('--apply', action='store_true',
                    help='actually write. Without this the run is a dry run.')
    args = ap.parse_args()

    projects_dir = data_dir()
    if not projects_dir.is_dir():
        print(f'no such data dir: {projects_dir}')
        return 1

    ids = args.projects or sorted(
        f.name[:-len('_agent_log.json')]
        for f in projects_dir.glob('*_agent_log.json'))
    if not ids:
        print('no agent logs found')
        return 0

    print(f'{"APPLY" if args.apply else "DRY RUN"} over {projects_dir}')
    total: Counter = Counter()
    for pid in ids:
        print(f'\n{pid}:')
        total.update(repair(projects_dir, pid, args.apply))

    print('\nper-persona totals:')
    for key, n in sorted(total.items()):
        print(f'  {key:40s} {n}')
    if not args.apply and any(k not in ('no-persona', 'no-session-id', 'unreadable')
                              for k in total):
        print('\nnothing was written. re-run with --apply to keep these.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
