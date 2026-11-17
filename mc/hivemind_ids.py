"""Id check for hivemind paths (MC-1179 OS27).

A hivemind id or a workstream id becomes part of a file path under
data/hivemind/. Ids come from URLs, request bodies and model output, so a value
like ``../../x`` must be refused before it is joined into a path. The roster
path already applies the same pattern (mc/hivemind_roster.py _WS_ID_RE); this is
the legacy path's twin, kept in its own module so the routes file does not grow.
"""
from __future__ import annotations

import re
from typing import Any

_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


def is_safe_id(value: Any) -> bool:
    return isinstance(value, str) and _ID_RE.match(value) is not None


def check_id(value: Any, what: str = 'id') -> str:
    """Return ``value`` if it is safe to use in a path, else raise ValueError."""
    if not is_safe_id(value):
        raise ValueError(f'invalid hivemind {what}: {value!r}')
    return value
