"""Prompt provenance: real launch/rollover signals, never historic log prose."""
from __future__ import annotations

from threading import Lock
from typing import Any

_lock = Lock()
_seen_projects: set[str] = set()
_origins: dict[tuple[str, str], str] = {}


def note_resume(project_id: str, session_id: str, predecessor: str) -> None:
    with _lock:
        _origins.setdefault((project_id, session_id),
                            f'Session origin: existing chat resumed (session {predecessor}).')


def note_rollover(project_id: str, session_id: str, predecessor: str) -> None:
    if session_id:
        with _lock:
            _origins[project_id, session_id] = (
                f'Session origin: auto-fresh rollover from predecessor session {predecessor}.')


def origin_line(project_id: str, session_id: str, source: str = '',
                session: dict[str, Any] | None = None) -> str:
    if not session_id:
        return 'Session origin: unavailable (no session id supplied).'
    key = project_id, session_id
    with _lock:
        if key in _origins:
            if project_id not in _seen_projects:
                _origins[key] = (_origins[key].rstrip('.')
                                 + '; first session for this project after server start/restart.')
            _seen_projects.add(project_id)
            return _origins[key]
        if session and (session.get('claude_session_id') or session.get('provider_session_id')):
            origin = 'existing chat continued'
        elif source == 'agent':
            origin = 'agent-dispatched new session'
        elif source and source != 'ui':
            origin = f'{source}-started new session'
        else:
            origin = 'user-started new chat'
        if project_id not in _seen_projects:
            origin += '; first session for this project after server start/restart'
        _seen_projects.add(project_id)
        line = f'Session origin: {origin}.'
        _origins[key] = line
        return line


def recent_activity(entries: list[dict[str, Any]], session_id: str) -> str:
    lines = []
    for entry in entries:
        sid = entry.get('session_id') or ''
        if sid and sid == session_id:
            label = f'this session {sid}'
        elif sid:
            label = f'other/predecessor session {sid}; not this session'
        else:
            label = 'project history; session unknown, may predate this chat'
        lines.append(f"  - [{label}] {entry.get('ts', '')}: {entry.get('msg', '')}")
    return 'Recent activity:\n' + '\n'.join(lines)
