"""Per-turn clock for agent sessions -- the agent's missing sense of time.

THE DEFECT THIS CLOSES (Ron, 2026-11-03). A prompt carried at most today's
DATE, never the clock time or how long it had been since the agent's last
reply. A follow-up typed 9 hours after the agent last spoke read like it came
a second later, so the agent acted on state that had gone stale in the gap.

TWO PARTS, both prepended to the outgoing message (never the system prompt --
that is built once per spawn, which is exactly the staleness being fixed):

  1. TURN STAMP -- one leading line on every message Clayrune sends into an
     agent session:  `[Now 2026-11-04 07:10 PT · 9h 20m since your last reply]`
     Elapsed is measured from the session's last ASSISTANT output
     (`session['_last_reply_at']`, stamped by the stream readers via
     `note_reply`), not from the last status change or the last log line --
     both of those move when the USER sends, which would reset the clock the
     moment it needed to be read. A fresh session with no prior reply gets the
     `Now` part only.

  2. GAP BRIEFING -- when elapsed exceeds a threshold (default 30 min), a
     short `Since your last reply:` block of what changed in that window:
     commits on the project repo, backlog items whose status changed, and
     dispatched/sibling sessions that finished. Capped at `MAX_BRIEF_LINES`
     item lines with an explicit "+N more" line when truncated. NO LLM call --
     one `git log`, a scan of data already in memory.

Shape follows `mc/behavior_tail.py` (per-turn injection, `enabled()` rollback
lever read live, `render()` never raises) and lives in its own module per the
"one unit, one file" rule: the injection SITES in `agent_routes` are one-line
calls into `prepend()`.

Settings (all read live, no respawn): `turn_clock_enabled` (default True),
`turn_clock_gap_briefing_enabled` (default True), `turn_clock_gap_threshold_minutes`
(default 30).

WHAT THIS DELIBERATELY DOES NOT DO: persist anything, call a model, touch the
system prompt, or run for slash commands (the CLI only parses `/cmd` as the
FIRST bytes of a turn -- same carve-out as `_apply_mobile_brief`).
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from mc.core import _log

_REPLY_KEY = '_last_reply_at'

DEFAULT_THRESHOLD_MINUTES = 30
MAX_BRIEF_LINES = 10          # item lines, header and "+N more" line excluded
_ITEM_TEXT_CHARS = 70
_GIT_TIMEOUT_S = 3
_FINISHED_STATUSES = frozenset({'completed', 'error', 'idle'})


# ── settings ────────────────────────────────────────────────────────────────

def _cfg(key, default):
    try:
        from mc import state
        return state.CONFIG.get(key, default)
    except Exception:
        return default


def enabled() -> bool:
    """Master lever for the turn stamp (and, with it, the briefing). Default ON."""
    try:
        return bool(_cfg('turn_clock_enabled', True))
    except Exception:
        return True


def briefing_enabled() -> bool:
    try:
        return bool(_cfg('turn_clock_gap_briefing_enabled', True))
    except Exception:
        return True


def threshold_seconds() -> float:
    """Gap above which the briefing is appended. Bad/negative config falls
    back to the default rather than disabling or spamming the briefing."""
    try:
        minutes = float(_cfg('turn_clock_gap_threshold_minutes',
                             DEFAULT_THRESHOLD_MINUTES))
        if minutes < 0:
            minutes = DEFAULT_THRESHOLD_MINUTES
    except (TypeError, ValueError):
        minutes = DEFAULT_THRESHOLD_MINUTES
    return minutes * 60.0


# ── last-reply bookkeeping ──────────────────────────────────────────────────

def note_reply(session, now: float | None = None) -> None:
    """Stamp `session` as having just produced assistant output. Called from
    the stream readers on every assistant message. Never raises."""
    try:
        if isinstance(session, dict):
            session[_REPLY_KEY] = time.time() if now is None else now
    except Exception:
        pass


def last_reply_epoch(session) -> float | None:
    if not isinstance(session, dict):
        return None
    v = session.get(_REPLY_KEY)
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def _parse_iso(ts) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def seed_from_history(session, lines, ts) -> None:
    """A revived session starts with an empty dict -- recover the last
    assistant output time from the restored transcript lines. `lines`/`ts`
    are the parallel lists `_revive_history_lines` returns: user turns render
    as `> Label: ...`, everything else is assistant text, and `ts[i]` is None
    for synthetic marker lines. Never raises."""
    try:
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            if not isinstance(line, str) or line.lstrip().startswith('>'):
                continue
            if line.startswith('[') and line.rstrip().endswith(']'):
                continue            # synthetic bracket marker, not a reply
            epoch = _parse_iso(ts[i]) if i < len(ts) else None
            if epoch:
                session[_REPLY_KEY] = epoch
                return
    except Exception as e:
        _log(f'[turn-clock] seed_from_history failed: {e}')


# ── formatting ──────────────────────────────────────────────────────────────

def format_elapsed(seconds: float) -> str:
    """`9h 20m`, `3d 4h`, `42m`, `under a minute`. Whole units, two at most."""
    s = max(0, int(seconds))
    if s < 60:
        return 'under a minute'
    m = s // 60
    if m < 60:
        return f'{m}m'
    h, m = divmod(m, 60)
    if h < 24:
        return f'{h}h {m}m' if m else f'{h}h'
    d, h = divmod(h, 24)
    return f'{d}d {h}h' if h else f'{d}d'


def _tz_label(dt: datetime) -> str:
    """Short zone label. Windows reports `Pacific Standard Time`; fold long
    names to initials (`PST`). Falls back to the UTC offset."""
    name = dt.tzname() or ''
    if ' ' in name:
        name = ''.join(w[0] for w in name.split() if w).upper()
    if name and len(name) <= 5:
        return name
    off = dt.utcoffset()
    if off is None:
        return 'UTC'
    mins = int(off.total_seconds() // 60)
    sign = '+' if mins >= 0 else '-'
    mins = abs(mins)
    return f'UTC{sign}{mins // 60:02d}:{mins % 60:02d}'


def format_now(now_epoch: float) -> str:
    dt = datetime.fromtimestamp(now_epoch).astimezone()
    return f"{dt.strftime('%Y-%m-%d %H:%M')} {_tz_label(dt)}"


def stamp_line(session, now: float | None = None) -> str:
    now = time.time() if now is None else now
    line = f'[Now {format_now(now)}'
    last = last_reply_epoch(session)
    if last is not None and now >= last:
        line += f' · {format_elapsed(now - last)} since your last reply'
    return line + ']'


# ── gap briefing ────────────────────────────────────────────────────────────

def _clip(text: Any, n: int = _ITEM_TEXT_CHARS) -> str:
    t = ' '.join(str(text or '').split())
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


def commits_since(cwd, since_epoch: float) -> list[str]:
    """One `git log` on `cwd` (HEAD only), newest first. [] on any failure --
    not a repo, git missing, timeout -- because the briefing is best-effort."""
    if not cwd:
        return []
    since_iso = datetime.fromtimestamp(since_epoch, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%S+00:00')
    try:
        out = subprocess.run(
            ['git', '-C', str(cwd), 'log', f'--since={since_iso}',
             '--format=%h %s', '-n', str(MAX_BRIEF_LINES + 1)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=_GIT_TIMEOUT_S, check=False,
        )
    except Exception as e:
        _log(f'[turn-clock] git log failed in {cwd}: {e}')
        return []
    if out.returncode != 0:
        return []
    return [f'commit {_clip(l)}' for l in out.stdout.splitlines() if l.strip()]


def backlog_changes_since(project, since_epoch: float) -> list[str]:
    """Backlog items whose LAST status transition falls after `since_epoch`."""
    rows: list[tuple[float, str]] = []
    for item in (project or {}).get('backlog') or []:
        if not isinstance(item, dict):
            continue
        hist = item.get('status_history')
        if not isinstance(hist, list) or not hist or not isinstance(hist[-1], dict):
            continue
        last = hist[-1]
        at = _parse_iso(last.get('ts'))
        if at is None or at <= since_epoch:
            continue
        key = item.get('key') or item.get('id') or '?'
        rows.append((at, f"backlog {key} {last.get('from')}→{last.get('to')}: "
                         f"{_clip(item.get('text'))}"))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


def sessions_finished_since(session, sessions: Iterable[dict],
                            since_epoch: float) -> list[str]:
    """Sessions this one dispatched, or that share its spawner, that finished
    (completed / error / a Mode B turn boundary) after `since_epoch`."""
    me = (session or {}).get('session_id') or ''
    my_spawner = (session or {}).get('_spawned_by') or ''
    rows: list[tuple[float, str]] = []
    for s in sessions or ():
        if not isinstance(s, dict) or s is session:
            continue
        sid = s.get('session_id') or ''
        if not sid or sid == me or s.get('project_id') != (session or {}).get('project_id'):
            continue
        dispatched = me and me in (s.get('_notify_session'), s.get('_spawned_by'))
        sibling = my_spawner and s.get('_spawned_by') == my_spawner
        if not (dispatched or sibling):
            continue
        if s.get('status') not in _FINISHED_STATUSES:
            continue
        at = s.get('last_status_change_time')
        if not isinstance(at, (int, float)) or at <= since_epoch:
            continue
        who = ((s.get('character') or {}).get('agent_name') if isinstance(
            s.get('character'), dict) else '') or sid[:8]
        rel = 'dispatched' if dispatched else 'sibling'
        rows.append((float(at), f"{rel} session {who} {s.get('status')}: "
                                f"{_clip(s.get('task'))}"))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


def _cap(groups: list[list[str]]) -> tuple[list[str], int]:
    """Round-robin across groups so one noisy source can't crowd out the
    others, up to MAX_BRIEF_LINES. Returns (lines, dropped_count)."""
    total = sum(len(g) for g in groups)
    if total <= MAX_BRIEF_LINES:
        return [l for g in groups for l in g], 0
    out: list[str] = []
    i = 0
    while len(out) < MAX_BRIEF_LINES:
        progressed = False
        for g in groups:
            if i < len(g) and len(out) < MAX_BRIEF_LINES:
                out.append(g[i])
                progressed = True
        if not progressed:
            break
        i += 1
    return out, total - len(out)


def gap_briefing(project, session, since_epoch: float, *,
                 sessions: Iterable[dict] | None = None, cwd=None) -> str:
    """The `Since your last reply:` block, or '' when nothing changed."""
    groups = [
        sessions_finished_since(session, sessions if sessions is not None else (), since_epoch),
        backlog_changes_since(project, since_epoch),
        commits_since(cwd, since_epoch),
    ]
    lines, dropped = _cap(groups)
    if not lines:
        return ''
    body = ['Since your last reply:'] + [f'- {l}' for l in lines]
    if dropped:
        body.append(f'- (+{dropped} more, truncated)')
    return '\n'.join(body)


# ── public entry points ─────────────────────────────────────────────────────

def render(project, session, *, now: float | None = None,
           sessions: Iterable[dict] | None = None, cwd=None) -> str:
    """The header to put ahead of an outgoing message: the stamp line, plus the
    gap briefing when the gap exceeds the threshold. '' when disabled. Never
    raises -- a bug here must not cost a live turn its stdin write."""
    try:
        if not enabled():
            return ''
        now = time.time() if now is None else now
        out = stamp_line(session, now)
        last = last_reply_epoch(session)
        if (last is not None and briefing_enabled()
                and now - last > threshold_seconds()):
            try:
                brief = gap_briefing(project, session, last,
                                     sessions=sessions, cwd=cwd)
            except Exception as e:
                _log(f'[turn-clock] gap briefing failed: {e}')
                brief = ''
            if brief:
                out += '\n' + brief
        return out
    except Exception as e:
        _log(f'[turn-clock] render failed: {e}')
        return ''


def prepend(project, session, content: str, **kw) -> str:
    """`content` with the clock header in front, or unchanged when disabled."""
    header = render(project, session, **kw)
    return f'{header}\n\n{content}' if header else content
