"""Dispatch-callback follow-up gap (2026-09-15): revive dropped the spawner.

Ron noticed a dispatched child (Tilda, session 77fc8166f11e) answered a
follow-up Dave sent her and landed a commit, but Dave never got a
"[dispatched agent finished]" report -- he only found out by polling.

Two separate defects fed that symptom:

  1. `_maybe_notify_spawner`'s single `_notify_sent` latch fired at most once
     EVER for a session, not once per turn -- a dispatched child's SECOND
     answer (a follow-up reply, or an interrupt) never re-notified its
     spawner. Fixed by splitting the latch into `_notify_session_sent` /
     `_notify_workflow_sent` and re-arming only the former, via
     `_rearm_notify_for_new_turn`, whenever a followup/interrupt actually
     starts a new turn.

  2. `_revive_from_agent_log` / `_revive_non_claude_from_agent_log` build a
     BRAND NEW session dict from scratch when a purged/dead session comes
     back to life. Neither one carried `_notify_session` (or
     `_notify_workflow`) across from the durable agent_log row, so a revived
     child with a spawner silently lost the callback for good -- not just
     "not yet re-armed", but the field itself was gone. Fixed by
     reconstructing both from the row's `spawned_by_session_id` and
     (for a workflow-triggered dispatch) `trigger_id` ("{run_id}:{step}").

This file covers (2) and the split-latch behaviour of (1) directly; the
followup/interrupt call sites for (1) are exercised end-to-end by
test_one_process_per_conversation.py's route-level fixtures.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CSID = 'aaaaaaaa-1111-2222-3333-444444444444'


class _Stdin:
    def write(self, s):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self):
        self.pid = 555
        self.stdin = _Stdin()
        self.stdout = iter([])

    def poll(self):
        return None


@pytest.fixture()
def ar(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint's global-scope deps
    from mc import state as mc_state
    from mc.blueprints import agent_routes as _ar

    monkeypatch.setattr(_ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(_ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(_ar, '_refuse_duplicate_spawn', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_resume_cwd_for', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_revive_history_lines', lambda *a, **k: [])
    monkeypatch.setattr(_ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_read_agent_stream', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(_ar, '_sysprompt_file_args', lambda ctx: ([], None))
    monkeypatch.setattr(_ar, '_sysprompt_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(_ar.subprocess, 'Popen', lambda *a, **k: _Proc())

    class _DeliveryStore:
        def __init__(self):
            self.turn = 0

        def project_generation(self, project_id):
            return 1

        def allocate_turn(self, session_id):
            self.turn += 1
            return self.turn

    monkeypatch.setattr(_ar, '_delivery_store', _DeliveryStore())

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield _ar
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _project(tmp_path):
    pp = tmp_path / 'proj'
    pp.mkdir(exist_ok=True)
    return {'id': 'p1', 'project_path': str(pp), 'provider': 'claude'}


class TestReviveCarriesNotifySession:

    def test_revive_carries_notify_session_from_the_log_row(self, ar, tmp_path, monkeypatch):
        entries = [{'session_id': 's1', 'claude_session_id': CSID,
                    'spawned_by_session_id': 'parent-1', 'ts': '2026-09-15T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)

        session = ar._revive_from_agent_log('p1', 's1', 'still there?', _project(tmp_path))

        assert session is not None
        assert session['_notify_session'] == 'parent-1'

    def test_revive_reconstructs_notify_workflow_from_trigger_id(self, ar, tmp_path, monkeypatch):
        entries = [{'session_id': 's2', 'claude_session_id': CSID,
                    'trigger_type': 'workflow', 'trigger_id': 'run-x:us-stock-investor',
                    'ts': '2026-09-15T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)

        session = ar._revive_from_agent_log('p2', 's2', 'go', _project(tmp_path))

        assert session is not None
        assert session['_notify_workflow'] == {'run_id': 'run-x', 'step': 'us-stock-investor'}

    def test_revive_of_an_ordinary_session_sets_no_callback(self, ar, tmp_path, monkeypatch):
        """The overwhelming majority of revives are plain chats -- must stay silent."""
        entries = [{'session_id': 's3', 'claude_session_id': CSID,
                    'ts': '2026-09-15T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)

        session = ar._revive_from_agent_log('p3', 's3', 'go', _project(tmp_path))

        assert session is not None
        assert session['_notify_session'] == ''
        assert session['_notify_workflow'] is None

    def test_non_claude_revive_passes_notify_session_to_dispatch(self, ar, monkeypatch):
        entries = [{'session_id': 's4', 'provider': 'gemini', 'claude_session_id': '',
                    'spawned_by_session_id': 'parent-4', 'ts': '2026-09-15T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)
        captured = {}
        monkeypatch.setattr(ar, '_dispatch_agent_internal',
                            lambda pid, msg, **kw: captured.update(kw) or 's4')

        out = ar._revive_non_claude_from_agent_log('p4', 's4', 'go', {'id': 'p4'})

        assert out == 's4'
        assert captured['notify_session'] == 'parent-4'


class TestReviveDoesNotRearm:
    """MC-970, measured 2026-09-23: a human typing into a purged, already-
    notified dispatch used to come back with `_notify_session` carried
    forward from the durable log row unconditionally -- the revived session
    has no `_notify_session_sent` (a brand-new dict has never latched
    anything), so its NEXT completion fires the callback again even though
    the original dispatch already reported in. `/agent/send` and
    `agent_followup`'s revive-precheck -- the only two callers a human's
    typed message can reach -- pass `carry_notify=False` for exactly this
    reason; only `_revive_parent_for_delegation` (agent-to-agent delegation
    delivery, never a human keystroke) keeps the default `True`.
    """

    def test_carry_notify_false_drops_the_callback_on_claude_revive(self, ar, tmp_path, monkeypatch):
        entries = [{'session_id': 's5', 'claude_session_id': CSID,
                    'spawned_by_session_id': 'parent-1', 'ts': '2026-09-23T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)

        session = ar._revive_from_agent_log(
            'p5', 's5', 'are you still there?', _project(tmp_path), carry_notify=False)

        assert session is not None
        assert session['_notify_session'] == ''

    def test_carry_notify_false_drops_the_workflow_callback_too(self, ar, tmp_path, monkeypatch):
        entries = [{'session_id': 's6', 'claude_session_id': CSID,
                    'trigger_type': 'workflow', 'trigger_id': 'run-y:market-scout',
                    'ts': '2026-09-23T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)

        session = ar._revive_from_agent_log(
            'p6', 's6', 'go', _project(tmp_path), carry_notify=False)

        assert session is not None
        assert session['_notify_workflow'] is None

    def test_carry_notify_true_is_still_the_default(self, ar, tmp_path, monkeypatch):
        """`_revive_parent_for_delegation` calls this with no `carry_notify`
        kwarg at all -- the default must stay True or delegation delivery to
        a dead, nested parent silently breaks."""
        entries = [{'session_id': 's7', 'claude_session_id': CSID,
                    'spawned_by_session_id': 'grandparent-1', 'ts': '2026-09-23T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)

        session = ar._revive_from_agent_log('p7', 's7', 'go', _project(tmp_path))

        assert session is not None
        assert session['_notify_session'] == 'grandparent-1'

    def test_non_claude_carry_notify_false_drops_the_callback(self, ar, monkeypatch):
        entries = [{'session_id': 's8', 'provider': 'gemini', 'claude_session_id': '',
                    'spawned_by_session_id': 'parent-8', 'ts': '2026-09-23T00:00:00Z'}]
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid: entries)
        captured = {}
        monkeypatch.setattr(ar, '_dispatch_agent_internal',
                            lambda pid, msg, **kw: captured.update(kw) or 's8')

        out = ar._revive_non_claude_from_agent_log(
            'p8', 's8', 'go', {'id': 'p8'}, carry_notify=False)

        assert out == 's8'
        assert captured['notify_session'] == ''


def test_rearm_clears_only_the_session_latch():
    """`_rearm_notify_for_new_turn` must re-arm the spawner callback for a new
    turn without ever re-arming a workflow step's -- a workflow step completes
    at most once no matter how many follow-ups land on the session after."""
    from mc.blueprints import agent_routes as ar

    session = {'_notify_session_sent': True, '_notify_workflow_sent': True}
    ar._rearm_notify_for_new_turn(session)
    assert '_notify_session_sent' not in session
    assert session['_notify_workflow_sent'] is True


def test_notify_fires_again_after_rearm_but_not_workflow(monkeypatch):
    """Reproduces the Tilda/Dave symptom directly: a dispatched child answers
    a first turn (spawner notified), the spawner sends a follow-up (rearm),
    the child answers again -- the spawner must be notified a SECOND time.
    A workflow waiter present on the same session must fire only once, ever."""
    from mc.blueprints import agent_routes as ar

    spawner_calls = []
    workflow_calls = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda *a, **k: spawner_calls.append(a))
    monkeypatch.setattr(ar, '_notify_workflow_step',
                        lambda *a, **k: workflow_calls.append(a))
    monkeypatch.setattr(ar, '_allocate_delegation_turn', lambda session: 2)

    session = {
        'project_id': 'p1', 'session_id': 'child-1',
        '_notify_session': 'parent-1',
        '_notify_workflow': {'run_id': 'run-x', 'step': 'step-1'},
    }

    ar._maybe_notify_spawner(session, 'first answer')
    assert len(spawner_calls) == 1
    assert len(workflow_calls) == 1

    # A follow-up lands; the turn-start code re-arms before the new turn runs.
    ar._rearm_notify_for_new_turn(session)
    ar._maybe_notify_spawner(session, 'second answer')

    assert len(spawner_calls) == 2, 'spawner must hear about the follow-up turn too'
    assert spawner_calls[-1][3] == 'second answer'
    assert len(workflow_calls) == 1, 'a workflow step must not re-complete on a later follow-up'


def test_advance_delegation_turn_leaves_the_latch_untouched(monkeypatch):
    """`_advance_delegation_turn` is the MC-970 counterpart to
    `_rearm_notify_for_new_turn`: same turn bookkeeping (durable turn id,
    dispatch-pending log row), but a human-typed turn must not clear
    `_notify_session_sent` the way an agent-armed one does."""
    from mc.blueprints import agent_routes as ar

    calls = []
    monkeypatch.setattr(ar, '_allocate_delegation_turn', lambda session: calls.append('turn') or 2)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda session, strict=False: calls.append('pending'))

    session = {'_notify_session_sent': True, '_notify_workflow_sent': True}
    ar._advance_delegation_turn(session)

    assert session['_notify_session_sent'] is True
    assert session['_notify_workflow_sent'] is True
    assert calls == ['turn', 'pending'], 'must still do the same turn bookkeeping as the rearm path'


def test_human_followup_after_completion_does_not_refire_the_callback(monkeypatch):
    """MC-970's exact reported symptom, at the notify layer: a dispatched
    child completes and notifies its spawner once, then a HUMAN continues the
    same chat. That must advance the turn (so the conversation keeps working)
    without re-arming the latch -- so when this new turn also completes,
    `_maybe_notify_spawner` stays a no-op instead of reporting a second time
    for a task the spawner already knows is done."""
    from mc.blueprints import agent_routes as ar

    spawner_calls = []
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a, **k: spawner_calls.append(a))
    monkeypatch.setattr(ar, '_allocate_delegation_turn', lambda session: 2)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda session, strict=False: None)

    session = {
        'project_id': 'p1', 'session_id': 'child-1',
        '_notify_session': 'parent-1',
    }

    ar._maybe_notify_spawner(session, 'first answer, merged the PR')
    assert len(spawner_calls) == 1

    # Ron types a follow-up question into the now-finished child chat.
    ar._advance_delegation_turn(session)
    ar._maybe_notify_spawner(session, 'sure, here is more detail')

    assert len(spawner_calls) == 1, 'a human-typed follow-up must not refire the dispatch callback'
