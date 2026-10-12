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

The same env also carries CLAUDE_CODE_RETRY_WATCHDOG=1 for an unattended
trigger_type, so a 429 makes the CLI wait out the usage limit (a
`system/api_retry` event every ~30 s) instead of ending the run in ~1 s; see
`launch_env`. One env per spawn site -- never a second `env=`.

`UNATTENDED_TRIGGER_TYPES` mirrors `steward.fence._UNATTENDED_TRIGGER_TYPES`;
tests/test_launch_marker.py pins the two together.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from mc.provider_env_scope import scope_claude_env

# Read by steward/fence.py `_should_arm_for_unattended_trigger` (the hook runs in
# the Claude child's environment, so this is the one channel that needs no server).
LAUNCH_MARKER_ENV = 'CLAYRUNE_LAUNCHED_UNATTENDED'

# Claude Code: on a 429/529 keep the process alive and retry (up to 300x, no
# extra requests) instead of exiting. Wanted for unattended runs (nobody is
# there to re-send); not for an attended chat, which wants the error now.
RETRY_WATCHDOG_ENV = 'CLAUDE_CODE_RETRY_WATCHDOG'

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

    Also drops ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN that were saved under a
    non-claude provider (mc/provider_env_scope.py), so a Goose/Aider/Qwen key
    cannot move a Claude run off the subscription plan.
    """
    env: Dict[str, Any] = scope_claude_env(dict(os.environ if base is None else base))
    unattended = (trigger_type or '') in UNATTENDED_TRIGGER_TYPES
    if unattended and _fence_enabled():
        env[LAUNCH_MARKER_ENV] = '1'
    else:
        env.pop(LAUNCH_MARKER_ENV, None)
    if unattended and not (env.get(RETRY_WATCHDOG_ENV) or '').strip():
        env[RETRY_WATCHDOG_ENV] = '1'
    return env


def launch_cmd(cmd: List[str], trigger_type: Optional[str],
               cwd: Optional[str] = None) -> List[str]:
    """`cmd` as Popen should receive it. Called at every Claude spawn site next
    to `launch_env`, with the same `trigger_type`.

    Attended (or fence switched off): `cmd` itself, same object. Unattended:
    the fence self-test runs first and a failure raises, refusing the launch;
    on a CLI that supports `onFailure` the `--settings` file is swapped for one
    whose fence entry blocks when the hook cannot run. `cwd` is the child's
    working directory (pass the Popen `cwd=`). See mc/fence_fail_closed.py."""
    if (trigger_type or '') not in UNATTENDED_TRIGGER_TYPES or not _fence_enabled():
        return cmd
    from mc import fence_fail_closed
    return fence_fail_closed.launch_cmd(cmd, trigger_type, cwd)
