"""Workflow agent steps that finish but never advance their run -- incidents
run-42a3f2aa (2026-09-12) and run-5de9dfa5 (2026-09-14).

Both runs parked on a codex agent step whose session completed (agent_log row
`completed`) while the run stayed `running` and the one-live-run guard refused
every new run. The live wake path is a single best-effort chain
(`_log_agent_completion` -> `_maybe_notify_spawner` -> `on_agent_step_complete`),
so these tests pin two things:

1. The live hook advances a CODEX step end-to-end: real `_dispatch_agent_internal`
   -> `_dispatch_via_runtime` -> `CodexRuntime.dispatch` -> real subprocess ->
   `_mode_a_reader` -> `_runtime_log_completion`. Only the `codex` binary is
   replaced, by a script that prints codex's JSONL.
2. When that hook is missed, `workflows.reconcile_running_steps` (scheduler
   tick) advances the run from the durable record -- idempotently, never
   treating stopped/interrupted/error as success, never touching a session
   that is still working, and surfacing a step with no session and no record
   instead of leaving it silent.
"""
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone

import pytest

import mc.agent_runtime as agent_runtime_mod
from mc import state as mc_state
from tests.test_workflows import UI_HEADERS, _action, _agent, _doc, _edge, wf  # noqa: F401


@pytest.fixture()
def live():
    snap = dict(mc_state.agent_sessions)
    try:
        yield mc_state.agent_sessions
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snap)


def _two_step(wf_):
    doc = wf_.m.create_workflow(_doc('recon', [_agent('first'), _agent('second')],
                                     edges=[_edge('first', 'second')]))
    run = wf_.m.start_run(doc['id'])
    return run, run['steps']['first']['session_id'], doc['id']


_ZERO = {'advanced': 0, 'stalled': 0}


# ── reconciler rules ─────────────────────────────────────────────────────────

def test_a_live_session_still_working_is_left_alone_however_old(wf, live):
    run, sid, _ = _two_step(wf)
    live[sid] = {'session_id': sid, 'project_id': 'p1', 'status': 'running', 'log_lines': []}
    later = datetime.now(timezone.utc) + timedelta(days=3)
    assert wf.m.reconcile_running_steps(now=later) == _ZERO
    assert wf.m.get_run(run['id'])['status'] == 'running'
    assert len(wf.dispatch.calls) == 1


def test_a_completed_row_advances_the_run_when_the_hook_was_missed(wf, live):
    run, sid, _ = _two_step(wf)
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'completed', 'summary': 'FIRST DONE'}]

    assert wf.m.reconcile_running_steps() == {'advanced': 1, 'stalled': 0}
    after = wf.m.get_run(run['id'])
    assert after['steps']['first']['status'] == 'completed'
    assert after['steps']['first']['output'] == 'FIRST DONE'
    assert after['steps']['second']['status'] == 'running'
    assert len(wf.dispatch.calls) == 2

    # Idempotent: a second tick (and a late live hook) change nothing.
    assert wf.m.reconcile_running_steps() == _ZERO
    wf.m.on_agent_step_complete(run_id=run['id'], step_name='first', project_id='p1',
                                session_id=sid, status='completed', summary='LATE')
    again = wf.m.get_run(run['id'])
    assert again['steps']['first']['output'] == 'FIRST DONE'
    assert len(wf.dispatch.calls) == 2


def test_a_live_idle_session_without_a_row_advances_with_the_hook_summary(wf, live, monkeypatch):
    """Mode B: the turn boundary is 'done' but no agent_log row is written
    until the process exits -- the placeholder stays 'in_progress'."""
    monkeypatch.setattr(wf.m, '_session_summary', lambda s: 'LIVE REPLY')
    run, sid, _ = _two_step(wf)
    live[sid] = {'session_id': sid, 'project_id': 'p1', 'status': 'idle', 'log_lines': []}
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'in_progress', 'summary': ''}]

    assert wf.m.reconcile_running_steps()['advanced'] == 1
    assert wf.m.get_run(run['id'])['steps']['first']['output'] == 'LIVE REPLY'


@pytest.mark.parametrize('status', ['stopped', 'interrupted'])
def test_stopped_or_interrupted_is_never_treated_as_success(wf, live, status):
    run, sid, _ = _two_step(wf)
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': status, 'summary': 'half done'}]
    wf.m.reconcile_running_steps()
    after = wf.m.get_run(run['id'])
    assert after['status'] == 'interrupted'
    assert after['steps']['first']['status'] == 'failed'
    assert after['steps']['second']['status'] == 'pending'
    assert len(wf.dispatch.calls) == 1


