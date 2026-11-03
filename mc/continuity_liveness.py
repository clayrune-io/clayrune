"""Read-only verification of agent references carried in continuity text."""
from __future__ import annotations

import re
import subprocess
from typing import Any

from mc import state
from mc.core import _log

_ID = r'(?:[0-9a-f]{12}|hm_[0-9a-f]{8})'
_SESSION = re.compile(
    rf'(?i)(?:\bsession\s+[`#]?|\()({_ID})(?![0-9a-f])')
_AGENT_BRANCH = re.compile(rf'\bclayrune/agent/({_ID})(?![0-9a-f])', re.I)
_BRANCH = re.compile(r'\bbranch\s+[`\"\']?([\w][\w./-]*)', re.I)


def _branch_exists(project: dict[str, Any], branch: str) -> bool | None:
    path = project.get('project_path')
    if not path:
        return None
    try:
        result = subprocess.run(
            ['git', 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}'],
            cwd=path, capture_output=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode in (0, 1):
            return result.returncode == 0
        _log(f'[continuity] branch check failed (git rc={result.returncode})')
    except Exception as e:
        _log(f'[continuity] branch check failed: {e}')
    return None


def _session_live(project: dict[str, Any], sid: str) -> bool | None:
    session = state.agent_sessions.get(sid)
    if (not session or session.get('project_id') != project.get('id')
            or session.get('status') not in ('running', 'idle')):
        return False
    proc = session.get('proc')
    # Per-turn runtimes intentionally have no child between turns. The registry
    # (also the Floor's source) remains authoritative for an idle session.
    if proc is not None and session.get('status') == 'running':
        try:
            return proc.poll() is None
        except Exception as e:
            _log(f'[continuity] session {sid} poll failed: {e}')
            return None
    return True


def render_thread(project: dict[str, Any], text: str, age: str,
                  prefix: str = 'IN FLIGHT — ') -> str:
    """Verify explicit session/branch claims without rewriting stored memory."""
    ids = list(dict.fromkeys(m.group(1).lower() for regex in (_SESSION, _AGENT_BRANCH)
                            for m in regex.finditer(text)))
    if not ids:
        return f'{prefix}{text} ({age})'
    branches = [m.group(1).rstrip('./-') for m in _BRANCH.finditer(text)]
    branches.extend(m.group(0) for m in _AGENT_BRANCH.finditer(text))
    # Legacy wording: "fixing on branch (938e81dbb116)" names the branch
    # through its worktree session id, using the existing branch convention.
    if re.search(r'\bbranch\s*\(', text, re.I):
        branches.extend(f'clayrune/agent/{sid}' for sid in ids)
    branch_states = [_branch_exists(project, b) for b in dict.fromkeys(branches)]
    sessions = [(sid, _session_live(project, sid)) for sid in ids]
    dead = [sid for sid, live in sessions if live is False]
    unknown = any(live is None for _, live in sessions) or None in branch_states
    absent = False in branch_states
    if dead or absent or unknown:
        status = '; '.join(f'session {sid} ' + (
            'not running' if live is False else 'running' if live else 'liveness unavailable')
            for sid, live in sessions)
        if branches:
            status += ('; branch absent' if absent else '; branch check unavailable'
                       if None in branch_states else '; branch exists')
        label = 'LOST IN RESTART' if dead else 'STATE MISMATCH' if absent else 'UNVERIFIED'
        return f'{label}: {text} ({status}) ({age})'
    return f'{prefix}{text} ({age})'
