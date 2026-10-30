import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

from mc import dev_harness_dispatch as owner
from mc import dev_harness_runner as runner
from mc.blueprints import agent_routes as jobs
from test_dev_harness_diff import repo
from test_dev_harness_native_plan import inputs, inspect, approve
from test_dev_harness_integration import executable


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(jobs, 'get_manager', lambda _: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setitem(jobs.agent_sessions, 'session', {'project_id': 'one'})
    return jobs.agent_sessions['session']


def body(proposal, purpose='observation'):
    return {'session_id': 'session', 'expected_revision': 1,
            'native_plan_digest': proposal['native_plan_digest'], 'purpose': purpose}


def test_server_fixed_job_and_worker_queued_drift(executable, session, monkeypatch):
    proposal = inspect(executable)
    approve(executable, proposal)
    received = []
    def start(pid, sid, **kwargs):
        received.append(kwargs)
        return jsonify({'job_id': 'job-id'})
    monkeypatch.setattr(jobs, 'agent_job_start', start)
    with Flask(__name__).test_request_context():
        response, code = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
        assert code == 202 and response.get_json()['status'] == 'pending'
        run_id = response.get_json()['run_id']
    assert received[0]['job_data']['shell'] == 'bash'
    assert 'mc.dev_harness_dispatch' in received[0]['job_data']['command']
    (Path(executable[1]['project_path'])/'app.py').write_text('queued drift')
    monkeypatch.setattr(runner, '_execute', lambda *a, **k: pytest.fail('queued drift must not spawn'))
    result = owner.run_request(executable[0], 'one', run_id, register=lambda *a: None, unregister=lambda p: None)
    assert result['status'] == 'error'
    assert not owner._request_path(executable[0], 'one', run_id).exists()


def test_dispatch_refusal_and_exception_leave_no_runnable_request(executable, session, monkeypatch):
    proposal = inspect(executable)
    approve(executable, proposal)
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: (jsonify({'error': 'refused'}), 403))
    with Flask(__name__).test_request_context():
        response = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
        assert response.status_code == 403
    assert not list((executable[0].project_dir('one')/'run_requests').glob('*.json'))
    def failed(*a, **k):
        raise RuntimeError('sensitive-dispatch-error')
    monkeypatch.setattr(jobs, 'agent_job_start', failed)
    with Flask(__name__).test_request_context(), pytest.raises(owner.StoreError, match='dispatch failed'):
        owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
    assert not list((executable[0].project_dir('one')/'run_requests').glob('*.json'))


def test_proposal_never_executes_and_off_is_manifest_only(executable, session, monkeypatch):
    proposal = inspect(executable)
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: pytest.fail('no job may start'))
    with Flask(__name__).test_request_context(), pytest.raises(owner.RevisionConflict):
        owner.dispatch(executable[0], executable[1], {**body(proposal),'expected_revision':0}, kind='preview')
    approve(executable, proposal, mode='off')
    with Flask(__name__).test_request_context():
        response = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
        assert response.get_json()['code'] == 'mode_off'
        assert 'job_id' not in response.get_json()
def test_real_worker_completion_and_outer_termination(executable, session, monkeypatch):
    proposal = inspect(executable)
    approve(executable, proposal)
    hooks = []
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: (hooks.append(k['completion_hook']) or jsonify({'job_id': 'job'})))
    monkeypatch.setattr(jobs, '_job_on_complete', lambda result: hooks.append(result))
    with Flask(__name__).test_request_context():
        response, code = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
        run_id = response.get_json()['run_id']
    record = owner.run_request(executable[0], 'one', run_id, register=lambda *a: None, unregister=lambda p: None)
    assert record['status'] == 'pass', record
    hooks[0]({'timed_out': False})
    assert hooks[-1] == {'timed_out': False}
    with Flask(__name__).test_request_context():
        response, code = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
        run_id = response.get_json()['run_id']
    hooks[-1]({'timed_out': True})
    value = executable[0]._read_json(executable[0].project_dir('one')/'integration_runs'/(run_id+'.json'))
    assert value['status'] == 'error' and value['code'] == 'job_terminated'


@pytest.mark.parametrize('flag,status', [('incognito',400),('_codex_unattended_sandbox',403)])
def test_planless_observations_keep_session_guards(inputs, session, flag, status):
    session[flag] = True
    data = {'session_id':'session','expected_revision':0,'native_plan_digest':None,'purpose':'observation'}
    with Flask(__name__).test_request_context():
        _, code = owner.dispatch(inputs[0], inputs[1], data, kind='preview')
    assert code == status
    assert not inputs[0].root.exists()


