"""MC-964 Step E, report mode: classify each human message AT SEND TIME.

`mc.memory._scribe_classify_habits` only ever runs from the session-finish
Scribe (`_scribe_extract`), which a chat that rolls for days never reaches
(docs/_journal/b2d85e51-memory-retrieval-diagnosis.md, Bram 2026-10-15: 0
production mints; the 3 real habit-cue sentences since 09-24 all came from
chats that never finished). This module runs the SAME cue regex
(`memory._HABIT_CUE_RE`, regex only, no model call) once per human message
as it arrives, and only LOGS what it finds. It mints nothing; whether
minting turns on is decided after a week of this log (Dave, same journal,
"DECIDED WITHOUT RON" #1).

Also owns the complaint exclusion (T2, Dave's decision #2): "every time I X,
Y happens" is a bug report, not a standing habit, so it never mints. The
terminal Scribe path (`_scribe_classify_habits`) calls `is_complaint` too, so
the report and the mint can never disagree about what counts.

Log: one line to clayrune.log plus one JSONL row to
`data/habit_candidates/<project>.jsonl` (sibling of `data/projects/`, never
inside it: CLAUDE.md DATA_DIR-pollution rule). A row carries `verdict`
('candidate' | 'excluded_complaint') and `would_mint`, so the week of data
answers both "how often does the cue fire" and "how many were complaints".

Flags (`state.CONFIG`, both default True):
  * `habit_report_enabled`            - this whole observer (rollback: false).
  * `habit_complaint_exclusion_enabled` - T2 exclusion (rollback: false).
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from mc import state
from mc.core import _log

MAX_STATEMENT_CHARS = 2000   # same gate `_scribe_classify_habits` applies
_LOGGED_TEXT_CHARS = 400
_DEDUPE_WINDOW_S = 60

# Outcome clause of a bug report: what Ron GETS/SEES when he does the thing.
# "every time I enter the passcode I get this", "whenever I type ... the
# thinking badge shows". Decisive only after a TRIGGER cue ("whenever I",
# "every time I", "each time I"): "I always have to ..." is not a complaint.
_OUTCOME_RE = re.compile(
    r"\bi (?:get|got|see|saw|hit|keep (?:getting|seeing)|"
    r"am (?:asked|prompted|shown|forced|made|logged))\b"
    r"|\b(?:shows?|showing|appears?|pops? ?up|prompts?|"
    r"keeps? (?:on )?(?:showing|asking|happening|popping|appearing)|"
    r"happens?|breaks?|broke|fails?|failed|crash\w*|hangs?|freez\w+|"
    r"lags?|flicker\w*|resets?|reloads?|disappears?|logs? me out|asks? me)\b",
    re.IGNORECASE)
# Sentiment/defect words mark a report about product behaviour whatever cue
# fired ("... keeps showing every time I login. Annoying").
_DEFECT_RE = re.compile(
    r"\b(?:annoy\w*|bug|bugs|buggy|broken|glitch\w*|frustrat\w*|irritat\w*|"
    r"weird|wrong|problem|issue|error|not working|doesn'?t work|"
    r"(?:any ?way|anyway) to (?:skip|avoid|stop|disable)|"
    r"how (?:do|can) i (?:skip|avoid|stop|disable))\b",
    re.IGNORECASE)
_TRIGGER_CUE_RE = re.compile(r"^(?:whenever i|every time i|each time i)$",
                             re.IGNORECASE)


def _flag(name, default=True):
    return bool(state.CONFIG.get(name, default))


def enabled():
    return _flag('habit_report_enabled')


def is_complaint(stmt, cue=None):
    """True when `stmt` reads as a report about product behaviour rather than
    a standing operator habit: a defect word under any cue, or an outcome
    clause under a trigger cue. `cue` is the matched habit cue; looked up
    from the Scribe's regex when omitted. Off (always False) when
    `habit_complaint_exclusion_enabled` is false. Never raises."""
    try:
        if not _flag('habit_complaint_exclusion_enabled'):
            return False
        stmt = stmt or ''
        if _DEFECT_RE.search(stmt):
            return True
        if cue is None:
            from mc import memory as _mem
            m = _mem._HABIT_CUE_RE.search(stmt)
            cue = m.group(0) if m else ''
        return bool(_TRIGGER_CUE_RE.match(cue.strip()) and _OUTCOME_RE.search(stmt))
    except Exception as e:
        _log(f'[habit-report] complaint check failed: {e}')
        return False


def classify(message):
    """Regex-only verdict for one human message, or None when no habit cue
    fires (or the message fails the Scribe's own length gate). Returns
    {'cue', 'verdict', 'slug'}; `slug` is '' when the Scribe would refuse to
    mint (no content words). Never raises."""
    try:
        from mc import memory as _mem
        stmt = (message or '').strip()
        if not stmt or len(stmt) > MAX_STATEMENT_CHARS:
            return None
        m = _mem._HABIT_CUE_RE.search(stmt)
        if not m:
            return None
        cue = m.group(0).strip()
        verdict = 'excluded_complaint' if is_complaint(stmt, cue) else 'candidate'
        return {'cue': cue, 'verdict': verdict,
                'slug': _mem._habit_note_slug(stmt, cue)}
    except Exception as e:
        _log(f'[habit-report] classify failed: {e}')
        return None


def _log_dir():
    from mc import memory as _mem
    try:
        base = _mem.DATA_DIR.parent
    except Exception:
        base = Path(__file__).resolve().parent.parent / 'data'
    return base / 'habit_candidates'


def _session_identity(project_id, session_id, session):
    """(task, trigger_type, resolved session id) from the live session, else
    from the project's agent_log row (a chat revived after a restart has no
    live session when its next message lands)."""
    if session is None and session_id:
        s = state.agent_sessions.get(session_id)
        if s and s.get('project_id') == project_id:
            session = s
    if session is not None:
        return (session.get('task', '') or '', session.get('trigger_type', '') or '',
                session.get('session_id') or session_id)
    try:
        from mc.blueprints import agent_routes as _ar
        for r in _ar._load_agent_log(project_id) or []:
            if (session_id and (r.get('session_id') == session_id
                                or r.get('claude_session_id') == session_id
                                or session_id in (r.get('claude_session_ids') or []))):
                return (r.get('task', '') or '', r.get('trigger_type', '') or '',
                        session_id)
    except Exception as e:
        _log(f'[habit-report] agent_log lookup failed: {e}')
    return '', '', session_id


_recent = {}   # (session_id, message) -> monotonic seen-at; /agent/send hands
               # the same message on to /agent/followup in-process.


def _seen_recently(key):
    now = time.monotonic()
    for k in [k for k, t in _recent.items() if now - t > _DEDUPE_WINDOW_S]:
        _recent.pop(k, None)
    if key in _recent:
        return True
    _recent[key] = now
    return False


def observe_user_message(project_id, session_id, message, *, session=None,
                         human=True):
    """Classify one human message and log the result. REPORT MODE: writes no
    memory note. `human=False` (an agent posted it; labelled with the user's
    name in log_lines regardless) is skipped outright, mirroring
    `_habit_source_statements`' refusal to treat another agent's phrasing as
    the operator's. Returns the logged row (for tests) or None. Never raises.
    """
    try:
        if not human or not enabled():
            return None
        hit = classify(message)
        if hit is None:
            return None
        if _seen_recently((session_id or '', message)):
            return None
        from mc import memory as _mem
        task, trigger_type, sid = _session_identity(project_id, session_id, session)
        origin = _mem._stamp_origin(task.strip(), trigger_type)
        row = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'project_id': project_id,
            'session_id': sid or '',
            'trigger_type': trigger_type,
            'origin': origin,
            'cue': hit['cue'],
            'verdict': hit['verdict'],
            'would_mint': (hit['verdict'] == 'candidate' and bool(hit['slug'])
                           and origin == 'interactive'),
            'slug': hit['slug'],
            'text': (message or '').strip()[:_LOGGED_TEXT_CHARS],
        }
        _log(f"[habit-candidate] {project_id}/{(sid or '')[:12]} cue={hit['cue']!r} "
             f"verdict={row['verdict']} origin={origin} would_mint={row['would_mint']}: "
             f"{row['text'][:120]!r}")
        _append_row(project_id, row)
        return row
    except Exception as e:
        _log(f'[habit-report] observe failed: {e}')
        return None


def _append_row(project_id, row):
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        safe = ''.join(c for c in (project_id or '') if c.isalnum() or c in '-_') or 'unknown'
        with open(d / f'{safe}.jsonl', 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row) + '\n')
    except Exception as e:
        _log(f'[habit-report] log write failed: {e}')
