"""The one place a project id becomes a path (MC-1179 pass 07, P54).

A project record is `<data dir>/<id>.json`. The id arrives from a URL segment
or a request body, so building that path by string interpolation lets an id
such as `..\\other`, or an absolute path, name any `.json` file on the machine.
`project_record_path` is the chokepoint: it returns the path for a plain id
and None for anything else. Callers treat None as "no such project".

Shape: letters, digits, underscore, hyphen and dot after the first character;
the first character is a letter, digit or underscore (so `_ideas` and
`_incognito` pass, a leading dot does not). Case is not folded: records written
before ids were validated may carry capitals, and refusing them would hide
them. Every id in `data/projects` on this install fits, and the test suite
checks that against the live directory listing.
"""
import os
import re
from pathlib import Path
from typing import Optional

from mc.core import _log

_PLAIN_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}', re.ASCII)

# Ids already reported, so a client that keeps sending one bad id writes one
# log line, not one per request.
_LOGGED_REFUSALS: set = set()


def is_plain_project_id(project_id) -> bool:
    return (isinstance(project_id, str)
            and _PLAIN_ID.fullmatch(project_id) is not None
            and '..' not in project_id)


def _refuse(project_id, why: str) -> None:
    key = repr(project_id)[:200]
    if key in _LOGGED_REFUSALS:
        return
    if len(_LOGGED_REFUSALS) < 256:
        _LOGGED_REFUSALS.add(key)
    _log(f"[projects] refused project id {key}: {why}", flush=True)


def project_record_path(data_dir, project_id, suffix: str = '') -> Optional[Path]:
    """`<data_dir>/<project_id><suffix>.json`, or None when the id is not plain.

    `suffix` names a sidecar (`_agent_log`); it is a code constant, never user
    input. Never raises.
    """
    if not is_plain_project_id(project_id):
        _refuse(project_id, 'not a plain project id')
        return None
    candidate = Path(data_dir) / f'{project_id}{suffix}.json'
    # Second check, independent of the pattern: whatever the id contains, the
    # file must sit directly in the data dir.
    try:
        parent_ok = (os.path.normcase(os.path.realpath(candidate.parent))
                     == os.path.normcase(os.path.realpath(data_dir))
                     and candidate.name == f'{project_id}{suffix}.json')
    except (OSError, ValueError):
        parent_ok = False
    if not parent_ok:
        _refuse(project_id, 'resolves outside the data dir')
        return None
    return candidate
