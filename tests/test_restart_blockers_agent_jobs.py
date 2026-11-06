"""Open agent jobs block a restart (backlog af6cc23f).

A session parked on a Clayrune job (`POST agent/<sid>/job`, mc/agent_jobs.py)
is idle, so `_get_active_restart_blockers` used to ignore it -- yet `_jobs` is
in-memory, so a restart orphans the subprocess and drops the completion
callback that would wake the session. Open jobs now appear as rows in
`active_sessions`, which every restart/shutdown/update caller already blocks on
and which `force` already overrides.
"""
from __future__ import annotations

import time

import pytest
from flask import Flask

import mc.agent_jobs as aj
import mc.blueprints.system_routes as sr


def _job(job_id, session_id='s1', project_id='p1', status='running', command='pytest -q',
         age_s=125):
    return {'job_id': job_id, 'session_id': session_id, 'project_id': project_id,
            'command': command, 'status': status, 'started_at': time.time() - age_s,
            'proc': object()}


@pytest.fixture()
def env(monkeypatch):
    jobs = {}
    monkeypatch.setattr(aj, '_jobs', jobs)
    monkeypatch.setattr(sr, 'load_projects', lambda: [{'id': 'p1', 'name': 'Proj One'}])
    monkeypatch.setattr(sr, 'agent_sessions', {'s1': {'session_id': 's1', 'status': 'idle',
                                                      'project_id': 'p1'}})
    monkeypatch.setattr(sr, '_hivemind_sessions', {})
    monkeypatch.setattr(sr, '_is_cf_tunneled_request', lambda: False)  # injected by server wiring
    app =Flask('restart-blockers-jobs-test')
    app.register_blueprint(sr.bp)
    return {'jobs': jobs, 'client': app.test_client()}


def test_list_open_jobs_skips_finished_and_orders_oldest_first(env):
    env['jobs']['new'] = _job('new', age_s=10)
    env['jobs']['old'] = _job('old', age_s=500)
    env['jobs']['done'] = _job('done', status='completed')
    env['jobs']['late'] = _job('late', status='timeout')
    rows = aj.list_open_jobs()
    assert [r['job_id'] for r in rows] == ['old', 'new']
    assert all('proc' not in r for r in rows)


def test_open_job_is_listed_as_blocker_with_session_job_command_and_age(env):
    env['jobs']['abcdef1234567890'] = _job('abcdef1234567890', command='pytest -q tests/' + 'x' * 100)
    out = sr._get_active_restart_blockers()
    assert out['active_hiveminds'] == []
    (row,) = out['active_sessions']
    assert row['session_id'] == 's1'
    assert row['project_name'] == 'Proj One'
    assert row['job_id'] == 'abcdef1234567890'
    assert row['status'] == 'job_running'
    assert row['task_preview'].startswith('job abcdef12 running 2m: pytest -q tests/')
    assert row['task_preview'].endswith('…')


def test_finished_job_is_not_a_blocker(env):
    env['jobs']['j1'] = _job('j1', status='completed')
    env['jobs']['j2'] = _job('j2', status='timeout')
    assert sr._get_active_restart_blockers() == {'active_sessions': [], 'active_hiveminds': []}


def test_job_row_survives_the_adoption_partition_as_a_blocker(env, monkeypatch):
    # Flag ON and the session "adoptable": the job row is appended after the
    # partition, so it cannot be moved to adoptable_sessions (shim adoption does
    # not carry jobs).
    import mc.agent_shim_adopt as adopt
    monkeypatch.setattr(adopt, 'enabled_now', lambda: True)
    monkeypatch.setattr(adopt, 'is_adoptable_session', lambda *_a, **_k: True)
    monkeypatch.setattr(adopt, 'running_hivemind_workers', lambda: {})
    env['jobs']['j1'] = _job('j1')
    out = sr._get_active_restart_blockers()
    assert [r['job_id'] for r in out['active_sessions']] == ['j1']
    assert 'j1' not in [r.get('job_id') for r in out.get('adoptable_sessions', [])]


def test_status_route_200_and_empty_with_no_jobs(env):
    resp = env['client'].get('/api/system/restart/status')
    assert resp.status_code == 200
    assert resp.get_json() == {'active_sessions': [], 'active_hiveminds': []}


def test_status_route_lists_the_job(env):
    env['jobs']['j1'] = _job('j1')
    body = env['client'].get('/api/system/restart/status').get_json()
    assert [r['job_id'] for r in body['active_sessions']] == ['j1']


def test_restart_409_with_open_job_and_force_still_proceeds(env, monkeypatch):
    fired = []
    monkeypatch.setattr(sr, '_perform_server_restart_async', lambda entry: fired.append(entry))
    monkeypatch.setattr(sr.state, '_LAST_RESTART_TIME', 0)
    env['jobs']['j1'] = _job('j1')

    resp = env['client'].post('/api/system/restart', json={'confirmed': True})
    assert resp.status_code == 409
    assert resp.get_json()['active_sessions'][0]['job_id'] == 'j1'
    assert fired == []

    resp = env['client'].post('/api/system/restart', json={'confirmed': True, 'force': True})
    assert resp.status_code == 202
    assert len(fired) == 1 and fired[0]['forced'] is True


def test_restart_proceeds_without_force_when_no_jobs(env, monkeypatch):
    fired = []
    monkeypatch.setattr(sr, '_perform_server_restart_async', lambda entry: fired.append(entry))
    monkeypatch.setattr(sr.state, '_LAST_RESTART_TIME', 0)
    resp = env['client'].post('/api/system/restart', json={'confirmed': True})
    assert resp.status_code == 202
    assert len(fired) == 1


@pytest.mark.usefixtures('env')
def test_job_scan_failure_degrades_to_the_old_payload(monkeypatch):
    def boom():
        raise RuntimeError('nope')
    monkeypatch.setattr(aj, 'list_open_jobs', boom)
    assert sr._get_active_restart_blockers() == {'active_sessions': [], 'active_hiveminds': []}
