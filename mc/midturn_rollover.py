"""Mid-turn context rollover (docs/CONTEXT_ECONOMY_SPEC.md §2, extended).

`_auto_fresh_trigger` (agent_routes) only runs when a NEW message arrives from
Clayrune. A dispatched worker gets one prompt and then a single long tool loop,
so its context climbed 0 -> 325k tokens across 301 model calls with no message
boundary for the check to run at (docs/_journal/rollover-not-firing.md).

This module holds the parts that need no Flask/agent_routes state:

  * bookkeeping the stream readers feed (`note_tool_use`, `note_tool_results`):
    which tool calls are still awaiting a result, the last few calls, and any
    `run_in_background` jobs;
  * user text the transcript handoff cannot carry (`note_user_message`,
    `note_assistant_text`, `note_turn_complete`): messages delivered while a
    turn was running (stdin steer, queued follow-up) and the latest user
    message (MC-1088);
  * `should_roll` — the gate, evaluated at a tool_result boundary only;
  * `build_state_block` — what a mid-task roll must carry that the transcript
    handoff (role/text turns, 8000 chars) does not: the original task verbatim,
    branch/worktree, `git status` + `git diff --stat`, the last tool calls,
    and background jobs;
  * `record_roll` — durable per-roll log outside DATA_DIR (the project
    activity_log keeps 20 entries).

The roll itself goes through the existing interrupt path in agent_routes, so
the MC session_id is kept and `notify_session` callbacks still fire.

Off unless `midturn_rollover_enabled` is true (default false).
"""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from mc import state
from mc.core import _log, TimestampedLines
from mc.rollover_threshold import threshold_for

RECENT_TOOLS_KEEP = 10
# MC-964 Step C: the last tool_results' first lines, carried across a roll
# because the transcript handoff (role/text turns only) drops tool_result
# content entirely — the exact gap that cost a prior roll its own measurements
# (docs/_journal/b2d85e51-memory-overhaul.md). Capped, not unlimited: a larger
# carry raises the fresh session's floor on every roll (plan §7.3).
TOOL_RESULT_CARRY_CAP_BYTES = 4096
_TOOL_RESULT_HEAD_CHARS = 300
_JOURNAL_MARKER = 'docs/_journal/'
# A roll that lands on a fresh prefix already over the threshold (threshold set
# below the size of the injected context) would otherwise re-roll at every tool
# boundary. Re-rolling needs this much growth over the fresh session's first
# reading, so the worst case is one roll per this many tokens of real work.
MIN_GROWTH_TOKENS = 20_000
# Consecutive refused/failed roll attempts before this session stops trying.
# Every tool boundary re-evaluates `should_roll`, so without a back-off three
# failures caused by one brief fault (agent_log locked on Windows) land within
# seconds and switch rollover off for the whole session. After a failure the
# next attempt therefore waits for MIN_GROWTH_TOKENS of real growth over the
# context size the failed attempt was made at.
MAX_ROLL_FAILURES = 3
_BG_JOBS_KEEP = 10
# MC-1088: user text carried across a roll. Bounded like the other `_mt_*`
# fields: the newest few mid-turn messages, each capped, so a flood of steers
# cannot inflate the fresh session's floor.
UNANSWERED_KEEP = 5
USER_MSG_CAP_CHARS = 2000
_INPUT_PREVIEW = 160
_GIT_OUT_CAP = 4000
_GIT_TIMEOUT_S = 10
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

ROLL_MESSAGE = ("Your context was rolled over mid-task because it reached the "
                "token limit. This is the SAME task, not a new one — continue "
                "it from where it stopped using the state above. Do not "
                "restart it and do not redo finished work.")


def enabled():
    return bool(state.CONFIG.get('midturn_rollover_enabled', False))


def _preview(tool_input):
    """Short, single-line rendering of a tool input for the recent-calls list."""
    if not isinstance(tool_input, dict):
        return ''
    for key in ('command', 'file_path', 'path', 'pattern', 'url', 'description',
                'prompt', 'query'):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            return ' '.join(val.split())[:_INPUT_PREVIEW]
    return ' '.join(json.dumps(tool_input, default=str).split())[:_INPUT_PREVIEW]