def test_an_error_row_fails_the_run(wf, live):
    run, sid, _ = _two_step(wf)
    wf.agent_logs['p1'] = [{'session_id': sid, 'status': 'error', 'summary': 'boom'}]
    wf.m.reconcile_running_steps()
    after = wf.m.get_run(run['id'])
    assert after['status'] == 'failed'
    assert after['steps']['second']['status'] == 'pending'


def test_the_live_hook_with_a_stopped_session_does_not_advance(wf):
    """Stop used to fall through to 'completed' and hand partial text on."""
    run, sid, _ = _two_step(wf)
    wf.m.on_agent_step_complete(run_id=run['id'], step_name='first', project_id='p1',
                                session_id=sid, status='stopped', summary='partial')
    after = wf.m.get_run(run['id'])
    assert after['status'] == 'interrupted'
    assert after['steps']['second']['status'] == 'pending'
    assert len(wf.dispatch.calls) == 1


def test_no_session_and_no_record_is_surfaced_after_the_grace_not_before(wf, live, monkeypatch):
    logs = []
    monkeypatch.setattr(wf.m, '_log', lambda *a, **k: logs.append(' '.join(map(str, a))))
    monkeypatch.setitem(mc_state.CONFIG, 'workflow_stalled_step_seconds', 600)
    run, sid, doc_id = _two_step(wf)

    assert wf.m.reconcile_running_steps() == _ZERO  # inside the race window
    assert wf.m.get_run(run['id'])['status'] == 'running'

    later = datetime.now(timezone.utc) + timedelta(seconds=660)
    assert wf.m.reconcile_running_steps(now=later) == {'advanced': 0, 'stalled': 1}
    after = wf.m.get_run(run['id'])
    assert after['status'] == 'interrupted'
    assert 'no live session' in after['error'] and sid[:12] in after['error']
    assert after['steps']['first'].get('stalled_at')
    assert any('marked interrupted' in line for line in logs)

    # Visible through the existing run API ...
    body = wf.client.get(f"/api/workflow-runs/{run['id']}").get_json()
    assert body['status'] == 'interrupted' and 'no live session' in body['error']
    # ... frees the one-live-run guard, and a late completion cannot revive it.
    wf.m.on_agent_step_complete(run_id=run['id'], step_name='first', project_id='p1',
                                session_id=sid, status='completed', summary='late')
    assert wf.m.get_run(run['id'])['status'] == 'interrupted'
    assert wf.m.start_run(doc_id)['status'] == 'running'


def test_adoption_and_the_reconciler_share_one_verdict(wf, live):
    """Boot never trusts a live session (there are none) and interrupts an
    unconfirmed step at once; the tick waits out the grace window instead."""
    run, sid, _ = _two_step(wf)
    wf.m.adopt_on_startup()
    assert wf.m.get_run(run['id'])['status'] == 'interrupted'


def test_operator_cancel_frees_the_guard_over_http_even_with_the_agent_still_live(wf, live):
    run, sid, doc_id = _two_step(wf)
    live[sid] = {'session_id': sid, 'project_id': 'p1', 'status': 'running', 'log_lines': []}
    assert wf.client.post(f'/api/workflows/{doc_id}/run', headers=UI_HEADERS).status_code == 409

    resp = wf.client.post(f"/api/workflow-runs/{run['id']}/cancel", headers=UI_HEADERS)
    assert resp.status_code == 200 and resp.get_json()['run']['status'] == 'cancelled'

    started = wf.client.post(f'/api/workflows/{doc_id}/run', headers=UI_HEADERS)
    assert started.status_code in (200, 201), started.get_json()
    assert wf.m.get_run(run['id'])['status'] == 'cancelled'
    wf.m.reconcile_running_steps()  # the tick never un-cancels
    assert wf.m.get_run(run['id'])['status'] == 'cancelled'


# ── codex end-to-end ─────────────────────────────────────────────────────────

_REPLY = 'HPE leads my bullish watchlist.'
_FAKE_CODEX = textwrap.dedent(f"""
    import json, sys
    sys.stdin.read()
    print(json.dumps({{'type': 'thread.started', 'thread_id': 'fake-thread-1'}}))
    print(json.dumps({{'type': 'turn.started'}}))
    print(json.dumps({{'type': 'item.completed',
                      'item': {{'type': 'agent_message', 'text': {_REPLY!r}}}}}))
    print(json.dumps({{'type': 'turn.completed', 'usage': {{}}}}))
    sys.stdout.flush()
""")


