"""Deliver current Clayrune instructions alongside native conversation history.

Codex, Gemini and Qwen resume their saved messages, which can include an older
injected persona. The caller refreshes the session context from current rules;
this boundary must send it even when the vendor already has a transcript.
"""
from __future__ import annotations

import hashlib

CONTEXT_HEADER = '=== CURRENT CLAYRUNE CONTEXT ==='
MESSAGE_HEADER = "=== THE USER'S MESSAGE ==="


def resume_prompt(context: str, message: str) -> str:
    """Reference setup first, then an explicit boundary for the new task.

    Send no prior-turn excerpt: native resume already carries that history.
    Keep empty/incognito context empty rather than recovering old instructions.
    """
    if not context.strip():
        return message
    # Rules and tasks may themselves quote our headings. An envelope-specific
    # boundary avoids mistaking quoted reference text for the real message.
    boundary = MESSAGE_HEADER + ' ' + hashlib.sha256(context.encode()).hexdigest()
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
