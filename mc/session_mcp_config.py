"""Per-launch `clayrune` MCP server registration (BUILTINS-7a).

Adds the first-party cross-session server to the MCP selection a session is
launched with. Pure functions over (project, config); the wiring points in
`mc/blueprints/agent_routes.py` call these and nothing else.

Selection rules (the user's choice always wins):
  * `clayrune_mcp_enabled` false in config -> never added.
  * Project did NOT opt into trimming (no `enabled_mcp_servers` list):
      Claude  -> an ADDITIVE `--mcp-config` (no `--strict-mcp-config`), so the
                 native global/project/plugin fleet is untouched.
      Gemini / Qwen -> added to the merged set they are already given.
  * Project HAS an `enabled_mcp_servers` list (empty or not): honoured exactly.
    `clayrune` is in the catalog like `engram`, so it is selectable, but it is
    only loaded when the list names it. An empty list stays empty.
  * Incognito projects and frozen (bundled) builds get nothing: a frozen app
    has no `python` to launch the server with.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from mc.core import _log

SERVER_NAME = 'clayrune'
CONFIG_KEY = 'clayrune_mcp_enabled'
SERVER_SCRIPT = Path(__file__).resolve().parent / 'clayrune_mcp' / 'server.py'


def is_enabled(config: Mapping[str, Any]) -> bool:
    return bool((config or {}).get(CONFIG_KEY, True))


def _base_url(config: Mapping[str, Any]) -> str:
    port = os.environ.get('MC_PORT') or (config or {}).get('port') or 5199
    return f'http://127.0.0.1:{int(port)}'


def server_spec(project: Optional[Mapping[str, Any]], config: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The stdio spec for this project's launches, or None when it must not load."""
    if not is_enabled(config):
        return None
    pid = str((project or {}).get('id') or '').strip()
    if not pid or (project or {}).get('_is_incognito_project'):
        return None
    if getattr(sys, 'frozen', False):
        _log('[clayrune-mcp] bundled build has no interpreter for the stdio server; not injected',
             level='warn')
        return None
    return {'command': sys.executable or 'python',
            'args': [str(SERVER_SCRIPT), '--project', pid, '--base-url', _base_url(config)]}


def add_to_catalog(catalog: Dict[str, Any], project: Optional[Mapping[str, Any]],
                   config: Mapping[str, Any]) -> Dict[str, Any]:
    """Make `clayrune` selectable in the loadout. A user-defined server of the
    same name wins (setdefault), like the engram re-declaration."""
    spec = server_spec(project, config)
    if spec is not None:
        catalog.setdefault(SERVER_NAME, spec)
    return catalog


def claude_additive_flags(project: Optional[Mapping[str, Any]], resolved: Optional[str],
                          config: Mapping[str, Any]) -> List[str]:
    """Extra Claude flags for an UNTRIMMED project; [] whenever the project
    opted into a selection (`resolved` is a JSON string) or the server is off."""
    if resolved is not None and resolved != '':
        return []
    if isinstance((project or {}).get('enabled_mcp_servers'), list):
        return []  # resolution failed open; do not second-guess an explicit choice
    spec = server_spec(project, config)
    if spec is None:
        return []
    # First in the list so the following `--print` ends the variadic option.
    return ['--mcp-config', json.dumps({'mcpServers': {SERVER_NAME: spec}})]


def add_to_effective_servers(servers: Dict[str, Any], project: Optional[Mapping[str, Any]],
                             config: Mapping[str, Any]) -> Dict[str, Any]:
    """Gemini/Qwen untrimmed set (already gemini-shaped; stdio is identical)."""
    spec = server_spec(project, config)
    if spec is not None and SERVER_NAME not in servers:
        servers[SERVER_NAME] = spec
    return servers