@pytest.fixture()
def codex_env(tmp_path, monkeypatch):
    import server  # noqa: F401  (imports + wires the blueprints)
    from mc import workflows as wfm
    from mc.blueprints import agent_routes as ar

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    project = {'id': 'proj1', 'project_path': str(project_path), 'provider': 'codex'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'proj1' else None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_resolve_runtime_model', lambda *a, **k: '')

    script = tmp_path / 'fake_codex.py'
    script.write_text(_FAKE_CODEX, encoding='utf-8')
    codex_home = tmp_path / 'codex_sessions'
    codex_home.mkdir()
    monkeypatch.setattr(agent_runtime_mod, '_CODEX_HOME', codex_home)
    rt = agent_runtime_mod.CodexRuntime()
    monkeypatch.setattr(rt, '_cmd_prefix', lambda: [sys.executable, str(script)])
    saved = dict(agent_runtime_mod._RUNTIMES)
    agent_runtime_mod.register_runtime(rt)

    monkeypatch.setattr(wfm, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(wfm, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(wfm, '_dispatch_agent_internal', ar._dispatch_agent_internal)
    monkeypatch.setattr(wfm, '_load_agent_log', ar._load_agent_log)
    monkeypatch.setattr(wfm, '_session_summary', ar._last_reply_text)

    snap = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'wf': wfm, 'ar': ar, 'sessions': mc_state.agent_sessions}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snap)
        agent_runtime_mod._RUNTIMES.clear()
        agent_runtime_mod._RUNTIMES.update(saved)


def _wait_for(pred, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _start_codex_run(env):
    wfm = env['wf']
    record = wfm.create_workflow({
        'name': 'Check US stocks', 'trigger': {'type': 'manual'},
        'nodes': [
            {'type': 'agent', 'name': 'us-stock-investor', 'project_id': 'proj1',
             'character': '', 'prompt': 'scan the market', 'x': 0, 'y': 0},
            _action('Send email', 'notify_operator',
                    config={'message': '{{steps.us-stock-investor.output}}'}),
        ],
        'edges': [{'from': 'us-stock-investor', 'to': 'Send email'}],
    })
    run = wfm.start_run(record['id'])
    sid = run['steps']['us-stock-investor']['session_id']
    assert env['sessions'][sid]['provider'] == 'codex'
    return run, sid


def _assert_completed(env, run_id):
    final = env['wf'].get_run(run_id)
    assert final['status'] == 'completed', final
    assert final['steps']['us-stock-investor']['output'] == _REPLY
    assert final['steps']['Send email']['status'] == 'completed'


def test_codex_step_advances_the_run_through_the_live_hook(codex_env):
    run, _ = _start_codex_run(codex_env)
    assert _wait_for(lambda: (codex_env['wf'].get_run(run['id']) or {}).get('status') == 'completed')
    _assert_completed(codex_env, run['id'])


def test_codex_step_advances_through_the_reconciler_when_the_hook_is_missed(codex_env, monkeypatch):
    ar, wfm = codex_env['ar'], codex_env['wf']
    # The state run-5de9dfa5 was in: the session finishes and its agent_log
    # row says completed, but nothing ever wakes the run.
    monkeypatch.setattr(ar, '_maybe_notify_spawner', lambda *a, **k: None)
    run, sid = _start_codex_run(codex_env)

    def _row_completed():
        return any(e.get('session_id') == sid and e.get('status') == 'completed'
                   for e in ar._load_agent_log('proj1'))
    assert _wait_for(_row_completed), ar._load_agent_log('proj1')
    assert wfm.get_run(run['id'])['status'] == 'running'  # parked, exactly as in the incident

    assert wfm.reconcile_running_steps() == {'advanced': 1, 'stalled': 0}
    _assert_completed(codex_env, run['id'])
    assert wfm.reconcile_running_steps() == _ZERO


def test_a_raise_before_the_wake_still_wakes_the_workflow(codex_env, monkeypatch):
    """`_log_agent_completion`'s wrapper: an exception on the way to the
    notify call (here, the summary scan) must not strand the run."""
    ar = codex_env['ar']

    class _Boom:
        def match(self, *_a, **_k):
            raise RuntimeError('summary scan exploded')
    monkeypatch.setattr(ar._agent_runtime, '_SEED_LINE_RE', _Boom())
    run, _ = _start_codex_run(codex_env)
    assert _wait_for(lambda: (codex_env['wf'].get_run(run['id']) or {}).get('status') == 'completed')
    assert codex_env['wf'].get_run(run['id'])['steps']['us-stock-investor']['output'] == _REPLY
