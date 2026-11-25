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
    handoff (role/text turns, 8000 chars) does not: the historical first message,
    branch/worktree, `git status` + `git diff --stat`, the last tool calls,
    and background jobs;
  * `record_roll` — durable per-roll log outside DATA_DIR (the project
    activity_log keeps 20 entries).

The roll itself goes through the existing interrupt path in agent_routes, so
the MC session_id is kept and `notify_session` callbacks still fire.

Off unless `midturn_rollover_enabled` is true (default false).
"""
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from mc import state
from mc import bg_job_liveness as _bg_live
from mc.core import _log, TimestampedLines
from mc.long_job_guidance import job_route_url as _job_url
from mc.rollover_fresh_floor import clear_floor
from mc import rollover_decision as _decision

RECENT_TOOLS_KEEP = 10
# MC-964 Step C: the last tool_results' first lines, carried across a roll
# because the transcript handoff (role/text turns only) drops tool_result
# content entirely — the exact gap that cost a prior roll its own measurements
# (docs/_journal/b2d85e51-memory-overhaul.md). Capped, not unlimited: a larger
# carry raises the fresh session's floor on every roll (plan §7.3).
TOOL_RESULT_CARRY_CAP_BYTES = 4096
_TOOL_RESULT_HEAD_CHARS = 300
_JOURNAL_MARKER = 'docs/_journal/'
# A shell write into a journal (`cat >> .../docs/_journal/x.md`, `tee -a`).
# Write forms only: the carried section says "written", so a `cat` read is out.
_JOURNAL_SHELL_WRITE_RE = re.compile(
    r'''(?:>|\btee(?:\s+-a)?\s)\s*["']?'''
    r'''([^\s"'<>|;&()]*docs[\\/]_journal[\\/][^\s"'<>|;&()*]+\.md)''')
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
# User instructions must survive a roll in full. Retire unanswered messages
# on main-conversation text after a roll or on turn completion, never by size.
_INPUT_PREVIEW = 160
# Line caps, not a character cap: 1,000 characters bound at ~14 `diff --stat`
# rows, so later files AND the trailing `N files changed` summary vanished.
# When the line cap bites, the last line is kept (that summary; for `status
# --short` just the last entry) and the count of omitted lines is stated.
_GIT_OUT_LINES = 30
_GIT_LINE_CAP = 160
_GIT_TIMEOUT_S = 10
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

ROLL_MESSAGE = ("Your context was rolled over mid-task because it reached the "
                "token limit. This is the SAME task, not a new one — continue "
                "it from where it stopped using the state above. Do not "
                "restart it and do not redo finished work.")


# State block frame and section heads. `carried_user_text` reads back what
# `build_state_block` writes, so both use these.
_BLOCK_START = '=== Mid-task rollover state ==='
_BLOCK_END = '=== End of mid-task rollover state ==='
_INFLIGHT_HEAD = ('--- Message this turn was answering when the roll '
                  'happened (NOT answered yet; finish it) ---')
_UNANSWERED_HEAD = '--- Unanswered user messages (answer these first) ---'
_LATEST_HEAD = '--- Latest user message (current direction) ---'
_FIRST_IS_CURRENT = 'the current task; no later message was recorded'
_CWD_HEAD = '--- Working directory ---'
_NO_UNANSWERED = '(none)'


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
    """The `file_path` a Write/Edit call targeted, or the file a shell command
    redirected into, if it's under `docs/_journal/` — MC-964 Step C carries
    these across a roll since a fresh session otherwise has no way to know
    which journal file already holds this task's log (re-reading the wrong/no
    file re-derives it)."""
    if not isinstance(tool_input, dict):
        return None
    fp = tool_input.get('file_path')
    if not isinstance(fp, str) or not fp:
        command = tool_input.get('command')
        hit = (_JOURNAL_SHELL_WRITE_RE.search(command)
               if isinstance(command, str) else None)
        return hit.group(1) if hit else None
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
        # `tool_use_id` lets note_bg_job_result attach the output file and
        # the job's processes once the tool_result arrives (MC-1149).
        jobs.append({'tool': name, 'input': _preview(tool_input), 'tool_use_id': tid})
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


_BG_SHELL_TOOLS = frozenset({'bash', 'powershell', 'shell', 'run_shell_command'})


def note_bg_job_result(session, tool_use_id, tool_name, text):
    """Attach a background job's output file, task id and processes to its
    `_mt_bg_jobs` entry from the Bash tool_result that announced it
    (MC-1149; shapes and the descendant scan: mc/bg_job_liveness.py). A
    foreground call the CLI moved to the background after its timeout has no
    `run_in_background` input, so it never got an entry from note_tool_use;
    it dies at a roll the same way, so one is added here. Never raises."""
    if not enabled():
        return
    try:
        facts = _bg_live.parse_tool_result(text)
        if not facts:
            return
        jobs = session.setdefault('_mt_bg_jobs', [])
        job = next((j for j in jobs if tool_use_id and j.get('tool_use_id') == tool_use_id),
                   None)
        if job is None:
            if not facts['moved_on_timeout']:
                return
            # Only a shell tool can be moved to the background; a Read or
            # Grep whose output quotes the phrase is not a job (AP39).
            if tool_name and tool_name.lower() not in _BG_SHELL_TOOLS:
                return
            job = {'tool': tool_name or 'Bash', 'input': '(foreground call moved to the '
                   'background; see the recent tool calls)', 'tool_use_id': tool_use_id}
            jobs.append(job)
            del jobs[:-_BG_JOBS_KEEP]
        job.update({'output_file': facts['output_file'], 'task_id': facts['task_id'],
                    'moved_on_timeout': facts['moved_on_timeout'],
                    'started': time.time()})
        job['holders'] = _bg_live.holder_pids(
            facts['output_file'], getattr(session.get('proc'), 'pid', None))
    except Exception as e:
        _log(f"[midturn-rollover] note_bg_job_result failed: {e}")


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


def note_user_message(session, text, *, mid_turn=False):
    """Record a user message for the rollover state block (MC-1088).

    Every message is the session's latest user message. One that is not
    `mid_turn` also opens a turn (`note_turn_prompt`). `mid_turn=True` marks
    one delivered WHILE a turn was running -- stdin steer, or a follow-up held
    in `pending_followups`: the CLI folds it into the running turn (transcript
    `queue-operation` ... `absorbed_mid_turn`) or the interrupt that performs
    a roll drops the queue, and either way the transcript handoff (role/text
    turns) does not reliably carry it. Those are kept in `_mt_unanswered_msgs`
    until post-roll assistant text retires carried entries, or the turn ends
    (`note_turn_complete`). Keep the full text of every pending instruction.
    Never raises."""
    if not enabled() or not isinstance(text, str) or not text.strip():
        return
    try:
        message = text.strip()
        session['_mt_latest_user_msg'] = message
        if not mid_turn:
            session['_mt_turn_prompt'] = message
        else:
            msgs = session.setdefault('_mt_unanswered_msgs', [])
            msgs.append({'ts': datetime.now(timezone.utc).strftime('%H:%M:%SZ'),
                         'text': message})
    except Exception as e:
        _log(f"[midturn-rollover] note_user_message failed: {e}")


def note_turn_prompt(session, text):
    """Record the message that opened the turn now running, without making it
    the latest message (a follow-up delivered from the queue may have newer
    ones queued behind it). A mid-turn steer never replaces it, so a roll can
    still say what the turn was answering; `note_turn_complete` clears it."""
    if not enabled() or not isinstance(text, str) or not text.strip():
        return
    session['_mt_turn_prompt'] = text.strip()


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
    session.pop('_mt_turn_prompt', None)


def note_self_started_turn(session):
    """The CLI started a turn Clayrune did not send (a background-job wake, a
    peer message). No message of ours opened it, so a turn prompt still set is
    left over from a send that was refused or never written. Showing that as
    "NOT answered yet" pointed the fresh session at a message the agent never
    received (2026-11-25 review, F6)."""
    session.pop('_mt_turn_prompt', None)


def note_message_undelivered(session, text, *, mid_turn=False, previous_prompt=None):
    """`text` was noted (`note_user_message`, same `mid_turn`) and then did not
    reach the agent: the send was refused, or its stdin write failed. Take the
    note back, so a roll never calls the message unanswered and an undelivered
    child report is not answered ahead of the retry that delivers it.

    `previous_prompt` is `_mt_turn_prompt` as it stood before the note: a
    refused interrupt leaves the old turn running, and that turn is still
    answering it. Only a note for this same text is undone; a later message
    that opened a turn of its own is left alone. The latest-message record is
    not rewound. Never raises."""
    try:
        if not isinstance(text, str) or not text.strip():
            return
        message = text.strip()
        if mid_turn:
            msgs = session.get('_mt_unanswered_msgs') or []
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get('text') == message and not msgs[i].get('carried'):
                    del msgs[i]
                    break
        elif session.get('_mt_turn_prompt') == message:
            if previous_prompt and previous_prompt != message:
                session['_mt_turn_prompt'] = previous_prompt
            else:
                session.pop('_mt_turn_prompt', None)
    except Exception as e:
        _log(f"[midturn-rollover] note_message_undelivered failed: {e}")


def note_send_response(session, text, previous_prompt, status_code, body):
    """Undo the note for `text` when the handler's response says it was not
    delivered. An unknown outcome (`stdin_write_ack: unknown`, `delivered:
    None`) may still have reached the agent, so its note stays."""
    if not isinstance(status_code, int) or status_code < 400:
        return
    body = body if isinstance(body, dict) else {}
    if body.get('stdin_write_ack') == 'unknown' or body.get('delivered', False) is None:
        return
    note_message_undelivered(session, text, previous_prompt=previous_prompt)


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
        decision = session.get('_mt_attempt_decision') or _decision.capture(
            session, 'midturn', 'tokens', tokens)
        _decision.append_row(_log_dir(), decision, {'event': 'gave_up', 'failures': failures})


def begin_roll(session):
    """Reset the per-process readings for the fresh session a roll is about to
    start. The first main-conversation reading it reports becomes the baseline
    `should_roll` measures growth from (MIN_GROWTH_TOKENS). Called by
    agent_interrupt once the roll is committed to, so a refused or failed
    attempt never leaves a baseline armed."""
    clear_floor(session)
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
    lines = [ln if len(ln) <= _GIT_LINE_CAP else ln[:_GIT_LINE_CAP] + ' [... truncated]'
             for ln in text.splitlines()]
    if len(lines) > _GIT_OUT_LINES:
        omitted = len(lines) - _GIT_OUT_LINES
        lines = (lines[:_GIT_OUT_LINES - 1]
                 + [f'[... truncated: {omitted} line(s) omitted]', lines[-1]])
    return '\n'.join(lines) or '(clean)'


def _job_route_url(session):
    """This session's server-owned job route, for the state block's relaunch
    advice. Port resolution matches mc/desk_dispatch.py."""
    import os
    try:
        port = int(os.environ.get('MC_PORT') or state.CONFIG.get('port') or 5199)
    except (TypeError, ValueError):
        port = 5199
    return _job_url(port, session.get('project_id', '') or '<project>',
                    session.get('session_id', '') or '<session>')


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

    When the roll interrupts a turn, the message that opened it comes first:
    on 2026-11-25 a steer became the only "current direction" and the message
    being worked on was never answered. A text quoted there is not printed
    again; the later sections point at it.

    MC-1088 puts the user's mid-turn messages next ("Unanswered user messages
    (answer these first)") and the latest user message as current direction:
    a roll used to rebuild the session from the first
    prompt alone, so messages the CLI had absorbed mid-turn were never
    answered (2026-10-12, transcript 03546a91).

    MC-964 Step C adds two sections the plan's replay showed missing: the
    session's own `docs/_journal/` file(s) (so the fresh session re-reads its
    own log instead of re-deriving what it already wrote), and the last
    tool_results' first lines (`note_tool_result_text`, capped at
    `TOOL_RESULT_CARRY_CAP_BYTES`) — the transcript handoff carries only
    role/text turns, so command/search OUTPUT otherwise vanishes at the roll."""
    task = session.get('task') or '(first message unavailable)'
    recent = session.get('_mt_recent_tools') or []
    calls = '\n'.join(f"- {c['name']}: {c['input']}" if c['input'] else f"- {c['name']}"
                      for c in recent) or '(none recorded)'
    # MC-1149: per job, the output file and whether its processes survived
    # the tree kill the roll just did -- "relaunch any that are still needed"
    # produced duplicates of survivors and lost the output of the dead.
    job_text = _bg_live.render_jobs(
        session.get('_mt_bg_jobs') or [], now=time.time(),
        job_url=_job_route_url(session))
    journal_paths = session.get('_mt_journal_paths') or []
    journal_text = ('\n'.join(f"- {jp}" for jp in journal_paths)
                     or '(none written under docs/_journal/ this session)')
    heads = session.get('_mt_tool_result_heads') or []
    heads_text = ('\n'.join(f"- {h['tool']}: {h['head']}" for h in heads)
                  or '(none captured)')
    unanswered = session.get('_mt_unanswered_msgs') or []
    unanswered_text = ('\n'.join(f"- [{m['ts']}] {m['text']}" for m in unanswered)
                       or _NO_UNANSWERED)
    latest = session.get('_mt_latest_user_msg')
    latest_body = latest or '(none recorded after the first message)'
    # A dispatched one-prompt worker never records a later message, and its
    # first message IS the task: only call it historical when something later
    # superseded it (AP40).
    first_label = ("historical; may be long finished, not the current task"
                   if latest else _FIRST_IS_CURRENT)
    inflight = session.get('_mt_turn_prompt')
    # A one-prompt worker's first turn: the first-message section already
    # presents it as the current task (AP40), so don't add a second copy.
    if latest is None and inflight == task.strip():
        inflight = None
    inflight_text = ''
    first_body = task
    if inflight:
        inflight_text = f"{_INFLIGHT_HEAD}\n{inflight}\n\n"
        quoted = '(the message this turn was answering, quoted in the first section)'
        if latest == inflight:
            latest_body = quoted
        if task.strip() == inflight:
            first_label, first_body = 'the turn still in progress', quoted
    latest_text = f"{_LATEST_HEAD}\n{latest_body}\n\n"
    return (
        f"{_BLOCK_START}\n"
        f"{inflight_text}"
        f"{_UNANSWERED_HEAD}\n{unanswered_text}\n\n"
        f"{latest_text}"
        f"--- First message of this chat ({first_label}) ---\n{first_body}\n\n"
        f"{_CWD_HEAD}\n{cwd}\n"
        f"Branch: {_git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD')}\n\n"
        f"--- git status --short ---\n{_git(cwd, 'status', '--short')}\n\n"
        f"--- git diff --stat ---\n{_git(cwd, 'diff', '--stat')}\n\n"
        f"--- Last {len(recent)} tool call(s) ---\n{calls}\n\n"
        f"--- Journal file(s) written this session ---\n{journal_text}\n\n"
        f"--- Recent tool_result heads (<={TOOL_RESULT_CARRY_CAP_BYTES // 1024}KB) ---\n{heads_text}\n\n"
        f"--- Background jobs ---\n{job_text}\n"
        f"{_BLOCK_END}")


