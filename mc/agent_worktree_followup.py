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

from mc import isolated_launch as _launch


def resolve_followup_cwd(session: dict, project: dict | None, project_path: str,
                         create: Callable | None = None) -> tuple[str, str]:
    """Return `(cwd, note)` for respawning `session`'s next turn.

    `note` is '' when `_agent_cwd` is usable as-is (the overwhelmingly common
    case: shared tree, or a worktree that still exists). Otherwise it is a
    one-line human sentence saying what changed, for the agent log.

    An isolated session goes through `isolated_launch.launch_cwd`: its worktree
    is recreated from base at the same path when it was removed, and when that
    cannot be done `LaunchRefused` is raised. It used to fall back to the
    project tree and flip the session to non-isolated, which put its next turn
    in the shared checkout without anyone having decided that (MC-1165 round 4).
    The caller must hold `worktree_lifecycle.resuming` for the session.

    A session that was never isolated keeps the old behaviour: a recorded
    directory that is gone falls back to the project tree, and says so.
    """
    if (session or {}).get('_worktree_isolated'):
        return _launch.launch_cwd(session, project, project_path, create=create)
    cwd = (session or {}).get('_agent_cwd') or ''
    if not cwd or cwd == project_path or Path(cwd).is_dir():
        return (cwd or project_path), ''
    session['_cwd_moved_from'] = cwd
    session['_agent_cwd'] = project_path
    return project_path, (f'Worktree {cwd} no longer exists; this follow-up '
                          f'runs in the project tree instead.')
