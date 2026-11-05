"""Which UTM source and medium each Desk platform gets (MC-1152 P2, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 6 (`utm_source` is the
platform/listing source slug, `utm_medium` is "supplied by the channel adapter").
This is DATA, not logic: `mc/desk_impact_links.py` reads it and decides nothing.

A platform that is not listed gets no tracked link, by design:

  * `blog` -- a post on the operator's own site links to the same site. Tagging
    that traffic would count internal navigation as channel traffic.
  * `reddit`, `discord`, anything new -- no adapter has said what these are yet.
    A guessed source slug would be attribution nobody can audit.

MC-1151 (agent listings) and MC-1153 (email) add their rows here when they land:
one line each, medium `agent` / `email` (spec section 11).

Values must satisfy `desk_impact_schema` (`utm_source` URL-safe, `utm_medium` one
of `MEDIUMS`); `tests/test_desk_impact_links.py` pins every row.
"""
from __future__ import annotations

# platform -> (utm_source, utm_medium)
CHANNEL_UTM: dict[str, tuple[str, str]] = {
    'x': ('x', 'social'),
    'linkedin': ('linkedin', 'social'),
    'youtube': ('youtube', 'social'),
    'instagram': ('instagram', 'social'),
    'tiktok': ('tiktok', 'social'),
}
