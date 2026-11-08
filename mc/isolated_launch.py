"""MC-1165 round 4: the one rule for where an isolated session's process starts.

A session with `_worktree_isolated` set was promised its own worktree and
branch. Every time it is respawned the process must start THERE. Three rounds
of per-path guards each left another respawn path that quietly started it in
the project checkout instead (queued follow-up replay, the failed-resume
recovery that passed the project path straight to the spawn, the vendor
follow-up that flipped the session to non-isolated when recreation failed).
This module is the single place that decides, so a path cannot forget.

`launch_cwd` returns the session's existing worktree, or recreates it (same
path, same branch) when it was removed, or raises `LaunchRefused`. It never
returns the project checkout for an isolated session. It must be called inside
`worktree_lifecycle.resuming` / `removal_window` for the session: recreation and
the spawn that follows must not interleave with a removal.

A refusal is not a loss. `mark_refused` leaves the session in `error` with a
plain reason and keeps the request it could not run in
`pending_recovery_message`, which the guardian retries with its normal backoff
and which makes the cold-review callback and the removal timer leave the branch
alone. Pure policy plus one call into `agent_worktree`; no server state.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from mc import agent_worktree as _aw
from mc import worktree_lifecycle as _lifecycle


class LaunchRefused(RuntimeError):
    """The session is isolated and a usable worktree could not be produced."""


class RevivalRefused(LaunchRefused):
    """A purged isolated conversation could not be revived into its worktree.

    Raised by the two `_revive_*_from_agent_log` functions INSTEAD of returning
    None. None means "nothing to revive here", and every caller answers that by
    starting a fresh dispatch, which for a lone agent runs in the project
    checkout. A refusal is a different answer: the conversation exists, its
    worktree is the only place it may run, and that cannot be had. Callers turn
    this into a 409 (HTTP) or a failed run (scheduler) carrying `str(exc)` and
    never fall through to a fresh dispatch (MC-1165 r6)."""


def _same(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def held_tree_mismatch(session: dict, project: dict | None) -> str:
    """'' when an isolated session's process ran in the worktree its own id
    names; otherwise one sentence saying which tree it actually held.

    Every finish-time step (cold review, merge-back, LOC, removal) finds the
    work by `session_id` alone: `.clayrune/agents/<id>` and `clayrune/agent/<id>`.
    A run that holds some OTHER tree (a dispatch that minted a fresh id but was
    handed a reused session's tree, MC-1165 r9) is invisible to all of them, so
    its work is never reviewed or merged. Callers log this loudly; it is not
    recoverable by retrying. Never raises."""
    try:
        sid = (session or {}).get('session_id', '')
        held = (session or {}).get('_agent_cwd') or ''
        if not (session or {}).get('_worktree_isolated') or not sid or not held:
            return ''
        expected = _aw.worktree_path(project or {}, sid)
        if expected is None:
            return ''
        if _same(held, str(expected)):
            return ''
        try:
            if os.path.commonpath([os.path.normcase(os.path.realpath(held)),
                                   os.path.normcase(os.path.realpath(str(expected)))]
                                  ) == os.path.normcase(os.path.realpath(str(expected))):
                return ''  # a subdirectory of its own tree
        except ValueError:
            pass
        return (f"session {sid[:12]} ran in {held}, not in its own worktree "
                f"{expected} (branch {_aw.branch_name(sid)}"
                f"{'' if _aw.branch_exists(project or {}, sid) else ', which does not exist'})")
    except Exception as e:  # diagnostic only; never block the finish path
        return f"session tree identity could not be checked: {type(e).__name__}: {e}"


def _usable(path: str, project_path: str) -> bool:
    """A real worktree: an existing directory, not the project checkout, whose
    `.git` marker git itself resolves to a worktree of THIS project's
    repository. A bare directory under `.clayrune/agents/` is not one (git run
    inside it would act on the project repository), and neither is a restored
    directory whose registration git has since removed (every git command in it
    fails)."""
    if not (path and not _same(path, project_path) and Path(path).is_dir()
            and (Path(path) / '.git').exists()):
        return False
    return _aw.valid_worktree(project_path, path)[0]


def launch_cwd(session: dict, project: dict | None, project_path: str,
               create: Callable | None = None) -> tuple[str, str]:
    """`(cwd, note)` for starting `session`'s next process. `note` is '' when its
    worktree was usable as it stood, otherwise one sentence for the agent log.

    Raises `LaunchRefused` for an isolated session that has no usable worktree
    and cannot get one. A non-isolated session has nothing to protect and gets
    its recorded directory, or the project path when that is gone."""
    sid = (session or {}).get('session_id', '')
    cwd = (session or {}).get('_agent_cwd') or ''
    if not session.get('_worktree_isolated'):
        return (cwd if cwd and Path(cwd).is_dir() else project_path), ''
    if not _lifecycle.holds(sid):
        raise LaunchRefused(f'launch directory for isolated session {sid[:12]} chosen '
                            f'outside the lifecycle exclusion')
    if _usable(cwd, project_path):
        session.pop('_launch_refused', None)
        return cwd, ''
    why = f'worktree {cwd or "(none recorded)"} is not usable'
    if not project or not sid:
        raise LaunchRefused(f'{why}, and the project record is unavailable to recreate it')
    try:
        ok, path = (create or _aw.create)(project, sid)
    except Exception as e:  # create() is documented best-effort; belt and braces
        ok, path = False, f'{type(e).__name__}: {e}'
    if not ok or not _usable(path, project_path):
        raise LaunchRefused(f'{why}, and recreating it failed: {path}')
    session['_agent_cwd'] = path
    session['_worktree_recreated'] = session.get('_worktree_recreated', 0) + 1
    session.pop('_launch_refused', None)
    return path, (f'Worktree {cwd or path} was missing; recreated it from base so this '
                  f'turn runs in an isolated tree again.')


def mark_refused(session: dict, message: str | None, reason: str, now: float) -> str:
    """Put a refused session into a visible, recoverable state. Call under the
    project lock. Returns the line written to the session log."""
    session['status'] = 'error'
    session['last_status_change_time'] = now
    if session.get('mode') == 'B':
        session['process_alive'] = False
    session['_launch_refused'] = reason
    if message:
        held = session.get('pending_recovery_message')
        if not held:
            session['pending_recovery_message'] = message
        elif message not in held:
            session['pending_recovery_message'] = f'{held}\n\n{message}'
    line = (f'[Launch refused: {reason}. The session was NOT started in the project '
            f'checkout.{" Its request is kept for retry." if message else ""}]')
    session.setdefault('log_lines', []).append(line)
    return line


def hold_vendor_recovery(session: dict) -> bool:
    """True when the guardian must NOT retry this session's held request.

    The guardian's retry (`_guardian_attempt_recovery` -> `_auto_dispatch_followup`)
    builds a `claude --continue` command and spawns it as engine 'claude'. A
    refused launch for a Codex/Gemini/Qwen session leaves its request in
    `pending_recovery_message`; handing that to Claude would run it on the wrong
    provider with the wrong continuation protocol while the session still says
    it is a vendor session. Provider-native recovery is not built, so such a
    session stays in `error`, `guardian_state='needs_attention'`, with the
    request retained for a human (resend / dismiss). Mirrors the vendor
    exclusion on guardian State 4. Claude's bounded retry is untouched. Call
    under the project lock; the log line is written once."""
    if (session.get('provider') or 'claude').lower() == 'claude':
        return False
    if session.get('guardian_state') != 'needs_attention':
        session['guardian_state'] = 'needs_attention'
        session.setdefault('log_lines', []).append(
            f"[Guardian: auto-recovery is Claude-only; this {session.get('provider')} "
            f"session's request is kept, not retried. Resend it yourself or dismiss it.]")
    return True


def owned_worktree(project: dict, session_id: str, *, logged: bool = False) -> str:
    """The worktree path this conversation owns, or '' when it never had one.

    A revived (purged) session has no `_worktree_isolated` flag in memory. Two
    things persist it. From r5 on the agent-log row carries `worktree_isolated`
    (written at dispatch, kept through completion); the caller passes it as
    `logged`, and that alone is enough even after the worktree directory and its
    branch are both gone (merged and deleted). Rows written before r5 carry no
    flag, so git's own state is the fallback: the worktree directory
    `<project>/.clayrune/agents/<sid>` or the branch `clayrune/agent/<sid>`
    that `agent_worktree.create` made for it (kept when a no-commit end removes
    the tree). The path is returned even if it is unusable; `launch_cwd` then
    recreates it or refuses, it never falls back to the project checkout. A
    legacy row whose tree AND branch are both gone leaves no record anywhere and
    revives as an ordinary session (disclosed residual)."""
    wt = _aw.worktree_path(project, session_id)
    if wt is None:
        return ''
    if logged or wt.exists() or _aw.branch_exists(project, session_id):
        return str(wt)
    return ''
