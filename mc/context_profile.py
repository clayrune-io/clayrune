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
"""
from __future__ import annotations

import fnmatch
from typing import Mapping, Optional

from mc import agent_runtime

PROFILES = ('full', 'lean')


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
