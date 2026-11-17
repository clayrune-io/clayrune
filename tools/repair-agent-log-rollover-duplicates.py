#!/usr/bin/env python
"""Remove the fake "interrupted" rows the old startup backfill made (MC-1179 S1).

Before S1 was fixed, the startup transcript backfill only knew a conversation's
CURRENT transcript. Every earlier transcript of a conversation that rolled over
(or resumed fresh) was re-imported as a synthesized `interrupted` row, evicting
real history from the capped log and queueing a bogus scribe call per row.
The fix stops new ones; this removes the ones already in the logs.

A row is removed only when ALL hold:
  - `synthesized` is true
  - it has no `session_id`
  - `status` is `interrupted`
  - its `claude_session_id` appears in the `claude_session_ids` of some OTHER,
    non-candidate row (read through `agent_log_file.known_transcript_ids`)

A real interrupted row, a synthesized row whose transcript no other row lists,
and the conversation's own row are never touched.

DRY RUN BY DEFAULT -- prints, per project, how many rows it would remove and
nothing from the rows' text. --apply takes the agent log's own lock
(`agent_log_file.transaction`, the one the server's writer holds), copies the
log to a dated backup beside it, then writes the trimmed list with the same
atomic writer the server uses. Idempotent.

    python tools/repair-agent-log-rollover-duplicates.py                    # dry run, all projects
    python tools/repair-agent-log-rollover-duplicates.py --project mission_control
    python tools/repair-agent-log-rollover-duplicates.py --project mission_control --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mc import agent_log_file                                        # noqa: E402
from mc.atomic_json import read_text_with_retry, write_json_atomic   # noqa: E402

LOG_SUFFIX = '_agent_log.json'


def data_dir() -> Path:
    """Same resolution as the other offline agent-log tools."""
    root = Path(os.environ.get('MC_DATA_DIR') or REPO_ROOT)
    return root / 'data' / 'projects'


def _is_candidate_shape(row) -> bool:
    return (isinstance(row, dict) and row.get('synthesized') is True
            and not row.get('session_id') and row.get('status') == 'interrupted')


def duplicate_indexes(rows: list) -> list[int]:
    """Indexes of rows that satisfy every removal condition."""
    protected = [r for r in rows if not _is_candidate_shape(r)]
    # Only the lineage list counts as "listed by another row": strip each
    # protected row's current-transcript key so known_transcript_ids reads
    # exactly `claude_session_ids`.
    listed = agent_log_file.known_transcript_ids(
        [{'claude_session_ids': r.get('claude_session_ids')}
         for r in protected if isinstance(r, dict)])
    return [i for i, r in enumerate(rows)
            if _is_candidate_shape(r) and r.get('claude_session_id') in listed]


def _backup_path(log_file: Path) -> Path:
    # Never ends in .json: load_projects() treats every *.json in this
    # directory as a project record.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return log_file.with_name(f'{log_file.name}.pre-rollover-cleanup-{stamp}')


def _read_rows(log_file: Path):
    rows = json.loads(read_text_with_retry(log_file))
    if not isinstance(rows, list):
        raise ValueError('not a list')
    return rows


def clean(log_file: Path, apply: bool) -> tuple[int, int]:
    """Returns (rows removed or removable, rows in the log). (0, 0) = skipped."""
    try:
        if not apply:
            rows = _read_rows(log_file)
            return len(duplicate_indexes(rows)), len(rows)
        with agent_log_file.transaction(log_file):
            rows = _read_rows(log_file)
            drop = set(duplicate_indexes(rows))
            if drop:
                shutil.copy2(log_file, _backup_path(log_file))
                write_json_atomic(log_file, [r for i, r in enumerate(rows) if i not in drop],
                                  indent=2, ensure_ascii=False)
            return len(drop), len(rows)
    except Exception as e:
        # A log we cannot read is never rewritten (MC-946).
        print(f'  ! {log_file.name} unreadable ({type(e).__name__}) -- SKIPPING, not rewriting')
        return 0, 0


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
    ids = args.projects or sorted(f.name[:-len(LOG_SUFFIX)]
                                  for f in projects_dir.glob(f'*{LOG_SUFFIX}'))
    if not ids:
        print('no agent logs found')
        return 0

    print(f'{"APPLY" if args.apply else "DRY RUN"} over {projects_dir}')
    total = 0
    for pid in ids:
        log_file = projects_dir / f'{pid}{LOG_SUFFIX}'
        if not log_file.is_file():
            print(f'{pid}: no agent log')
            continue
        n, size = clean(log_file, args.apply)
        total += n
        verb = 'removed' if args.apply else 'would remove'
        print(f'{pid}: {verb} {n} of {size} row(s)')
    print(f'\ntotal: {total}' + ('' if args.apply else ' -- DRY RUN, nothing written (pass --apply)'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
