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


def test_durable_spawner_of_record_rejects_pollution_on_manual_row():
    """The exact reported production case (project 'Find Ron a Job',
    2026-09-24): Delaney's own row is an ordinary user-opened chat
    (`trigger_type='manual'`, no `dispatched_by_session_id` because it
    predates that field) whose legacy `spawned_by_session_id` was re-armed to
    Cutler purely because Cutler messaged Delaney with `notify_session=Cutler`.
    A row that never carries the durable dispatch marker must NOT have its
    legacy field trusted as a spawner -- Delaney must resolve to no spawner,
    not to Cutler.
    """
    delaney_row = {
        'session_id': '5edd10858aec', 'trigger_type': 'manual', 'source': '',
        'spawned_by_session_id': 'b35bf09c440e', 'dispatched_by_session_id': None,
    }
    assert ar._durable_spawner_of_record(delaney_row) == ''


def test_durable_spawner_of_record_trusts_legacy_dispatch_marked_row():
    """A pre-fix row that WAS actually dispatched (`trigger_type='dispatch'`,
    set once at dispatch time, never touched by send/interrupt) is safe to
    fall back to its legacy `spawned_by_session_id` when the durable field is
    missing -- this is Cutler's real row from the same incident, and it must
    still resolve to Delaney so Cutler's banner stays correct.
    """
    cutler_row = {
        'session_id': 'b35bf09c440e', 'trigger_type': 'dispatch',
        'spawned_by_session_id': '5edd10858aec', 'dispatched_by_session_id': None,
    }
    by_sid = {
        'b35bf09c440e': cutler_row,
        '5edd10858aec': {
            'session_id': '5edd10858aec', 'trigger_type': 'manual', 'source': '',
            'spawned_by_session_id': 'b35bf09c440e', 'dispatched_by_session_id': None,
        },
    }
    assert ar._durable_spawner_of_record(cutler_row, by_sid) == '5edd10858aec'


def test_durable_spawner_of_record_breaks_two_node_cycle():
    """Two dispatch-marked rows whose legacy fields point at each other is
    proof of mutual pollution (both re-armed each other's callback target at
    some point) -- neither side's legacy value is trustworthy, so both must
    resolve to no spawner rather than each claiming the other dispatched it.
    """
    row_a = {'session_id': 'A', 'trigger_type': 'dispatch',
              'spawned_by_session_id': 'B', 'dispatched_by_session_id': None}
    row_b = {'session_id': 'B', 'trigger_type': 'dispatch',
              'spawned_by_session_id': 'A', 'dispatched_by_session_id': None}
    by_sid = {'A': row_a, 'B': row_b}
    assert ar._durable_spawner_of_record(row_a, by_sid) == ''
    assert ar._durable_spawner_of_record(row_b, by_sid) == ''


def test_revive_non_claude_does_not_repollute_from_manual_row(monkeypatch):
    """Defect caught in review of 855d960: reviving a session whose row looks
    like Delaney's (manual trigger, no durable field, legacy field re-armed to
    a callback target that was never a real spawner) must not pass that
    pollution through to `_dispatch_agent_internal`'s `spawned_by=` -- doing
    so would let `_log_agent_dispatch_pending` write it back as a fresh
    `dispatched_by_session_id`, making the false banner permanent instead of
    the one-turn artifact it was before this fix.
    """
    entry = {
        'session_id': '5edd10858aec', 'trigger_type': 'manual', 'source': '',
        'provider': 'codex', 'project_generation': 1,
        'spawned_by_session_id': 'b35bf09c440e', 'dispatched_by_session_id': None,
    }
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_continuation_effort', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_requested_model_snapshot', lambda *a, **k: '')

    captured = {}
    def _fake_dispatch(*a, **k):
        captured.update(k)
        return {'session_id': entry['session_id']}
    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fake_dispatch)

    result = ar._revive_non_claude_from_agent_log('p', entry['session_id'], 'hi', {'id': 'p'})
    assert result == entry['session_id']
    assert captured.get('spawned_by', '') == ''


def test_revive_non_claude_carries_legacy_spawner_for_dispatch_marked_row(monkeypatch):
    """Counterpart: a row that WAS actually dispatched (Cutler's shape) must
    still carry its real spawner across a revive -- the fix must reject
    pollution without also breaking the legitimate legacy-fallback case.
    """
    entry = {
        'session_id': 'b35bf09c440e', 'trigger_type': 'dispatch', 'source': 'agent',
        'provider': 'codex', 'project_generation': 1,
        'spawned_by_session_id': '5edd10858aec', 'dispatched_by_session_id': None,
    }
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_continuation_effort', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_requested_model_snapshot', lambda *a, **k: '')

    captured = {}
    def _fake_dispatch(*a, **k):
        captured.update(k)
        return {'session_id': entry['session_id']}
    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fake_dispatch)

    result = ar._revive_non_claude_from_agent_log('p', entry['session_id'], 'hi', {'id': 'p'})
    assert result == entry['session_id']
    assert captured.get('spawned_by', '') == '5edd10858aec'


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
