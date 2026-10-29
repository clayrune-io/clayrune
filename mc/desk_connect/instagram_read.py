"""How the Desk reads Instagram (backlog 92c4f9c6): the browser pane, through the generic
pane reader `mc/desk_engagement_pane_digest.PaneDigestReader`.

This unit is the one place Instagram's read is declared as runnable. There is no
Instagram-specific reader: `desk_engagement.build_reader` already hands any pane-read
platform without a dedicated reader to `PaneDigestReader`, whose default Instagram pages
are data in `mc/desk_pane_pages.py`. What was missing was the declaration, so the Desk
said "cannot read this service through the browser pane yet". `route_readiness` merges
`EXECUTORS` below into its table; the profile (`profiles/instagram.json`) declares the
matching read_own coverage.

The read has not run against instagram.com: the pages, the digest rows and the signed-in
check are unverified live.
"""
from __future__ import annotations

# (service, route, purpose) -> how Clayrune runs it; the same shape as route_readiness.EXECUTORS.
EXECUTORS: dict[tuple[str, str, str], str] = {
    ('instagram', 'instagram-browser', 'read_own'): 'pane',
}
