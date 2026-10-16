"""Resume-target hygiene for Claude CLI sessions.

Incident 2026-10-15 (cad3d, MC session 2d54c95f3c0e): a mid-turn rollover
spawned a fresh child whose SessionStart hooks outlasted the 20 s stdin
watchdog, so it was killed before it ever read the message. The CLI emits
`system/hook_*` events — carrying its brand-new session_id — BEFORE it reads
stdin, and `_note_claude_sid` committed that id. The id never got a transcript,
so every later follow-up ran `claude -r <that id>` and died with
"No conversation found with session ID". Nothing handled that error.

Three independent guards live here (pure functions, no Flask/server import,
every dependency injected so they unit-test without a CLI):

1. `is_startup_hook_event` — the reader must not adopt a session_id from a
   pre-stdin hook event; `init` (which the CLI emits only after it has drained
   stdin) is the earliest trustworthy source.
2. `assess_resume_target` — before any `-r <id>` spawn, check the id has a
   transcript (and the CLI has not already rejected it); if not, name the
   newest earlier id that does, so the caller takes the existing rollover
   handoff path (`_auto_fresh_handoff(fallback_native_id=...)`).
3. `observe_child_line` / `startup_grace_applies` — record "the CLI rejected
   this id" from the child's own output, and tell the stdin watchdog when a
   slow child is still demonstrably starting (emitting hook progress).
"""
import json
import re
import time

NO_CONVERSATION_MARKER = 'No conversation found with session ID'
_NO_CONVERSATION_ID = re.compile(
    r'No conversation found with session ID:?\s*([0-9A-Za-z-]{8,64})')

# A child that has not drained stdin is killed after `_STDIN_WRITE_KILL_TIMEOUT`
# of SILENCE, but never later than this many seconds after the write started.
# Measured 2026-10-15 (see tests/test_resume_target.py): the CLI reads no stdin
# until its SessionStart hooks finish, and emits `hook_progress` every ~2 s
# while they run.
STARTUP_GRACE_MAX_S = 120.0


def is_startup_hook_event(msg):
    """True for a `system/hook_*` stream event. Those carry the session_id the
    CLI chose at startup, but they are emitted before the CLI has read a single
    byte of stdin, so the id may never get a transcript."""
    return (isinstance(msg, dict) and msg.get('type') == 'system'
            and str(msg.get('subtype') or '').startswith('hook_'))


def no_conversation_id(line):
    """The session id the CLI reported it could not resume, or None.

    Matches only the CLI's own error shapes — a bare text line, or an
    `is_error` result envelope. An assistant/tool message that merely quotes
    the phrase (this very error gets pasted into chats) must not poison the
    live session id."""
    if not isinstance(line, str) or NO_CONVERSATION_MARKER not in line:
        return None
    text = line.strip()
    if text.startswith('{'):
        try:
            msg = json.loads(text)
        except ValueError:
            return None
        if not (isinstance(msg, dict) and msg.get('type') == 'result'
                and msg.get('is_error')):
            return None
    elif not text.startswith(NO_CONVERSATION_MARKER):
        return None
    m = _NO_CONVERSATION_ID.search(text)
    return m.group(1) if m else None


def observe_child_line(session, proc, line, *, log=None, now=None):
    """Per-stdout-line bookkeeping for a Claude child. Never raises.

    Stamps `session['_child_line_at']` ({pid, at}) so the stdin watchdog can
    tell a slow-but-alive startup from a hung child, and records an id the CLI
    rejected in `session['_unresumable_sids']`."""
    try:
        session['_child_line_at'] = {
            'pid': getattr(proc, 'pid', None),
            'at': time.time() if now is None else now}
        bad = no_conversation_id(line)
        if bad:
            bad_list = session.setdefault('_unresumable_sids', [])
            if bad not in bad_list:
                bad_list.append(bad)
            if log:
                log(f"[resume-heal] {session.get('session_id', '')[:12]}: CLI "
                    f"rejected resume of {bad[:12]} — the next send starts from "
                    f"a handoff")
    except Exception as e:  # observability only — a reader must never die here
        if log:
            log(f"[resume-heal] observe_child_line failed: {e}")


def startup_grace_applies(session, proc, started_at, idle_s, *, now=None):
    """True when the stdin watchdog should keep waiting: `proc` printed
    something within the last `idle_s` seconds (so it is starting, not hung) and
    the write began less than STARTUP_GRACE_MAX_S ago."""
    if session is None:
        return False
    now = time.time() if now is None else now
    if now - started_at >= STARTUP_GRACE_MAX_S:
        return False
    stamp = session.get('_child_line_at')
    if not isinstance(stamp, dict) or stamp.get('pid') != getattr(proc, 'pid', None):
        return False
    return now - float(stamp.get('at') or 0) < idle_s


def assess_resume_target(session, claude_sid, all_sids, find_transcript, *, log=None):
    """Decide whether `claude -r claude_sid` can work.

    Returns True when `claude_sid` is NOT resumable (no transcript on disk, or
    the CLI already said "No conversation found" for it). In that case
    `session['_rolled_from']` is pointed at the newest id in `all_sids` (oldest
    first) that still has a transcript — or cleared when none does — so the
    caller's rollover handoff carries that id's real turns, or falls to the
    labelled "handoff unavailable" fresh start. Returns False (and touches
    nothing) when `claude_sid` is fine. Never raises."""
    if not claude_sid:
        return False
    try:
        rejected = set(session.get('_unresumable_sids') or [])
        if claude_sid not in rejected and find_transcript(claude_sid):
            return False
        fallback = None
        for sid in reversed(list(all_sids or [])):
            if sid == claude_sid or sid in rejected:
                continue
            if find_transcript(sid):
                fallback = sid
                break
        if fallback:
            session['_rolled_from'] = fallback
        else:
            session.pop('_rolled_from', None)
        if log:
            why = 'rejected by the CLI' if claude_sid in rejected else 'has no transcript'
            log(f"[resume-heal] {session.get('session_id', '')[:12]}: resume target "
                f"{claude_sid[:12]} {why}; "
                + (f"carrying the handoff from {fallback[:12]}" if fallback
                   else "no earlier transcript exists, starting fresh"))
        return True
    except Exception as e:
        if log:
            log(f"[resume-heal] assess failed for {claude_sid[:12]}: {e}")
        return False
