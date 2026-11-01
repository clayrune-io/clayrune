"""Fence parity for the `clayrune` MCP tools and the background-job route (BUILTINS-7a).

The unattended steward fence (`steward/fence.py`) only ever sees a tool call
the agent makes itself. Done with curl, `POST .../agent/<sid>/job` is a Bash
command whose body carries the shell command, so the fence blocks it. Done
through an MCP tool, the same request never passes a Bash hook and the fence
sees nothing. This module closes that gap without adding policy: it asks the
fence the same question, about the same text, the curl way would have asked.

  * `armed(session, config)` is the existing unattended rule, reused verbatim
    from `agent_runtime.codex_fence_armed_decision` (anything but a confirmed
    `trigger_type == 'manual'`, and `fence_unattended_enabled` on).
  * `check_command` classifies a shell command exactly as a Bash tool call.
  * `check_request` classifies the curl a caller would have typed to make the
    same HTTP request (path + JSON body), which is what the fence saw before.

`steward/fence.py` is human-owned (MC-1084); this only imports from it.
"""
from __future__ import annotations

import json
import shlex
from typing import Any, Mapping, Optional, Tuple

from mc.agent_runtime import codex_fence_armed_decision


def armed(session: Optional[Mapping[str, Any]], config: Mapping[str, Any]) -> bool:
    """True when this session's own tool calls would run under the armed fence."""
    return codex_fence_armed_decision(dict(session) if session is not None else None,
                                      bool((config or {}).get('fence_unattended_enabled', True)))


def check_command(command: str, cwd: Optional[str] = None,
                  project_path: Optional[str] = None) -> Tuple[bool, str]:
    """(blocked, reason) for a shell command, as the fence rules it for a Bash call."""
    from steward import fence
    decision = fence.classify_action('Bash', {'command': command}, cwd, project_path)
    return bool(decision.blocked), str(decision.reason or '')


def curl_equivalent(port: Any, path: str, body: Mapping[str, Any]) -> str:
    """The curl command an agent would type to POST `body` to `path`."""
    return (f'curl -s -X POST http://localhost:{port}{path} '
            f'-H "Content-Type: application/json" -d {shlex.quote(json.dumps(body))}')


def check_request(port: Any, path: str, body: Mapping[str, Any], cwd: Optional[str] = None,
                  project_path: Optional[str] = None) -> Tuple[bool, str]:
    return check_command(curl_equivalent(port, path, body), cwd, project_path)
