"""Backlog 95592335: callback ping-pong between two mutual spawners.

Dave dispatched Sol_Tobin (Sol.spawned_by = Dave) and Sol_Tobin's follow-up
named Dave as its `notify_session` (Dave.spawned_by = Sol). Each finished turn
delivered a '[dispatched agent finished]' callback to the other. Delivering it
to a parent that is not in memory (every Codex turn, and any idled-out Claude
session) goes through `_revive_parent_for_delegation`, which rebuilt the
parent with `_notify_session` carried from its log row -- the sender itself --
so the callback-started turn ended with a fresh latch and fired a callback
straight back. Observed 2026-11-01/02, 3+ rounds of real Claude + Codex turns.
"""
import threading
from types import SimpleNamespace

import pytest

from mc.delegation_delivery import DeliveryStore


def test_echo_helper_matches_only_the_exact_sender():
    from mc.blueprints import agent_routes as ar
    assert ar._callback_echoes_to_sender('sol', 'sol') is True
    assert ar._callback_echoes_to_sender(' sol ', 'sol') is True
    assert ar._callback_echoes_to_sender('dave', 'sol') is False
    assert ar._callback_echoes_to_sender('', 'sol') is False
    assert ar._callback_echoes_to_sender('sol', '') is False
    assert ar._callback_echoes_to_sender(None, None) is False


def _claude_revive_rig(monkeypatch, tmp_path):
    import server  # noqa: F401 -- wires the blueprint's global-scope deps
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar, 'agent_sessions', {})
    entry = {'session_id': 'dave', 'status': 'completed', 'provider': 'claude',
             'claude_session_id': 'claude-native-9', 'agent_model': 'claude-model',
             'requested_effort': 'high', 'incognito': False, 'source': 'agent',
             'spawned_by_session_id': 'sol', 'dispatched_by_session_id': 'sol',
             'trigger_type': 'schedule', 'ts': '1'}
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delivery.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resume_cwd_for', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_revive_history_lines', lambda *a, **k: ([], []))
    monkeypatch.setattr(ar, '_refuse_duplicate_spawn', lambda *a, **k: False)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake-claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda ctx: ([], None))
    monkeypatch.setattr(ar, '_sysprompt_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_memory_turn', SimpleNamespace(seed_delivered=lambda *a: None))

    class Proc:
        pid = 999
        stdout = iter([])
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda cmd, **kw: Proc())
    monkeypatch.setattr(ar, 'get_manager', lambda pid: SimpleNamespace(
        lock=threading.RLock(), session_ids=set(), ensure_guardian=lambda: None))
    return ar, {'id': 'p', 'project_path': str(tmp_path)}


def test_claude_parent_revived_for_its_own_spawners_callback_does_not_echo(monkeypatch, tmp_path):
    ar, project = _claude_revive_rig(monkeypatch, tmp_path)
    ar._revive_parent_for_delegation('p', 'dave', 'callback from sol', project,
                                     from_session_id='sol')
    session = ar.agent_sessions['dave']
    # No spawner callback is armed: the turn ends without messaging Sol, and
    # the immutable spawner-of-record is untouched.
    assert not session['_notify_session']
    assert session['_spawned_by'] == 'sol'


def test_claude_parent_revived_for_another_childs_callback_still_reports_up(monkeypatch, tmp_path):
    """MC-946 semantics: Dave's own spawner still hears Dave's answer to a
    callback from a DIFFERENT child (A -> B -> C, C reports to B, B to A)."""
    ar, project = _claude_revive_rig(monkeypatch, tmp_path)
    ar._revive_parent_for_delegation('p', 'dave', 'callback from c', project,
                                     from_session_id='some-other-child')
    assert ar.agent_sessions['dave']['_notify_session'] == 'sol'


def _codex_revive_rig(monkeypatch):
    from mc.blueprints import agent_routes as ar
    entry = {'session_id': 'sol', 'status': 'completed', 'provider': 'codex',
             'provider_session_id': 'native-sol-1', 'agent_model': 'gpt-6.1-sol',
             'requested_effort': 'high', 'incognito': False, 'source': 'agent',
             'spawned_by_session_id': 'dave', 'trigger_type': 'dispatch', 'ts': '1'}
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **k: calls.append(k))
    return ar, calls


def test_codex_parent_revived_for_its_own_spawners_callback_does_not_echo(monkeypatch):
    ar, calls = _codex_revive_rig(monkeypatch)
    ar._revive_parent_for_delegation('p', 'sol', 'callback from dave', {},
                                     from_session_id='dave')
    assert len(calls) == 1
    assert calls[0]['notify_session'] == ''
    assert calls[0]['reuse_session_id'] == 'sol'


def test_codex_parent_revived_for_another_childs_callback_still_reports_up(monkeypatch):
    ar, calls = _codex_revive_rig(monkeypatch)
    ar._revive_parent_for_delegation('p', 'sol', 'callback from c', {},
                                     from_session_id='some-other-child')
    ar._revive_parent_for_delegation('p', 'sol', 'no sender known', {})
    assert [c['notify_session'] for c in calls] == ['dave', 'dave']


@pytest.mark.parametrize('sender,latched', [('sol', True), ('other', False)])
def test_live_parent_spawner_latch_is_spent_for_a_callback_from_its_spawner(
        monkeypatch, tmp_path, sender, latched):
    """A parent still in memory whose latch had been re-armed: the callback
    turn from the session it would notify must not leave the latch open."""
    import tempfile
    from flask import Flask
    from mc.blueprints import agent_routes as ar
    ar._delegation_app = Flask('test-pingpong')
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, 'load_project', lambda pid: {
        'id': pid, 'project_path': tempfile.gettempdir()})
    monkeypatch.setattr(ar, 'get_manager', lambda pid: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)

    class Runtime:
        def write_followup(self, handle, message):
            handle.session_dict['provider_session_id'] = 'thread'
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda provider: Runtime())
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', store)
    body = {'event_id': 'sender:turn:1', 'child_session_id': sender, 'who': 'w',
            'status': 'completed', 'task': 't', 'summary': 's',
            'provider': 'codex', 'model': 'm', 'message': 'callback'}
    store.enqueue('sender:turn:1', 'p', 'parent', body)
    store.accept('sender:turn:1', 'p', 'parent', body)
    row = store.claim_inbox()
    assert row
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': {
        'project_id': 'p', 'status': 'completed', 'mode': 'A', 'provider': 'codex',
        'agent_model': 'gpt-5.6-luna', 'incognito': False, 'log_lines': [],
        'session_id': 'parent', '_notify_session': 'sol'}})
    ar._process_inbox(row)
    assert bool(ar.agent_sessions['parent'].get('_notify_session_sent')) is latched
