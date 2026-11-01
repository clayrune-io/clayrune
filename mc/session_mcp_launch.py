"""Runtime MCP bridge registry and per-session launch state (BUILTINS-7b)."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from mc import mcp_bridge_codex, mcp_bridge_goose, mcp_bridge_kiro, mcp_bridge_opencode
from mc.mcp_launch_config import servers_from_json

VENDORS = frozenset(('codex', 'opencode', 'goose', 'kiro'))


def resolve(project: Dict[str, Any], resolved: Optional[str], config: Dict[str, Any]) -> str:
    """Canonical Claude-shaped catalog; preserve explicit selection, including []."""
    import json
    from mc import mcp, session_mcp_config

    if project.get('_is_incognito_project') or getattr(sys, 'frozen', False):
        return json.dumps({'mcpServers': {}})
    if resolved is not None:
        servers = servers_from_json(resolved)
        chosen = dict(servers or {})
    elif isinstance(project.get('enabled_mcp_servers'), list):
        # Resolver failed: an explicit list must never become an untrimmed fleet.
        raise ValueError('Cannot resolve the explicit MCP selection; launch refused')
    else:
        chosen = {name: spec for name, spec in mcp._read_global_servers().items()
                  if isinstance(spec, dict)}
        if project.get('project_path'):
            chosen.update({name: spec for name, spec in
                           mcp._read_project_servers(project['project_path']).items()
                           if isinstance(spec, dict)})
        session_mcp_config.add_to_effective_servers(chosen, project, config)
    if not session_mcp_config.is_enabled(config):
        chosen.pop(session_mcp_config.SERVER_NAME, None)
    return json.dumps({'mcpServers': chosen})


def prepare(vendor: str, cmd: List[str], *, session: Dict[str, Any], cwd: str,
            config_json: Optional[str] = None, env_extra: Optional[Dict[str, str]] = None,
            incognito: bool = False, prompt: str = '', system_prompt: str = '') -> Tuple[List[str], Dict[str, str]]:
    """Apply a selection before spawn; keep it for native resume and model retry.

    Callers own check_launch/prepare_env. This function never spawns a model,
    changes a fence flag, or falls back to the native fleet after a bridge error.
    """
    if config_json is None:
        config_json = session.get('_mcp_config_json') or ''
    if incognito or session.get('incognito') or getattr(sys, 'frozen', False):
        config_json = '{"mcpServers": {}}'
    config_json = config_json or ''
    servers = servers_from_json(config_json)
    env = os.environ.copy()
    env.update(session.get('_mcp_launch_env_extra') or {})
    env.update(env_extra or {})
    if servers is None:
        return cmd, env
    result = list(cmd)
    if vendor == 'codex':
        split = result.index('exec')
        prefix = result[:split]
        result[split:split] = mcp_bridge_codex.launch_args(servers, prefix, env, cwd)
    elif vendor == 'opencode':
        env.update(mcp_bridge_opencode.launch_env(servers, result[:1], env, cwd))
    elif vendor == 'goose':
        result.extend(mcp_bridge_goose.launch_args(servers, prompt, system_prompt,
                                                  prefix=result[:1], env=env, cwd=cwd))
    elif vendor == 'kiro':
        result.extend(mcp_bridge_kiro.launch_args(servers, env, prefix=result[:1], cwd=cwd))
    else:
        raise ValueError('Unknown MCP launch bridge')
    session['_mcp_config_json'] = config_json
    session['_mcp_launch_env_extra'] = dict(env_extra or session.get('_mcp_launch_env_extra') or {})
    return result, env
