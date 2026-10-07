"""MC-1081: a follow-up into a session whose worktree is gone must still run.

`_worktree_merge_back_on_end` removes an isolated agent's worktree and branch
when a turn ends with nothing to keep (status 'clean'/'nothing') — a run that
stopped to ask a question, or only read code, commits nothing. The session
stays in `agent_sessions` with `_agent_cwd` pointing at the deleted tree, and
the next follow-up handed that path straight to `subprocess.Popen(cwd=...)`:
`[WinError 267] The directory name is invalid`, session status `error`, run
lost (Sol_Tobin b4e0ad89dcc0, 2026-10-07).

Fix shape: recreate on demand rather than keep every no-commit worktree alive.
Keeping them would leak a tree per read-only run until close/expiry AND would
not cover the other deleters (`gc_stale` at startup, a manual delete, a
restart); recreating at the follow-up covers all of them with one check at the
single point that needs a directory. `agent_worktree.create` is deterministic
on the session id, so the tree comes back at the SAME path — the engine's
cwd-keyed transcript lookup and the session's `_agent_cwd` stay valid.

Pure policy plus one call into `agent_worktree`; the caller owns logging the
returned note (agent log + activity feed) so this module has no server state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from mc import agent_worktree as _aw


def resolve_followup_cwd(session: dict, project: dict | None, project_path: str,
                         create: Callable | None = None) -> tuple[str, str]:
    """Return `(cwd, note)` for respawning `session`'s next turn.

    `note` is '' when `_agent_cwd` is usable as-is (the overwhelmingly common
    case: shared tree, or a worktree that still exists). Otherwise it is a
    one-line human sentence saying what changed, for the agent log.

    Never raises and never returns a path that is not an existing directory
    unless `project_path` itself is missing (nothing better exists then):
      1. worktree missing + session was isolated  -> recreate from base at the
         same path; `_agent_cwd` unchanged.
      2. recreate fails (or session was not isolated) -> fall back to the
         project tree, mark the session non-isolated so its end-hook does not
         try to merge back a tree that no longer exists, and say so.
    """
    cwd = (session or {}).get('_agent_cwd') or ''
    if not cwd or cwd == project_path or Path(cwd).is_dir():
        return (cwd or project_path), ''

    sid = (session or {}).get('session_id', '')
    reason = ''
    if session.get('_worktree_isolated') and project and sid:
        try:
            ok, path = (create or _aw.create)(project, sid)
        except Exception as e:  # create() is documented best-effort; belt and braces
            ok, path = False, f'{type(e).__name__}: {e}'
        if ok and Path(path).is_dir():
            session['_agent_cwd'] = path
            session['_worktree_recreated'] = session.get('_worktree_recreated', 0) + 1
            return path, (f'Worktree {cwd} was removed after the previous turn '
                          f'(it ended with nothing to keep); recreated it from '
                          f'base so this follow-up runs in an isolated tree again.')
        reason = f' (recreate failed: {path})'

    session['_cwd_moved_from'] = cwd
    session['_agent_cwd'] = project_path
    session['_worktree_isolated'] = False
    return project_path, (f'Worktree {cwd} no longer exists{reason}; this follow-up '
                          f'runs in the project tree instead of an isolated one.')
