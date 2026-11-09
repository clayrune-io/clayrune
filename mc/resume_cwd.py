"""Resume provenance and refusal policy (MC-1167).

Finding a transcript is a read capability, not evidence of its original cwd.
This module never recreates a directory or relocates a conversation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from mc import isolated_launch


class ResumeCwdMissing(ValueError):
    """The transcript is readable but its recorded working tree is unusable."""

    def __init__(self, cwd: str, native_id: str, provider: str = 'claude', *, detail: str = ''):
        self.cwd = cwd
        self.native_id = native_id
        self.provider = provider
        super().__init__(
            f'This conversation\'s working tree was removed or is no longer usable: {cwd}. '
            + (f'Worktree validation failed: {detail}. ' if detail else '') +
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


def require_cwd(cwd: str, native_id: str, provider: str = 'claude',
                project_path: str = '') -> str:
    try:
        usable = Path(cwd).is_dir() and (
            not project_path or not is_worktree(cwd, project_path)
            or isolated_launch._usable(cwd, project_path))
    except Exception as exc:
        # A known cwd whose validation fails must never become "no provenance"
        # at the route's best-effort transcript lookup boundary.
        raise ResumeCwdMissing(cwd, native_id, provider,
                               detail=f'{type(exc).__name__}: {exc}') from exc
    if not usable:
        raise ResumeCwdMissing(cwd, native_id, provider)
    return cwd


def is_worktree(cwd: str, project_path: str) -> bool:
    """Only a direct per-agent tree under this project is managed isolation."""
    return Path(cwd).resolve().parent == (
        Path(project_path) / '.clayrune' / 'agents').resolve()


def transcript_cwd(runtime, project_path: str, native_id: str,
                   provider: str) -> str | None:
    """Use each provider's exact provenance, including after tree removal.

    Claude's reader may search broadly, so only its recorded cwd counts.
    Qwen's exact encoded directory is provenance even without a JSON cwd.
    Codex accepts an empty lookup cwd; validate its metadata against THIS
    project before using it, so another project's native id cannot relocate us.
    """
    if provider == 'codex':
        transcript = runtime.transcript_path('', native_id)
        cwd = recorded_cwd(transcript) if transcript else None
        if cwd and (isolated_launch._same(cwd, project_path)
                    or is_worktree(cwd, project_path)):
            return require_cwd(cwd, native_id, provider, project_path)
        return None

    candidates = [Path(project_path)]
    agents = Path(project_path) / '.clayrune' / 'agents'
    if agents.is_dir():
        candidates.extend(p for p in agents.iterdir() if p.is_dir())
    for candidate in candidates:
        transcript = runtime.transcript_path(str(candidate), native_id)
        if not transcript:
            continue
        cwd = recorded_cwd(transcript)
        if not cwd and provider == 'qwen':
            cwd = str(candidate)
        if cwd:
            return require_cwd(cwd, native_id, provider, project_path)
    return None


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
