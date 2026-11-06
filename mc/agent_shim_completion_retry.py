"""MC-1150 P4: retry an owed completion from the scheduler tick.

Boot replay (mc/agent_shim_completion.py) runs once. When it fails -- a transient
`_write_run` error is enough -- the record stays on disk and every reconciler now
leaves that workflow step alone (`workflows._completion_owed`), so without this
the run would sit `running` until the next boot. Each tick this module runs the
SAME replay again, for the owed sessions of running workflow runs -- on ANY step
of the run, not just the one still `running`: a replay that recorded its step and
then failed to clear the record leaves a `completed` step with the record still
owed (Fenn, MC-1150 P4 fourth review). Same ack rules (the record is cleared only
when `replay_pending` clears it; a step already recorded is acknowledged without
being recorded again), then the run is advanced the way boot's workflow adoption
would. `advanced` counts only advances that moved the run.

Bounds: once per session per call (the tick is 30s); not before boot's workflow
adoption has finished; not while another replay holds the lock; never for a
session that is live in this process with a stamp (its own thread acks it).
Not gated on `agent_survive_restart_enabled` (spec 6.3.1: obligations are
honored while OFF). Never raises.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from mc import agent_shim_completion as _comp
from mc import state
from mc import workflows as _wf


def _log(msg: str) -> None:
    _comp._log(msg)


def retry_owed_workflow_steps(root: Optional[Any] = None, *,
                              runners: Optional[Mapping[str, _comp.Runner]] = None) -> dict:
    """Returns {'retried': n, 'replayed': n, 'advanced': n}."""
    counts = {'retried': 0, 'replayed': 0, 'advanced': 0}
    try:
        if not _wf.boot_adoption_done():
            return counts
        owed = {}
        for run_id, _, sid, _st in _wf.running_run_session_steps():
            live = state.agent_sessions.get(sid)
            if live is not None and _comp.pending_of(live) is not None:
                continue                         # its own thread is mid-completion
            if _comp.is_owed(sid, root):
                owed[sid] = run_id
        if not owed:
            return counts
        counts['retried'] = len(owed)
        rep = _comp.replay_pending(root, runners=runners, only=set(owed), wait=False)
        for sid, why in rep.retained.items():
            _log(f'[agent-shim-completion] retry of {sid[:12]} (run {owed.get(sid, "")[:12]}) '
                 f'failed, record kept: {why}')
        for sid in rep.replayed:
            counts['replayed'] += 1
            if _wf.advance_idle_run(owed[sid]):
                counts['advanced'] += 1
    except Exception as e:
        _log(f'[agent-shim-completion] retry tick failed: {type(e).__name__}: {e}')
    return counts
