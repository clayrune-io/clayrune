"""Tell a dispatched agent's spawner that the child is paused on a question.

A child that ends a turn on an `mc:question` block (or a native AskUserQuestion)
is PAUSED, not finished: `_maybe_notify_spawner` must never send the
"[dispatched agent finished]" callback for it (2026-09-18 duplicate-notify fix).
But that guard also meant the spawner heard nothing at all, so a question
reached only the child's chat and the offline channel -- the parent that
dispatched the work, and is the one meant to decide, never woke (Tilda
53b139a983d6 / Dave, 2026-10-05).

This module builds a DISTINCT callback ("[dispatched agent asked a question]")
and hands it to the same durable outbox the finished callback uses. It never
touches `_notify_session_sent`, so the child's eventual real finish still fires
the normal callback. Message-building and per-question dedupe live here; the
only code in agent_routes.py is the call from `_maybe_notify_spawner`.
"""
from __future__ import annotations

from typing import Any

from mc.core import _log
from mc.delegation_delivery import callback_payload, event_id_for_turn

# Per-session set of question ids whose callback was already enqueued. Kept on
# the session dict (same lifetime as `pending_questions`), so the Mode B result
# path and the stream-reader `finally` -> `_log_agent_completion` path, which
# both reach `_maybe_notify_spawner` for one pause, send one callback.
SENT_KEY = '_question_callbacks_sent'

# Fixed, not the child's live status: the payload for an event id must be
# byte-identical on every attempt (the outbox rejects a changed payload under
# the same id as a conflict).
PAUSED_STATUS = 'waiting_for_answer'


def spawner_of(session: dict) -> str:
    """The session id to notify, or '' when there is none (no spawner, a
    session naming itself, or an incognito session)."""
    if session.get('incognito'):
        return ''
    sid = (session.get('_notify_session') or '').strip()
    return '' if not sid or sid == session.get('session_id') else sid


def question_event_id(child_sid: str, turn: int, question_id: str) -> str:
    """Distinct from the finished event for the same turn, and still rsplit-able
    on ':turn:' to the child id (delegation status list does that)."""
    return f"{event_id_for_turn(child_sid, turn)}:question:{question_id}"


def _option_lines(options: Any) -> list[str]:
    lines = []
    for opt in options if isinstance(options, list) else []:
        if isinstance(opt, dict):
            label = str(opt.get('label') or '').strip()
            desc = str(opt.get('description') or '').strip()
            if label or desc:
                lines.append(f"   - {label}: {desc}" if label and desc else f"   - {label or desc}")
        elif str(opt).strip():
            lines.append(f"   - {str(opt).strip()}")
    return lines


def render_questions(pending: dict) -> str:
    """The question(s) of one pending entry, verbatim: header, text, options."""
    out = []
    questions = pending.get('questions')
    if not isinstance(questions, list):
        questions = [pending]          # native AskUserQuestion with a bare body
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            out.append(f"{i}. {q}")
            continue
        header = str(q.get('header') or '').strip()
        text = str(q.get('question') or q.get('text') or '').strip()
        out.append(f"{i}. " + (f"[{header}] " if header else '') + text)
        out.extend(_option_lines(q.get('options')))
        if q.get('multiSelect'):
            out.append("   (more than one option may be chosen)")
    return '\n'.join(out)


def question_payload(child: dict, pending: dict, event_id: str) -> dict:
    """Same shape as `callback_payload` (the inbox only needs `message`), with
    the question as the summary and a message that says how to answer."""
    payload = callback_payload(child, render_questions(pending), event_id)
    sid = child.get('session_id', '')
    pid = child.get('project_id', '')
    payload['status'] = PAUSED_STATUS
    payload['message'] = (
        f"[dispatched agent asked a question] {payload['who']} (session {sid[:12]}) "
        f"is PAUSED waiting for an answer. It has NOT finished.\n\n"
        f"Task: {child.get('task', '')}\n\n"
        f"Its question, verbatim:\n{payload['summary']}\n\n"
        f"To answer: POST /api/project/{pid}/agent/send with "
        f'{{"session_id": "{sid}", "message": "<your answer>"}}. That resumes the '
        f"paused agent (the send route hands an idle session to /agent/followup, "
        f"the same path its chat form uses). Answer with an option's label, or "
        f"your own text. The question also shows in the agent's chat, so the "
        f"user may have answered it already; if the agent is no longer paused, "
        f"do not send. When it truly finishes you get the normal "
        f"[dispatched agent finished] callback.")
    return payload


def notify_spawner_of_question(session: dict, store: Any) -> int:
    """Enqueue one question callback per not-yet-sent pending question.

    Returns how many were enqueued. Never raises: a failure here must not break
    the completion path that calls it. Does nothing for a session with no
    spawner, for a turn whose finished callback is already spent
    (`_notify_session_sent` -- a human-typed follow-up, MC-970; the question
    stays in the child's chat for them), or without a delivery store."""
    try:
        parent = spawner_of(session)
        if not parent or session.get('_notify_session_sent'):
            return 0
        if store is None:
            _log('[delegation-delivery] store not initialized; question callback '
                 'not sent', flush=True)
            return 0
        sent = session.setdefault(SENT_KEY, [])
        turn = int(session.get('_delegation_turn', 1))
        child_sid = session.get('session_id', '')
        project_id = session.get('project_id', '')
        count = 0
        for pending in session.get('pending_questions') or []:
            qid = pending.get('question_id') if isinstance(pending, dict) else ''
            if not qid or qid in sent:
                continue
            event_id = question_event_id(child_sid, turn, qid)
            payload = question_payload(session, pending, event_id)
            try:
                store.record_completion_source(event_id, project_id, parent, payload)
                if store.enqueue(event_id, project_id, parent, payload):
                    count += 1
            except Exception as exc:
                _log(f'[delegation-delivery] question enqueue failed for {event_id}: {exc}',
                     flush=True)
                continue          # not marked sent: the next boundary retries
            sent.append(qid)
        return count
    except Exception as exc:
        _log(f'[delegation-delivery] question callback failed: {exc}', flush=True)
        return 0
