"""Checks and encoders for the public /v1/connect enrollment page.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

MC-1179 MB26. Kept out of routes_public.py because it is its own concern:
where the page may send the browser afterwards.

* `canonical_callback` -- the loopback callback check. urlparse and a browser
  disagree about hosts once a backslash is involved
  (`http://evil.example\\@127.0.0.1/...` parses as 127.0.0.1, navigates to
  evil.example), so reject the characters the two parsers treat differently
  and rebuild the URL from its parsed parts instead of echoing the string.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# Only loopback is a legitimate callback: it is where the local Clayrune
# receives the enrollment token.
ALLOWED_CALLBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CALLBACK_PATH = "/api/mc-callback"

# A backslash, userinfo marker, whitespace or control character in the raw
# string is never part of a real callback and is what splits the parsers.
_FORBIDDEN_CALLBACK_CHARS = re.compile(r"[\\@\s\x00-\x1f\x7f]")


def canonical_callback(url: str) -> Optional[str]:
    """Return `http://<host>[:<port>]/api/mc-callback` rebuilt from the parsed
    parts, or None if `url` is not a plain loopback callback."""
    if not url or _FORBIDDEN_CALLBACK_CHARS.search(url):
        return None
    try:
        u = urlparse(url)
        port = u.port  # raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        return None
    if u.scheme != "http":
        return None
    host = (u.hostname or "").lower()
    if host not in ALLOWED_CALLBACK_HOSTS:
        return None
    if u.path != CALLBACK_PATH or u.params or u.query or u.fragment:
        return None
    return f"http://{host}" + (f":{port}" if port is not None else "") + CALLBACK_PATH
