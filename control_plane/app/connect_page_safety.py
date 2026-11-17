"""Checks and encoders for the public /v1/connect enrollment page.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

MC-1179 MB25 + MB26. Kept out of routes_public.py because it is its own
concern: what may be reflected into the page, and where the page may send the
browser afterwards.

* `canonical_callback` -- the loopback callback check. urlparse and a browser
  disagree about hosts once a backslash is involved
  (`http://evil.example\\@127.0.0.1/...` parses as 127.0.0.1, navigates to
  evil.example), so reject the characters the two parsers treat differently
  and rebuild the URL from its parsed parts instead of echoing the string.
* `valid_device_pub` -- 32-byte standard base64, the same check
  /v1/signin/complete applies, run before the value is rendered.
* `json_for_script` -- json.dumps leaves `</script>` intact, so `<`, `>`, `&`
  and the JS line separators are written as \\uXXXX escapes.
* `content_security_policy` -- nonce-based, mirroring the sign-in page.
"""
from __future__ import annotations

import base64
import json
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


def valid_device_pub(pub: str) -> bool:
    """True iff `pub` is standard base64 of exactly 32 bytes."""
    try:
        return len(base64.b64decode(pub, validate=True)) == 32
    except Exception:
        return False


def json_for_script(value) -> str:
    """json.dumps that cannot terminate or reopen an inline <script> element."""
    return (json.dumps(value)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029"))


def content_security_policy(nonce: str, auth_domain: str) -> str:
    """Policy for the connect page. The inline module script carries `nonce`;
    the Firebase SDK is an ES module from gstatic (no SRI possible), and the
    popup sign-in flow loads gapi from apis.google.com and frames the project's
    auth domain."""
    frame_src = ["https://*.firebaseapp.com"]
    if re.fullmatch(r"[A-Za-z0-9.-]+", auth_domain or "") and not auth_domain.endswith(".firebaseapp.com"):
        frame_src.append(f"https://{auth_domain}")
    return "; ".join([
        "default-src 'none'",
        f"script-src 'nonce-{nonce}' https://www.gstatic.com https://apis.google.com",
        "connect-src 'self' https://identitytoolkit.googleapis.com "
        "https://securetoken.googleapis.com https://*.googleapis.com",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "frame-src " + " ".join(frame_src),
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])
