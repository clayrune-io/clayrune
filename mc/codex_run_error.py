"""Why a Codex run really failed, and which stderr lines are not the answer.

MC-1147. `codex exec --json` does not always deliver a session-level failure
(`usage_limit_exceeded`) as a stream event; the authoritative copy is the
rollout's `task_complete.error`, written before the process exits. Meanwhile
Codex's own tracing output (`<ISO ts> ERROR rmcp::transport::worker: ...`) is
merged into stdout and was being read as the assistant's reply, so a quota
failure surfaced as an MCP sign-in error glued to the front of a partial
reply, and no allowance was recorded.

Three pure-ish pieces, each used from one place in `mc/agent_runtime.py`:

* `is_tracing_line` / `is_mcp_startup_warning` -- classify stderr lines.
* `read_rollout_error` -- the rollout's own error for THIS run.
* `begin_turn` / `capture` / `summary_override` -- session bookkeeping.

Nothing here imports `mc.agent_runtime` (it imports us).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mc import allowance_state as _allowance_state
from mc.core import _log

SESSION_KEY = '_codex_run_error'

# Rust `tracing` default format: `2026-11-03T17:00:08.742449Z ERROR rmcp::...:`.
# Anchored on the timestamp + level + module path so an assistant sentence that
# merely starts with "ERROR" is never classified as a log line.
_TRACING_RE = re.compile(
    r'^\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s+'
    r'(?:ERROR|WARN|INFO|DEBUG|TRACE)\s+[A-Za-z_][\w]*(?:::[\w]+)+')

# An MCP server that needs OAuth sign-in (or whose transport closed because of
# it) logs this on every startup. The run continues without that server, so it
# is a warning about a tool, never the cause of the run failing.
_MCP_STARTUP_PHRASES = ('AuthRequired', 'Transport channel closed')

# How much of the rollout's tail to scan. `task_complete` is the last record
# of a turn; a turn's closing records are far smaller than this.
_TAIL_BYTES = 256 * 1024

# Codex stamps `completed_at` in whole seconds and the reader starts a moment
# before the CLI does; one second of truncation plus margin.
_RUN_WINDOW_SLACK_S = 2.0


def is_tracing_line(line: str) -> bool:
    """True for a Codex/Rust tracing log line (stderr merged into stdout)."""
    return bool(_TRACING_RE.match(line or ''))


def is_mcp_startup_warning(line: str) -> bool:
    """True for an rmcp AuthRequired / Transport-channel-closed line, whether
    raw or already carrying an MC `[codex warn]` label."""
    t = line or ''
    return 'rmcp::transport' in t and any(p in t for p in _MCP_STARTUP_PHRASES)


def _record_epoch(rec: dict, payload: dict) -> Optional[float]:
    done = payload.get('completed_at')
    if isinstance(done, (int, float)) and not isinstance(done, bool):
        return float(done)
    ts = rec.get('timestamp')
    if isinstance(ts, str) and ts:
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    return None


def read_rollout_error(rollout: Optional[Path], since_epoch: float) -> Optional[dict]:
    """`{'codex_error_info', 'message'}` from the rollout's LAST `task_complete`
    when it carries an error and finished at/after `since_epoch`, else None.

    The time gate matters: a resumed thread's rollout keeps every earlier
    turn's `task_complete`. If this run died before writing its own, the
    previous turn's error must not be reported as this run's cause. A record
    whose finish time cannot be established is refused for the same reason.
    Never raises.
    """
    if rollout is None:
        return None
    try:
        size = rollout.stat().st_size
        with open(rollout, 'rb') as fh:
            if size > _TAIL_BYTES:
                fh.seek(size - _TAIL_BYTES)
            chunk = fh.read()
    except OSError as e:
        _log(f"[codex-run-error] rollout unreadable ({rollout.name}): {e}", flush=True)
        return None
    for line in reversed(chunk.decode('utf-8', errors='replace').split('\n')):
        if '"task_complete"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        payload = rec.get('payload') if isinstance(rec, dict) else None
        if not isinstance(payload, dict) or payload.get('type') != 'task_complete':
            continue
        # The LAST task_complete decides: an older failed turn is history.
        epoch = _record_epoch(rec, payload)
        if epoch is None or epoch < since_epoch - _RUN_WINDOW_SLACK_S:
            return None
        err = payload.get('error')
        if not isinstance(err, dict):
            return None
        info = err.get('codex_error_info')
        if not isinstance(info, str) or not info:
            return None
        return {'codex_error_info': info, 'message': str(err.get('message') or '')}
    return None


def begin_turn(session: Dict[str, Any]) -> float:
    """Reader start: forget a previous turn's captured error, return the
    wall-clock moment this run began (the `since_epoch` for the rollout)."""
    session.pop(SESSION_KEY, None)
    return time.time()


def format_summary(err: dict) -> str:
    return f"[codex error: {err['codex_error_info']}] {err['message']}".rstrip()


def _recorded_since(since_epoch: float) -> bool:
    """True when Codex's allowance record was written during this run -- the
    stream's own ALLOWANCE_EXHAUSTED branch got there first. (Not the
    session's `_allowance_exhausted` flag: that one is never cleared, so it
    would also be true for a run whose PREVIOUS turn hit the limit.)"""
    rec = _allowance_state.get('codex')
    if not rec:
        return False
    try:
        at = datetime.fromisoformat(str(rec.get('recorded_at'))).timestamp()
    except ValueError:
        return False
    return at >= since_epoch - _RUN_WINDOW_SLACK_S


def capture(session: Dict[str, Any], rollout: Optional[Path],
            since_epoch: float) -> Optional[dict]:
    """After an errored Codex run: read its rollout error and act on it.

    Stores the error on the session (for `summary_override`), appends one
    bracketed chat line, and for `usage_limit_exceeded` records the allowance
    exhaustion -- unless the stream already did this run, in which case only
    the summary source is stored. Never raises.
    """
    try:
        err = read_rollout_error(rollout, since_epoch)
        if err is None:
            return None
        session[SESSION_KEY] = err
        is_limit = err['codex_error_info'] == 'usage_limit_exceeded'
        streamed = is_limit and _recorded_since(since_epoch)
        if is_limit and not streamed:
            exhausted = _allowance_state.detect_from_codex_message(
                {'type': 'event_msg', 'payload': {'error': {
                    'message': err['message'],
                    'codex_error_info': err['codex_error_info']}}})
            if exhausted:
                _allowance_state.record_exhaustion('codex', **exhausted)
                session['_allowance_exhausted'] = True
        if not streamed:
            session['log_lines'].append(format_summary(err))
        return err
    except Exception as e:
        _log(f"[codex-run-error] capture failed: {e}", flush=True)
        return None


def summary_override(session: Dict[str, Any]) -> Optional[str]:
    """The completion summary for a Codex run that failed with a rollout
    error, or None to let the ordinary reply-text path decide."""
    err = session.get(SESSION_KEY)
    if not isinstance(err, dict) or session.get('status') != 'error':
        return None
    return format_summary(err)


def locate_rollout(locate: Callable[[str, str], Optional[Path]], project_path: str,
                   session: Dict[str, Any]) -> Optional[Path]:
    """Rollout for this session's Codex thread, or None (no thread id yet, or
    the lookup failed). `locate` is `CodexRuntime.transcript_path`."""
    thread_id = session.get('provider_session_id') or ''
    if not thread_id:
        return None
    try:
        return locate(project_path, thread_id)
    except Exception as e:
        _log(f"[codex-run-error] rollout lookup failed: {e}", flush=True)
        return None
