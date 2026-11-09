"""Resume provenance and refusal policy (MC-1167).

Finding a transcript is a read capability, not evidence of its original cwd.
This module never recreates a directory or relocates a conversation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


class ResumeCwdMissing(ValueError):
    """The transcript is readable but its recorded working directory is gone."""

    def __init__(self, cwd: str, native_id: str, provider: str = 'claude'):
        self.cwd = cwd
        self.native_id = native_id
        self.provider = provider
        super().__init__(
            f'This conversation\'s working tree was removed: {cwd}. '
            'Start a new chat or restore that directory before continuing. '
            'The original transcript has been kept; no process was started.')


def recorded_cwd(transcript: str | Path) -> str | None:
    """First cwd recorded by Claude JSONL or a Codex session_meta record.

    Ignore partial/malformed JSONL rows, as transcript readers do. I/O errors
    propagate to the caller's logged lookup boundary.
    """
    with Path(transcript).open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            metadata = row.get('payload') if row.get('type') == 'session_meta' else row
            if not isinstance(metadata, dict):
                continue
            cwd = metadata.get('cwd')
            if isinstance(cwd, str) and cwd.strip() and Path(cwd).is_absolute():
                return cwd
    return None


def require_cwd(cwd: str, native_id: str, provider: str = 'claude') -> str:
    if not Path(cwd).is_dir():
        raise ResumeCwdMissing(cwd, native_id, provider)
    return cwd


def is_worktree(cwd: str, project_path: str) -> bool:
    """Only a direct per-agent tree under this project is managed isolation."""
    return Path(cwd).resolve().parent == (
        Path(project_path) / '.clayrune' / 'agents').resolve()


def refusal(error: ResumeCwdMissing, data: dict, project_id: str,
            update_log: Callable, log: Callable, timestamp: str) -> dict:
    """Keep rejected input on the conversation row, and echo it for the client.

    No delivery/retry is implied. Incognito requests only get the response copy.
    Persistence failure is explicit and never causes a fallback dispatch.
    """
    message = data.get('message', data.get('task', ''))
    session_id = data.get('session_id') or error.native_id
    saved = False
    if not data.get('incognito'):
        def retain(rows):
            row = next((r for r in rows if r.get('session_id') == session_id), None)
            if row and row.get('incognito'):
                return False
            if row is None:
                native_field = ('claude_session_id' if error.provider == 'claude'
                                else 'provider_session_id')
                row = {'session_id': session_id, native_field: error.native_id,
                       'provider': error.provider, 'status': 'error'}
            else:
                rows.remove(row)
            # A refused request is new activity: keep it inside the log's
            # existing newest-N retention limit even when the chat is old.
            rows.insert(0, row)
            row.setdefault('resume_refusals', []).append({
                'message': message, 'cwd': error.cwd, 'ts': timestamp,
                'native_id': error.native_id})
            return True
        try:
            saved = bool(update_log(project_id, retain))
        except Exception as exc:
            log(f'[resume-cwd] could not retain refused request for {session_id}: {exc}')
    return {'ok': False, 'code': 'resume_cwd_missing', 'error': str(error),
            'session_id': session_id, 'cwd': error.cwd,
            'request_message': message, 'request_saved': saved}
