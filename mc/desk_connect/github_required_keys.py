"""The credential environment variables a staged repository documents, for the Desk setup to ask for.

Deterministic and file-only: no model, no process, no network. Every source is text a stranger
wrote, so only variable NAMES leave this module (never a value; a value that is not a placeholder
is not even looked at beyond deciding whether the name is a credential).

Sources, all inside the reviewed clone:
- the server configs `mcp_installer.extract_config` found (example files or the README),
- every JSON block of the README (a tier-1 example file must not hide what the README documents),
- example MCP files in either shape (`mcpServers`, or the `servers` of `.vscode/mcp.json`),
- `.env.example`-style files (`NAME=value` lines; commented lines are optional settings, skipped).

A name is a credential when `mcp_installer` calls it one (credential-like name, or a placeholder
value). Names that differ only by a trailing number form one family of fallback slots (KEY, KEY2,
KEY3): the first is asked for, the rest are listed as optional.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from mc import mcp_installer as installer

MAX_ASKED = 6
MAX_OPTIONAL = 8
_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
_EXAMPLE_FILES = ('.mcp.json.example', 'mcp.json.example', '.mcp.example.json', 'mcp.example.json',
                  '.vscode/mcp.json', '.cursor/mcp.json') + installer._EXAMPLE_FILENAMES
_ENV_FILES = ('.env.example', '.env.sample', '.env.template', '.env.dist', 'env.example', 'example.env', '.example.env')
_ENV_LINE_RE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]{0,63})\s*=\s*(.*)$')
_MAX_READ = 200_000


def _read(path: Path) -> str:
    try:
        if path.is_file() and path.stat().st_size <= _MAX_READ:
            return path.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        from mc.core import _log
        _log(f'[desk-connect] credential source unreadable: {type(e).__name__}', flush=True)
    return ''


def _server_maps(obj, depth: int = 0):
    """Every `mcpServers` / `servers` mapping of server objects in a decoded JSON value."""
    if depth > 6:
        return
    if isinstance(obj, dict):
        for key in ('mcpServers', 'servers'):
            val = obj.get(key)
            if isinstance(val, dict) and val and all(isinstance(v, dict) for v in val.values()):
                yield val
        for v in obj.values():
            yield from _server_maps(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _server_maps(v, depth + 1)


def _from_servers(servers: dict, source: str, found: list) -> None:
    for entry in installer.detect_secrets(servers):
        found.append((entry['key'], source))


def _from_json(text: str, source: str, found: list) -> None:
    try:
        data = json.loads(re.sub(r'(?m)^\s*//.*$', '', text))
    except ValueError:
        return
    for servers in _server_maps(data):
        _from_servers(servers, source, found)


def _from_env_file(text: str, source: str, found: list) -> None:
    for line in text.splitlines():
        m = _ENV_LINE_RE.match(line)
        if m and installer.is_credential_slot(m.group(1), m.group(2).strip().strip('"\'')):
            found.append((m.group(1), source))


def _family(name: str) -> str:
    return re.sub(r'_?\d+$', '', name)


def detect(install_dir: str, configs: dict | None = None) -> dict:
    """`{'ask': [{'env', 'source'}], 'optional': [{'env', 'source'}]}`, names only, in documented order."""
    root = Path(install_dir)
    found: list[tuple[str, str]] = []
    if configs:
        _from_servers(configs, 'repository configuration', found)
    for rel in _EXAMPLE_FILES:
        text = _read(root / rel)
        if text:
            _from_json(text, rel, found)
    for name in installer._README_NAMES:
        text = _read(root / name)
        if text:
            for block in installer.json_fences(text):
                _from_json(block, name, found)
            break
    for rel in _ENV_FILES:
        text = _read(root / rel)
        if text:
            _from_env_file(text, rel, found)
    sources: dict[str, list[str]] = {}
    for name, source in found:
        if _NAME_RE.match(name):
            seen = sources.setdefault(name, [])
            if source not in seen:
                seen.append(source)
    families: dict[str, list[str]] = {}
    for name in sources:
        families.setdefault(_family(name), []).append(name)
    ask: list[dict] = []
    optional: list[dict] = []
    for names in families.values():
        # The unnumbered (or lowest-numbered) slot is the one a server reads first.
        names.sort(key=lambda n: (n != _family(n), int(n[len(n.rstrip('0123456789')):] or 0)))
        for i, name in enumerate(names):
            row = {'env': name, 'source': ', '.join(sources[name][:3])}
            if i == 0 and len(ask) < MAX_ASKED:
                ask.append(row)
            elif len(optional) < MAX_OPTIONAL:
                optional.append(row)
    return {'ask': ask, 'optional': optional}
