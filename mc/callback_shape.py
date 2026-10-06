"""How a dispatch callback names the brief it is answering (MC-1057).

A callback used to echo the whole Task the spawner itself wrote (median ~1.8k
chars of a ~5.9k-char message, max 26k). The spawner already holds that brief
and the message stays in its context for every later turn until rollover, so
the echo was paid for again on each read. The callback now carries a one-line
title and the exact GET that returns the full brief when it is actually needed.

The child's final message is never shortened or reshaped here -- only the echo
of the spawner's own words is.

Leaf module: no imports from mc.blueprints or server.
"""
from __future__ import annotations

TITLE_MAX_CHARS = 100


def task_title(task: str, limit: int = TITLE_MAX_CHARS) -> str:
    """First non-empty line of `task`, whitespace-collapsed, clipped to `limit`
    characters (an ellipsis counts toward the limit). Never raises."""
    try:
        for line in str(task or '').splitlines():
            line = ' '.join(line.split())
            if line:
                return line if len(line) <= limit else line[:max(1, limit - 3)].rstrip() + '...'
    except Exception:
        pass
    return ''


def brief_hint(project_id: str, session_id: str) -> str:
    """The exact GET that returns the child's full row, `task` included."""
    return (f"GET /api/project/{project_id}/agent/log?session_id={session_id}"
            if project_id and session_id else '')


def task_line(child: dict) -> str:
    """`Task: <title>` plus the retrieval hint, as one line."""
    title = task_title(child.get('task', ''))
    hint = brief_hint(child.get('project_id', ''), child.get('session_id', ''))
    line = f"Task: {title}" if title else "Task: (none recorded)"
    if hint:
        line += f"  [full brief: {hint}]"
    return line
