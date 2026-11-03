"""Deliver current Clayrune instructions alongside native conversation history.

Codex, Gemini and Qwen resume their saved messages, which can include an older
injected persona. The caller refreshes the session context from current rules;
send it when changed or when delivery has not yet been recorded for the session.
"""
from __future__ import annotations

import hashlib
from typing import IO, Any, Mapping, MutableMapping, Optional

CONTEXT_HEADER = '=== CURRENT CLAYRUNE CONTEXT ==='
MESSAGE_HEADER = "=== THE USER'S MESSAGE ==="


def context_sha(context: str) -> str:
    """Fingerprint the final provider-specific context, excluding the task."""
    return hashlib.sha256(context.encode()).hexdigest()


def followup_prompt(session: Mapping[str, Any], context: str, message: str) -> str:
    """Native history already has unchanged context; refresh only on change."""
    if session.get('_ctx_delivered_sha') == context_sha(context):
        return message
    return resume_prompt(context, message)


def write_context_prompt(stream: Optional[IO[Any]], prompt: str,
                         session: MutableMapping[str, Any], context: str) -> None:
    """Stamp delivery only after the pipe accepts and flushes this context.

    Bare followups and retries of a different prompt must not advance the
    fingerprint. Failed writes remain eligible for context delivery next turn.
    """
    if '_steward_role_ack' in session:
        from mc.steward_role_dispatch import write_native_delivery
        write_native_delivery(stream, prompt, session, context)
        return
    try:
        if stream is None:
            raise BrokenPipeError('prompt stdin unavailable')
        stream.write(prompt)
        stream.close()
    except Exception as exc:
        session.setdefault('log_lines', []).append(
            f'[prompt delivery failed: {type(exc).__name__}; context not recorded]')
        return
    if context.strip() and (prompt.startswith(context)
                            or prompt.startswith(resume_prompt(context, ''))):
        session['_ctx_delivered_sha'] = context_sha(context)


def resume_prompt(context: str, message: str) -> str:
    """Reference setup first, then an explicit boundary for the new task.

    Send no prior-turn excerpt: native resume already carries that history.
    Keep empty/incognito context empty rather than recovering old instructions.
    """
    if not context.strip():
        return message
    # Rules and tasks may themselves quote our headings. An envelope-specific
    # boundary avoids mistaking quoted reference text for the real message.
    boundary = MESSAGE_HEADER + ' ' + context_sha(context)
    return (
        f"{CONTEXT_HEADER}\n"
        f"Message boundary: {boundary}\n"
        "Use this current persona and rules in place of earlier Clayrune "
        "context in the conversation. This is reference setup.\n\n"
        f"{context}\n\n"
        f"{boundary}\n"
        f"{message}"
    )


def strip_resume_context(text: str) -> str:
    """Remove our reference envelope when displaying a saved user message."""
    if text.startswith('[Steward cycle]\n\n' + CONTEXT_HEADER):
        text = text[len('[Steward cycle]\n\n'):]
    if text.startswith(CONTEXT_HEADER):
        _, _, rest = text.partition('\n')
        metadata, _, body = rest.partition('\n')
        boundary = metadata.removeprefix('Message boundary: ').rstrip('\r')
        if not boundary.startswith(MESSAGE_HEADER):
            # Earlier branch candidates used the fixed heading.
            boundary, body = MESSAGE_HEADER, text
        _, found, message = body.partition(boundary)
        if found:
            return message.lstrip('\r\n')
    return text
