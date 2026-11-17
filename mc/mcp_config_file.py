"""Reading `~/.claude.json` and a project's `.mcp.json` without mistaking "unreadable" for "empty".

`mc.mcp` used to read both files through a helper that returned `{}` for a missing file, an empty
file, a read error and a parse error alike, and every writer then replaced the file with
`{"mcpServers": {...}}`. A file that exists but cannot be read or parsed at that instant (a UTF-8
BOM from Windows PowerShell 5.1 or Notepad, a truncated file, a sharing violation) was therefore
overwritten, and for `~/.claude.json` that erased the Claude Code account and settings state kept
in the same file (MC-1179 pass 11, SF1).

Two readers, one rule:

    read_strict(path)    absent or empty -> {}.  Present but unreadable, unparseable or not a JSON
                         object -> raises `McpConfigUnreadable`. Every read-then-replace path uses
                         this and refuses to write on the error; the file is left byte-identical.
    read_lenient(path)   the same, but the error becomes `{}` plus ONE log line per (file, reason)
                         until the file reads cleanly again. For listing/lookup paths that only
                         display or select, where an empty fleet is a safe fallback.

Decoding is `utf-8-sig`, so a BOM parses as the file it is. Nothing here ever moves, renames or
rewrites a file.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class McpConfigUnreadable(OSError):
    """The file exists but could not be read as a JSON object. An `OSError` so the Desk Connect
    registration paths, which already catch `(OSError, ValueError)` around the writers, keep
    their structured failure; the MCP routes catch it first and answer 409."""

    def __init__(self, path: Path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(
            f'{self.path} exists but cannot be read ({reason}). It was left unchanged; '
            f'fix or restore the file, then try again.')


_logged: set[tuple[str, str]] = set()
_logged_lock = threading.Lock()


def read_strict(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError as e:
        raise McpConfigUnreadable(path, f'not valid UTF-8: {e.reason}') from e
    except OSError as e:
        raise McpConfigUnreadable(path, f'{type(e).__name__}: {e.strerror or e}') from e
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise McpConfigUnreadable(path, f'not valid JSON: {e.msg} at line {e.lineno}') from e
    if not isinstance(data, dict):
        raise McpConfigUnreadable(path, f'top level is {type(data).__name__}, not an object')
    return data


def read_lenient(path: Path) -> dict[str, Any]:
    try:
        data = read_strict(path)
    except McpConfigUnreadable as e:
        key = (str(path), e.reason)
        with _logged_lock:
            first = key not in _logged
            _logged.add(key)
        if first:
            from mc.core import _log
            _log(f'[mcp] {e}', flush=True)
        return {}
    with _logged_lock:
        for k in [k for k in _logged if k[0] == str(path)]:
            _logged.discard(k)
    return data
