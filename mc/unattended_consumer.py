"""Who is READING: is this context build for an unattended consumer?

The reader half of `_UNATTENDED_LOOP_RULE` (CLAUDE.md, "Learning-system safety
rails", rule 2: a human on at least one side of every learning loop). The
writer half stamps `origin` with `distiller.is_unattended_session`: anything
whose `trigger_type` is not literally 'manual' is unattended. Until ML4
(docs/_review/2026-11-16_03_memory_learning.md) the dispatch-time reader asked
a different question, the task TEXT only, so a session started by an ordinary
schedule or by another agent wrote as unattended and read as attended, and a
schedule-to-schedule or dispatch-to-dispatch loop had no human on either side.

This module is the one place the reader's answer is computed, so the
dispatch-time read floor (`_build_agent_context`), turn 1's delivered-set seed
(`memory_turn.seed_delivered`) and every later turn
(`memory_turn._refresh_for_turn`, which already used the session test) cannot
drift apart again.

Leaf module: imports `mc.distiller` only, never `mc.blueprints.*` or `server`.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from mc import distiller as _distiller


def for_session(task: Optional[str], session: Optional[Mapping[str, Any]]) -> bool:
    """The reader test for a session dict in hand. A dict with no
    `trigger_type` is unattended, the same fail-safe the writer stamp uses."""
    trigger_type = (session or {}).get('trigger_type', '')
    return _distiller.is_unattended_session(task, trigger_type)


def for_context_build(explicit: Optional[bool], task: Optional[str], *,
                      session_id: str = '',
                      sessions: Optional[Mapping[str, Any]] = None,
                      trigger_type: Optional[str] = None) -> bool:
    """The reader test for one `_build_agent_context` call.

    In order, and each step can only answer "unattended" or defer:

    1. `explicit is True` (the steward paths) is never downgraded.
    2. A task that announces itself as unattended is unattended.
    3. `trigger_type` passed by the caller. A fresh dispatch builds its context
       BEFORE its session dict is registered, so the dispatcher hands over the
       value the session is about to be created with. It outranks a dict
       already registered under a reused session id, which is the previous
       run's provenance, not this one's.
    4. The registered session's own `trigger_type` (respawns, non-claude
       dispatch): ground truth the caller's arguments cannot fake, and what a
       human joining the session rewrites to 'manual'.
    5. A named session with neither: provenance unknown, so unattended.
    6. No session named at all (a one-off build with nothing to key on): the
       text test from step 2 was the whole answer, as before.
    """
    if explicit is True or _distiller.is_unattended_task(task):
        return True
    if trigger_type is not None:
        return _distiller.is_unattended_session(task, trigger_type)
    if not session_id:
        return False
    session = (sessions or {}).get(session_id)
    if isinstance(session, Mapping):
        return for_session(task, session)
    return True
