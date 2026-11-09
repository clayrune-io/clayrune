"""Conservative local screen for already stripped owner-mail text.

This is a hold detector, not an authorization decision or a complete injection
classifier. The tool-free extractor also classifies the entire message.
"""
from __future__ import annotations

import html
import re
import unicodedata

MAX_TEXT_BYTES = 16 * 1024
_HTML = re.compile(r'<\s*(?:/?[a-z][a-z0-9:-]*\b[^>]*|!--.*?)>', re.I | re.S)
_INSTRUCTIONS = re.compile(
    r'\b(?:ignore|disregard|override)\b.{0,80}\b(?:previous|prior|above|instructions?|system)\b'
    r'|\b(?:run|execute|install|uninstall|send|approve|authorize|grant|chmod|sudo)\b'
    r'|\b(?:permissions?|passcodes?|credentials?)\b'
    r'|\b(?:system|developer)\s*(?:message|prompt|:)'
    r'|\b(?:tool_use|tool_call|mcp__\w+|functions\.[a-z_]+)\b'
    r'|\b(?:curl|wget|powershell|pwsh|cmd\.exe|bash|python\d*|pip\d*|npm|npx)\b'
    r'|(?:^|[\n;&|])\s*(?:git|node|sh|rm|del|eval|exec)\s'
    r'|```|`|\$\(|&&|\|\|', re.I | re.S)


def _invisible(char: str) -> bool:
    cp = ord(char)
    return (unicodedata.category(char) in {'Cf', 'Cs'}
            or (unicodedata.category(char) == 'Cc' and char not in '\r\n\t')
            or cp in {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0}
            or 0x180B <= cp <= 0x180F or 0xFE00 <= cp <= 0xFE0F
            or 0xE0000 <= cp <= 0xE0FFF)


def screen_text(text: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Return a closed hold code or empty string. Never return input fragments."""
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
    if _HTML.search(normalized):
        return 'unsupported_html'
    if _INSTRUCTIONS.search(normalized):
        return 'instruction_like'
    return ''
