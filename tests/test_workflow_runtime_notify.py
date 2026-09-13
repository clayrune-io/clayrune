"""Non-claude completion callbacks (workflow step + spawner) — incident
run-42a3f2aa, 2026-09-12.

A workflow agent step whose character pinned `provider: codex` finished
(agent_log row `completed`, trigger_id `run-42a3f2aa:us-stock-investor`), but
the run sat `running` forever and the email step after it never fired.

Mechanism: `_dispatch_agent_internal` accepted `notify_session` and
`notify_workflow`, stored both on the claude Mode A/B session dicts, and then
returned early into `_dispatch_via_runtime` for every NON-claude provider
WITHOUT passing either one. That function built its own session dict with no
`_notify_*` keys, so `_maybe_notify_spawner` hit its "no waiter" return on
completion and `on_agent_step_complete` was never called — no exception, no
log line (0 `[notify-workflow]` lines in a 1.5M-line clayrune.log). The same
drop silently cost every non-claude agent dispatched with
`notify_session` its report-back to the spawner.

These tests drive the REAL `_dispatch_agent_internal` -> `_dispatch_via_runtime`
-> `_mode_a_reader` -> `_runtime_log_completion` -> `_log_agent_completion`
chain with a fake non-claude runtime, so they break if any link drops the
fields again.
"""
import json
import time

import pytest

import mc.agent_runtime as agent_runtime_mod
from tests.test_runtime_completion_log import _FakeRuntime


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (imports + wires the blueprints)
    from mc import state as mc_state
    from mc import workflows as wf
    from mc.blueprints import agent_routes as ar

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)

    # The project's default provider is the fake non-claude runtime -- the
    # incident resolved codex from the character's engine pin, which lands on
    # the same `provider_name != 'claude'` branch.
    project = {'id': 'proj1', 'project_path': str(tmp_path), 'provider': 'fakeprov'}
    monkeypatch.setattr(ar, 'load_project',
                        lambda pid: project if pid == 'proj1' else None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)

    spawner_calls = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda pid, sid, child, summary: spawner_calls.append(
                            {'project_id': pid, 'notify_sid': sid,
                             'child': child.get('session_id'), 'summary': summary}))

    monkeypatch.setattr(wf, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(wf, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(wf, '_dispatch_agent_internal', ar._dispatch_agent_internal)
    monkeypatch.setattr(wf, '_load_agent_log', lambda pid: [])

    runtime = _FakeRuntime()
    saved = dict(agent_runtime_mod._RUNTIMES)
    agent_runtime_mod.register_runtime(runtime)
    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'ar': ar, 'wf': wf, 'runtime': runtime, 'tmp_path': tmp_path,
               'sessions': mc_state.agent_sessions, 'spawner_calls': spawner_calls}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)
        agent_runtime_mod._RUNTIMES.clear()
        agent_runtime_mod._RUNTIMES.update(saved)


def _finish_turn(env_, sid, lines):
    """Run one Mode-A turn for `sid` through the real reader + completion hook."""
    session = env_['sessions'][sid]
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=sid, provider='fakeprov', mode='A',
        project_path=str(env_['tmp_path']), project_id='proj1',
        session_dict=session,
        meta={'callbacks': env_['runtime'].dispatch_callbacks or {}})
    env_['runtime'].run_turn(handle, lines)


def _wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_non_claude_dispatch_carries_both_callback_fields(env):
    sid = env['ar']._dispatch_agent_internal(
        'proj1', 'task', trigger_type='workflow', trigger_id='run-x:step',
        notify_session='spawner00001',
        notify_workflow={'run_id': 'run-x', 'step': 'step'})
    session = env['sessions'][sid]
    assert session['provider'] == 'fakeprov'
    assert session.get('_notify_session') == 'spawner00001'
    assert session.get('_notify_workflow') == {'run_id': 'run-x', 'step': 'step'}


def test_non_claude_child_reports_back_to_its_spawner(env):
    sid = env['ar']._dispatch_agent_internal(
        'proj1', 'task', notify_session='spawner00001')
    _finish_turn(env, sid, ['Vance here.', 'HPE leads.'])
    assert env['spawner_calls'] == [{
        'project_id': 'proj1', 'notify_sid': 'spawner00001',
        'child': sid, 'summary': 'HPE leads.'}]


def test_workflow_agent_step_on_non_claude_provider_advances_the_run(env):
    wf = env['wf']
    record = wf.create_workflow({
        'name': 'Check US stocks', 'trigger': {'type': 'manual'},
        'nodes': [
            {'type': 'agent', 'name': 'us-stock-investor', 'project_id': 'proj1',
             'character': '', 'prompt': 'scan the market', 'x': 0, 'y': 0},
            {'type': 'action', 'name': 'action-2', 'action': 'notify_operator',
             'config': {'message': '{{steps.us-stock-investor.output}}'}, 'x': 0, 'y': 0},
        ],
        'edges': [{'from': 'us-stock-investor', 'to': 'action-2'}],
    })
    run = wf.start_run(record['id'])
    step = run['steps']['us-stock-investor']
    assert step['status'] == 'running'
    sid = step['session_id']
    assert env['sessions'][sid]['provider'] == 'fakeprov'

    _finish_turn(env, sid, ['HPE leads my bullish watchlist.'])

    assert _wait_for(lambda: (wf.get_run(run['id']) or {}).get('status') == 'completed'), \
        json.dumps(wf.get_run(run['id']), indent=1)
    final = wf.get_run(run['id'])
    assert final['steps']['us-stock-investor']['status'] == 'completed'
    assert final['steps']['us-stock-investor']['output'] == 'HPE leads my bullish watchlist.'
    # notify_operator is suppressed under PYTEST_CURRENT_TEST -- it completes
    # without shelling out to the mailer.
    assert final['steps']['action-2']['status'] == 'completed'
