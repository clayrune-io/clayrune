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
