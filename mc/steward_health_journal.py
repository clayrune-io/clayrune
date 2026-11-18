"""What the steward health panel reads (review 2026-11-16, P65).

``steward.core.loop_health()`` used to count ``DECISION NEEDED`` / ``BLOCKED``
backlog notes on the charter item and take "last activity" from the newest note.
An unattended steward must never write backlog notes (AGENT_RULES.md, CLAUDE.md
"unattended agents never write backlog notes"), so a steward that obeys the rule
showed zero decisions and no activity while healthy. This module reads the
sources a rule-following steward actually writes:

* last activity  = the newest ``## <stamp> | <event>`` heading in the steward's
  journal (the role's ``journal_path``, or ``docs/_journal/<charter>-steward.md``
  for a steward with no role, the name ``mc.steward_role_task`` derives);
* pending decisions = decisions in ``OPEN_STATUS`` in the role state ledger, so an
  answered decision is retired;
* blocked = the ``blocked_*`` and ``decision_blocked`` events that are still
  current: those after the newest ``completed`` heading. ``completed`` is the
  lifecycle event the role dispatcher writes when it confirms the previous cycle's
  session closed (it also sets ``last_completed_at`` in the ledger), so a block
  followed by a cycle that got through no longer counts. A journal with no
  ``completed`` heading yet counts only blocks from the last 7 days.

A steward that still writes notes and has no journal yet keeps the note-based
numbers. A source that is missing where it should exist, or unreadable, is logged
and reported in ``unknown`` and is never turned into a healthy zero.

``project_health`` never raises. Pure reading; it writes nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable

from mc.core import _log
from mc.steward_role_model import RoleError, assignment, identifier, journal_path
from mc.steward_role_state import OPEN_STATUS, RoleStateStore, StateError

# The writer (steward_role_comms.append_journal) emits '## <iso stamp> | <event>'
# and escapes a body line that starts with '#', so a heading at line start is real.
_HEADING = re.compile(r'^## (?P<stamp>\S+)(?: \| (?P<event>[a-z_]+))?')
_BLOCKED_EVENTS = frozenset({'decision_blocked'})
_CYCLE_DONE_EVENT = 'completed'
_BLOCKED_WINDOW = timedelta(days=7)   # only used while no cycle has completed yet


def _is_blocked(event: str) -> bool:
    return event.startswith('blocked_') or event in _BLOCKED_EVENTS


def _when(value: Any) -> datetime | None:
    """A timestamp as an aware datetime, or None. Naive values are read as local."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _note_numbers(notes: Iterable[Any]) -> tuple[int, int, str | None]:
    """Today's rule, unchanged: (DECISION NEEDED count, BLOCKED count, last note ts)."""
    rows = [n for n in notes if isinstance(n, dict)]
    pend = sum(1 for n in rows if str(n.get('text', '')).startswith('DECISION NEEDED'))
    blk = sum(1 for n in rows if str(n.get('text', '')).startswith('BLOCKED'))
    return pend, blk, (rows[-1].get('ts') if rows else None)


def _read_journal(path: Path) -> tuple[str | None, int]:
    """(newest heading stamp, current blocked-event count). Raises OSError/UnicodeError."""
    last: str | None = None
    last_dt: datetime | None = None
    blocked: list[datetime | None] = []   # blocked events since the last completed cycle
    saw_completed = False
    with open(path, encoding='utf-8', errors='strict') as fh:
        for line in fh:
            m = _HEADING.match(line)
            if not m:
                continue
            event = m.group('event') or ''
            dt = _when(m.group('stamp'))
            if event == _CYCLE_DONE_EVENT:
                saw_completed, blocked = True, []
            elif _is_blocked(event):
                blocked.append(dt)
            if dt is not None and (last_dt is None or dt >= last_dt):
                last, last_dt = m.group('stamp'), dt
    if last is None:
        # A journal with no parsable heading is still evidence the steward wrote.
        last = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec='seconds')
    if saw_completed:
        return last, len(blocked)
    cutoff = datetime.now(timezone.utc) - _BLOCKED_WINDOW
    return last, sum(1 for dt in blocked if dt is None or dt >= cutoff)