def _pending(session):
    """The pending tool-call id set, scoped to the CURRENT process. Every path
    that replaces `session['proc']` (user interrupt, stop+resume, guardian,
    idle eviction, the Mode A AskUserQuestion kill) orphans whatever the old
    process had in flight — no tool_result will ever arrive for it, so an id
    left behind would hold `should_roll` False for the rest of the session.
    Comparing the owning proc here covers all of them without a hook at each
    reassignment."""
    proc = session.get('proc')
    if session.get('_mt_pending_proc') is not proc:
        session['_mt_pending_proc'] = proc
        session['_mt_pending_tools'] = set()
    return session.setdefault('_mt_pending_tools', set())


def _journal_path(tool_input):
    """The `file_path` a Write/Edit call targeted, if it's under
    `docs/_journal/` — MC-964 Step C carries these across a roll since a
    fresh session otherwise has no way to know which journal file already
    holds this task's log (re-reading the wrong/no file re-derives it)."""
    if not isinstance(tool_input, dict):
        return None
    fp = tool_input.get('file_path')
    if not isinstance(fp, str) or not fp:
        return None
    return fp if _JOURNAL_MARKER in fp.replace('\\', '/') else None


def note_tool_use(session, block):
    """Record one `tool_use` content block. While the flag is off this records
    nothing, and drops any pending set left from an earlier enabled stretch:
    a result that arrives while off is never discarded, so keeping the id
    would block every roll after the flag comes back on."""
    if not enabled():
        session.pop('_mt_pending_tools', None)
        return
    if not isinstance(block, dict):
        return
    name = block.get('name', '')
    tool_input = block.get('input')
    tid = block.get('id')
    if tid:
        _pending(session).add(tid)
    recent = session.setdefault('_mt_recent_tools', [])
    recent.append({'name': name, 'input': _preview(tool_input)})
    del recent[:-RECENT_TOOLS_KEEP]
    if isinstance(tool_input, dict) and tool_input.get('run_in_background'):
        jobs = session.setdefault('_mt_bg_jobs', [])
        jobs.append({'tool': name, 'input': _preview(tool_input)})
        del jobs[:-_BG_JOBS_KEEP]
    jp = _journal_path(tool_input)
    if jp:
        paths = session.setdefault('_mt_journal_paths', [])
        if jp not in paths:
            paths.append(jp)


def note_tool_result_text(session, tool_name, text):
    """Record one tool_result's first line into the rollover carry buffer
    (MC-964 Step C). Called by the caller's existing extraction (`agent_routes
    ._extract_tool_result_text`) alongside `note_tool_results`, so this module
    never needs its own copy of the Claude content-block parsing. Oldest
    entries drop first once the running total crosses
    `TOOL_RESULT_CARRY_CAP_BYTES`, so a roll always carries the most RECENT
    evidence rather than whatever arrived first."""
    if not enabled() or not text:
        return
    head = next((ln.strip() for ln in text.splitlines() if ln.strip()), '')
    if not head:
        return
    head = head[:_TOOL_RESULT_HEAD_CHARS]
    entries = session.setdefault('_mt_tool_result_heads', [])
    entries.append({'tool': tool_name or '(tool)', 'head': head})
    total = sum(len(e['tool']) + len(e['head']) for e in entries)
    while total > TOOL_RESULT_CARRY_CAP_BYTES and len(entries) > 1:
        removed = entries.pop(0)
        total -= len(removed['tool']) + len(removed['head'])


def note_tool_results(session, content):
    """Clear the pending-call ids a `user` message's tool_result blocks answer.
    Returns True when at least one of them answered a call we tracked (a tool
    boundary). A result for a call we never saw — one already in flight when
    the flag was switched on — is not a boundary: other untracked calls may
    still be running beside it."""
    if not enabled():
        session.pop('_mt_pending_tools', None)
        return False
    if not isinstance(content, list):
        return False
    pending = _pending(session)
    seen = False
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'tool_result':
            tid = block.get('tool_use_id')
            if tid in pending:
                seen = True
                pending.discard(tid)
    return seen


def _cap_user_text(text):
    text = str(text).strip()
    if len(text) > USER_MSG_CAP_CHARS:
        return text[:USER_MSG_CAP_CHARS] + ' [... truncated]'
    return text


