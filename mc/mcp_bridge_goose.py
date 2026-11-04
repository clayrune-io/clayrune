"""Goose launch-owned recipes select the exact MCP set without changing its profile."""
from __future__ import annotations

import subprocess
import re
from typing import Any, Dict, List

from mc.mcp_launch_config import Servers, private_json, remote_url, transport


def translate(servers: Servers) -> List[Dict[str, Any]]:
    extensions = []
    for name, spec in sorted(servers.items()):
        kind = transport(spec)
        if kind == 'sse':
            raise ValueError('Goose MCP does not support the selected SSE transport')
        out: Dict[str, Any] = {'name': name, 'description': '', 'timeout': 300}
        if kind == 'stdio':
            out.update(type='stdio', cmd=spec['command'], args=spec.get('args', []),
                       envs=spec.get('env', {}))
            if spec.get('cwd'):
                out['cwd'] = spec['cwd']
        else:
            out.update(type='streamable_http', uri=remote_url(spec))
            if spec.get('headers'):
                out['headers'] = spec['headers']
        extensions.append(out)
    return extensions


def _literal(value: str) -> str:
    # Goose preprocesses {{...}} even inside raw blocks. Emit every opening
    # delimiter through a quoted literal (which that preprocessor exempts),
    # then resume raw text. This also prevents its inheritance regex from
    # treating example include/extends directives as template instructions.
    escaped = re.sub(r'\{\{|\{%', lambda match:
                     "{% endraw %}{{ '" + match.group() + "' }}{% raw %}", value)
    return '{% raw %}' + escaped + '{% endraw %}'


def _literal_fields(value: Any) -> Any:
    if isinstance(value, str):
        return _literal(value) if '{{' in value or '{%' in value else value
    if isinstance(value, list):
        return [_literal_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _literal_fields(item) for key, item in value.items()}
    return value


def launch_args(servers: Servers, prompt: str, system_prompt: str = '', *,
                prefix: List[str] | None = None, env: Dict[str, str] | None = None,
                cwd: str = '') -> List[str]:
    recipe = {'version': '1.0.0', 'title': 'Clayrune session',
              'description': 'Per-launch MCP selection', 'extensions': _literal_fields(translate(servers)),
              'instructions': _literal(system_prompt or 'Complete the supplied task.'),
              'prompt': _literal(prompt)}
    # An explicit extensions array, even [], suppresses both default and plugin
    # extensions. --no-profile would also suppress the recipe's selected set.
    path = private_json(recipe)
    if prefix:
        try:
            check = subprocess.run(prefix + ['recipe', 'validate', str(path)], env=env, cwd=cwd,
                                   capture_output=True, timeout=20,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if check.returncode:
                raise ValueError()
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ValueError('Goose cannot load the per-launch MCP recipe; launch refused') from None
    return ['--recipe', str(path)]
