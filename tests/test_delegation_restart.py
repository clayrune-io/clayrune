"""Disposable loopback HTTP restart evidence for durable delegation.

This intentionally does not import server.boot or start a provider.  The child
process is a tiny Flask/DeliveryStore harness with a fake transport/runtime;
the test process talks to it over real 127.0.0.1 HTTP.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
HARNESS = r'''
import json, sys, urllib.request
from flask import Flask, jsonify, request
from mc.delegation_delivery import DeliveryBlocked, DeliveryDeferred, DeliveryStore, DeliveryUncertain, drain_once
from mc.blueprints import agent_routes as ar

db, port = sys.argv[1], int(sys.argv[2])
launch_file = db + '.fake-handoff-count'
store = DeliveryStore(db)
app = Flask('delegation-restart-harness')
ar._delivery_store = store
ar.PORT = port
ar.DATA_DIR = __import__('pathlib').Path(db).parent
ar.agent_sessions.clear()
ar.agent_sessions['parent'] = {'project_id': 'p', 'status': 'completed',
                                'provider': 'codex', 'agent_model': 'fake-model',
                                'pinned_model': 'fake-model', 'incognito': False}
ar.get_manager = lambda pid: type('Manager', (), {'lock': __import__('threading').RLock()})()
ar._model_quota_blocked = lambda *args: ''
def fake_followup(pid):
    launches['count'] += 1
    open(launch_file, 'w').write(str(launches['count']))
    return jsonify(ok=True, session_id='parent'), 200
ar.agent_followup = fake_followup
ar._delegation_app = app
app.register_blueprint(ar.bp)
mode = {'value': 'ok'}
launches = {'count': int(open(launch_file).read()) if __import__('os').path.exists(launch_file) else 0}

def payload(row):
    return json.loads(row['payload'])['payload']

@app.post('/seed')
def seed():
    d = request.get_json()
    store.enqueue(d['event_id'], d['project_id'], d['parent_session_id'], d['payload'])
    return jsonify(ok=True), 201

@app.post('/mode')
def set_mode():
    mode['value'] = request.get_json()['value']
    return jsonify(ok=True)

def send(row):
    if mode['value'] == 'before_receipt':
        raise TimeoutError('sender died before receipt')
    try:
        ar._deliver_outbox(row)
        if mode['value'] == 'lost_response':
            raise TimeoutError('response lost after durable acceptance')
    except Exception:
        if mode['value'] == 'lost_response':
            raise TimeoutError('response lost after durable acceptance')
        raise

def process(row):
    if mode['value'] == 'lost_response':
        raise DeliveryDeferred('receipt response was lost; parent work remains pending')
    if mode['value'] == 'busy':
        raise DeliveryDeferred('parent busy')
    if mode['value'] == 'ambiguous':
        raise DeliveryUncertain('submission outcome unknown')
    if mode['value'] == 'blocked':
        raise DeliveryBlocked('parent missing')
    return ar._process_inbox(row)

@app.post('/drain')
def drain():
    return jsonify(done=drain_once(store, send_outbox=send, process_inbox=process), launches=launches['count'])

app.run('127.0.0.1', port, threaded=True, use_reloader=False)
'''


def _port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def _http(port, path, method='GET', body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture()
def harness(tmp_path):
    port = _port(); db = tmp_path / 'delivery.db'
    env = {'SystemRoot': os.environ.get('SystemRoot', r'C:\Windows'),
           'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT),
           'TEMP': str(tmp_path), 'TMP': str(tmp_path), 'HOME': str(tmp_path / 'home'),
           'USERPROFILE': str(tmp_path / 'profile'), 'APPDATA': str(tmp_path / 'appdata'),
           'LOCALAPPDATA': str(tmp_path / 'localappdata'), 'MC_REMOTE_ENABLED': '0'}
    pids = []; children = []
    def start():
        child = subprocess.Popen([sys.executable, '-c', HARNESS, str(db), str(port)],
                                 cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        pids.append(child.pid)
        children.append(child)
        return child
    def cleanup_children():
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=3)
        assert all(child.poll() is not None for child in children)
    proc = start()
    register_url = os.environ.get('MC_TEST_PROCESS_REGISTER_URL')
    if register_url:
        try:
            for pid in pids:
                status, body = _http_external(register_url, {'pid': pid, 'name': 'delegation restart test harness',
                                                              'project_id': os.environ.get('MC_TEST_PROCESS_PROJECT', 'mission_control'),
                                                              'command': 'pytest delegation restart harness'})
                assert status == 200 and body.get('ok') is True
        except Exception:
            cleanup_children()
            raise
    ready = False
    for _ in range(40):
        try:
            if _http(port, '/api/project/x/agent/delegation/status?event_id=x')[0] == 404: break
        except Exception: pass
        time.sleep(.05)
    else:
        cleanup_children()
        raise AssertionError('restart harness did not become ready within 2 seconds')
    try:
        class HarnessHandle:
            def __init__(self): self.proc = proc
            @property
            def port(self): return port
            def restart(self):
                self.proc.terminate(); self.proc.wait(timeout=3); self.proc = start()
                if register_url:
                    try:
                        status, body = _http_external(register_url, {'pid': self.proc.pid, 'name': 'delegation restart test harness',
                                                                      'project_id': os.environ.get('MC_TEST_PROCESS_PROJECT', 'mission_control'),
                                                                      'command': 'pytest delegation restart harness'})
                        assert status == 200 and body.get('ok') is True
                    except Exception:
                        cleanup_children()
                        raise
                for _ in range(40):
                    try:
                        if _http(port, '/api/project/x/agent/delegation/status?event_id=x')[0] == 404: return
                    except Exception: pass
                    time.sleep(.05)
                cleanup_children()
                raise AssertionError('restarted harness did not become ready within 2 seconds')
        handle = HarnessHandle()
        yield handle
    finally:
        current = locals().get('handle')
        if current is not None:
            current.proc.terminate(); current.proc.wait(timeout=3)
            assert current.proc.poll() is not None
            assert all(child.poll() is not None for child in children)
        else:
            proc.terminate(); proc.wait(timeout=3)


def _http_external(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=3) as response:
        return response.status, json.loads(response.read())


def _seed(port, event='e1', project='p', parent='parent'):
    p = {'event_id': event, 'project_id': project, 'parent_session_id': parent,
         'payload': {'event_id': event, 'child_session_id': 'child', 'summary': 'full result', 'message': 'full result'}}
    assert _http(port, '/seed', 'POST', p)[0] == 201


def test_restart_before_receipt_and_duplicate_safe_recovery(harness, tmp_path):
    _seed(harness.port)
    assert _http(harness.port, '/mode', 'POST', {'value': 'before_receipt'})[0] == 200
    assert _http(harness.port, '/drain', 'POST', {})[0] == 200
    assert _http(harness.port, '/api/project/p/agent/delegation/status?event_id=e1')[1]['outbox']['state'] == 'pending'
    # A new process reopens the same SQLite file and receives the event once.
    # The fixture's process is deliberately not reused; this second launch is
    # an explicit restart with the same disposable database.
    harness.restart()
    assert _http(harness.port, '/mode', 'POST', {'value': 'ok'})[0] == 200
    deadline = time.time() + 4
    while time.time() < deadline:
        _http(harness.port, '/drain', 'POST', {})
        state = _http(harness.port, '/api/project/p/agent/delegation/status?event_id=e1')[1]
        if state['outbox']['state'] == 'delivered' and state['inbox']['state'] == 'submitted':
            break
        time.sleep(.2)
    else:
        raise AssertionError(state)
    assert state['outbox']['state'] == 'delivered'
    assert state['inbox']['state'] == 'submitted'
    assert json.loads(state['outbox']['payload'])['payload']['event_id'] == 'e1'
    assert state['inbox']['parent_session_id'] == 'parent'
    evidence = json.loads(state['inbox']['submit_evidence'])
    assert evidence['parent_session_id'] == 'parent'
    assert _http(harness.port, '/drain', 'POST', {})[1]['launches'] == 1
    harness.restart()
    final = _http(harness.port, '/drain', 'POST', {})[1]
    assert final['done'] == 0 and final['launches'] == 1
    state = _http(harness.port, '/api/project/p/agent/delegation/status?event_id=e1')[1]
    assert state['outbox']['state'] == 'delivered' and state['inbox']['state'] == 'submitted'


def test_lost_receipt_response_busy_and_ambiguous_are_not_replayed(harness):
    _seed(harness.port, 'e2')
    assert _http(harness.port, '/mode', 'POST', {'value': 'lost_response'})[0] == 200
    _http(harness.port, '/drain', 'POST', {})
    assert _http(harness.port, '/api/project/p/agent/delegation/status?event_id=e2')[1]['inbox']['state'] == 'pending'
    _seed(harness.port, 'e3'); _http(harness.port, '/mode', 'POST', {'value': 'busy'}); _http(harness.port, '/drain', 'POST', {})
    assert _http(harness.port, '/api/project/p/agent/delegation/status?event_id=e3')[1]['inbox']['state'] == 'pending'
    _seed(harness.port, 'e4'); _http(harness.port, '/mode', 'POST', {'value': 'ambiguous'}); _http(harness.port, '/drain', 'POST', {})
    assert _http(harness.port, '/api/project/p/agent/delegation/status?event_id=e4')[1]['inbox']['state'] == 'uncertain'


def test_wrong_project_status_and_retry_are_404(harness):
    _seed(harness.port, 'e5')
    assert _http(harness.port, '/api/project/other/agent/delegation/status?event_id=e5')[0] == 404
    assert _http(harness.port, '/api/project/other/agent/delegation/retry', 'POST', {'event_id': 'e5'})[0] == 404
