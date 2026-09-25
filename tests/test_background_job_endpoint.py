"""POST/GET .../agent/<session_id>/job -- the engine-agnostic background-job
endpoint (MC-958 follow-up, backlog b49cf71f).

CodexRuntime (mc/agent_runtime.py ~7107) runs `codex exec --json` once per
turn and exits at turn end; Gemini/Qwen are the same shape. None of them has
Claude Mode B's self-waking `run_in_background` (mc/background_tasks.py).
This endpoint is the substitute: Clayrune runs the command as a real
subprocess and delivers the result into the SAME session through the
existing delegation-delivery outbox/inbox (mc/delegation_delivery.py) --
the same path `_notify_agent_spawner` uses for a dispatched child's
completion, just self-addressed. That path already knows how to revive a
session whose process has exited (`_process_inbox` -> parent is None ->
`_revive_parent_for_delegation`), which is what makes this work for a
Codex/Gemini/Qwen session between turns.

Lightweight Flask app (mirrors tests/test_delegation_status.py), not the
full `server` wiring: only the module attributes the two routes actually
touch are patched.
"""
from __future__ import annotations

import time

import pytest
from flask import Flask

from mc.blueprints import agent_routes as ar
from mc.delegation_delivery import DeliveryStore


def _wait_for(predicate, timeout=8.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    app = Flask('agent-jobs-test')
    app.register_blueprint(ar.bp)

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'codex'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'p1' else None)
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path / 'data' / 'projects')
    monkeypatch.setattr(ar, '_POPEN_FLAGS', 0)
    monkeypatch.setattr(ar, '_STARTUPINFO', None)
    monkeypatch.setattr(ar, '_persist_pid_ledger', lambda: None)
    monkeypatch.setattr(ar, '_proc_identity', lambda pid: ('test.exe', 0.0))
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delivery.sqlite3'))

    ar.agent_sessions['s1'] = {
        'project_id': 'p1', 'session_id': 's1', 'status': 'idle',
        'provider': 'codex',
    }
    ar.agent_sessions['s-incog'] = {
        'project_id': 'p1', 'session_id': 's-incog', 'status': 'idle',
        'incognito': True,
    }
    try:
        yield app.test_client()
    finally:
        ar.agent_sessions.pop('s1', None)
        ar.agent_sessions.pop('s-incog', None)


def _py(code: str) -> str:
    import sys
    return f'"{sys.executable}" -c "{code}"'


def test_job_start_returns_job_id_and_pid_then_completes(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py("print('hi')")})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['job_id']
    assert body['pid'] > 0

    job_id = body['job_id']
    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running')

    status = client.get(f'/api/project/p1/agent/s1/job/{job_id}')
    assert status.status_code == 200
    sbody = status.get_json()
    assert sbody['status'] == 'completed'
    assert sbody['exit_code'] == 0
    assert 'proc' not in sbody


def test_job_completion_delivers_into_the_owning_session(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py("print('deliver-me')")})
    job_id = resp.get_json()['job_id']
    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running')

    # Reuse of the SAME delivery store _notify_agent_spawner writes to --
    # not a parallel notification mechanism.
    assert _wait_for(lambda: ar._delivery_store.status('outbox', f'job:{job_id}', 'p1') is not None)
    row = ar._delivery_store.status('outbox', f'job:{job_id}', 'p1')
    assert row['parent_session_id'] == 's1'
    assert 'deliver-me' in row['payload']
    assert 'background job finished' in row['payload']


def test_job_timeout_delivers_a_timeout_message(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py('import time; time.sleep(30)'),
                             'timeout_minutes': 0.02})
    assert resp.status_code == 200
    job_id = resp.get_json()['job_id']

    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running',
                     timeout=15.0)
    assert ar._agent_jobs.get_job(job_id)['status'] == 'timeout'
    assert _wait_for(lambda: ar._delivery_store.status('outbox', f'job:{job_id}', 'p1') is not None)
    row = ar._delivery_store.status('outbox', f'job:{job_id}', 'p1')
    assert 'timed out' in row['payload']


def test_job_start_rejects_unknown_session(client):
    resp = client.post('/api/project/p1/agent/does-not-exist/job',
                       json={'command': 'echo hi'})
    assert resp.status_code == 404


def test_job_start_rejects_session_in_another_project(client, tmp_path):
    ar.agent_sessions['s-other'] = {'project_id': 'p2', 'session_id': 's-other',
                                    'status': 'idle'}
    try:
        resp = client.post('/api/project/p1/agent/s-other/job',
                           json={'command': 'echo hi'})
        assert resp.status_code == 404
    finally:
        ar.agent_sessions.pop('s-other', None)


def test_job_start_rejects_incognito_session(client):
    resp = client.post('/api/project/p1/agent/s-incog/job',
                       json={'command': 'echo hi'})
    assert resp.status_code == 400


def test_job_start_rejects_missing_command(client):
    resp = client.post('/api/project/p1/agent/s1/job', json={})
    assert resp.status_code == 400


