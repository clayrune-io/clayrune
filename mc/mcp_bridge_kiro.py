"""Kiro MCP selection in an exclusively created, launch-owned named agent."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Dict, List, Mapping

from mc.mcp_launch_config import Servers, private_json, remote_url, transport


def translate(servers: Servers) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, spec in servers.items():
        if transport(spec) == 'stdio':
            entry: Dict[str, Any] = {'command': spec['command'], 'args': spec.get('args', []),
                                     'env': spec.get('env', {})}
            if spec.get('cwd'):
                raise ValueError('Kiro MCP agent configuration does not support a selected server cwd')
        else:
            entry = {'url': remote_url(spec)}
            if spec.get('headers'):
                entry['headers'] = spec['headers']
        out[name] = entry
    return {'mcpServers': out, 'includeMcpJson': False, 'tools': ['*']}


def launch_args(servers: Servers, env: Mapping[str, str], *,
                prefix: List[str] | None = None, cwd: str = '') -> List[str]:
    # KIRO_HOME remains the operator's, preserving authentication/session state.
    # Never overwrite an existing agent: mkstemp creates an unpredictable name
    # exclusively, and our cleanup registry only removes that owned file.
    root = Path(env.get('KIRO_HOME') or Path.home() / '.kiro')
    if not root.is_absolute() or root == Path.home() or root in Path.home().parents:
        raise ValueError('Invalid KIRO_HOME for a per-launch MCP agent; launch refused')
    path = private_json(translate(servers), directory=root / 'agents', name_key='name')
    if prefix:
        try:
            check = subprocess.run(prefix + ['agent', 'validate', '--path', str(path)],
                                   env=dict(env), cwd=cwd, capture_output=True, timeout=20,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if check.returncode:
                raise ValueError()
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ValueError('Kiro cannot load the per-launch MCP agent; launch refused') from None
    # The filename is the agent selector. No user agent/config is edited in place.
    return ['--agent', path.stem]
