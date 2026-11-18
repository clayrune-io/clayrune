"""A worker's escalation reaches the person by web push (MC-1179 OS26 e).

An escalation used to be one line in the bus file and one event to dashboards
open at that moment; hivemind sessions are unattended, so nobody was told. This
raises the same web push the question channel and the PushNotification tool
use, honouring the project's `notify_push_enabled`. It never raises: a push
that cannot be sent must not fail the worker's escalate call.
"""
from __future__ import annotations

from typing import Any

from mc.core import _log


def notify(api: Any, hivemind_id: str, manifest: dict, msg: dict) -> bool:
    """Queue one push for an escalation `msg`. True when one was queued."""
    try:
        from mc.blueprints import push_mobile
        if push_mobile.PUSH_VAPID_PATH is None:
            return False
        pid = manifest.get('project_id') or ''
        project = api.load_project(pid) or {}
        if not project.get('notify_push_enabled', True):
            return False
        who = msg.get('workstream_id') or msg.get('from') or 'a worker'
        title = ('Hivemind needs you: ' + (manifest.get('title') or hivemind_id))[:80]
        body = f"{who}: {msg.get('content') or '(no message)'}"[:240]
        push_mobile._queue_push(title, body, url=f'/?project={pid}', project_id=pid, kind='agent')
        return True
    except Exception as exc:
        _log(f'[hivemind] escalation push not raised for {hivemind_id}: {exc}', flush=True)
        return False
