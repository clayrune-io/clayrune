"""A managed session-log entry is ONE physical line (ML15, MC-1179).

SESSION_LOG.md is read back with `str.splitlines()` and only a line that
starts `- [` is an entry (`mc.memory._mem_split_full`). An entry that reaches
the writer holding a line break is therefore stored whole once and cut at the
break by the next commit: the first physical line survives, the rest (the
label's closing `**` and the whole Scribe summary) is dropped. Its
`last_entry_hash` is the hash of the multi-line string too, which no stored
line ever matches again, so each later checkpoint of that session appends
instead of superseding.

`one_line` is the write-boundary half of the fix: `_commit_managed_entry`
passes every entry through it before hashing or storing, so no caller can
store a multi-line entry whatever it built its label from. It joins on the
same breaks `splitlines()` splits on (`\\r`, `\\v`, `\\f`, `\\x1c`-`\\x1e`,
`\\x85`, `\\u2028`, `\\u2029` as well as `\\n`), because that is the reader's
definition of a line.

Leaf module: imports nothing from `mc`.
"""
from __future__ import annotations


def one_line(entry, log=None, project_id='') -> str:
    """-> `entry` as a single physical line. An entry that is already one line
    comes back unchanged (byte for byte); one that is not has its line breaks
    replaced by single spaces, and `log` (when given) is told once."""
    text = entry or ''
    parts = text.splitlines()
    if len(parts) <= 1 and (not parts or parts[0] == text):
        return text
    flat = ' '.join(p.strip() for p in parts if p.strip())
    if log is not None:
        log(f"[mem-log] {project_id}: an entry arrived with "
            f"{len(parts) - 1} line break(s); stored as one line "
            f"({flat[:60]!r})")
    return flat
