"""Shared classification for the backlog-journal export + migrate pair.

Both scripts MUST agree on two things or the migration loses data: which notes
count as unattended, and what a given item's journal file is called. A note the
exporter calls interactive and the migrator calls unattended would be deleted
from the item without ever having been written to the journal.
"""

import re

# Notes an UNATTENDED cycle wrote — steward runs, night-review, campaign trackers.
# Matched on the note body, because `agent_code` cannot carry this on its own: of
# 3855 notes on mission_control only 36 are labelled ('steward' / 'night-review'),
# the rest are opaque per-session hashes shared by attended and unattended runs
# alike. The markers below are the prefixes those cycles actually used, taken
# from the live data rather than invented.
_AUTO_MARKERS = (
    re.compile(r'^\s*\[?\s*steward\b', re.I),
    re.compile(r'^\s*\[?\s*night[- ]review\b', re.I),
    re.compile(r'^\s*campaign\s+(pulse|tracker|\d)', re.I),
    re.compile(r'^\s*\(cont', re.I),          # a split continuation of one of the above
    re.compile(r'^\s*weekly\s+soak\s+check\b', re.I),
    re.compile(r'^\s*soak\s+result\b', re.I),
    re.compile(r'^\s*alternativeto\s+submitted\b', re.I),
    re.compile(r'^\s*awesome-selfhosted\b', re.I),
    re.compile(r'^\s*openalternative\b', re.I),
    re.compile(r'^\s*decision\s+needed\b', re.I),
)

_AUTO_CODES = {'steward', 'night-review'}


def is_auto_note(note):
    """True when this note was written by an unattended cycle."""
    if (note.get('agent_code') or '') in _AUTO_CODES:
        return True
    text = note.get('text') or ''
    return any(p.search(text) for p in _AUTO_MARKERS)


def slug(text, n=48):
    s = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return (s[:n].rstrip('-') or 'item')


def journal_name(item):
    """The journal filename for a backlog item. Stable across both scripts."""
    title = ' '.join((item.get('text') or '').split())[:90]
    return f'{item.get("id") or "unknown"}-{slug(title)}.md'