def test_job_start_rejects_nonexistent_cwd(client, tmp_path):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': 'echo hi',
                             'cwd': str(tmp_path / 'nope-not-a-dir')})
    assert resp.status_code == 400


def test_job_start_rejects_nonpositive_timeout(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': 'echo hi', 'timeout_minutes': 0})
    assert resp.status_code == 400


def test_job_status_unknown_job_id_404s(client):
    resp = client.get('/api/project/p1/agent/s1/job/does-not-exist')
    assert resp.status_code == 404


def test_job_status_rejects_wrong_owning_session(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py("print('x')")})
    job_id = resp.get_json()['job_id']
    ar.agent_sessions['s2'] = {'project_id': 'p1', 'session_id': 's2', 'status': 'idle'}
    try:
        wrong = client.get(f'/api/project/p1/agent/s2/job/{job_id}')
        assert wrong.status_code == 404
    finally:
        ar.agent_sessions.pop('s2', None)
    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running')


# ── shell field (MC-958 follow-up, backlog b49cf71f, bug 1) ────────────────

def test_job_start_rejects_unknown_shell(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': 'echo hi', 'shell': 'fish'})
    assert resp.status_code == 400
    assert 'fish' in resp.get_json()['error']


def test_job_start_returns_and_persists_the_resolved_shell(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py("print('hi')")})
    body = resp.get_json()
    assert body['shell'] == 'bash'
    assert 'shell_note' in body
    job_id = body['job_id']
    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running')
    status = client.get(f'/api/project/p1/agent/s1/job/{job_id}')
    assert status.get_json()['shell'] == 'bash'


def test_job_finished_message_names_the_resolved_shell(client):
    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py("print('deliver-me')")})
    job_id = resp.get_json()['job_id']
    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running')
    assert _wait_for(lambda: ar._delivery_store.status('outbox', f'job:{job_id}', 'p1') is not None)
    row = ar._delivery_store.status('outbox', f'job:{job_id}', 'p1')
    assert 'shell: bash' in row['payload']


# ── spawner-callback hold while a job is open (bug 2, same b49cf71f live
# test): the same latch (`_notify_session_sent`) that MC-958's Claude-side
# fix (7b82905) guards for `run_in_background` must also hold for this
# engine-agnostic job facility, or a Codex/Gemini/Qwen spawner only ever
# sees "started the job" and never the real answer. ──────────────────────

def _capture_notify(monkeypatch):
    sent = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda pid, nsid, child, summary: sent.append(summary))
    return sent


def test_turn_end_holds_spawner_callback_while_job_runs(client, monkeypatch):
    sent = _capture_notify(monkeypatch)
    session = ar.agent_sessions['s1']
    session['_notify_session'] = 'spawner-sid'
    session['log_lines'] = []

    resp = client.post('/api/project/p1/agent/s1/job',
                       json={'command': _py('import time; time.sleep(1.5)')})
    job_id = resp.get_json()['job_id']

    ar._notify_spawner_unless_jobs_open(session, 'STARTED - turn 1')
    assert sent == []
    assert session.get(ar._JOB_DEFERRED_KEY)
    assert any('agent job(s) still running' in l for l in session['log_lines'])

    assert _wait_for(lambda: ar._agent_jobs.get_job(job_id)['status'] != 'running',
                     timeout=10.0)
    ar._notify_spawner_unless_jobs_open(session, 'FINISHED - turn 2')
    assert sent == ['FINISHED - turn 2']


def test_wait_cap_sends_interim_then_rearms_for_the_job_path(client, monkeypatch, tmp_path):
    sent = _capture_notify(monkeypatch)
    monkeypatch.setitem(ar.state.CONFIG, 'background_wait_max_minutes', 120)
    session = ar.agent_sessions['s1']
    session['_notify_session'] = 'spawner-sid'
    session['log_lines'] = ['STARTED']
    now = time.time()
    session[ar._JOB_DEFERRED_KEY] = now - 121 * 60

    assert ar._release_held_job_notify_if_expired(session, now) is True
    assert len(sent) == 1 and sent[0].startswith('[interim: still waiting')
    assert session.get(ar._JOB_INTERIM_KEY) is True
    assert session.get('_notify_session_sent') is True

    # The job finally ends -- _job_on_complete must rearm the latch the
    # interim report spent, so the wake turn's own callback still fires.
    result = {'job_id': 'fake-job', 'project_id': 'p1', 'session_id': 's1',
             'pid': 999999, 'command': 'echo hi', 'cwd': '.', 'shell': 'bash',
             'shell_note': None, 'exit_code': 0, 'timed_out': False,
             'duration_s': 1.0, 'log_path': str(tmp_path / 'x.log'), 'tail': 'hi'}
    ar._job_on_complete(result)
    assert session.get(ar._JOB_INTERIM_KEY) is None
    assert session.get('_notify_session_sent') is None

    ar._notify_spawner_unless_jobs_open(session, 'FINISHED - turn 2')
    assert sent == [sent[0], 'FINISHED - turn 2']
