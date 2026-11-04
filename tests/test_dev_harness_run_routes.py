import pytest

from mc.blueprints import agent_routes as jobs
from test_dev_harness_diff import repo
from test_dev_harness_native_plan import inputs, inspect, approve
from test_dev_harness_integration import executable
from test_dev_harness_routes import client, URL
from test_dev_harness_native_plan_routes import bind
from test_dev_harness_dispatch import session, body


def test_exact_schema_no_execution_injection(client, inputs, session, monkeypatch):
    c, _ = client
    bind(monkeypatch, inputs)
    data = {'session_id':'session','expected_revision':0,'native_plan_digest':None,'purpose':'observation'}
    for key in ('command','shell','cwd','argv','environment','report','lease','direct_argv'):
        assert c.post(URL+'/preview', json={**data,key:True}).status_code == 400
    for raw in ('null','{"purpose":"observation","purpose":"observation"}'):
        assert c.post(URL+'/preview',data=raw).status_code == 400
    assert c.post(URL+'/preview',json={**data,'expected_revision':True}).status_code == 400
    response = c.post(URL+'/preview',json=data)
    assert response.status_code == 200 and response.get_json()['code'] == 'manifest_only'
    assert 'job_id' not in response.get_json()
    assert c.post(URL+'/preview',json={**data,'expected_revision':1}).status_code == 409
    assert c.post(URL+'/preview',json={**data,'session_id':'absent'}).status_code == 404
    assert c.get(URL+'/runs/'+'a'*32).status_code == 404


def test_approved_202_pending_and_project_scoped_read(client, executable, session, monkeypatch):
    from flask import jsonify
    c, _ = client
    bind(monkeypatch, executable)
    proposal = inspect(executable)
    approve(executable, proposal)
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: jsonify({'job_id':'job'}))
    response = c.post(URL+'/preview', json=body(proposal))
    assert response.status_code == 202
    data = response.get_json()
    assert data['status']=='pending' and data['authorizes_merge'] is False
    read = c.get(URL+'/runs/'+data['run_id'])
    assert read.status_code == 200 and read.get_json()['status']=='pending'
    assert c.get('/api/project/absent/dev-harness/runs/'+data['run_id']).status_code == 404
    assert c.post(URL+'/run',json={**body(proposal),'target':'HEAD','incoming':'HEAD'}).status_code == 400
    run = {k:v for k,v in body(proposal).items() if k!='purpose'}
    assert c.post(URL+'/run',json={**run,'target':'HEAD','incoming':'HEAD'}).status_code == 202


def test_corrupt_read_projection_has_fixed_error(client, inputs, monkeypatch):
    from mc import dev_harness_integration
    c, _ = client
    bind(monkeypatch, inputs)
    def corrupt(*a):
        raise ValueError('sensitive-corrupt-field')
    monkeypatch.setattr(dev_harness_integration, 'read_run', corrupt)
    response = c.get(URL+'/runs/'+'a'*32)
    assert response.status_code == 500
    assert response.get_json() == {'status':'error','error':'integration_record_unavailable'}


def test_pending_nonhead_target_uses_original_refs_for_staleness(client, executable, session, monkeypatch):
    from flask import jsonify
    from pathlib import Path
    from test_dev_harness_diff import branch
    c, _ = client
    bind(monkeypatch, executable)
    proposal = inspect(executable)
    approve(executable, proposal)
    branch(Path(executable[1]['project_path']))
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: jsonify({'job_id':'job'}))
    data = {k:v for k,v in body(proposal).items() if k!='purpose'}
    response = c.post(URL+'/run',json={**data,'target':'incoming','incoming':'master'})
    assert response.status_code == 202
    read = c.get(URL+'/runs/'+response.get_json()['run_id'])
    assert read.get_json()['stale'] is False
