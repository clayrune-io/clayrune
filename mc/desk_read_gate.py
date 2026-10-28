"""The structured reason a browser-pane connection is not read yet because reading has not been
allowed, as opposed to because the person is not signed in (Ron 2026-10-28: a LinkedIn card with a
working sign-in said "Needs sign-in" because its profile was not on the agent-read list).

`capability()` of `PaneDigestReader` returns `{'gate': reads_off(...)}` beside its `reason`;
`desk_engagement.platform_coverage` forwards it, and the Desk (static/js/desk-v1-reads-off.js)
draws its own state from `gate.kind` instead of from the wording of `reason`. Two refusals share it
because one human action fixes both: the profile is switched off (`agent_read_off`), or it is on
but this site is not on its list (`domain_not_allowed`). Switching either on is the passcode-gated
human editor (`PUT /api/browser/profiles/<name>/agent-read`); nothing here writes policy.
"""
from __future__ import annotations

from mc import browser_agent_read as _policy

READS_OFF = 'reads_off'


def site_of(url: str | None) -> str:
    """The bare site a person would allow for `url` (`www.` dropped, as the Connect wizard does),
    or '' when `url` is not a plain https address."""
    host = _policy.url_host(url) or ''
    return host[4:] if host.startswith('www.') else host


def reads_off(profile: str, url: str | None) -> dict:
    """`{kind, profile, site}`: this profile may not be read for this site until a human allows it."""
    return {'kind': READS_OFF, 'profile': profile, 'site': site_of(url)}


def message(gate: dict | None) -> str | None:
    """The one plain line for a coverage row whose route is gated (no profile name, no internal term)."""
    if not isinstance(gate, dict) or gate.get('kind') != READS_OFF:
        return None
    site = gate.get('site')
    return f'Reading is off for {site}' if site else 'Reading is off'
