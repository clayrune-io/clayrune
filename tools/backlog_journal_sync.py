"""Create or extend a backlog item's journal file without ever rewriting it.

MC-1179 TL14. The export used to build each journal from the item's notes and
overwrite the file. Journals are the append-only log unattended agents write to
and `docs/_journal/` is gitignored, so an overwrite destroyed the only copy of
whatever an agent had appended since the last export.

The contract here: a journal that exists is never rewritten. Notes whose
`(timestamp, agent_code)` heading is not already in the item's journal(s) are
appended at the end; every other byte stays as it was.
"""

import re
from collections import Counter

from backlog_journal_common import is_auto_note, journal_name

# "### <ts>  ·  <code>  ·  <kind>". The kind is deliberately not part of the
# key: it comes from marker rules that can change, and a changed rule must not
# make an already-exported note look new.
_HEADING = re.compile(r'^### (.*?)  ·  (.*?)  ·  (?:unattended|interactive)[ \t]*\r?$', re.M)


def _note_key(note):
    return (note.get('ts') or note.get('created') or '', note.get('agent_code') or 'user')


def _entry_lines(note):
    ts, code = _note_key(note)
    kind = 'unattended' if is_auto_note(note) else 'interactive'
    body = (note.get('text') or '').rstrip()
    lines = [f'### {ts}  ·  {code}  ·  {kind}', '', body, '']
    if len(note.get('text') or '') >= 1999:
        lines += ['> NOTE: this entry is exactly at the old 2000-byte backlog cap — '
                  'it was almost certainly truncated mid-sentence at write time and '
                  'the tail is not recoverable.', '']
    return lines


def render_journal(item):
    """The full text of a new journal file for `item` (which has notes)."""
    notes = item.get('notes') or []
    iid = item.get('id') or 'unknown'
    text = (item.get('text') or '').strip()
    title = ' '.join(text.split())[:90]
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
        lines += _entry_lines(n)
    return '\n'.join(lines)


def find_journals(out_dir, item):
    """Existing journal files of this item, found by id so a changed item text
    (and so a changed slug) never produces a second file. The file under the
    current derived name comes first, then the rest in name order."""
    iid = item.get('id') or 'unknown'
    exact = journal_name(item)
    found = [f for f in out_dir.glob('*.md')
             if f.name != 'INDEX.md' and f.stem.split('-', 1)[0] == iid]
    return sorted(found, key=lambda f: (f.name != exact, f.name))


def _append_bytes(existing, text):
    sep = b'\r\n' if b'\r\n' in existing else b'\n'
    if not existing or existing.endswith(sep + sep):
        prefix = b''
    elif existing.endswith(b'\n'):
        prefix = sep
    else:
        prefix = sep + sep
    return prefix + text.replace('\n', sep.decode()).encode('utf-8')


def sync_journal(out_dir, item):
    """Bring the item's journal up to date. Returns (path, outcome, appended).

    outcome: 'created' (no journal existed), 'appended' (new notes added to an
    existing one), 'unchanged', or 'none' when the item has no notes and no
    journal. Never raises for an existing file's content.
    """
    notes = item.get('notes') or []
    existing = find_journals(out_dir, item)
    if not existing:
        if not notes:
            return None, 'none', 0
        path = out_dir / journal_name(item)
        path.write_text(render_journal(item), encoding='utf-8')
        return path, 'created', len(notes)

    target = existing[0]
    # Union over every journal of the id: a note already in an older-slug file
    # must not be appended again to the one we extend.
    have = Counter()
    target_bytes = b''
    for f in existing:
        raw = f.read_bytes()
        if f == target:
            target_bytes = raw
        for m in _HEADING.finditer(raw.decode('utf-8', errors='replace')):
            have[(m.group(1), m.group(2))] += 1
    pending = []
    for n in notes:
        key = _note_key(n)
        if have[key] > 0:
            have[key] -= 1
        else:
            pending.append(n)
    if not pending:
        return target, 'unchanged', 0
    lines = []
    for n in pending:
        lines += _entry_lines(n)
    with open(target, 'ab') as fh:
        fh.write(_append_bytes(target_bytes, '\n'.join(lines)))
    return target, 'appended', len(pending)