def _section(block, head, next_head):
    """Body of one state-block section, '' when the section is absent."""
    start = block.find(head + '\n')
    if start < 0:
        return ''
    start += len(head) + 1
    end = block.find('\n\n' + next_head, start)
    return block[start:end].strip() if end >= 0 else ''


def carried_user_text(text):
    """The user's own words inside the state block that opens a mid-task-rolled
    session: the message the interrupted turn was answering, then the messages
    sent while it ran. '' when `text` holds no state block, or the block names
    no message still owed an answer.

    Such a turn is nothing but injected text, so the handoff reader dropped it
    whole and the NEXT handoff opened on an Assistant line with the message it
    was answering nowhere in it (2026-11-25 review, F4). Never raises."""
    if not isinstance(text, str):
        return ''
    start = text.find(_BLOCK_START)
    end = text.find(_BLOCK_END, start)
    if start < 0 or end < 0:
        return ''
    block = text[start:end]
    inflight = _section(block, _INFLIGHT_HEAD, _UNANSWERED_HEAD)
    if not inflight:
        # A one-prompt worker: its first message IS the turn in flight (AP40).
        inflight = _section(
            block, f'--- First message of this chat ({_FIRST_IS_CURRENT}) ---', _CWD_HEAD)
    unanswered = _section(block, _UNANSWERED_HEAD, _LATEST_HEAD)
    parts = [inflight] if inflight else []
    if unanswered and unanswered != _NO_UNANSWERED:
        parts.append(f'[sent while that turn was running]\n{unanswered}')
    return '\n\n'.join(parts)


