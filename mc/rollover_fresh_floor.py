"""Verify Claude's first main-conversation input against its native transcript."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mc.core import _log

_PREFIX_BYTES = 4 * 1024 * 1024
_PROBES = 3


def clear_floor(session: dict[str, Any]) -> None:
    session['_mt_fresh_floor'] = None


def _opening_prompt(content: object) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    types = {block.get('type') for block in content if isinstance(block, dict)}
    return bool(types & {'text', 'image'}) and 'tool_result' not in types


def _matches_start(
    path: Path, native: str, call: str, tokens: int,
    normalize: Callable[[Any], int | None],
) -> bool | None:
    """Read a bounded prefix. None means the first call may still be flushing."""
    rooted = prompted = False
    remaining = _PREFIX_BYTES
    try:
        with path.open('rb') as stream:
            while remaining:
                raw = stream.readline(remaining)
                if not raw or not raw.endswith(b'\n'):
                    return None if len(raw) < remaining else False
                remaining -= len(raw)
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if not isinstance(row, dict):
                    return False
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
                    return prompted and message.get('id') == call and size == tokens
    except FileNotFoundError:
        return None
    except ValueError:
        return False
    except OSError as error:
        _log(f'[rollover-floor] could not read native transcript: {error}')
    return False


class FloorProbe:
    """Keep the first candidate from this reader, even if disk lags stdout."""

    def __init__(self, proc: object) -> None:
        self.proc = proc
        self.native = ''
        self.candidate: tuple[str, int] | None = None
        self.probes = 0
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
            self.candidate = None
            self.probes = 0
        kind = event.get('type')
        if kind not in ('assistant', 'result', 'user') or event.get('parent_tool_use_id'):
            return
        if self.candidate is None:
            message = event.get('message')
            if (kind != 'assistant' or event.get('session_id') != native
                    or not isinstance(message, dict) or str(message.get('model', '')).startswith('<')):
                return
            call, tokens = message.get('id'), normalize(message.get('usage'))
            if not isinstance(call, str) or not call or tokens is None:
                return
            self.candidate = call, tokens
        if self.probes >= _PROBES:
            return
        self.probes += 1
        path = find(self.cwd, native)
        if path is None:
            return
        call, tokens = self.candidate
        match = _matches_start(path, native, call, tokens, normalize)
        if match is not None:
            self.probes = _PROBES
        if (match and session.get('claude_session_id') == native
                and session.get('proc') is self.proc
                and not session.get('_interrupting') and not session.get('_respawn_in_flight')):
            session['_mt_fresh_floor'] = tokens
