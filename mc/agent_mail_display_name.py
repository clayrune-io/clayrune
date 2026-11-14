"""Human-readable sender name for outbound agent mail (From / Reply-To).

The address model stays opaque (install local-part + per-thread token); only an
RFC 5322 display name is added so the mail does not read as spam. The name comes
from the SENDING SESSION's character, resolved server-side — never from the
agent's request, whose keys stay exactly {request_id, subject, body, kind}.

Header-injection surface: the name ends up in mail headers, so it is validated
against a closed allowlist and REPLACED by FALLBACK on any failure. It never
fails a send. The Worker re-validates with the same rules
(workers/agent-mail/display-name.js); keep the two in step.
"""
from __future__ import annotations

import re
from typing import Any

from mc.core import _log

FALLBACK = 'Clayrune agent'
MAX_LEN = 40
_ALLOWED = re.compile(r"[A-Za-z0-9 ._'-]+")
_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]')


def sanitize(raw: Any) -> str:
    """The name if it passes the allowlist, else FALLBACK. Never raises."""
    if not isinstance(raw, str) or _CONTROL.search(raw):
        return FALLBACK
    name = re.sub(r' +', ' ', raw).strip()
    if not name or len(name) > MAX_LEN or not _ALLOWED.fullmatch(name):
        return FALLBACK
    return name


def for_session(project: dict[str, Any] | None, session: dict[str, Any] | None) -> str:
    """Display name for a session: its character's agent_name, else FALLBACK."""
    name = ''
    try:
        from mc.blueprints import agent_routes as ar
        name = ar._session_character_parts(project, session)[1]
    except Exception as e:
        _log(f'[agent-mail] display name lookup failed: {e}', flush=True)
    return sanitize(name)
