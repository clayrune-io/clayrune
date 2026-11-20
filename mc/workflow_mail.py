"""Select the completed workflow agent whose chat should receive mail replies."""
import os
import tempfile
from contextlib import contextmanager


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


@contextmanager
def body_file(body: str):
    """Yield the path of a temp file holding `body`; the file is removed on exit.

    The mailer takes the body as `--body-file`, never as an argument: a step
    output over about 32,000 characters fails process creation on Windows
    (WinError 206), and a body that starts with `-` is refused by argparse.
    """
    fd, path = tempfile.mkstemp(prefix='clayrune-wf-mail-', suffix='.txt')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(body)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
