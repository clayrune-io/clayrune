"""Body attribution from the sending session, never from an outbound request."""
from __future__ import annotations

from typing import Any

from mc.agent_mail_display_name import for_session


def sender_line(project: dict[str, Any], session: dict[str, Any]) -> str:
    # Keep operator-edited project labels on one line as well.
    project_name = ' '.join(str(project.get('name') or project['id']).split())
    return (f"From {for_session(project, session)} in {project_name} "
            f"(chat {session['session_id']})")
