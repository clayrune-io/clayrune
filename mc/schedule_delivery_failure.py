"""Record failed scheduled deliveries without replaying the addressed task."""
from mc.agent_runtime import PromptDeliveryError
from mc.core import _log, now_iso


def is_delivery_error(error: Exception) -> bool:
    # Revival defines its error in the route module; resolve after server wiring.
    from mc.blueprints.agent_routes import ReviveDeliveryError
    return isinstance(error, (PromptDeliveryError, ReviveDeliveryError))


def record_failure(project_id: str, schedule_id: str, session_id: str,
                   task: str, error: Exception) -> None:
    """Use the existing serialized log transaction, preserving conversation data.

    Continued schedule fires share a run row. Mark that row failed, or create
    a failure row when revival failed before any pending row was published.
    """
    from mc.blueprints.agent_routes import _update_agent_log

    def failed(rows):
        row = next((r for r in rows if r.get('session_id') == session_id
                    and r.get('trigger_type') == 'schedule'
                    and r.get('trigger_id') == schedule_id), None)
        if row is None:
            row = {'session_id': session_id, 'trigger_type': 'schedule',
                   'trigger_id': schedule_id}
            rows.insert(0, row)
        row.update(status='error', summary=f'Scheduled delivery failed: {error}',
                   ts=now_iso(), task=task)

    try:
        _update_agent_log(project_id, failed)
    except Exception as exc:
        _log(f'[scheduler] could not save delivery failure for {session_id}: {exc}',
             flush=True)
