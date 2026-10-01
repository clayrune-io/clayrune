#!/usr/bin/env python
"""Count agent-log rows that are really Scribe/condense/Distiller one-shots.

The startup transcript backfill used to import every transcript in a project's
directory as a synthesized "chat" row. The toolless one-shots (Scribe, condense,
working-state, Phase 4 Distiller generators) run with cwd=project_path, so the
CLI files their transcripts there too -- and they are the NEWEST files, so they
filled the backfill's slots and then evicted real chats past
`agent_log_max_entries` (drop_shipping_company: 466 of 500 rows).

The backfill now skips them (server.py `_backfill_agent_log_from_transcripts`,
`exclude_transforms=True`). This reports how many ALREADY-IMPORTED rows are
one-shots, per project, i.e. what an exclusion would cover. READ-ONLY: it has no
write mode, and nothing in any log is deleted.

A row counts when it is `synthesized`, its transcript is found, and that
transcript is a transform (one user turn containing TRANSFORM_DATA_FENCE --
the same test `list_sessions` uses). `no-transcript` rows are counted
separately and NOT treated as one-shots.

    python tools/report-oneshot-log-rows.py
    python tools/report-oneshot-log-rows.py --project drop_shipping_company
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

from mc import agent_runtime as _art  # noqa: E402
from mc import memory as _memory      # noqa: E402


def data_dir() -> Path:
    return Path(os.environ.get('MC_DATA_DIR') or REPO_ROOT) / 'data' / 'projects'


def report(projects_dir: Path, project_id: str, cap: int) -> Counter:
    c: Counter = Counter()
    try:
        log = json.loads((projects_dir / f'{project_id}_agent_log.json').read_text(encoding='utf-8'))
        pp = json.loads((projects_dir / f'{project_id}.json').read_text(encoding='utf-8')).get('project_path', '')
    except Exception as e:
        print(f'  ! {project_id}: unreadable ({e})')
        c['unreadable'] += 1
        return c
    rt = _art.get_runtime('claude')
    c['rows'] = len(log)
    for row in log:
        if not isinstance(row, dict) or not row.get('synthesized'):
            continue
        c['synthesized'] += 1
        csid = row.get('claude_session_id') or ''
        f = _memory._find_transcript_file(pp, csid) if (pp and csid) else None
        if not f:
            c['no-transcript'] += 1
            continue
        try:
            is_transform = rt._session_row(f, f.stat().st_mtime).get('transform')  # pyright: ignore[reportAttributeAccessIssue]
        except OSError:
            c['no-transcript'] += 1
            continue
        c['oneshot' if is_transform else 'real-synthesized'] += 1
    c['rows-after-exclusion'] = c['rows'] - c['oneshot']
    c['at-cap'] = int(bool(cap and c['rows'] >= cap))
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', action='append', dest='projects')
    args = ap.parse_args()
    projects_dir = data_dir()
    ids = args.projects or sorted(
        f.name[:-len('_agent_log.json')] for f in projects_dir.glob('*_agent_log.json'))
    cap = 500
    try:
        cap = int(json.loads((REPO_ROOT / 'data' / 'config.json').read_text(
            encoding='utf-8')).get('agent_log_max_entries', 500) or 0)
    except Exception:
        pass
    print(f'READ-ONLY report over {projects_dir} (agent_log_max_entries={cap})\n')
    print(f'{"project":32s} {"rows":>5s} {"synth":>6s} {"oneshot":>8s} {"real-syn":>9s} {"no-tx":>6s}  at-cap')
    tot: Counter = Counter()
    for pid in ids:
        c = report(projects_dir, pid, cap)
        tot.update(c)
        if c['unreadable']:
            continue
        print(f'{pid:32s} {c["rows"]:5d} {c["synthesized"]:6d} {c["oneshot"]:8d} '
              f'{c["real-synthesized"]:9d} {c["no-transcript"]:6d}  {"yes" if c["at-cap"] else ""}')
    print(f'{"TOTAL":32s} {tot["rows"]:5d} {tot["synthesized"]:6d} {tot["oneshot"]:8d} '
          f'{tot["real-synthesized"]:9d} {tot["no-transcript"]:6d}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
