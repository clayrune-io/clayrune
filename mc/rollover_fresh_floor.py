"""Read Claude's first main-conversation input from its native transcript.

The fresh floor of a native segment is the size of its FIRST model call. The
reader that started the segment sees that call on stdout; one that attached
later (server restart, revive, adopted process) does not, and its own first
reading is some later, larger call. Either way the transcript's first call is
the measurement, so that is what becomes the floor (2026-11-25 review, F1/F2:
a late reading taken as the floor moved the limit up at every restart, and
leaving it unknown let a pin under the real floor back in).
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mc.core import _log

_PREFIX_BYTES = 4 * 1024 * 1024
_PROBE_SECONDS = 60.0
_NO_START = 0    # never a real reading: a first call always carries a prompt


def clear_floor(session: dict[str, Any]) -> None:
    session['_mt_fresh_floor'] = None


def _opening_prompt(content: object) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    types = {block.get('type') for block in content if isinstance(block, dict)}
    return bool(types & {'text', 'image'}) and 'tool_result' not in types


def _start_tokens(
    path: Path, native: str, normalize: Callable[[Any], int | None],
) -> int | None:
    """Size of the segment's first main call, from a bounded prefix. None
    means that call may still be flushing; `_NO_START` means the segment has
    no start of its own to measure (it continues an earlier one, or the
    prefix holds no call)."""
    rooted = prompted = False
    remaining = _PREFIX_BYTES
    try:
        with path.open('rb') as stream:
            while remaining:
                raw = stream.readline(remaining)
                if not raw or not raw.endswith(b'\n'):
                    return None if len(raw) < remaining else _NO_START
                remaining -= len(raw)
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if not isinstance(row, dict):
                    return _NO_START
                if (row.get('sessionId') != native or row.get('isSidechain')
                        or row.get('parentToolUseID') or row.get('parent_tool_use_id')):
                    continue
                rooted |= 'parentUuid' in row and row['parentUuid'] is None
                message = row.get('message')
                if not isinstance(message, dict):
                    continue
                if row.get('type') == 'user' and not row.get('isMeta'):
                    prompted |= rooted and _opening_prompt(message.get('content'))
                if row.get('type') != 'assistant':
                    continue
                if str(message.get('model', '')).startswith('<'):
                    continue
                size = normalize(message.get('usage'))
                if size is not None:
                    return size if prompted else _NO_START
    except FileNotFoundError:
        return None
    except ValueError:
        return _NO_START
    except OSError as error:
        _log(f'[rollover-floor] could not read native transcript: {error}')
    return _NO_START


class FloorProbe:
    """Wait for this reader's first main call, then read the segment's start
    from disk, which may lag stdout.

    The read is retried on each later main-conversation event until it gives
    an answer, for `_PROBE_SECONDS` after that first call. A count of events
    was the bound before: three events ahead of the first row's flush left the
    floor unknown for the whole segment (2026-11-25 review, F7)."""

    def __init__(self, proc: object, clock: Callable[[], float] = time.monotonic) -> None:
        self.proc = proc
        self.clock = clock
        self.native = ''
        self.seen = False
        self.settled = False
        self.deadline = 0.0
        self.cwd: str | None = None

    def observe(
        self, session: dict[str, Any], event: dict[str, Any],
        find: Callable[[str | None, str], Path | None],
        normalize: Callable[[Any], int | None],
    ) -> None:
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            self.cwd = event.get('cwd')
        native = session.get('claude_session_id')
        if not native or session.get('incognito') or session.get('_mt_fresh_floor') is not None:
            return
        if native != self.native:
            self.native = native
            self.seen = False
            self.settled = False
        kind = event.get('type')
        if kind not in ('assistant', 'result', 'user') or event.get('parent_tool_use_id'):
            return
        if not self.seen:
            message = event.get('message')
            if (kind != 'assistant' or event.get('session_id') != native
                    or not isinstance(message, dict) or str(message.get('model', '')).startswith('<')):
                return
            call = message.get('id')
            if not isinstance(call, str) or not call or normalize(message.get('usage')) is None:
                return
            self.seen = True
            self.deadline = self.clock() + _PROBE_SECONDS
        if self.settled or self.clock() > self.deadline:
            return
        path = find(self.cwd, native)
        if path is None:
            return
        tokens = _start_tokens(path, native, normalize)
        if tokens is None:
            return    # the first row is still on its way to disk
        self.settled = True
        if (tokens and session.get('claude_session_id') == native
                and session.get('proc') is self.proc
                and not session.get('_interrupting') and not session.get('_respawn_in_flight')):
            session['_mt_fresh_floor'] = tokens
