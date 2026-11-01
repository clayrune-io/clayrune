"""OpenCode runtime-only MCP configuration, including explicit inherited disables."""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Mapping

from mc.mcp_launch_config import Servers, remote_url, transport


def translate(servers: Servers, inherited: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {name: {'enabled': False} for name in inherited if name not in servers}
    for name, spec in servers.items():
        if transport(spec) == 'stdio':
            entry: Dict[str, Any] = {'type': 'local', 'command': [spec['command'], *spec.get('args', [])],
                                     'environment': spec.get('env', {}), 'enabled': True}
            if spec.get('cwd'):
                entry['cwd'] = spec['cwd']
        else:
            entry = {'type': 'remote', 'url': remote_url(spec), 'enabled': True}
            if spec.get('headers'):
                entry['headers'] = spec['headers']
        previous = inherited.get(name)
        if isinstance(previous, dict) and previous.get('type') not in (None, entry['type']):
            # OpenCode deep-merges its inline config; a transport change can
            # retain invalid fields from the old union member. Never load it silently.
            raise ValueError('OpenCode native MCP name has a conflicting transport; launch refused')
        out[name] = entry
    return out


def launch_env(servers: Servers, prefix: List[str], env: Mapping[str, str],
               cwd: str) -> Dict[str, str]:
    try:
        result = subprocess.run(prefix + ['debug', 'config'], env=dict(env), cwd=cwd,
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError()
        data = json.loads(result.stdout)
        inherited = data.get('mcp', {})
        content = json.loads(env.get('OPENCODE_CONFIG_CONTENT') or '{}')
        if not isinstance(inherited, dict) or not isinstance(content, dict):
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        raise ValueError('Cannot resolve native OpenCode MCP names; launch refused') from None
    content['mcp'] = translate(servers, inherited)
    overrides = {'OPENCODE_CONFIG_CONTENT': json.dumps(content)}
    try:
        proof = subprocess.run(prefix + ['debug', 'config'], env={**env, **overrides}, cwd=cwd,
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=20,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        actual = json.loads(proof.stdout)['mcp']
        active = {name: cfg for name, cfg in actual.items() if cfg.get('enabled', True)}
        if proof.returncode or active.keys() != servers.keys():
            raise ValueError()
        for name in servers:
            expected = content['mcp'][name]
            if any(active[name].get(key, {}) != expected.get(key, {})
                   for key in ('type', 'command', 'environment', 'cwd', 'url', 'headers')):
                raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('OpenCode did not load the exact per-launch MCP selection; launch refused') from None
    return overrides