def _schedule_rows(data_root: Path | None) -> list[dict]:
    if data_root is None:
        raise ValueError('data root is not configured')
    path = Path(data_root) / 'schedules.json'
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('schedules file is not a list')
    return [r for r in rows if isinstance(r, dict)]


def project_health(project: dict, charter: dict | None, notes: Iterable[Any],
                   data_root: Path | str | None) -> dict[str, Any]:
    """{'decisions_pending', 'blocked', 'last_note_ts', 'activity_source', 'unknown'}.

    ``activity_source`` is 'journal', 'notes' or 'none'. ``unknown`` lists what
    could not be read; the numeric fields then hold only what was readable.
    """
    pend, blk, last_note = _note_numbers(notes or [])
    unknown: list[str] = []
    root = Path(data_root) if data_root is not None else None
    pid = str((project or {}).get('id') or '')
    j_last: str | None = None
    j_blocked = 0
    j_present = False
    try:
        row = None          # the role schedule row, when the project is role-bound
        journal: Path | None = None
        if project.get('steward_assignment') is not None:
            try:
                row = assignment(project, _schedule_rows(root))
            except (RoleError, OSError, ValueError) as exc:
                _log(f'[steward-health] {pid}: role assignment unreadable: {exc}')
                unknown.append(f'role assignment unreadable ({type(exc).__name__})')
            if row is not None:
                try:
                    journal = journal_path(project, row['steward_role'].get('journal_path'))
                except (RoleError, KeyError, AttributeError, TypeError) as exc:
                    _log(f'[steward-health] {pid}: role journal path invalid: {exc}')
                    unknown.append('role journal path invalid')
        elif charter and charter.get('id') and str(project.get('project_path') or '').strip():
            # (A project with no registered path can never have a journal: not unknown.)
            try:
                cid = identifier(charter['id'], 'charter_item_id')
                journal = journal_path(project, f'docs/_journal/{cid}-steward.md')
            except RoleError as exc:
                _log(f'[steward-health] {pid}: journal path not resolvable: {exc}')
                unknown.append('journal path not resolvable')

        if journal is not None and journal.exists():
            try:
                j_last, j_blocked = _read_journal(journal)
                j_present = True
            except (OSError, UnicodeError) as exc:
                _log(f'[steward-health] {pid}: journal {journal.name} unreadable: {exc}')
                unknown.append(f'journal unreadable ({type(exc).__name__})')

        if row is not None and root is not None:
            try:
                state = RoleStateStore(root).read(pid, row['id'])
                pend = sum(1 for d in state['decisions'] if d.get('status') in OPEN_STATUS)
            except (StateError, RoleError, KeyError, TypeError) as exc:
                _log(f'[steward-health] {pid}: role ledger unreadable: {exc}')
                unknown.append('decision ledger unreadable')
        # A steward with no role has no ledger: that is a normal configuration, not a
        # failure, so it adds nothing to `unknown`; its pending count stays the
        # note-based one set above.
    except Exception as exc:   # never let a panel read break the endpoint
        _log(f'[steward-health] {pid}: health read failed: {exc}')
        unknown.append(f'health read failed ({type(exc).__name__})')
        j_last, j_blocked, j_present = None, 0, False

    blk += j_blocked
    candidates = [(_when(j_last), j_last, 'journal') if j_present else (None, None, 'journal'),
                  (_when(last_note), last_note, 'notes')]
    dated = [c for c in candidates if c[0] is not None]
    if dated:
        _, last_ts, source = max(dated, key=lambda c: c[0])
    else:
        last_ts, source = None, 'none'
    return {'decisions_pending': pend, 'blocked': blk, 'last_note_ts': last_ts,
            'activity_source': source, 'unknown': unknown}
