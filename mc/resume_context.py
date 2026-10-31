"""Deliver current Clayrune instructions alongside native conversation history.

Codex, Gemini and Qwen resume their saved messages, which can include an older
injected persona. The caller refreshes the session context from current rules;
this boundary must send it even when the vendor already has a transcript.
"""
from __future__ import annotations

CONTEXT_HEADER = '=== CURRENT CLAYRUNE CONTEXT ==='
MESSAGE_HEADER = "=== THE USER'S MESSAGE ==="


def resume_prompt(context: str, message: str) -> str:
    """Reference setup first, then an explicit boundary for the new task.

    Send no prior-turn excerpt: native resume already carries that history.
    Keep empty/incognito context empty rather than recovering old instructions.
    """
    if not context.strip():
        return message
    return (
        f"{CONTEXT_HEADER}\n"
        "Use this current persona and rules in place of earlier Clayrune "
        "context in the conversation. This is reference setup.\n\n"
        f"{context}\n\n"
        f"{MESSAGE_HEADER}\n"
        f"{message}"
    )


def strip_resume_context(text: str) -> str:
    """Remove our reference envelope when displaying a saved user message."""
    if text.startswith(CONTEXT_HEADER):
        _, found, message = text.partition(MESSAGE_HEADER)
        if found:
            return message.lstrip('\r\n')
    return text
