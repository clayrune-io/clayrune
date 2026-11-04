"""Keep native-resume setup stable and deliver task-scoped memory in messages."""
from __future__ import annotations

from typing import Any, Callable

from mc import memory_turn


def project_fingerprint(project: dict[str, Any], fingerprint: Callable,
                        log: Callable) -> str:
    """Match Claude's best-effort fallback when a fingerprint cannot be read."""
    try:
        return fingerprint(project)
    except Exception as exc:
        log(f'[native-followup] fingerprint failed, reusing stash: {exc}')
        return ''


def record_build(project: dict[str, Any], session: dict[str, Any],
                 task: str, fingerprint: str) -> None:
    """Record the inputs and memory already represented by a successful build."""
    session['_system_prompt_fp'] = fingerprint
    if not session.get('incognito'):
        memory_turn.seed_delivered(project, session, task)


def refresh_stash(project: dict[str, Any], session: dict[str, Any], task: str,
                  *, fingerprint: Callable, rebuild: Callable, log: Callable) -> None:
    """Rebuild only a missing/stale stash, using the same rule as Claude."""
    if session.get('incognito'):
        return
    fp = project_fingerprint(project, fingerprint, log)
    context = session.get('_system_prompt') or ''
    stale = bool(fp) and session.get('_system_prompt_fp') != fp
    if context and not stale:
        return
    try:
        rebuilt = rebuild(project, session, task=task)
    except Exception as exc:
        log(f'[native-followup] context refresh failed, reusing stash: {exc}')
        return
    session['_system_prompt'] = rebuilt
    record_build(project, session, task, fp)


def message_for_turn(project: dict[str, Any], session: dict[str, Any], message: str) -> str:
    """Attach fresh memory to the message without changing the setup stash.

    Native (non-Claude) follow-ups: the clock header is added by the caller
    (`agent_routes._clock_wrap`), not here -- this module has no access to the
    live session registry the gap briefing reads."""
    if session.get('incognito'):
        return message
    refresh = memory_turn.refresh_for_turn(project, session, message)
    return refresh['block'] + '\n\n' + message if refresh['block'] else message