def note_user_message(session, text, *, mid_turn=False):
    """Record a user message for the rollover state block (MC-1088).

    Every message is the session's latest user message. `mid_turn=True` marks
    one delivered WHILE a turn was running -- stdin steer, or a follow-up held
    in `pending_followups`: the CLI folds it into the running turn (transcript
    `queue-operation` ... `absorbed_mid_turn`) or the interrupt that performs
    a roll drops the queue, and either way the transcript handoff (role/text
    turns) does not reliably carry it. Those are kept in `_mt_unanswered_msgs`
    until post-roll assistant text retires carried entries, or the turn ends
    (`note_turn_complete`), newest `UNANSWERED_KEEP` only.
    Never raises."""
    if not enabled() or not isinstance(text, str) or not text.strip():
        return
    try:
        capped = _cap_user_text(text)
        session['_mt_latest_user_msg'] = capped
        if mid_turn:
            msgs = session.setdefault('_mt_unanswered_msgs', [])
            msgs.append({'ts': datetime.now(timezone.utc).strftime('%H:%M:%SZ'),
                         'text': capped})
            msgs[:] = msgs[-UNANSWERED_KEEP:]
    except Exception as e:
        _log(f"[midturn-rollover] note_user_message failed: {e}")


def note_assistant_text(session):
    """Post-roll assistant text retires only messages carried into that process.
    Steers received after the roll remain unstamped and must survive. Called
    only for non-empty main-conversation text, never tool-only output.
    Never raises."""
    try:
        if not enabled():
            return
        msgs = session.get('_mt_unanswered_msgs')
        if msgs:
            msgs[:] = [m for m in msgs if not m.get('carried')]
    except Exception as e:
        _log(f"[midturn-rollover] note_assistant_text failed: {e}")


def note_turn_complete(session):
    """A turn's `result` arrived: whatever was absorbed into it was answered
    (one `result` covers the original prompt and every steer folded into it,
    docs/MID_TURN_SEND.md). A roll never reaches here -- it kills the process
    before any `result` -- so messages with no post-roll text survive repeated
    rolls."""
    session.pop('_mt_unanswered_msgs', None)


def note_call_tokens(session, tokens, parent_tool_use_id=None):
    """Record the MAIN conversation's context size from one model call.
    A subagent (Task) call streams through the same reader with a
    `parent_tool_use_id`; its usage describes the subagent's own, separate
    context, so it must not decide when the parent rolls."""
    if not enabled() or parent_tool_use_id or tokens is None:
        return
    session['_mt_main_tokens'] = tokens
    if session.get('_mt_awaiting_baseline'):
        session['_mt_awaiting_baseline'] = False
        session['_mt_baseline_tokens'] = tokens


def should_roll(session, over_threshold):
    """True when the session should roll NOW: flag on, a Claude stream (the
    only vendor with a per-call usage figure — the others report usage at turn
    end, where the follow-up check already runs), token threshold crossed, no
    tool call still awaiting its result, no roll already requested or in
    flight, and — after a previous roll — real growth over the fresh session's
    first reading. `over_threshold` is agent_routes._context_tokens_over_threshold."""
    if not enabled():
        return False
    if (session.get('provider') or 'claude').lower() != 'claude':
        return False
    _observe_failures(session)
    if session.get('_mt_roll_requested') or session.get('_interrupting'):
        return False
    if session.get('incognito') or session.get('waiting_for_question') \
            or session.get('waiting_for_plan_approval'):
        return False
    if not session.get('claude_session_id'):
        return False    # the handoff is built from the transcript; no id, no roll
    if session.get('_mt_roll_failures', 0) >= MAX_ROLL_FAILURES:
        return False    # already announced by _observe_failures
    if _pending(session):
        return False
    tokens = session.get('_mt_main_tokens')
    if not over_threshold(tokens, session):
        return False
    base = session.get('_mt_baseline_tokens')
    if base is not None and tokens - base < MIN_GROWTH_TOKENS:
        return False    # fresh prefix alone is at/over the threshold: no loop
    failed_at = session.get('_mt_fail_tokens')
    if failed_at is not None and tokens - failed_at < MIN_GROWTH_TOKENS:
        return False    # back off: the last attempt failed at this size
    session['_mt_attempt_tokens'] = tokens    # the caller attempts the roll now
    return True