def test_fixed_invocation_through_real_session_job_and_native_children(executable, session, monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from mc import agent_jobs, state
    from mc.dev_harness_store import HarnessStore
    from mc.dev_harness_integration import read_run
    inputs = (HarnessStore(executable[0].root.parent/'data'), *executable[1:])
    store, project, *_ = inputs
    install_root = store.root.parent.parent
    monkeypatch.setenv('MC_DATA_DIR', str(install_root))
    project_file = install_root/'data/projects/one.json'
    project_file.parent.mkdir(parents=True)
    project_file.write_text(json.dumps(project))
    registered = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            registered.append(data['pid'])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setitem(state.CONFIG, 'port', server.server_port)
    monkeypatch.setattr(jobs, 'load_project', lambda _: project)
    monkeypatch.setattr(jobs, '_register_process', lambda proc,*args: registered.append(proc.pid))
    monkeypatch.setattr(jobs, '_job_log_dir', lambda: tmp_path/'.clayrune/outer-logs')
    done = threading.Event()
    monkeypatch.setattr(jobs, '_job_on_complete', lambda result: done.set())
    try:
        proposal = inspect(inputs)
        approve(inputs, proposal)
        with Flask(__name__).test_request_context():
            response, code = owner.dispatch(store, project, body(proposal), kind='preview')
            result = response.get_json()
        assert code == 202
        assert done.wait(40)
        value = read_run(store, project, result['run_id'])
        assert value['status'] == 'pass', value
        assert len(registered) == 3  # Outer fixed worker, native version, native command.
        job = agent_jobs.public_job(result['job_id'])
        assert 'synthetic-output' not in Path(job['log_path']).read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_worker_cli_corrupt_request_never_prints_traceback(inputs, monkeypatch):
    import subprocess
    import sys
    store = inputs[0]
    run_id = 'a'*32
    monkeypatch.setenv('MC_DATA_DIR', str(store.root.parent.parent))
    # CLI derives <MC_DATA_DIR>/data/dev_harness; keep this entirely temporary.
    request = store.root.parent.parent/'data/dev_harness/one/run_requests'/(run_id+'.json')
    request.parent.mkdir(parents=True)
    request.write_text('{broken-sensitive-input')
    result = subprocess.run([sys.executable,'-m','mc.dev_harness_dispatch',run_id],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert result.stderr == ''
    assert json.loads(result.stdout) == {'status':'error','code':'worker_inputs_unavailable','authorizes_merge':False}


def test_approved_nongit_preview_remains_manifest_only(executable, session, monkeypatch, tmp_path_factory):
    root = tmp_path_factory.mktemp('nongit-project')
    (root/'app.py').write_text('source\n')
    (root/'pyproject.toml').write_text('[tool.fixture]\n')
    inputs = (executable[0], {**executable[1],'project_path':str(root)}, *executable[2:])
    proposal = inspect(inputs)
    assert proposal['plan']['payload']['git_common_dir'] is None
    approve(inputs, proposal)
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: pytest.fail('nongit preview must not start a job'))
    with Flask(__name__).test_request_context():
        response = owner.dispatch(inputs[0], inputs[1], body(proposal), kind='preview')
    assert response.get_json()['code'] == 'manifest_only'


def test_completion_before_queue_persistence_cannot_recreate_request(executable, session, monkeypatch):
    proposal = inspect(executable)
    approve(executable, proposal)
    monkeypatch.setattr(jobs, '_job_on_complete', lambda result: None)
    def finish_first(*a, **kwargs):
        kwargs['completion_hook']({'timed_out':True})
        return jsonify({'job_id':'finished-before-registration-returned'})
    monkeypatch.setattr(jobs, 'agent_job_start', finish_first)
    with Flask(__name__).test_request_context():
        response, code = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
    assert code == 202
    run_id = response.get_json()['run_id']
    assert not owner._request_path(executable[0], 'one', run_id).exists()


def test_consumed_request_replay_cannot_execute_or_overwrite_terminal_result(executable, session, monkeypatch):
    proposal = inspect(executable)
    approve(executable, proposal)
    monkeypatch.setattr(jobs, 'agent_job_start', lambda *a, **k: jsonify({'job_id':'job'}))
    with Flask(__name__).test_request_context():
        response, _ = owner.dispatch(executable[0], executable[1], body(proposal), kind='preview')
    run_id = response.get_json()['run_id']
    path = owner._request_path(executable[0], 'one', run_id)
    queued = path.read_bytes()
    registered = []
    first = owner.run_request(executable[0], 'one', run_id,
        register=lambda proc,*a: registered.append(proc.pid), unregister=lambda p: None)
    assert first['status'] == 'pass' and len(registered) == 2
    path.write_bytes(queued)
    replay = owner.run_request(executable[0], 'one', run_id,
        register=lambda *a: pytest.fail('consumed request must not execute'), unregister=lambda p: None)
    assert replay['status'] == 'error'
    terminal = executable[0]._read_json(executable[0].project_dir('one')/'integration_runs'/(run_id+'.json'))
    assert terminal == first
    assert not path.exists()
