"""OS-attributed creator -> Hivemind worker lineage (MC-1107).

The creator is a managed session, not the Hivemind id or a request-body id.
Capture it once before saving the manifest; later manual spawns, scheduler
ticks and retries inherit it without attributing the server to itself.
"""
from __future__ import annotations

from typing import Any

from mc import dispatch_lineage, state
from mc.core import now_iso


def creator_of_request(request: Any) -> str:
    """Reuse dispatch's OS attribution. Body/Origin fields are never consulted."""
    return dispatch_lineage.attributed_caller_session(
        request, state.agent_sessions, state.tracked_processes)


def worker_metadata(manifest: dict) -> dict[str, str]:
    """The same identity for the live dict and adapters that rebuild it.

    Legacy and human-created manifests have no attributed creator. Do not
    infer one from config, notification targets, or the later spawn caller.
    """
    creator = manifest.get('created_by_session_id')
    return {'_spawned_by': creator.strip() if isinstance(creator, str) else ''}


def record_worker(session: dict, project_generation: int) -> None:
    """Persist lineage before launch, including for housekeeping workers.

    The ordinary pending writer excludes housekeeping. Use its serialized
    agent-log transaction for a minimal pending row instead, without memory
    or usage turn-start side effects. Completion replaces this pending row.
    Write errors propagate: never launch an attributed worker without its
    durable lineage. No callback is armed by recording a spawner.
    """
    creator = (session.get('_spawned_by') or '').strip()
    if not creator or session.get('incognito'):
        return
    from mc.blueprints import agent_routes as ar

    row = {
        'ts': now_iso(),
        'session_id': session['session_id'],
        'project_id': session['project_id'],
        'project_generation': project_generation,
        'task': session.get('task', ''),
        'status': 'in_progress',
        'summary': '',
        'started_at': session.get('started_at', ''),
        'provider': session.get('provider', ''),
        'hivemind_id': session.get('hivemind_id', ''),
        'hivemind_ws_id': session.get('hivemind_ws_id', ''),
        'trigger_type': 'hivemind_worker',
        'trigger_id': session.get('trigger_id', ''),
        'dispatched_by_session_id': creator,
        'spawned_by_session_id': '',
    }

    def insert(rows: list[dict]) -> None:
        if not any(r.get('session_id') == row['session_id'] for r in rows):
            rows.insert(0, row)

    ar._update_agent_log(session['project_id'], insert)
