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
# Plain imperative words and sensitive nouns. Agents use them in their own
# notes ("run the tests", "approve"), so a reply that QUOTES the note would
# always hit them; quoted lines may skip this half only (see screen_text).
_INSTRUCTION_WORDS = re.compile(
    r'\b(?:run|execute|install|uninstall|send|approve|authorize|grant|chmod|sudo)\b'
    r'|\b(?:permissions?|passcodes?|credentials?)\b', re.I | re.S)
# Command syntax and prompt-override phrasing. Held on every line, quoted or not.
_INSTRUCTION_COMMANDS = re.compile(
    r'\b(?:ignore|disregard|override)\b.{0,80}\b(?:previous|prior|above|instructions?|system)\b'
    r'|\b(?:system|developer)\s*(?:message|prompt|:)'
    r'|\b(?:tool_use|tool_call|mcp__\w+|functions\.[a-z_]+)\b'
    r'|\b(?:curl|wget|powershell|pwsh|cmd\.exe|bash|python\d*|pip\d*|npm|npx)\b'
    r'|(?:^|[\n;&|>])\s*(?:git|node|sh|rm|del|eval|exec)\s'
    r'|```|`|\$\(|&&|\|\|', re.I | re.S)
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


def screen_text(text: str, *, max_bytes: int = MAX_TEXT_BYTES,
                quoted_lines_exempt: bool = False) -> str:
    """Return a closed hold code or empty string. Never return input fragments.

    quoted_lines_exempt skips ONLY the plain-word half (_INSTRUCTION_WORDS) on
    '>' quoted lines. It is for the whole-message pre-screen: a reply quotes the
    agent's own outbound note, whose words ("send", "approve", "run") would
    otherwise hold every reply. Command syntax, override phrasing, HTML and
    invisible Unicode are still held on every line, quoted bytes never reach the
    session, and the extracted reply is re-screened with the full check.
    """
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
    if _INSTRUCTION_COMMANDS.search(normalized):
        return 'instruction_like'
    checked = normalized
    if quoted_lines_exempt:
        checked = '\n'.join(line for line in normalized.splitlines()
                            if not line.lstrip().startswith('>'))
    if _INSTRUCTION_WORDS.search(checked):
        return 'instruction_like'
    return ''
