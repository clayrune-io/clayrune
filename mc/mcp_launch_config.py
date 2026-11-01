"""Shared validation and private launch artifacts for vendor MCP bridges."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping, Optional

from mc.core import _log

Servers = Dict[str, Dict[str, Any]]
_owned_files: list[Path] = []


def outside_repository(directory: Path) -> Path:
    """Launch configs can contain values; never write them under a repository."""
    directory = directory.resolve()
    if any((parent / '.git').exists() for parent in (directory, *directory.parents)):
        raise ValueError('Vendor configuration home is inside a repository; MCP launch refused')
    return directory


def servers_from_json(value: str) -> Optional[Servers]:
    """None retains native discovery; an explicit empty object means deny all.

    Refuse malformed launch input instead of silently inheriting the native fleet.
    Do not include configuration values in errors (they may contain credentials).
    """
    if not value or not value.strip():
        return None
    try:
        data = json.loads(value)
        servers = data['mcpServers']
        if not isinstance(servers, dict):
            raise ValueError()
        for name, spec in servers.items():
            if not isinstance(name, str) or not name or not isinstance(spec, dict):
                raise ValueError()
            transport = spec.get('type') or ('http' if 'httpUrl' in spec else
                                           'sse' if 'sseUrl' in spec else
                                           'stdio' if spec.get('command') else 'http')
            if transport not in ('stdio', 'http', 'sse'):
                raise ValueError()
            if transport == 'stdio':
                if not isinstance(spec.get('command'), str) or not spec['command']:
                    raise ValueError()
                if not isinstance(spec.get('args', []), list) or not all(
                        isinstance(arg, str) for arg in spec.get('args', [])):
                    raise ValueError()
            elif not isinstance(spec.get('url') or spec.get('httpUrl') or spec.get('sseUrl'), str):
                raise ValueError()
            for key in ('env', 'headers'):
                if not isinstance(spec.get(key, {}), dict) or not all(
                        isinstance(k, str) and isinstance(v, str)
                        for k, v in spec.get(key, {}).items()):
                    raise ValueError()
        return servers
    except (KeyError, TypeError, ValueError):
        raise ValueError('Invalid resolved MCP launch configuration') from None


def transport(spec: Mapping[str, Any]) -> str:
    return str(spec.get('type') or ('http' if 'httpUrl' in spec else
                                  'sse' if 'sseUrl' in spec else
                                  'stdio' if spec.get('command') else 'http'))


def remote_url(spec: Mapping[str, Any]) -> str:
    return str(spec.get('url') or spec.get('httpUrl') or spec.get('sseUrl') or '')


def private_json(data: Any, *, directory: Optional[Path] = None,
                 prefix: str = 'clayrune-mcp-', name_key: str = '') -> Path:
    """Exclusive, launch-owned file outside the source tree; never replace a user file."""
    directory = outside_repository(directory or Path(tempfile.gettempdir()))
    directory.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=prefix, suffix='.json', dir=directory)
    path = Path(filename)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            if name_key:
                data = {**data, name_key: path.stem}
            json.dump(data, stream, ensure_ascii=True)
        _owned_files.append(path)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def private_text(data: str, directory: Path, suffix: str) -> Path:
    directory = outside_repository(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix='clayrune-mcp-', suffix=suffix, dir=directory)
    path = Path(filename)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
        _owned_files.append(path)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _cleanup() -> None:
    for path in _owned_files:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log(f'[mcp-launch] artifact cleanup failed: {exc}', flush=True)


atexit.register(_cleanup)