def _observe_failures(session):
    """The caller (agent_routes._maybe_midturn_roll) counts a failed attempt in
    `_mt_roll_failures` and nothing else, so failures are noticed here, at the
    next evaluation: a new one arms the back-off from the context size that
    attempt was made at, and reaching MAX_ROLL_FAILURES gives up out loud once."""
    failures = session.get('_mt_roll_failures', 0)
    if failures > session.get('_mt_seen_failures', 0):
        session['_mt_seen_failures'] = failures
        session['_mt_fail_tokens'] = session.get('_mt_attempt_tokens')
    if failures >= MAX_ROLL_FAILURES and not session.get('_mt_gave_up'):
        session['_mt_gave_up'] = True
        tokens = session.get('_mt_attempt_tokens')
        note = (f"[Mid-turn rollover gave up after {failures} failed attempts; "
                f"this session will not roll again, so its context keeps growing "
                f"until the next message arrives.]")
        try:
            session.setdefault('log_lines', TimestampedLines()).append(note)
        except Exception as e:
            _log(f"[midturn-rollover] session log write failed: {e}")
        _log(f"[midturn-rollover] {session.get('project_id', '')}/"
             f"{session.get('session_id', '')}: gave up after {failures} failed "
             f"rolls (last attempt at {tokens} tokens)")
        _append_row(session, {'event': 'gave_up', 'failures': failures,
                              'context_tokens': tokens})


def begin_roll(session):
    """Reset the per-process readings for the fresh session a roll is about to
    start. The first main-conversation reading it reports becomes the baseline
    `should_roll` measures growth from (MIN_GROWTH_TOKENS). Called by
    agent_interrupt once the roll is committed to, so a refused or failed
    attempt never leaves a baseline armed."""
    session['_mt_main_tokens'] = None
    session['_mt_awaiting_baseline'] = True
    session['_mt_baseline_tokens'] = None
    session['_mt_roll_failures'] = 0
    for k in ('_mt_seen_failures', '_mt_fail_tokens', '_mt_gave_up'):
        session.pop(k, None)
    session['_mt_rolls'] = session.get('_mt_rolls', 0) + 1


def _git(cwd, *args):
    try:
        # stdin=DEVNULL: the reader thread runs inside a server with no usable
        # stdin handle on Windows, and an inherited one fails with WinError 6.
        out = subprocess.run(
            ['git', *args], cwd=cwd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
            encoding='utf-8', errors='replace', timeout=_GIT_TIMEOUT_S)
    except Exception as e:
        return f'(git {" ".join(args)} failed: {e})'
    text = (out.stdout or '').strip() or (out.stderr or '').strip()
    if out.returncode != 0:
        return f'(git {" ".join(args)} exited {out.returncode}: {text[:300]})'
    if len(text) > _GIT_OUT_CAP:
        text = text[:_GIT_OUT_CAP] + '\n[... truncated]'
    return text or '(clean)'


def build_roll_state_block(session, cwd):
    """Stamp the messages carried by an actual roll, then render its state.
    Kept separate from pure rendering used by inspection/tests. The interrupt
    path calls this after killing the old process and before starting its
    replacement, so old-process text cannot retire the stamped messages."""
    for msg in session.get('_mt_unanswered_msgs') or []:
        msg['carried'] = True
    return build_state_block(session, cwd)


