"""Fenced code blocks in third-party text, found in one pass.

The text is a registry README, a documentation page or a paste: its author is not the
person connecting. The pattern this replaces,

    ```[A-Za-z0-9_+-]*[ \\t]*\\r?\\n(.*?)\\r?\\n[ \\t]*```      (DOTALL)

rescanned to the end of the text from every opening fence that had no closing one, so
80 KB of `a```` lines took 8 seconds with the interpreter lock held (MC-1179 DCW-C1).
`fenced_blocks` returns the same blocks in the same order, in time linear in the text:
once one opener has no closer, no later opener can have one either, and the scan stops.

    fenced_blocks()        the inner text of each fenced block
    drop_comment_lines()   a block without its `//` comment lines
"""
from __future__ import annotations

_LANG = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-')
_FENCE = '```'


def _opener_end(text: str, i: int) -> int:
    """Index just past the opening fence line that starts at `i`, or -1."""
    j, n = i + 3, len(text)
    while j < n and text[j] in _LANG:
        j += 1
    while j < n and text[j] in ' \t':
        j += 1
    if j < n and text[j] == '\r':
        j += 1
    return j + 1 if j < n and text[j] == '\n' else -1


def _closer(text: str, start: int) -> tuple[int, int]:
    """(content end, index past the closing fence) for the first closing fence line at
    or after `start`; (-1, -1) when there is none."""
    n = len(text)
    nl = text.find('\n', start)
    while nl != -1:
        j = nl + 1
        while j < n and text[j] in ' \t':
            j += 1
        if text.startswith(_FENCE, j):
            end = nl - 1 if nl > start and text[nl - 1] == '\r' else nl
            return end, j + 3
        nl = text.find('\n', j)
    return -1, -1


def fenced_blocks(text: str) -> list[str]:
    out: list[str] = []
    pos = 0
    while True:
        i = text.find(_FENCE, pos)
        if i == -1:
            return out
        start = _opener_end(text, i)
        if start == -1:
            pos = i + 1
            continue
        end, after = _closer(text, start)
        if end == -1:
            return out
        out.append(text[start:end])
        pos = after


def drop_comment_lines(block: str) -> str:
    """`block` without the lines whose first non-blank characters are `//`."""
    return '\n'.join(line for line in block.split('\n') if not line.lstrip().startswith('//'))
