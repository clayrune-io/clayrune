"""Per-model context-profile resolution (backlog 4a11b6a5).

`_build_agent_context` (mc/blueprints/agent_routes.py) used to slim itself by
VENDOR alone (`_is_claude`): every Claude model, including Haiku, got the full
~47KB floor (MEMORY.md session log, recent conversations, recent activity),
and every non-Claude model got the lean one regardless of size or capability.
Neither axis is what actually predicts the failure the slim path exists to
avoid -- a weaker model reading prompt-history-shaped sections as a live task
list and going off doing phantom work on a plain "Hi" -- that is a property
of the MODEL, not the vendor.

Mirrors `AgentRuntime.image_input_for(model)`: each runtime declares which of
its own models are 'lean' via `CONTEXT_PROFILE_PATTERNS`/
`CONTEXT_PROFILE_DEFAULT`, and this module adds ONE thing runtimes don't own
-- a user-facing escape hatch (config.json `context_profile_overrides`) to
reclassify any model without a code change.

`resolve_job_shape()` below is the SECOND, orthogonal axis (backlog 8ead5755):
MODEL tier says how capable the engine reading the prompt is; JOB SHAPE says
what kind of session this is -- a human's own chat ('conversation') versus a
one-off session another agent dispatched with a brief that reports home
('task'). A dispatched task-shape session needs the rules, its brief, and
task-scoped memory; it does not need a human chat's history (recent
conversations, continuity, sibling activity, roster, the delegation-cost
card) -- none of that describes ITS job, only a project's ongoing
conversational state. `_build_agent_context` reads the resolved shape the
same way it reads the resolved model profile: never a raw `source` compare
inline, always through this one function.
"""
from __future__ import annotations

import fnmatch
from typing import Mapping, Optional

from mc import agent_runtime

PROFILES = ('full', 'lean')
JOB_SHAPES = ('conversation', 'task')


def resolve(provider: str, model: str,
           overrides: Optional[Mapping[str, str]] = None) -> str:
    """'full' or 'lean' for this (provider, model) pair. Never raises.

    `overrides` (config.json `context_profile_overrides`) keys may be an
    exact model id or an fnmatch glob (e.g. 'claude-haiku-*'), checked in
    iteration order -- first match wins, before the runtime's own declared
    default. An unknown provider or a bad override value never blocks
    dispatch: it just falls through to 'full', the conservative default that
    matches today's Claude (non-Haiku) behavior.
    """
    m = (model or '').strip().lower()
    if overrides:
        for pattern, profile in overrides.items():
            prof = str(profile or '').strip().lower()
            if prof not in PROFILES:
                continue
            if fnmatch.fnmatch(m, str(pattern or '').strip().lower()):
                return prof
    try:
        runtime = agent_runtime.get_runtime((provider or '').strip().lower())
    except KeyError:
        return 'full'
    try:
        profile = runtime.context_profile_for(model)
    except Exception:
        return 'full'
    return profile if profile in PROFILES else 'full'


def resolve_job_shape(source: str = '', prior_shape: str = '') -> str:
    """'conversation' or 'task' for a session. Never raises.

    Resolved ONCE, at dispatch/session start -- `prior_shape`, when it already
    names a known shape, wins outright and short-circuits everything else.
    That's what makes a resume/revive/respawn reproduce the SAME shape a
    session already had (read back from the session dict / its agent_log row)
    instead of recomputing from `source` on every turn -- a session's shape
    must not drift mid-conversation just because a later turn's caller looks
    different from the one that dispatched it.

    A fresh resolution (no prior_shape yet) keys off `source`, the one signal
    that already distinguishes a programmatic dispatch from a human's own
    chat at `/agent/dispatch` (see that route's own comment: `source='agent'`
    for a caller with no browser Origin, i.e. another agent; '' or 'ui' for
    the app). 'agent' -> 'task'; everything else (including '' -- a human
    chat, a schedule fire, a workflow step: none of those are one-off
    dispatched-with-a-brief sessions) -> 'conversation', the conservative
    default that matches today's full floor.
    """
    prior = (prior_shape or '').strip().lower()
    if prior in JOB_SHAPES:
        return prior
    return 'task' if (source or '').strip().lower() == 'agent' else 'conversation'
