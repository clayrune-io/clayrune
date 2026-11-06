"""MC-1148 phase 14: once-per-run, best-effort server chat anchors.

Use the agent-read card's TimestampedLines path. The run record holds a
durable claim BEFORE the append: a crash can lose an anchor, as allowed by
the design's API-list recovery contract, but cannot cause duplicate emission.
No reporter-controlled ownership or marker text is accepted here.
"""
from __future__ import annotations

import time

from mc import state
from mc.atomic_json import write_json_atomic
from mc.core import TimestampedLines, _log
from mc.test_run_store import TestRunStore, _LOCK

FLAG = 'test_run_progress_enabled'
_CLAIM = 'chat_anchor_claimed'


def post(store: TestRunStore, project_id: str, run_id: str) -> bool:
    """True only for a new append; failures never escape into execution.

    The authoritative enrollment binds project and owner. The claim is
    internal metadata, outside replay/snapshots and reporter event payloads.
    All store writers share _LOCK and preserve that metadata. The installation
    has one server, matching the store's single-server concurrency contract.
    """
    try:
        if state.CONFIG.get(FLAG) is not True:
            return False
        with _LOCK:
            record = store._read(project_id, run_id)
            owner = record['enrollment']['owner_session_id']
            if not owner:
                return False
            session = state.agent_sessions.get(owner)
            if (not isinstance(session, dict) or project_id == '_incognito'
                    or session.get('project_id') != project_id
                    or session.get('session_id', owner) != owner
                    or session.get('incognito') or session.get('_codex_unattended_sandbox')):
                return False
            if record.get(_CLAIM) is True:
                return False
            line = f'[test-run:{run_id}]'
            lines = session.setdefault('log_lines', TimestampedLines())
            # Persist first, even if an earlier line survived without a claim.
            record[_CLAIM] = True
            write_json_atomic(store._path(project_id, run_id), record, allow_nan=False)
            if line in lines:
                return False
            lines.append(line)
            session['last_output_time'] = time.time()
            return True
    except Exception as e:
        _log(f'[test-run-anchor] posting the chat anchor failed: {e}', flush=True)
        return False
