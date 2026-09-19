"""Mid-turn context rollover (docs/CONTEXT_ECONOMY_SPEC.md §2, extended).

`_auto_fresh_trigger` (agent_routes) only runs when a NEW message arrives from
Clayrune. A dispatched worker gets one prompt and then a single long tool loop,
so its context climbed 0 -> 325k tokens across 301 model calls with no message
boundary for the check to run at (docs/_journal/rollover-not-firing.md).

This module holds the parts that need no Flask/agent_routes state:

  * bookkeeping the stream readers feed (`note_tool_use`, `note_tool_results`):
    which tool calls are still awaiting a result, the last few calls, and any
    `run_in_background` jobs;
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
from mc.core import _log

RECENT_TOOLS_KEEP = 10
_BG_JOBS_KEEP = 10
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


def note_tool_use(session, block):
    """Record one `tool_use` content block. No-op while the flag is off."""
    if not enabled() or not isinstance(block, dict):
        return
    name = block.get('name', '')
    tool_input = block.get('input')
    tid = block.get('id')
    if tid:
        session.setdefault('_mt_pending_tools', set()).add(tid)
    recent = session.setdefault('_mt_recent_tools', [])
    recent.append({'name': name, 'input': _preview(tool_input)})
    del recent[:-RECENT_TOOLS_KEEP]
    if isinstance(tool_input, dict) and tool_input.get('run_in_background'):
        jobs = session.setdefault('_mt_bg_jobs', [])
        jobs.append({'tool': name, 'input': _preview(tool_input)})
        del jobs[:-_BG_JOBS_KEEP]


def note_tool_results(session, content):
    """Clear the pending-call ids a `user` message's tool_result blocks answer.
    Returns True when at least one tool_result was seen (a tool boundary)."""
    if not enabled() or not isinstance(content, list):
        return False
    pending = session.setdefault('_mt_pending_tools', set())
    seen = False
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'tool_result':
            seen = True
            pending.discard(block.get('tool_use_id'))
    return seen


def should_roll(session, over_threshold):
    """True when the session should roll NOW: flag on, a Claude stream (the
    only vendor with a per-call usage figure — the others report usage at turn
    end, where the follow-up check already runs), token threshold crossed, no
    tool call still awaiting its result, and no roll already requested or in
    flight. `over_threshold` is agent_routes._context_tokens_over_threshold."""
    if not enabled():
        return False
    if (session.get('provider') or 'claude').lower() != 'claude':
        return False
    if session.get('_mt_roll_requested') or session.get('_interrupting'):
        return False
    if session.get('incognito') or session.get('waiting_for_question') \
            or session.get('waiting_for_plan_approval'):
        return False
    if session.get('_mt_pending_tools'):
        return False
    return bool(over_threshold(session.get('context_tokens')))


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


def build_state_block(session, cwd):
    """The mid-task state the transcript handoff cannot carry. Every section is
    always present; an unavailable one says so instead of vanishing."""
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
    return (
        "=== Mid-task rollover state ===\n"
        f"--- Original task (verbatim) ---\n{task}\n\n"
        f"--- Working directory ---\n{cwd}\n"
        f"Branch: {_git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD')}\n\n"
        f"--- git status --short ---\n{_git(cwd, 'status', '--short')}\n\n"
        f"--- git diff --stat ---\n{_git(cwd, 'diff', '--stat')}\n\n"
        f"--- Last {len(recent)} tool call(s) ---\n{calls}\n\n"
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


def record_roll(session, context_tokens):
    """Append one JSONL line per roll: server log + a file that survives.
    Never raises — a failed write must not break the roll."""
    pid = session.get('project_id', '') or 'unknown'
    row = {
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'project_id': pid,
        'session_id': session.get('session_id', ''),
        'claude_session_id': session.get('claude_session_id', ''),
        'context_tokens': context_tokens,
        'threshold': state.CONFIG.get('context_rollover_tokens'),
        'recent_tools': len(session.get('_mt_recent_tools') or []),
        'epoch': int(time.time()),
    }
    _log(f"[midturn-rollover] {pid}/{row['session_id']}: context "
         f"{context_tokens} tokens crossed threshold mid-turn — rolling")
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        safe = ''.join(c for c in pid if c.isalnum() or c in '-_') or 'unknown'
        with open(d / f'{safe}.jsonl', 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row) + '\n')
    except Exception as e:
        _log(f"[midturn-rollover] log write failed: {e}")
