"""Mid-turn Send routing (MC-1086).

A message sent to a session whose turn is still running used to always
interrupt-and-resume: kill the process, respawn with `-r`, lose whatever the
turn had in flight. Claude Code can now fold a stream-json user message
written to a live process into the turn it is running, so the default is to
STEER; vendors with no live process QUEUE the message for the next turn; the
old behaviour stays available as an explicit INTERRUPT.

This module owns the routing decision and the two new actions. It imports
nothing from `mc.blueprints` (agent_routes passes in the pieces it owns), so
it is testable without a Flask app.

Evidence that stdin steering works on Claude Code 2.1.296 (real runs,
2026-10-12, haiku, a turn of six sequential 6s Bash calls): a user message
written 11.0s in, mid tool call, was consumed at the next tool boundary -- the
CLI emitted ONE `result` for the whole turn, no kill, no respawn; a "stop and
answer now" steer ended the turn after the call in flight, a "also append X"
steer was honoured in the final answer. Run logs: docs/MID_TURN_SEND.md.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional, Tuple

STEER = 'steer'
QUEUE = 'queue'
INTERRUPT = 'interrupt'

# What the composer asks for. Anything but an explicit 'interrupt' is 'steer'.
INTENT_STEER = 'steer'
INTENT_INTERRUPT = 'interrupt'


def normalize_intent(raw: Any) -> str:
    """Request-body `send_mode` -> INTENT_*. Unknown/absent means steer."""
    if isinstance(raw, str) and raw.strip().lower() == INTENT_INTERRUPT:
        return INTENT_INTERRUPT
    return INTENT_STEER


def provider_mode_b_kind(provider: str) -> Optional[str]:
    """`ProviderCapabilities.mode_b_kind` for `provider`, or None when it
    cannot be read (unknown provider, runtime registry not ready). Callers
    treat None as "don't know" and keep the interrupt behaviour."""
    try:
        from mc import agent_runtime
        return agent_runtime.get_runtime(provider).capabilities().mode_b_kind
    except Exception as e:
        print(f'[mid-turn-send] capabilities lookup failed for {provider!r}: {e}', flush=True)
        return None


def _live_proc(session: dict) -> bool:
    proc = session.get('proc')
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def decide(session: dict, mode_b_kind: Optional[str], intent: str) -> Tuple[str, str]:
    """Route a send to a session whose status is 'running'.

    Returns (route, reason). `reason` is for the log and the tests, never
    shown to the user. Order matters: every INTERRUPT guard sits above the
    STEER/QUEUE choice, so a state we cannot steer into keeps today's
    behaviour rather than getting a new one.
    """
    if intent == INTENT_INTERRUPT:
        return INTERRUPT, 'explicit'
    # A turn blocked on a question/plan card was already auto-resolved by the
    # CLI (the Mode B reader kills the process on AskUserQuestion). Folding a
    # message into that turn would steer a turn that is already wasted.
    if (session.get('waiting_for_question') or session.get('waiting_for_plan_approval')
            or session.get('pending_questions')):
        return INTERRUPT, 'waiting-on-user-card'
    # A kill/respawn is already in motion: `proc` is about to be replaced and
    # a stdin write would land on the process being torn down.
    if session.get('_interrupting') or session.get('_respawn_in_flight'):
        return INTERRUPT, 'respawn-in-flight'
    # status says running but the process is gone: only interrupt-and-resume
    # heals that; queueing or writing stdin would wait on nothing.
    if not _live_proc(session):
        return INTERRUPT, 'no-live-process'

    provider = (session.get('provider') or 'claude').lower()
    if provider == 'claude' and mode_b_kind == 'native' and session.get('mode') == 'B':
        return STEER, 'claude-mode-b-live-stdin'
    if mode_b_kind in ('none', 'synthetic') or provider == 'claude':
        # No stdin channel into a running turn (vendors; Claude Mode A, whose
        # prompt is a -p argument): hold it for the next turn.
        return QUEUE, 'no-live-stdin'
    # Unknown capability: keep the old behaviour.
    return INTERRUPT, 'capability-unknown'


def queue_message(session: dict, message: str, user_label: str) -> None:
    """Hold `message` for the next turn. Same shape Mode A's /agent/followup
    queue uses: `pending_followups` plus a `> [queued]` chat line."""
    session.setdefault('pending_followups', []).append(message)
    session['log_lines'].append(f"> [queued] {user_label}: {message}")
    session.pop('_send_already_logged', None)


def build_steer_payload(content: str) -> str:
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}) + '\n'


def steer_live(session: dict, *, content: str,
               write: Callable[..., Optional[dict]],
               owner: Optional[Tuple[str, str]] = None,
               ack_timeout: float = 5.0) -> dict:
    """Write `content` to the live Claude process's stdin so the CLI folds it
    into the turn it is running. No kill, no respawn, no new delegation turn
    (it is the same CLI turn; the one `result` it ends with covers both).

    `write` is agent_routes' `_bounded_stdin_write` (bounded, watchdogged,
    shares the session's stdin_lock). `owner` is (key, value) for the turn
    owner: a human steering a turn takes it over exactly as an interrupt does
    (MC-970), so a later background wake is not announced to a spawner.

    Returns {'ack': 'written'|'failed'|'unknown', 'error'?}. On 'failed' the
    message was NOT delivered and the caller falls back to interrupt.
    """
    session.pop('_send_already_logged', None)
    session.pop('pending_questions', None)
    session['waiting_for_question'] = False
    session['waiting_for_plan_approval'] = False
    ack = write(session['proc'], build_steer_payload(content), session=session,
                lock=session.get('stdin_lock'), wait_for_ack=ack_timeout,
                context='steer-B')
    ack = ack or {'ack': 'unknown'}
    if ack.get('ack') == 'written':
        session['last_output_time'] = time.time()
        if owner:
            session[owner[0]] = owner[1]
    return ack


# ── vendor queue drain ──────────────────────────────────────────────────────

def drain_next(session: dict, deliver: Callable[[dict, str], Tuple[bool, str]]) -> bool:
    """Deliver the oldest queued message of a NON-claude session now that its
    turn has ended. Returns True if one was handed off.

    Claude Mode A drains `pending_followups` in its own reader; vendor
    runtimes never did, because nothing could queue to them before MC-1086.
    The guardian's stuck-queue replay (`_auto_dispatch_followup`) spawns
    `claude -r`, which is wrong for a vendor session -- agent_routes skips
    vendor sessions there and this is their only drain.
    """
    if (session.get('provider') or 'claude').lower() == 'claude':
        return False
    pending = session.get('pending_followups') or []
    # 'stopped' / user-interrupted turns: _stop_session already cleared the
    # queue, and anything left is stale by the user's own act.
    if not pending or session.get('status') not in ('completed', 'idle', 'error'):
        return False
    if session.get('_interrupted'):
        return False
    msg = pending.pop(0)
    if not pending:
        session.pop('pending_followups', None)
    ok, err = deliver(session, msg)
    if not ok:
        session['log_lines'].append(f"[queued follow-up not sent ({err}): {msg}]")
    return True


def deliver_via_followup(app: Any, followup: Callable[[str], Any],
                         session: dict, message: str) -> Tuple[bool, str]:
    """Deliver through the real /agent/followup handler (it owns the vendor
    respawn, allowance refusal and turn bookkeeping) from a non-request
    thread, the way the delegation inbox does."""
    if app is None:
        return False, 'no app context'
    with app.test_request_context(json={'message': message,
                                         'session_id': session.get('session_id', '')}):
        resp = app.make_response(followup(session.get('project_id', '')))
        if resp.status_code >= 400:
            body = resp.get_json(silent=True) or {}
            return False, str(body.get('error') or f'HTTP {resp.status_code}')
    return True, ''
