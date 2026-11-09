"""Record failed scheduled deliveries without replaying the addressed task."""
from mc.agent_runtime import PromptDeliveryError
from mc.core import _log, now_iso
from mc.delivery_attempt import DeliveryAttempt


def is_delivery_error(error: Exception) -> bool:
    # Revival defines its error in the route module; resolve after server wiring.
    from mc.blueprints.agent_routes import ReviveDeliveryError
    return isinstance(error, (PromptDeliveryError, ReviveDeliveryError))


def record_failure(project_id: str, schedule_id: str, session_id: str,
                   task: str, error: Exception) -> None:
    """Use the existing serialized log transaction, preserving conversation data.

    Continued fires share a row. Only the failed attempt may mark it failed;
    its replacement keeps both its live owner and its durable pending state.
    """
    from mc.blueprints.agent_routes import _update_agent_log, agent_sessions, get_manager

    attempt = getattr(error, 'delivery_attempt', None)
    if not isinstance(attempt, DeliveryAttempt):
        _log(f'[scheduler] skipped delivery failure for {session_id}: '
             'attempt identity unavailable', flush=True)
        return

    def failed(rows):
        row = next((r for r in rows if r.get('session_id') == session_id
                    and r.get('trigger_type') == 'schedule'
                    and r.get('trigger_id') == schedule_id), None)
        if row is not None and int(row.get('delegation_turn', 1)) > attempt.turn:
            _log(f'[scheduler] skipped superseded delivery failure for {session_id}: '
                 f'row turn exceeds failed turn {attempt.turn}', flush=True)
            return
        if row is None:
            row = {'session_id': session_id, 'trigger_type': 'schedule',
                   'trigger_id': schedule_id}
            rows.insert(0, row)
        row.update(status='error', summary=f'Scheduled delivery failed: {error}',
                   ts=now_iso(), task=task)

    try:
        # Existing lock order: manager -> log leaf. No acknowledgment or process
        # wait occurs here. A successor cannot publish between the check/write.
        with get_manager(project_id).lock:
            if not attempt.owns(agent_sessions.get(session_id)):
                _log(f'[scheduler] skipped superseded delivery failure for {session_id}: '
                     f'owner changed since turn {attempt.turn}', flush=True)
                return
            _update_agent_log(project_id, failed)
    except Exception as exc:
        _log(f'[scheduler] could not save delivery failure for {session_id}: {exc}',
             flush=True)