def build_state_block(session, cwd):
    """The mid-task state the transcript handoff cannot carry. Every section is
    always present; an unavailable one says so instead of vanishing.

    MC-1088 puts the user's mid-turn messages FIRST ("Unanswered user messages
    (answer these first)") and, when it differs from the original task, the
    latest user message: a roll used to rebuild the session from the first
    prompt alone, so messages the CLI had absorbed mid-turn were never
    answered (2026-10-12, transcript 03546a91).

    MC-964 Step C adds two sections the plan's replay showed missing: the
    session's own `docs/_journal/` file(s) (so the fresh session re-reads its
    own log instead of re-deriving what it already wrote), and the last
    tool_results' first lines (`note_tool_result_text`, capped at
    `TOOL_RESULT_CARRY_CAP_BYTES`) — the transcript handoff carries only
    role/text turns, so command/search OUTPUT otherwise vanishes at the roll."""
    task = session.get('task') or '(original task text unavailable)'
    recent = session.get('_mt_recent_tools') or []
    calls = '\n'.join(f"- {c['name']}: {c['input']}" if c['input'] else f"- {c['name']}"
                      for c in recent) or '(none recorded)'
    jobs = session.get('_mt_bg_jobs') or []
    if jobs:
        job_lines = '\n'.join(f"- {j['tool']}: {j['input']}" for j in jobs)
        job_text = (f"{job_lines}\n(Started with run_in_background under the "
                    f"old process; the roll ends that process tree, so relaunch "
                    f"any that are still needed.)")
    else:
        job_text = '(none)'
    journal_paths = session.get('_mt_journal_paths') or []
    journal_text = ('\n'.join(f"- {jp}" for jp in journal_paths)
                     or '(none written under docs/_journal/ this session)')
    heads = session.get('_mt_tool_result_heads') or []
    heads_text = ('\n'.join(f"- {h['tool']}: {h['head']}" for h in heads)
                  or '(none captured)')
    unanswered = session.get('_mt_unanswered_msgs') or []
    unanswered_text = ('\n'.join(f"- [{m['ts']}] {m['text']}" for m in unanswered)
                       or '(none)')
    latest = session.get('_mt_latest_user_msg')
    latest_text = (f"--- Latest user message ---\n{latest}\n\n"
                   if latest and latest != session.get('task') else '')
    return (
        "=== Mid-task rollover state ===\n"
        f"--- Unanswered user messages (answer these first) ---\n{unanswered_text}\n\n"
        f"{latest_text}"
        f"--- Original task (verbatim) ---\n{task}\n\n"
        f"--- Working directory ---\n{cwd}\n"
        f"Branch: {_git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD')}\n\n"
        f"--- git status --short ---\n{_git(cwd, 'status', '--short')}\n\n"
        f"--- git diff --stat ---\n{_git(cwd, 'diff', '--stat')}\n\n"
        f"--- Last {len(recent)} tool call(s) ---\n{calls}\n\n"
        f"--- Journal file(s) written this session ---\n{journal_text}\n\n"
        f"--- Recent tool_result heads (<={TOOL_RESULT_CARRY_CAP_BYTES // 1024}KB) ---\n{heads_text}\n\n"
        f"--- Background jobs ---\n{job_text}\n"
        "=== End of mid-task rollover state ===")


def _log_dir():
    """Sibling to `DATA_DIR` (`data/projects/`), never inside it — CLAUDE.md's
    DATA_DIR-pollution rule. Same resolution as mc.memory_push._log_dir."""
    from mc import memory as _mem
    try:
        base = _mem.DATA_DIR.parent
    except Exception:
        base = Path(__file__).resolve().parent.parent / 'data'
    return base / 'midturn_rollover_log'


def _append_row(session, extra):
    """Append one JSONL row to the durable roll log. Never raises."""
    pid = session.get('project_id', '') or 'unknown'
    row = {
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'project_id': pid,
        'session_id': session.get('session_id', ''),
        'claude_session_id': session.get('claude_session_id', ''),
        'threshold': threshold_for(session, state.CONFIG),
        'roll_number': session.get('_mt_rolls', 0),
        'epoch': int(time.time()),
    }
    row.update(extra)
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        safe = ''.join(c for c in pid if c.isalnum() or c in '-_') or 'unknown'
        with open(d / f'{safe}.jsonl', 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row) + '\n')
    except Exception as e:
        _log(f"[midturn-rollover] log write failed: {e}")


def record_roll(session, context_tokens, *, scribe_flushed=False):
    """Append one JSONL line per roll: server log + a file that survives.
    Never raises — a failed write must not break the roll.

    `scribe_flushed` (MC-964 Step C) records whether the forced pre-roll
    checkpoint (`agent_routes._maybe_midturn_roll` ->
    `memory._maybe_checkpoint(session, force=True)`) actually committed
    something — false covers both "nothing new to flush" and a real failure,
    which the acceptance test's replay distinguishes by cross-checking
    against the Scribe's own `checkpoint_*` stats for the same window."""
    pid = session.get('project_id', '') or 'unknown'
    _log(f"[midturn-rollover] {pid}/{session.get('session_id', '')}: context "
         f"{context_tokens} tokens crossed threshold mid-turn — rolling "
         f"(scribe_flushed={scribe_flushed})")
    _append_row(session, {
        'context_tokens': context_tokens,
        'recent_tools': len(session.get('_mt_recent_tools') or []),
        'journal_paths': len(session.get('_mt_journal_paths') or []),
        'tool_result_heads': len(session.get('_mt_tool_result_heads') or []),
        'unanswered_msgs': len(session.get('_mt_unanswered_msgs') or []),
        'scribe_flushed': bool(scribe_flushed),
    })
