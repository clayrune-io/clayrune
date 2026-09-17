"""F7: requested effort survives process recreation, not settings drift.

All execution uses fake runtime/Popen collaborators; no provider is invoked.
"""
import ast
import inspect
from types import SimpleNamespace

import pytest

from tests.test_revive_notify_carry import ar, _project, CSID
from tests.test_runtime_completion_log import env


@pytest.fixture
def captured_flags(ar, monkeypatch):
    captured = []
    runtime = SimpleNamespace(build_command=lambda **kw: captured.append(kw) or ['fake'])
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda _: runtime)
    monkeypatch.setattr(ar, '_resolve_project_mcp_config', lambda _: '')
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake')
    return captured


@pytest.mark.parametrize('character,project,expected', [
    ({'engine': {'effort': 'high'}}, {'agent_effort': 'medium'}, 'high'),
    (None, {'agent_effort': 'medium'}, 'medium'),
    (None, {}, 'low'),
])
def test_initial_effort_precedence(ar, monkeypatch, captured_flags, character, project, expected):
    monkeypatch.setitem(ar.state.CONFIG, 'agent_effort', 'low')
    requested = ar._requested_effort(project, character)
    ar._build_claude_flags(project, effort_override=requested)
    assert requested == expected
    assert captured_flags[-1]['effort'] == expected


def test_explicit_empty_is_not_omitted(ar, monkeypatch, captured_flags):
    monkeypatch.setitem(ar.state.CONFIG, 'agent_effort', 'high')
    assert ar._requested_effort({}, override='') == ''
    ar._build_claude_flags({}, effort_override='')
    assert captured_flags[-1]['effort'] == ''
    ar._build_claude_flags({})
    assert captured_flags[-1]['effort'] == 'high'


@pytest.mark.parametrize('record', [{'requested_effort': 'high'}, {'requested_effort': ''}, {}])
@pytest.mark.parametrize('streaming', [False, True])
def test_cold_revival_preserves_snapshot_or_native_default(ar, monkeypatch, tmp_path,
                                                          captured_flags, record, streaming):
    monkeypatch.setitem(ar.state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setitem(ar.state.CONFIG, 'agent_effort', 'low')
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **kw: None)
    row = dict(session_id='s1', claude_session_id=CSID, model='sonnet', **record)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [row])
    session = ar._revive_from_agent_log('p1', 's1', 'continue', _project(tmp_path))
    assert session is not None
    assert session['requested_effort'] == record.get('requested_effort', '')
    assert captured_flags[-1]['effort'] == record.get('requested_effort', '')


def test_hot_queued_followup_retains_effort_after_defaults_change(ar, monkeypatch, tmp_path,
                                                               captured_flags):
    project = _project(tmp_path)
    project['agent_effort'] = 'low'
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *a: ([], None))
    session = dict(project_id='p1', session_id='s1', requested_effort='high',
                   model='sonnet', log_lines=[], claude_session_id=CSID)
    ar._auto_dispatch_followup(session, 'continue')
    assert captured_flags[-1]['effort'] == 'high'


def test_pending_snapshot_keeps_explicit_empty(ar, monkeypatch):
    saved = []
    monkeypatch.setattr(ar, '_update_agent_log', lambda _, mutate: mutate(saved))
    ar._log_agent_dispatch_pending(dict(project_id='p1', session_id='s1', requested_effort=''))
    assert 'requested_effort' in saved[0]
    assert saved[0]['requested_effort'] == ''


@pytest.mark.parametrize('name', ['agent_followup', 'agent_interrupt', '_auto_recover_failed_resume'])
def test_every_respawn_flag_site_supplies_effort_snapshot(ar, name):
    tree = ast.parse(inspect.getsource(getattr(ar, name)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_build_claude_flags']
    assert calls
    assert all('effort_override' in {kw.arg for kw in call.keywords} for call in calls)


def test_non_claude_revival_carries_requested_effort_without_claiming_support(ar, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [dict(
        session_id='s1', provider='codex', provider_session_id='native1', requested_effort='high')])
    monkeypatch.setattr(ar, '_dispatch_agent_internal', lambda *a, **kw: captured.update(kw) or 's1')
    ar._revive_non_claude_from_agent_log('p1', 's1', 'continue', _project(tmp_path))
    assert captured['effort_override'] == 'high'


def test_non_claude_intent_is_visible_but_not_forwarded_as_supported(ar, monkeypatch, tmp_path):
    from threading import RLock
    captured = {}
    runtime = SimpleNamespace(build_command=lambda **kw: ['fake'],
                              dispatch=lambda **kw: captured.update(kw))
    manager = SimpleNamespace(lock=RLock(), ensure_guardian=lambda: None, session_ids=set())
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda _: runtime)
    monkeypatch.setattr(ar, 'get_manager', lambda _: manager)
    monkeypatch.setattr(ar, '_resolve_runtime_model', lambda *a: 'requested-model')
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)
    sid = ar._dispatch_via_runtime(_project(tmp_path), 'hello', provider_name='codex',
                                   effort_override='high')
    session = ar.agent_sessions[sid]
    assert session['requested_effort'] == 'high'
    assert session['effort_support'] == 'unsupported'
    assert any('not supported' in line for line in session['log_lines'])
    assert 'effort' not in captured and 'effort_override' not in captured


@pytest.mark.parametrize('requested', [dict(model='', agent_model='stale-default'),
                                      dict(agent_model=''), {}])
def test_completion_roundtrip_never_promotes_observed_model(env, monkeypatch, requested):
    routes = env['ar']
    monkeypatch.setattr(routes, '_find_transcript_file', lambda *a: 'fake-transcript')
    monkeypatch.setattr(routes, '_extract_transcript_telemetry', lambda _: {'model': 'observed-opus'})
    session = dict(project_id='proj1', session_id='s1', provider='claude',
                   claude_session_id=CSID, status='completed', log_lines=['Answer'], **requested)
    routes._log_agent_dispatch_pending(session)
    routes._log_agent_completion_body(session)
    row = routes._load_agent_log('proj1')[0]
    assert row['model'] == ''
    assert row['agent_model'] == ''
    assert row['observed_model'] == 'observed-opus'
    assert routes._prior_conversation_model('proj1', CSID, 'claude') == ''
    assert routes._continuation_model(row, {'agent_model': 'new-default'}) == ''


def test_latest_explicit_native_default_stops_older_pin_lookup(ar, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [
        dict(provider='codex', provider_session_id='native1', agent_model=''),
        dict(provider='codex', provider_session_id='native1', agent_model='old-model'),
    ])
    assert ar._prior_conversation_model('p1', 'native1', 'codex') == ''
