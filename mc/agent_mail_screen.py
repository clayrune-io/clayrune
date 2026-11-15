"""Format holds for already authenticated, stripped owner-mail text.

Sender/token authentication belongs to Worker ingress; local custody verifies
the service signature and thread binding. Instruction wording is valid input.
"""
from __future__ import annotations

import html
import re
import unicodedata

MAX_TEXT_BYTES = 16 * 1024
_HTML = re.compile(r'<\s*(?:/?[a-z][a-z0-9:-]*\b[^>]*|!--.*?)>', re.I | re.S)
# Mail clients wrap addresses and URLs in angle brackets ("Dave <a@b.io> wrote:",
# "<https://x>"). These autolinks hide nothing, so they are not HTML. No
# whitespace or quotes allowed inside, so no attribute can ride along.
_AUTOLINK = re.compile(
    r'<(?:[^\s<>"\'@]+@[^\s<>"\'@]+|(?:https?|mailto):[^\s<>"\']+)>', re.I)


def _invisible(char: str) -> bool:
    cp = ord(char)
    return (unicodedata.category(char) in {'Cf', 'Cs'}
            or (unicodedata.category(char) == 'Cc' and char not in '\r\n\t')
            or cp in {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0}
            or 0x180B <= cp <= 0x180F or 0xFE00 <= cp <= 0xFE0F
            or 0xE0000 <= cp <= 0xE0FFF)


def screen_text(text: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Return a closed format-hold code; apply equally to fresh and quoted text."""
    try:
        if len(text.encode('utf-8')) > max_bytes:
            return 'oversize_text'
    except UnicodeError:
        return 'invisible_unicode'
    if not text.strip():
        return 'empty_reply'
    if any(_invisible(c) for c in text):
        return 'invisible_unicode'
    # Entity decoding is screening only, never an alternate reply representation.
    normalized = unicodedata.normalize('NFKC', html.unescape(text))
    if any(_invisible(c) for c in normalized):
        return 'invisible_unicode'
    if _HTML.search(_AUTOLINK.sub(' ', normalized)):
        return 'unsupported_html'
    return ''
