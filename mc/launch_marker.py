"""Per-launch "this Claude child is unattended" marker (backlog dc480ad3, MC-1040).

steward/fence.py arms an unattended session by asking the server for the
session's trigger_type (GET /api/session/trigger-type). When that lookup
returns nothing (server down or restarting, or the session unknown to it) the
hook used to exit 0 and the child ran UNFENCED. This module closes that window
without a lookup: every Claude spawn site passes `env=launch_env(<the
session's trigger_type>)`, and the hook falls back to the marker only when the
lookup came back empty. When the lookup succeeds the lookup still wins, so a
human "I am here" re-stamp to 'manual' keeps working.

One helper, called at each spawn site. Respawn / follow-up / revive sites must
pass the session's STORED trigger_type (the one the lookup would return), never
recompute it from the caller's hints.

`UNATTENDED_TRIGGER_TYPES` mirrors `steward.fence._UNATTENDED_TRIGGER_TYPES`;
tests/test_launch_marker.py pins the two together.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Read by steward/fence.py `_should_arm_for_unattended_trigger` (the hook runs in
# the Claude child's environment, so this is the one channel that needs no server).
LAUNCH_MARKER_ENV = 'CLAYRUNE_LAUNCHED_UNATTENDED'

UNATTENDED_TRIGGER_TYPES = frozenset({
    'schedule', 'workflow', 'dispatch', 'hivemind_orchestrator', 'hivemind_worker',
})


def _fence_enabled() -> bool:
    # Same switch GET /api/session/trigger-type reports to the hook.
    try:
        from mc import state
        return bool(state.CONFIG.get('fence_unattended_enabled', True))
    except Exception:
        return True


def launch_env(trigger_type: Optional[str],
               base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for a Claude child: a copy of `base` (default: the server's
    environ) with the marker set when `trigger_type` arms the fence, and
    REMOVED otherwise.

    The removal matters: a server started from an agent's shell inherits the
    marker, and every attended chat it spawned would then carry it. Attended
    sessions must stay unfenced.
    """
    env: Dict[str, Any] = dict(os.environ if base is None else base)
    if (trigger_type or '') in UNATTENDED_TRIGGER_TYPES and _fence_enabled():
        env[LAUNCH_MARKER_ENV] = '1'
    else:
        env.pop(LAUNCH_MARKER_ENV, None)
    return env
