"""Deterministic reply extraction for recorded agent-mail events. No model, no screening.

A reply that reached local custody already passed the Worker's per-email token and
verified-owner (sender/DMARC) checks; it is the owner, same as typing in chat. This
only cuts the quoted history off and strips invisible characters.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Optional

MAX_TEXT_BYTES = 16 * 1024
# Gmail/Apple "On <date>, <name> wrote:" (the client may wrap it onto a second line),
# Outlook "-----Original Message-----", and a bare "________" rule.
_WROTE = re.compile(r'^\s*On\b.*\bwrote:\s*$', re.I)
_ORIGINAL = re.compile(r'^\s*-{2,}\s*Original Message\s*-{2,}\s*$', re.I)
_RULE = re.compile(r'^\s*_{5,}\s*$')
_FROM = re.compile(r'^\s*From:\s', re.I)
_HEADER_FIELD = re.compile(r'^\s*(?:Sent|Date|To|Subject):\s', re.I)


@dataclass(frozen=True)
class Extraction:
    reason: str
    envelope: Optional[dict[str, Any]] = None


def _invisible(char: str) -> bool:
    cp = ord(char)
    category = unicodedata.category(char)
    return (category in {'Cf', 'Cs'} or (category == 'Cc' and char not in '\r\n\t')
            or cp in {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0}
            or 0x180B <= cp <= 0x180F or 0xFE00 <= cp <= 0xFE0F)


def _quote_starts(lines: list[str], i: int) -> bool:
    line = lines[i]
    nxt = lines[i + 1] if i + 1 < len(lines) else ''
    return bool(_WROTE.match(line) or _ORIGINAL.match(line) or _RULE.match(line)
                or (re.match(r'^\s*On\b', line, re.I) and _WROTE.match(line + ' ' + nxt))
                or (_FROM.match(line) and _HEADER_FIELD.match(nxt)))


def extract_reply(text: str, *, max_reply_bytes: int = MAX_TEXT_BYTES) -> Extraction:
    text = ''.join(c for c in text if not _invisible(c))
    lines = text.splitlines()
    kept = []
    for i, line in enumerate(lines):
        if _quote_starts(lines, i):
            break
        if not line.lstrip().startswith('>'):
            kept.append(line)
    reply = '\n'.join(kept).strip()
    if not reply:
        return Extraction('empty_reply')
    reply = reply.encode('utf-8')[:max_reply_bytes].decode('utf-8', 'ignore').rstrip()
    return Extraction('', {'version': 1, 'reply_text': reply})
