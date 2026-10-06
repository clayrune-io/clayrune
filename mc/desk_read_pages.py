"""The activity-page addresses a person types on a pane-read account (`read_pages`).

Stored on the account as `[{'role', 'url'}]` and read by `PaneDigestReader`
(`mc/desk_engagement_pane_digest.py`), which treats them as authoritative over
discovery. The reader drops a malformed entry; this is the strict side, used when a
person SAVES one: a refused entry is an error they can fix, never a silent drop.
Whether the pane may then open the address is still the profile's agent-read list
(`mc/browser_agent_read.py`), not this file.
"""
from __future__ import annotations

from typing import Any

from mc import browser_agent_read as _policy
from mc.desk_engagement_pane_digest import MAX_USER_PAGES, ROLES


def validate(raw: Any) -> list[dict]:
    """The cleaned list, or ValueError naming the first bad entry. `[]` clears."""
    if not isinstance(raw, list):
        raise ValueError('read_pages must be a list of {role, url}')
    if len(raw) > MAX_USER_PAGES:
        raise ValueError(f'read_pages is limited to {MAX_USER_PAGES} addresses')
    out: list[dict] = []
    for e in raw:
        if not (isinstance(e, dict) and set(e) == {'role', 'url'}):
            raise ValueError('each read_pages entry is {role, url}')
        role = e['role']
        url = e['url'].strip() if isinstance(e['url'], str) else e['url']
        if role not in ROLES:
            raise ValueError(f'read_pages role must be one of {", ".join(ROLES)}')
        if not _policy.url_host(url):
            raise ValueError(f'read_pages address must be a plain https address: {str(url)[:80]!r}')
        if {'role': role, 'url': url} not in out:
            out.append({'role': role, 'url': url})
    return out
