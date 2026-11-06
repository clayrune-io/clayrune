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

Every advance this module makes goes through the one gate below (Fenn r5; Dave's
call, journal ebbb15a7): `advance_idle_run(idle_for_s=IDLE_RUN_GRACE_S)`, including
the advance after an owed replay. Acknowledging an old completed step does not mean
this retry owns the run -- the current step's completion callback may have recorded
and not yet advanced -- so a freshly written run is left alone and the idle scan
picks it up once stale. Recovering a step whose record failed to clear therefore
takes up to the grace plus one tick (~90s), not one tick.

Idle-run scan (Fenn r4 Repro B; Dave's call, journal ebbb15a7): a `running` run
with nothing in flight and a pending frontier also gets `advance_idle_run`, but
only once `run['updated']` is older than IDLE_RUN_GRACE_S. `workflows._write_run`
re-stamps `updated` on every run write, and `on_agent_step_complete`,
`resolve_decision` and `resume_due_waits` record a step, release the run lock and
THEN advance -- a tick inside that window would dispatch the successor first and
the caller's own advance would dispatch a second. The window is milliseconds; the
grace is a minute. An unparseable, missing, naive or future `updated` counts as
fresh (skipped). The scan's own read is lock-free, so it is only a pre-filter:
`advance_idle_run(idle_for_s=...)` re-checks `updated` on the run as read under the
run lock (a step completing after the scan re-stamps it). Those advances add to `advanced`. A run is excluded from the scan
only for the tick on which its replay succeeded (`replayed_runs`); a record that stays
owed does not exclude its run from later scans.

Bounds: once per session per call (the tick is 30s); not before boot's workflow
adoption has finished; not while another replay holds the lock; never for a
session that is live in this process with a stamp (its own thread acks it).
Not gated on `agent_survive_restart_enabled` (spec 6.3.1: obligations are
honored while OFF). Never raises.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from mc import agent_shim_completion as _comp
from mc import state
from mc import workflows as _wf


# A running, idle run is touched by the tick only after this long without a write.
IDLE_RUN_GRACE_S = 60


def _log(msg: str) -> None:
    _comp._log(msg)


def _is_stale(updated: Any, now: datetime) -> bool:
    """Cheap lock-free pre-filter only; `advance_idle_run(idle_for_s=...)` re-decides
    under the run lock, which is the check that counts."""
    return _wf.updated_older_than(updated, IDLE_RUN_GRACE_S, now)


def retry_owed_workflow_steps(root: Optional[Any] = None, *,
                              runners: Optional[Mapping[str, _comp.Runner]] = None) -> dict:
    """Returns {'retried': n, 'replayed': n, 'advanced': n}."""
    counts = {'retried': 0, 'replayed': 0, 'advanced': 0}
    try:
        if not _wf.boot_adoption_done():
            return counts
        owed = {}
        replayed_runs = set()
        for run_id, _, sid, _st in _wf.running_run_session_steps():
            live = state.agent_sessions.get(sid)
            if live is not None and _comp.pending_of(live) is not None:
                continue                         # its own thread is mid-completion
            if _comp.is_owed(sid, root):
                owed[sid] = run_id
        if owed:
            counts['retried'] = len(owed)
            rep = _comp.replay_pending(root, runners=runners, only=set(owed), wait=False)
            for sid, why in rep.retained.items():
                _log(f'[agent-shim-completion] retry of {sid[:12]} (run {owed.get(sid, "")[:12]}) '
                     f'failed, record kept: {why}')
            for sid in rep.replayed:
                counts['replayed'] += 1
                # Same under-lock 60s gate as the idle scan below (Fenn r5): acking an OLD
                # completed step does not mean this retry owns the run's frontier; the
                # current step's completion callback may be mid-follow-up. A run skipped
                # here is picked up by the idle scan on a later tick once it is stale.
                replayed_runs.add(owed[sid])
                if _wf.advance_idle_run(owed[sid], idle_for_s=IDLE_RUN_GRACE_S):
                    counts['advanced'] += 1
        now = datetime.now(timezone.utc)
        for run_id, updated in _wf.running_run_updated():
            if run_id in replayed_runs or not _is_stale(updated, now):
                continue
            if _wf.advance_idle_run(run_id, idle_for_s=IDLE_RUN_GRACE_S):
                _log(f'[agent-shim-completion] advanced idle run {run_id[:12]} '
                     f'(no write for over {IDLE_RUN_GRACE_S}s)')
                counts['advanced'] += 1
    except Exception as e:
        _log(f'[agent-shim-completion] retry tick failed: {type(e).__name__}: {e}')
    return counts