def _log_dir():
    """Sibling to `DATA_DIR` (`data/projects/`), never inside it — CLAUDE.md's
    DATA_DIR-pollution rule. Same resolution as mc.memory_push._log_dir."""
    from mc import memory as _mem
    try:
        base = _mem.DATA_DIR.parent
    except Exception:
        base = Path(__file__).resolve().parent.parent / 'data'
    return base / 'midturn_rollover_log'


def roll_decision(session):
    """Use the very threshold checked by the gate in the eventual audit row."""
    decision = None

    def over_threshold(tokens, source):
        nonlocal decision
        decision = _decision.capture(source, 'midturn', 'tokens', tokens)
        return decision.crosses_threshold

    return decision if should_roll(session, over_threshold) else None


def record_roll(session, decision, *, scribe_flushed=False):
    """Log an accepted mid-turn interruption using its outgoing decision."""
    data = decision.fields
    _log(f"[midturn-rollover] {data['project_id']}/{data['session_id']}: context "
         f"{data['context_tokens']} tokens crossed threshold mid-turn "
         f"(scribe_flushed={scribe_flushed})")
    _decision.append_row(_log_dir(), decision, {
        'roll_number': session.get('_mt_rolls', 0),
        'scribe_flushed': bool(scribe_flushed),
    })


def record_boundary_roll(decisions, source, project_id, session_id, native_id, reason, detail):
    decision = decisions[0] if decisions else _decision.capture(
        source, 'message_boundary', reason, detail, native_id=native_id)
    _decision.append_row(_log_dir(), decision, {
        'project_id': project_id,
        'session_id': session_id or decision.fields['session_id'],
    })
