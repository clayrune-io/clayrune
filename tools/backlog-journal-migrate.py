#!/usr/bin/env python3
"""Move unattended-cycle notes off live backlog items and into their journals.

Run `tools/backlog-journal-export.py` FIRST — this deletes notes from the project
record and `data/projects/<id>.json` is untracked, so the journal file is the only
copy that survives.

Scope: live items only (open / in_progress / blocked). Done items keep their notes
— they are the historical record and nobody is reading them as a work list, so
rewriting 874 of them buys nothing and risks plenty.

Each item's unattended notes are replaced by ONE pointer note naming its journal
file. Interactive notes — anything a human or an attended session wrote — stay on
the item, because those are usually the item's current state rather than a log.

Writes `data/projects/<id>.json` directly rather than going through the API: the
`notes` field on PATCH landed in the same change as this script, and the running
server still holds the old code. `load_project` re-reads the file on every request
with no in-memory cache, so a direct write is picked up immediately. Takes a
timestamped backup first.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backlog_journal_common import is_auto_note, journal_name  # noqa: E402

LIVE = ('open', 'in_progress', 'blocked')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='mission_control')
    ap.add_argument('--data-dir', default='data/projects')
    ap.add_argument('--journal-dir', default='docs/_journal')
    ap.add_argument('--apply', action='store_true', help='without this, dry-run only')
    args = ap.parse_args()

    path = Path(args.data_dir) / f'{args.project}.json'
    proj = json.loads(path.read_text(encoding='utf-8'))
    jdir = Path(args.journal_dir)

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    moved_notes = moved_items = 0
    report = []

    for it in proj.get('backlog', []) or []:
        if it.get('status') not in LIVE:
            continue
        notes = it.get('notes') or []
        if not notes:
            continue
        auto = [n for n in notes if is_auto_note(n)]
        keep = [n for n in notes if not is_auto_note(n)]
        if not auto:
            continue

        jf = journal_name(it)
        if not (jdir / jf).exists():
            report.append(f'  SKIP {it["id"]}: journal {jf} missing — export first')
            continue

        keep.append({
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z',
            'agent_code': 'user',
            'text': (f'{len(auto)} unattended-cycle note(s) moved to '
                     f'docs/_journal/{jf} on 2026-08-15. Backlog items carry the ask '
                     f'and its current state; the running log lives in the journal. '
                     f'Unattended agents append THERE, never here — see AGENT_RULES.md.'),
        })
        report.append(f'  {it["id"]}: {len(auto)} moved, {len(keep) - 1} kept  ->  {jf}')
        moved_notes += len(auto)
        moved_items += 1
        if args.apply:
            it['notes'] = keep

    print('\n'.join(report) or '  (nothing to move)')
    print(f'\n{moved_items} items, {moved_notes} notes')

    if not args.apply:
        print('DRY RUN — re-run with --apply')
        return 0

    # NOT inside data/projects/: load_projects() treats every *.json there as a
    # project record, and CLAUDE.md flags that dir as load-bearing.
    bak = Path('_scratch') / f'{args.project}.json.bak-{stamp}'
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, bak)
    path.write_text(json.dumps(proj, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'applied. backup: {bak}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
