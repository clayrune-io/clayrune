"""Who really dispatched a session (MC-1082, follow-up to MC-1077).

`mc/review_lineage.py` refuses a merge reviewer that descends from the branch
owner by dispatch. It reads the child's recorded spawner (`_spawned_by` live,
`dispatched_by_session_id` durable). That value used to be whatever the
dispatching caller typed as `notify_session`, so a builder that dispatched its
own reviewer without naming itself, or naming some other session, left no
lineage and the reviewer passed as independent.

The recorded spawner is now the OS-attributed caller (`mc.caller_attribution`:
loopback peer port -> owning PID -> parent chain -> managed session), which no
body field can change. `notify_session` stays what it was, the callback target.

Callers the OS cannot tie to a managed session (the UI, a human's own curl, a
scheduler or workflow dispatch) record no caller, and the route falls back to
the previous behaviour for them.

Hivemind workers (`hm_*`) are spawned by the orchestrator, not by a builder,
so they cannot choose a reviewer for a branch; they carry no lineage link.
"""
from __future__ import annotations

from mc import caller_attribution
from mc.core import _log


def attributed_caller_session(request, agent_sessions, tracked_processes) -> str:
    """The managed session the dispatching request descends from, or '' when
    the caller is not attributed to one (human, UI, unattributed, lookup
    unavailable). Never raises."""
    try:
        env = getattr(request, 'environ', None) or {}
        att = caller_attribution.attribute_caller(
            getattr(request, 'remote_addr', '') or '',
            env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
            caller_attribution.managed_roots(agent_sessions, tracked_processes))
    except Exception as e:
        _log(f'[dispatch-lineage] caller lookup failed: {e!r}')
        return ''
    if att.status == caller_attribution.ATTRIBUTED:
        return (att.session_id or '').strip()
    return ''


def spawner_of_record(attributed_caller: str, notify_session: str) -> str | None:
    """The `spawned_by` to hand `_dispatch_agent_internal`.

    An attributed caller always wins over `notify_session`: the body can name
    any session, the process tree cannot. With no attributed caller, None, so
    the dispatcher falls back to `notify_session` exactly as before."""
    caller = (attributed_caller or '').strip()
    if not caller:
        return None
    named = (notify_session or '').strip()
    if named and named != caller:
        _log(f'[dispatch-lineage] notify_session {named[:12]!r} differs from the '
             f'attributed caller {caller[:12]!r}; lineage records the caller')
    return caller
