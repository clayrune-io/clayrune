"""MC-1148 phase 14: once-per-run, best-effort server chat anchors.

Use the agent-read card's TimestampedLines path. The run record holds a
durable claim BEFORE the append: a crash can lose an anchor, as allowed by
the design's API-list recovery contract, but cannot cause duplicate emission.
No reporter-controlled ownership or marker text is accepted here.
"""
from __future__ import annotations

import time

from mc import state
from mc.core import TimestampedLines, _log
from mc.test_run_store import TestRunStore

FLAG = 'test_run_progress_enabled'
_CLAIM = 'chat_anchor_claimed'


def post(store: TestRunStore, project_id: str, run_id: str) -> bool:
    """True only for a new append; failures never escape into execution.

    The authoritative enrollment binds project and owner. The claim is
    internal metadata, outside replay/snapshots and reporter event payloads.
    The claim and the append run under this run's lock (store.locked), and every
    store writer preserves the metadata. The installation has one server,
    matching the store's single-server concurrency contract.
    """
    try:
        if state.CONFIG.get(FLAG) is not True:
            return False
        with store.locked(project_id, run_id) as run:
            owner = run.enrollment['owner_session_id']
            if not owner:
                return False
            session = state.agent_sessions.get(owner)
            if (not isinstance(session, dict) or project_id == '_incognito'
                    or session.get('project_id') != project_id
                    or session.get('session_id', owner) != owner
                    or session.get('incognito') or session.get('_codex_unattended_sandbox')):
                return False
            if run.claimed(_CLAIM):
                return False
            line = f'[test-run:{run_id}]'
            lines = session.setdefault('log_lines', TimestampedLines())
            # Persist first, even if an earlier line survived without a claim.
            run.claim(_CLAIM)
            if line in lines:
                return False
            lines.append(line)
            session['last_output_time'] = time.time()
            return True
    except Exception as e:
        _log(f'[test-run-anchor] posting the chat anchor failed: {e}', flush=True)
        return False
