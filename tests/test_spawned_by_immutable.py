"""Bugfix (Ron 2026-09-24): a session's spawner-of-record must survive a
callback re-arm.

Screenshots data/uploads/agent_a76f8460e4.png / agent_3275763548.png: Delaney
dispatched Cutler (correctly shown "Dispatched by Delaney" in Cutler's chat).
Cutler then messaged Delaney back with `notify_session=<Cutler>` so Cutler
itself gets woken on Delaney's next reply (MC-970's re-arm) — and Delaney's
OWN chat started showing "Dispatched by Cutler", a false recursive tag.

Root cause: the display read `_notify_session` / the durable row's
`spawned_by_session_id`, both of which `/agent/send` and the interrupt route
legitimately overwrite to whatever the CALLER passes as the next completion
callback target (MC-970) — that field answers "who gets notified next", not
"who dispatched this session, ever". Conflating the two meant any inbound
message carrying `notify_session` silently rewrote the target's spawner
display.

Fix: an immutable `_spawned_by` (live) / `dispatched_by_session_id` (durable
row) field, set once at dispatch or revive, never written by send/interrupt.
`_notify_session` / `spawned_by_session_id` remain exactly what MC-970 needs:
a re-armable callback target.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc.state as state  # noqa: E402

state.CONFIG.setdefault('port', 5199)

from mc.blueprints import agent_routes as ar  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_scribe(monkeypatch):
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a, **k: None)


def test_callback_rearm_does_not_touch_spawned_by(monkeypatch, tmp_path):
    """The exact reported scenario: A dispatches B (B._spawned_by = 'A').
    B then messages A with notify_session=B — the MC-970 re-arm sets
    A['_notify_session'] = 'B' for the next callback, but must NOT touch
    A['_spawned_by']. A was never dispatched by anyone; it must stay
    spawner-less.
    """
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)

    session_a = {
        'project_id': 'p', 'session_id': 'A', 'status': 'running',
        'task': 'do the job', 'log_lines': [],
    }
    # B was dispatched by A: immutable spawner set once, at dispatch.
    session_b = {
        'project_id': 'p', 'session_id': 'B', 'status': 'running',
        '_notify_session': 'A', '_spawned_by': 'A',
        'task': 'subtask', 'log_lines': [],
    }

    # B messages A with notify_session=B — this is exactly what MC-970's
    # re-arm does to the TARGET session (see the `_explicit_notify_session`
    # assignments in /agent/send and the interrupt route): only
    # `_notify_session` is overwritten.
    session_a['_notify_session'] = 'B'

    assert session_a.get('_spawned_by') in (None, '')
    assert session_b['_spawned_by'] == 'A'

    # Completion of each writes the durable row. A's row must show no
    # spawner; B's must still show A.
    session_a['status'] = 'completed'
    ar._log_agent_completion(dict(session_a))
    session_b['status'] = 'completed'
    ar._log_agent_completion(dict(session_b))

    log = {row['session_id']: row for row in ar._load_agent_log('p')}
    assert log['A']['dispatched_by_session_id'] == ''
    assert log['B']['dispatched_by_session_id'] == 'A'
    # The re-armable callback field still did its job independently.
    assert log['A']['spawned_by_session_id'] == 'B'


def test_row_spawned_by_reads_the_immutable_field_not_notify_session():
    """`_row_spawned_by` (feeds the rail + banner) must key off
    `dispatched_by_session_id` / `_spawned_by`, never the re-armable
    `spawned_by_session_id` / `_notify_session`."""
    # Durable row: has a stale re-armed callback target but no real spawner.
    log_entry = {'spawned_by_session_id': 'callback-target', 'dispatched_by_session_id': ''}
    assert ar._row_spawned_by(log_entry, live=None) == ''

    # Live session: same split.
    live = {'_notify_session': 'callback-target', '_spawned_by': 'real-parent'}
    assert ar._row_spawned_by({}, live) == 'real-parent'


def test_agent_status_spawned_by_reads_spawned_by_not_notify_session(monkeypatch, tmp_path):
    """`/agent/status`'s `spawned_by_session_id` field (feeds the chat header
    banner via conversation.js `spawnedBySessionId`) must come from the
    immutable `_spawned_by`, not the re-armable `_notify_session`."""
    import server  # noqa: F401
    from mc import state as mc_state

    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)} if pid == 'p1' else None))

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    mc_state.agent_sessions['A'] = {
        'project_id': 'p1', 'session_id': 'A', 'status': 'running',
        'task': 't', 'log_lines': [], 'started_at': 0,
        # A was never dispatched, but its callback target got re-armed to B.
        '_notify_session': 'B',
    }
    server.app.config['TESTING'] = True
    try:
        client = server.app.test_client()
        resp = client.get('/api/project/p1/agent/status')
        body = resp.get_json()
        row = next(r for r in body.get('sessions', body if isinstance(body, list) else [])
                   if r.get('session_id') == 'A')
        assert row['spawned_by_session_id'] == ''
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)
