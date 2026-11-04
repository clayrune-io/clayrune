"""Codex per-launch MCP overrides; native settings and authentication stay in place."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Mapping

from mc.mcp_launch_config import Servers, private_text, remote_url, transport


def _toml(value: Any) -> str:
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(k)} = {_toml(v)}' for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_toml(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def translate(servers: Servers, inherited_names: List[str]) -> Dict[str, Any]:
    # The root table merges: mcp_servers={} does NOT clear existing entries.
    # A dotted per-server override replaces that entry, including its transport.
    entries: Dict[str, Any] = {}
    for name in sorted(set(inherited_names) - servers.keys()):
        entries[name] = {'enabled': False}
    for name, spec in sorted(servers.items()):
        kind = transport(spec)
        if kind == 'sse':
            raise ValueError('Codex MCP does not support the selected SSE transport')
        out: Dict[str, Any] = {'enabled': True}
        if kind == 'stdio':
            out.update(command=spec['command'], args=spec.get('args', []), env=spec.get('env', {}))
            if spec.get('cwd'):
                out['cwd'] = spec['cwd']
        else:
            out['url'] = remote_url(spec)
            if spec.get('headers'):
                out['http_headers'] = spec['headers']
        entries[name] = out
    return entries


def launch_args(servers: Servers, prefix: List[str], env: Mapping[str, str],
                cwd: str) -> List[str]:
    """Read the CLI's effective names, without starting MCP servers or a model turn."""
    try:
        result = subprocess.run(prefix + ['mcp', 'list', '--json', '-c', 'features.plugins=false'],
                                env=dict(env), cwd=cwd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError()
        entries = json.loads(result.stdout)
        if not isinstance(entries, list) or not all(
                isinstance(entry, dict) and isinstance(entry.get('name'), str) for entry in entries):
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError('Cannot resolve native Codex MCP names; launch refused') from None
    selected = translate(servers, [entry['name'] for entry in entries])
    # File profiles are per invocation and keep values off the process command
    # line. Authentication still resolves from the unchanged CODEX_HOME.
    root = Path(env.get('CODEX_HOME') or Path.home() / '.codex')
    path = private_text('mcp_servers = ' + _toml(selected) + '\n[features]\nplugins = false\n',
                        root, '.config.toml')
    profile = path.name.removesuffix('.config.toml')
    try:
        proof = subprocess.run(prefix + ['--profile', profile, 'mcp', 'list', '--json'],
                               env=dict(env), cwd=cwd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=20,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        active = {row['name']: row for row in json.loads(proof.stdout) if row.get('enabled')}
        if proof.returncode or active.keys() != servers.keys():
            raise ValueError()
        for name, spec in servers.items():
            actual = active[name]['transport']
            if transport(spec) == 'stdio':
                if any(actual.get(key, default) != spec.get(key, default)
                       for key, default in [('command', ''), ('args', []), ('env', {})]):
                    raise ValueError()
            else:
                if actual.get('url') != remote_url(spec):
                    raise ValueError()
                if (actual.get('http_headers') or {}) != spec.get('headers', {}):
                    raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Codex did not load the exact per-launch MCP profile; launch refused') from None
    return ['--profile', profile]
