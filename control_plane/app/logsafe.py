"""Neutralise untrusted values before they reach a log line.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Log records are newline-delimited, so a value carrying CR/LF can append
fabricated lines to the log — an attacker-chosen `reason` or `user_id` becomes
a forged admin action in the record that an incident review later reads as
fact (CWE-117). Control characters can also corrupt terminal output for anyone
tailing the log.

`scrub()` is for VALUES interpolated into a log message, not for the message
template itself. Cheap enough to apply at every call site that touches request
data — prefer using it over deciding case by case whether a field "can" contain
a newline.
"""
from __future__ import annotations

_MAX = 200


def scrub(value: object, limit: int = _MAX) -> str:
    """Return `value` as a single-line, control-char-free, bounded string."""
    s = str(value)
    # Escape rather than strip: seeing `a\nb` in a log is a signal that
    # something tried a newline, whereas silently joining hides it.
    s = s.replace('\\', '\\\\').replace('\r', '\\r').replace('\n', '\\n')
    s = ''.join(c if c.isprintable() else f'\\x{ord(c):02x}' for c in s)
    if len(s) > limit:
        s = s[:limit] + '…'
    return s
