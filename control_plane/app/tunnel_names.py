"""Tunnel names: how one is made, and whose it is.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

A tunnel is named `mc-{username}-{suffix}`. The username grammar allows hyphens,
so `startswith("mc-sam-")` also matches `mc-sam-dev-xxxxxx`, the tunnel of the
account that owns `sam-dev` (MB23). Force cleanup deleted both. The suffix has a
fixed length, so ownership is "prefix, then exactly one suffix, then the end".
"""
from __future__ import annotations

import re
import secrets

# secrets.token_urlsafe(4) → 4 random bytes → 6 base64url characters, always.
_SUFFIX_BYTES = 4
_SUFFIX_LEN = 6
_SUFFIX_RE = f"[A-Za-z0-9_-]{{{_SUFFIX_LEN}}}"


def new_tunnel_name(username: str) -> str:
    suffix = secrets.token_urlsafe(_SUFFIX_BYTES)
    assert len(suffix) == _SUFFIX_LEN
    return f"mc-{username}-{suffix}"


def is_tunnel_of(name: str, username: str) -> bool:
    """True only for a name `new_tunnel_name(username)` could have produced."""
    return re.fullmatch(f"mc-{re.escape(username)}-{_SUFFIX_RE}", name or "") is not None
