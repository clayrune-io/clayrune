"""Where the /v1/signin page may send the browser after sign-in.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

MC-1179 MB27. Kept out of routes_auth.py because it is its own concern, and
kept beside connect_page_safety.py because it is the same parser disagreement
MB26 closed for the connect callback: urlparse reads
`https://evil.example\\@a.<zone>/` as host `a.<zone>`, a browser treats the
backslash as a slash and navigates to evil.example.

Unlike the loopback callback, a return_to legitimately carries a path, a query
and a fragment, so the URL is not rebuilt. Instead the raw authority (the text
between `https://` and the first `/`, `?` or `#`) must be a bare hostname with
an optional numeric port, and it must be the host urlparse saw. That leaves no
room for userinfo, and a backslash, whitespace or control character anywhere in
the string is refused outright.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

_SCHEME = "https://"

# Browsers read a backslash as a slash in an https URL and strip tab/newline
# before parsing; neither is part of a destination the Worker builds.
_FORBIDDEN_CHARS = re.compile(r"[\\\s\x00-\x1f\x7f]")

_AUTHORITY = re.compile(r"([a-z0-9.-]+)(?::[0-9]{1,5})?", re.IGNORECASE)


def safe_return_to(url: str, zone: str) -> Optional[str]:
    """Return `url` unchanged if it is an https URL whose host is `zone` or a
    subdomain of it, else None."""
    if not url or _FORBIDDEN_CHARS.search(url):
        return None
    if url[:len(_SCHEME)].lower() != _SCHEME:
        return None
    authority = re.split(r"[/?#]", url[len(_SCHEME):], maxsplit=1)[0]
    m = _AUTHORITY.fullmatch(authority)
    if m is None:
        return None
    try:
        u = urlparse(url)
        u.port  # raises ValueError on an out-of-range port
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host != m.group(1).lower():
        return None
    zone = zone.lower()
    if host != zone and not host.endswith("." + zone):
        return None
    return url
