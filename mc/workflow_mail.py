"""Select the completed workflow agent whose chat should receive mail replies."""


def mail_context_args(run: dict) -> list[str]:
    """Return mailer flags for the latest completed agent, or no flags.

    start_run stores steps in topological order and the runner executes them
    serially in that order. Only dispatched agent steps carry both IDs; pending,
    skipped and failed steps must never claim replies to a workflow email.
    """
    for step in reversed(list((run.get('steps') or {}).values())):
        if (step.get('status') == 'completed'
                and step.get('project_id') and step.get('session_id')):
            return ['--project', step['project_id'], '--session', step['session_id']]
    return []
