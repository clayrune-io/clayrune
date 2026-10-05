"""Scrub untrusted text before it is shown to the user (spec "Detection and trust").

A registry listing's name and a page's URL are written by a stranger and rendered on the
Desk. Control characters, line/paragraph separators and every format character (the bidi
overrides and isolates U+202A-202E / U+2066-2069, zero-width joiners, the BOM) are removed,
whitespace runs collapse to one space, and the result is capped. Nothing here trusts the
text; it only keeps it from rearranging the surrounding sentence or hiding in it.
"""
from __future__ import annotations

import unicodedata

EVIDENCE_URL_MAX = 300
_DROP = {'Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp'}


def clean(text, limit: int) -> str:
    s = text if isinstance(text, str) else ''
    kept = ''.join(' ' if ch in '\t\n\r\v\f' else ch for ch in s
                   if ch in '\t\n\r\v\f' or unicodedata.category(ch) not in _DROP)
    return ' '.join(kept.split())[:limit]


def clean_url(url) -> str:
    """A URL for display: control/format characters dropped, whitespace removed, 300 max."""
    s = url if isinstance(url, str) else ''
    return ''.join(ch for ch in s if unicodedata.category(ch) not in _DROP and not ch.isspace())[:EVIDENCE_URL_MAX]
