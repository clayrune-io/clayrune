"""Reviewing standing positions — the part that makes Dave wiser, not just older.

`DAVE_DESIGN.md` §9 phase 3. A position carries three things: a verdict, a
reason, and **what would change our mind**. That third field is the whole point
of reviewing being tractable — you do not re-read an archive looking for things
that might have gone stale, you test a small set of named conditions.

It also answers MC-898, which asked for a daily sweep of "what has changed in
the field". Those are one job, not two. An open-ended sweep of the field has no
stopping condition and no way to tell an interesting finding from a relevant
one; the standing positions supply the query set. "Has anything changed that
trips one of our own rulings" is a question with an answer.

THREE PROPERTIES THIS MODULE EXISTS TO GUARANTEE, all of them learned:

1. **The reviewer reports; it never edits.** An unattended agent that rewrites
   the rulings steering every other agent is the authority-guard violation
   wearing a different hat (`CLAUDE.md`, learning-system safety rails). Nothing
   here writes to a position note. `record_review` writes only to its own
   sidecar, and the sidecar cannot change what any prompt says.

2. **A flag is raised once, not every night.** `preference-5c17ba9d`: alerts on
   flat continuations train the reader to ignore them, which costs you the one
   night it mattered. A flag is keyed to the position's CONTENT hash, so it
   re-arms when a human edits the reason and stays quiet otherwise.

3. **The sidecar is JSON in the memory dir, deliberately.** Not under
   `DATA_DIR` — `load_projects()` treats every `*.json` there as a project and
   a stray one 500s both restart endpoints. The memory dir globs `*.md` for its
   corpus, so a `.json` beside the notes is inert.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mc import memory as _mem
from mc.core import _atomic_write_text, _log

# Beside the notes, not among them: `_mem_corpus` globs '*.md'.
REVIEW_STATE_FILE = 'position_review.json'

# How long a position rests between reviews. Nightly re-checking of a ruling
# that names a slow condition ("if the link layer rots") is pure cost — the
# nightly run picks up whatever has come due rather than re-testing everything.
DEFAULT_INTERVAL_DAYS = 7

# Two schedules can fire the same review cycle at once (MC-909). This module
# must be safe on its own rather than depend on that bug staying fixed.
_LOCK_TIMEOUT_S = 10
_LOCK_POLL_S = 0.05


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def position_hash(rec):
    """Identity of a position's CONTENT, which is what re-arms a cleared flag.

    Deliberately excludes `decided` and the body: re-saving a position without
    changing what it claims should not make the reviewer shout again, while
    editing the reason means a human has re-opened the question and the old
    verdict no longer covers it.
    """
    blob = '|'.join(str(rec.get(k) or '') for k in
                         ('subject', 'verdict', 'reason', 'expires_when', 'triggers'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def _state_path(project) -> Path:
    # A project dict with no resolvable memory path yields None here, which
    # used to surface as a TypeError on `/` inside the read path's catch-all —
    # readable only as "review state unreadable", which is not what went wrong.
    base = _mem._get_memory_path(project)
    if not base:
        raise ValueError(f'project {(project or {}).get("id")!r} has no memory path')
    return Path(base).parent / REVIEW_STATE_FILE


def _read_state_file(p: Path):
    """Load the sidecar at `p` directly. Never raises."""
    try:
        if not p.is_file():
            return {}
        d = json.loads(p.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log(f'[positions] review state unreadable: {e}')
        return {}


def read_state(project):
    """Review bookkeeping, keyed by position filename. Never raises."""
    return _read_state_file(_state_path(project))


@contextlib.contextmanager
def _locked_state(project):
    """Exclusive hold on the sidecar for a read-modify-write, yielding the
    freshly-read state dict to mutate in place before returning.

    Plain `os.O_CREAT | os.O_EXCL` rather than fcntl/msvcrt so the same code
    path works on every OS this project ships to, matching `_atomic_write_text`
    next door. A stale lock (a reviewer killed mid-write) is broken after
    `_LOCK_TIMEOUT_S` rather than wedging every future review forever — that
    failure mode is worse than the tiny window it reopens.
    """
    p = _state_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_path = p.parent / f'{p.name}.lock'
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() > deadline:
                try:
                    stale = (time.time() - lock_path.stat().st_mtime) > _LOCK_TIMEOUT_S
                except OSError:
                    stale = True
                if stale:
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    continue
                raise TimeoutError(f'positions_review sidecar lock busy: {lock_path}')
            time.sleep(_LOCK_POLL_S)
    try:
        os.close(fd)
        state = _read_state_file(p)
        yield state
        _atomic_write_text(p, json.dumps(state, indent=2, sort_keys=True) + '\n')
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def positions_due(project, interval_days=DEFAULT_INTERVAL_DAYS, now=None):
    """Positions whose turn it is, newest-ruling first.

    A position with no `expires_when` is still returned — it is *more* worth a
    look, not less. A ruling nobody wrote a trigger for is one nobody has
    thought about since the day it was made, and the reviewer's most useful
    output on it is "this needs a trigger" rather than a stale-check.
    """
    now = now or _now()
    state = read_state(project)
    cutoff = now - timedelta(days=max(0, int(interval_days or 0)))
    out = []
    for rec in _mem.list_positions(project):
        st = state.get(rec.get('file') or '') or {}
        last = _parse_iso(st.get('last_reviewed'))
        # An edited position is due immediately: its previous review judged
        # text that no longer exists.
        changed = st.get('hash') and st.get('hash') != position_hash(rec)
        if last and not changed and last > cutoff:
            continue
        out.append(dict(rec, _review=st, _changed=bool(changed),
                        _never_reviewed=last is None))
    return out


def should_flag(project, rec, tripped, state=None):
    """Whether this trip is NEW. Silence on a repeat is the feature.

    A position that tripped last week and still trips today is not news; the
    email about it was already sent and the answer is already in Ron's court.
    Re-raising it every night is exactly how a channel stops being read.

    `state` lets a caller that is already holding the sidecar (`record_review`,
    inside its lock) pass the freshly-read dict instead of triggering a second,
    unlocked read — the second read is what let a concurrent writer's update
    go unseen.
    """
    if not tripped:
        return False
    st = ((state if state is not None else read_state(project))
          .get(rec.get('file') or '') or {})
    if not st.get('flagged'):
        return True
    # Already flagged — only speak again if the position itself has changed,
    # which means a human touched it and the old flag no longer applies.
    return st.get('flagged_hash') != position_hash(rec)


def record_review(project, rec, tripped=False, finding='', now=None):
    """Log the outcome against this position. Writes ONLY the sidecar.

    Returns the entry as stored, including `newly_flagged` — the caller uses
    that to decide whether Ron hears about it, so the decision to interrupt a
    human lives in one place rather than at every call site.

    A flag raised for a human is cleared only by a human — by editing or
    forgetting the position, both of which change `position_hash` and are the
    one channel `should_flag` already treats as "the old flag no longer
    applies". An agent's own `tripped=False` on the SAME text is a disagreement,
    not a resolution: it cannot make the flag vanish, only record that the
    latest review contests it (`contested`). Without this, a flag Ron has
    already been emailed about goes silently missing from the report the next
    time anyone reviews it (MC-910) — and restoring it naively would then
    re-email him for a trip that was never actually news.
    """
    now = now or _now()
    fn = rec.get('file') or ''
    if not fn:
        raise ValueError('position has no file')
    h = position_hash(rec)
    with _locked_state(project) as state:
        prev = state.get(fn) or {}
        hash_changed = bool(prev.get('hash')) and prev.get('hash') != h
        prev_flagged = bool(prev.get('flagged'))
        new_flag = should_flag(project, rec, tripped, state=state)
        if tripped:
            flagged = True
        elif hash_changed:
            # A human edited (or Ron's own re-decision superseded) the
            # position since the last review — that IS the human response
            # a pending flag was waiting on. A fresh judgment on new text is
            # not this module erasing anything.
            flagged = False
        else:
            # Same text, agent says the condition no longer looks true. The
            # flag stays open for the human to close; contest it instead.
            flagged = prev_flagged
        contested = (not tripped) and (not hash_changed) and prev_flagged
        entry = {
            'hash': h,
            'last_reviewed': now.isoformat(),
            'tripped': bool(tripped),
            'finding': str(finding or '')[:2000],
            'flagged': flagged,
            'contested': contested,
            'flagged_hash': h if new_flag else prev.get('flagged_hash', ''),
            'flagged_at': now.isoformat() if new_flag else prev.get('flagged_at', ''),
        }
        state[fn] = entry
    return dict(entry, newly_flagged=new_flag)


def open_flags(project):
    """Positions currently flagged as possibly expired, for the digest."""
    state = read_state(project)
    out = []
    for rec in _mem.list_positions(project):
        st = state.get(rec.get('file') or '') or {}
        if st.get('flagged'):
            out.append(dict(rec, _review=st))
    return out


def render_brief(project, interval_days=DEFAULT_INTERVAL_DAYS, now=None):
    """The reviewing agent's worksheet: what to check, and how to check it.

    Deterministic on purpose. The model's job is judging whether a condition
    has come true, which needs a model; deciding WHICH conditions are due does
    not, and a model doing it would quietly review a different set each night.
    """
    due = positions_due(project, interval_days=interval_days, now=now)
    pid = (project or {}).get('id') or '?'
    if not due:
        return f'No positions due for review in {pid}.'
    lines = [f'{len(due)} position(s) due for review in {pid}.', '']
    for r in due:
        why = ('edited since its last review' if r.get('_changed')
               else 'never reviewed' if r.get('_never_reviewed')
               else f'last reviewed {(r.get("_review") or {}).get("last_reviewed", "?")[:10]}')
        lines.append(f'### {r.get("subject")}')
        lines.append(f'- file: `{r.get("file")}`  ({why})')
        lines.append(f'- verdict: **{r.get("verdict")}**, decided {r.get("decided") or "?"}')
        lines.append(f'- reason: {r.get("reason")}')
        exp = (r.get('expires_when') or '').strip()
        lines.append(f'- what would change our mind: '
                     + (exp if exp else '**NOTHING RECORDED — propose one**'))
        prev = (r.get('_review') or {}).get('finding')
        if prev:
            lines.append(f'- last finding: {prev}')
        if (r.get('_review') or {}).get('contested'):
            lines.append('- NOTE: the last review found the tripped condition no '
                         'longer holds, but the flag stays open — only a human '
                         'closes it (edit or forget the position)')
        lines.append('')
    return '\n'.join(lines)
