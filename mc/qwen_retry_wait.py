"""Wait-only accounting for Qwen's documented stderr capacity heartbeats.

Only explicit remaining-sleep windows count. Time in model/tool execution,
silence, or a request after that sleep expires never counts as waiting.
Short sleeps without a heartbeat cannot be measured, so this is explicitly
partial support. The timer is local to a spawned turn, not vendor history.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict

from mc import state
from mc.retry_budgets import durations

KEY = '_qwen_retry_wait'
_HEARTBEAT = re.compile(
    r'^\[qwen-code\] Waiting for API capacity\.\.\. attempt (\d+), retry in (\d+)s$')


def _eligible(session: Dict[str, Any]) -> bool:
    from mc.launch_marker import UNATTENDED_TRIGGER_TYPES
    return (session.get('provider') == 'qwen'
            and session.get('trigger_type') in UNATTENDED_TRIGGER_TYPES)


def _account(wait: Dict[str, Any], now: float) -> None:
    wait['spent'] += max(0.0, min(now, wait['until']) - wait['last'])
    wait['last'] = max(wait['last'], now)


def observe_line(session: Dict[str, Any], line: str, now: float) -> bool:
    """Consume only a precise vendor heartbeat; other text is not a signal."""
    if not _eligible(session):
        return False
    match = _HEARTBEAT.fullmatch(line.strip())
    proc = session.get('proc')
    wait = session.get(KEY)
    if match:
        if not wait or wait.get('proc') is not proc:
            wait = {'proc': proc, 'spent': 0.0, 'last': now, 'until': now,
                    'notice': None}
            session[KEY] = wait
        _account(wait, now)
        wait['until'] = now + int(match[2])
        session['last_output_time'] = now
        if wait['notice'] is None or now - wait['notice'] >= 300:
            session.setdefault('log_lines', []).append(line.strip())
            wait['notice'] = now
        return True
    # A real protocol event can end a known wait early. Arbitrary stderr
    # warnings must neither start a timer nor extend its end.
    if wait and wait.get('proc') is proc:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return False
        if isinstance(event, dict) and event.get('type') in {
                'assistant', 'stream_event', 'result', 'user'}:
            _account(wait, now)
            wait['until'] = now
    return False


def check_session(session: Dict[str, Any], now: float, lock: Any,
                  kill: Callable[[Any], Any]) -> bool:
    """Guardian tick; stop only the same owned process in a known wait window."""
    if not _eligible(session):
        return False
    with lock:
        proc = session.get('proc')
        wait = session.get(KEY)
        if (session.get('status') != 'running' or not proc or proc.poll() is not None
                or not wait or wait.get('proc') is not proc
                or session.get('guardian_state') == 'recovering'
                or now >= wait['until'] or session.get('waiting_for_question')
                or session.get('waiting_for_plan_approval')):
            return False
        _account(wait, now)
        minutes = durations(state.CONFIG)['max_wait']
        if wait['spent'] < minutes * 60:
            return False
        session['status'] = 'error'
        session['last_status_change_time'] = now
        session['guardian_state'] = 'needs_attention'
        session['log_lines'].append(
            f'[qwen retry cap: observed capacity waiting reached {minutes} min; '
            'stopped this turn. Usage classification and cadence are unsupported.]')
    kill(proc)
    return True
