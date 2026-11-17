"""Bounded output buffer for terminal sessions, addressed by ABSOLUTE position.

A terminal session keeps at most MAX_CHUNKS output chunks and cuts to the last
KEEP_CHUNKS past that. The SSE stream used to follow the buffer with a list
index, which stops pointing at anything once the head of the list has been cut
away: after the first trim the stream went permanently silent (MC-1179 SF20).

So the session also carries `dropped`, a running count of the chunks every trim
removed. A chunk's absolute position is `dropped + its index in output_lines`;
a reader keeps an absolute position and asks `read_from` for what is new.

A reader whose position is older than the oldest kept chunk resumes from the
oldest kept chunk. Nothing is added to the stream to say so.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Tuple

MAX_CHUNKS = 5000
KEEP_CHUNKS = 3000

# One lock for every session: the critical sections are a slice or, every
# ~2,000 chunks, a 3,000-element copy. It keeps `dropped` and `output_lines`
# changing together, so a reader never pairs a new list with an old count.
_lock = threading.Lock()


def append_chunk(session: Dict[str, Any], text: str) -> None:
    """Append one output chunk, trimming the buffer past MAX_CHUNKS."""
    with _lock:
        lines = session['output_lines']
        lines.append(text)
        if len(lines) > MAX_CHUNKS:
            removed = len(lines) - KEEP_CHUNKS
            session['output_lines'] = lines[-KEEP_CHUNKS:]
            session['dropped'] = session.get('dropped', 0) + removed


def read_from(session: Dict[str, Any], position: int) -> Tuple[List[str], int]:
    """Chunks at absolute position >= `position`, and the position to ask for
    next time. A position older than the oldest kept chunk starts at the
    oldest kept chunk."""
    with _lock:
        dropped = session.get('dropped', 0)
        start = max(position - dropped, 0)
        chunks = session['output_lines'][start:]
    return chunks, dropped + start + len(chunks)


def snapshot(session: Dict[str, Any]) -> Tuple[int, List[str]]:
    """(dropped, a copy of the kept chunks), taken together."""
    with _lock:
        return session.get('dropped', 0), list(session['output_lines'])
