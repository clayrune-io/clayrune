"""Id check for workflow action steps (MC-1188 / DCW-W3).

An action node's `config` values are templates filled from earlier steps, and
an agent step's output is untrusted text. `project_id` and `item_id` then become
URL path parts (`/api/project/{pid}/backlog/{item_id}`) and a journal file name
(`docs/_journal/{item_id}-*.md`). Checked AFTER rendering, a value that is not
a plain id fails the step instead of reaching a glob, a path join or a URL.

Pure and dependency-free so `mc/workflows.py` imports it without a cycle.
"""
from __future__ import annotations

import re

# A leading underscore is admitted: reserved project ids are `_ideas` and
# `_incognito`. A leading `.` or `-` is not (dotfiles, option-shaped names).
_ID_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}')


def valid_id(value) -> bool:
    """True for a plain id: starts alphanumeric or `_`, then letters, digits, `_`, `.`
    or `-`, at most 64 characters, and no `..` anywhere."""
    return (isinstance(value, str) and _ID_RE.fullmatch(value) is not None
            and '..' not in value)


def require_id(label: str, value) -> str:
    """Return `value` if it is a plain id, else raise ValueError naming `label`
    (never echoing the value: it may be attacker-shaped text)."""
    if not valid_id(value):
        raise ValueError(f'{label} is not a plain id (letters, digits, _ . - only, '
                         f'max 64, no ".."); refusing to use it as a path or URL part')
    return value
