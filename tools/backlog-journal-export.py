#!/usr/bin/env python3
"""Export backlog-item notes into per-item journal files.

WHY THIS EXISTS (2026-08-15, Ron's call). Unattended agents — steward cycles,
night-review, campaign trackers — had been using backlog notes as their durable
log. Measured on mission_control that day: 16 KB of actual task text against
132 KB of notes on the 28 live items, and 1.24 MB across all 902.

That is not just untidy, it silently destroys data. `_append_note_to_backlog_item`
truncates every note at `text[:2000]` and keeps only `notes[-50:]`, both without
a word to the caller. The steward charter item was sitting at exactly 50 notes
with 24 of them cut mid-sentence; its oldest surviving note was 2026-08-04 though
the charter began 2026-07-11, and `data/projects/<id>.json` is untracked, so
roughly three weeks of findings were already unrecoverable. Worse, the steward
had NOTICED the truncation and started splitting long findings into "(cont.)"
notes — which burns extra slots out of the same 50 and made the eviction run
faster.

The rule now: a backlog item carries the ask and its current state; an agent's
running log goes in a journal file named by the item. This script does the
one-time extraction, and stays around so the same export can be re-run.

Output: <out-dir>/<item-id>-<slug>.md, one per item that has notes, plus an
INDEX.md. Read-only against the live server — it never mutates the backlog.
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backlog_journal_common import is_auto_note, journal_name  # noqa: E402

DEFAULT_HOST = 'http://localhost:5199'

def fetch(host, project_id):
    url = f'{host}/api/project/{project_id}/backlog'
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    return data if isinstance(data, list) else (data.get('backlog') or data.get('items') or [])


def write_journal(out_dir, item):
    notes = item.get('notes') or []
    if not notes:
        return None
    iid = item.get('id') or 'unknown'
    text = (item.get('text') or '').strip()
    title = ' '.join(text.split())[:90]
    path = out_dir / journal_name(item)

    lines = [
        f'# {title}',
        '',
        f'- Backlog item: `{iid}`  ({item.get("priority") or "normal"} / {item.get("status") or "open"})',
        f'- Notes: {len(notes)}  ({sum(1 for n in notes if is_auto_note(n))} written by unattended cycles)',
        '',
        'Append-only log for this item. Unattended agents write HERE, never to the',
        'backlog note API — see AGENT_RULES.md. Newest entries go at the bottom.',
        '',
        '## Item text at export',
        '',
        '```',
        text,
        '```',
        '',
        '## Log',
        '',
    ]
    for n in notes:
        ts = n.get('ts') or n.get('created') or ''
        code = n.get('agent_code') or 'user'
        kind = 'unattended' if is_auto_note(n) else 'interactive'
        body = (n.get('text') or '').rstrip()
        lines += [f'### {ts}  ·  {code}  ·  {kind}', '', body, '']
        if len(n.get('text') or '') >= 1999:
            lines += ['> NOTE: this entry is exactly at the old 2000-byte backlog cap — '
                      'it was almost certainly truncated mid-sentence at write time and '
                      'the tail is not recoverable.', '']

    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='mission_control')
    ap.add_argument('--host', default=DEFAULT_HOST)
    ap.add_argument('--out', default='docs/_journal')
    ap.add_argument('--live-only', action='store_true',
                    help='export only open/in_progress/blocked items')
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    items = fetch(args.host, args.project)
    if args.live_only:
        items = [i for i in items if i.get('status') in ('open', 'in_progress', 'blocked')]

    written, agent_written, index = 0, 0, []
    for it in items:
        p = write_journal(out_dir, it)
        if p:
            written += 1
            n = it.get('notes') or []
            index.append((it.get('status') or '', it.get('id') or '', len(n), p.name,
                          ' '.join((it.get('text') or '').split())[:70]))

    # Journals an AGENT wrote directly. This is the documented happy path —
    # AGENT_RULES.md tells unattended cycles to append to
    # docs/_journal/<item-id>-<slug>.md and points at INDEX.md as the map of
    # "every item to its file". Indexing only the items that happen to carry
    # legacy backlog NOTES left exactly the agent-authored journals invisible,
    # which is the opposite of the intent.
    by_id = {(i.get('id') or ''): i for i in items}
    seen = {r[3] for r in index}
    for f in sorted(out_dir.glob('*.md')):
        if f.name in ('INDEX.md',) or f.name in seen:
            continue
        iid = f.stem.split('-', 1)[0]
        it = by_id.get(iid) or {}
        title = ' '.join((it.get('text') or '').split())[:70]
        if not title:
            # An orphan: the item was deleted, or this file predates it. Still
            # list it — an unindexed journal is one nobody reads.
            first = next((ln.strip('# ').strip()
                          for ln in f.read_text(encoding='utf-8', errors='replace')
                                     .splitlines() if ln.strip()), f.stem)
            title = first[:70]
        index.append((it.get('status') or '—', iid, 0, f.name, title))
        agent_written += 1

    index.sort(key=lambda r: -r[2])
    idx = ['# Backlog journals', '',
           f'Exported from `{args.project}` — {written} items with notes '
           f'({sum(r[2] for r in index)} entries), plus {agent_written} '
           f'agent-written journal(s).', '',
           'One file per backlog item. The item itself carries the ask and its current',
           'state; this is where the running log lives. Unattended agents append here.',
           'A `0` in the notes column means the journal was written directly by an',
           'agent rather than exported from legacy backlog notes — that is the',
           'intended path, not a gap.',
           '',
           '| notes | status | item | file |', '|---:|---|---|---|']
    for status, iid, n, fname, title in index:
        idx.append(f'| {n} | {status} | `{iid}` {title} | [{fname}]({fname}) |')
    (out_dir / 'INDEX.md').write_text('\n'.join(idx) + '\n', encoding='utf-8')

    print(f'wrote {written} journal files (+{agent_written} agent-written already on disk) + INDEX.md to {out_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
