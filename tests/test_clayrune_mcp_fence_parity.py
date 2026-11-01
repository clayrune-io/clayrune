"""Fence parity for the `clayrune` MCP tools and the job route (BUILTINS-7a send-back).

Probe, run against the real `steward.fence.classify_action`: a force-push was
blocked as a Bash command and as the body of a curl to /agent/<sid>/job, but
NOT when sent through `clayrune_start_job` / `clayrune_dispatch_agent`, because
no Bash hook ever saw it. These pin the four cases, and that a manual
(attended) session is left alone.
"""
from __future__ import annotations

import json

import pytest
from flask import Flask

from mc import caller_attribution, state
from mc.blueprints import agent_routes as ar
from mc.blueprints import clayrune_mcp_routes
from mc.caller_attribution import ATTRIBUTED, Attribution
from mc.clayrune_mcp import fence_parity, tools
from tests.test_clayrune_mcp_tools import FENCE_PATH, ME, PID, FakeServer

BAD = 'git push --force origin master'
GOOD = 'git status'


# -- the classifier itself: the probe's baseline ------------------------------

def test_probe_baseline_bash_and_curl_forms_are_blocked():
    assert fence_parity.check_command(BAD)[0] is True
    assert fence_parity.check_command(GOOD)[0] is False
    job = f'/api/project/{PID}/agent/{ME}/job'
    assert fence_parity.check_request(5199, job, {'command': BAD})[0] is True
    assert fence_parity.check_request(5199, job, {'command': GOOD})[0] is False


def test_armed_follows_the_existing_unattended_rule():
    assert fence_parity.armed({'trigger_type': 'manual'}, {}) is False
    assert fence_parity.armed({'trigger_type': 'dispatch'}, {}) is True
    assert fence_parity.armed({}, {}) is True                       # unknown -> fail closed
    assert fence_parity.armed({'trigger_type': 'dispatch'}, {'fence_unattended_enabled': False}) is False


# -- route level: POST /agent/<sid>/job ---------------------------------------

def _reached(resp):
    """True when the request got past the fence to the (stubbed) start_job."""
    return resp.status_code == 400 and resp.get_json() == {'error': 'reached start_job'}


@pytest.fixture()
def job_client(tmp_path, monkeypatch):
    app = Flask('job-fence-test')
    app.register_blueprint(ar.bp)
    pp = tmp_path / 'proj'
    pp.mkdir()
    project = {'id': 'p1', 'project_path': str(pp)}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'p1' else None)
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path / 'data' / 'projects')
    started = []

    def fake_start_job(**kw):
        started.append(kw)
        raise ar._agent_jobs.ShellResolutionError('reached start_job')

    monkeypatch.setattr(ar._agent_jobs, 'start_job', fake_start_job)
    for sid, trig in (('s-unatt', 'dispatch'), ('s-manual', 'manual')):
        ar.agent_sessions[sid] = {'project_id': 'p1', 'session_id': sid, 'status': 'idle',
                                  'trigger_type': trig}
    try:
        yield app.test_client(), started
    finally:
        ar.agent_sessions.pop('s-unatt', None)
        ar.agent_sessions.pop('s-manual', None)


def test_job_route_403s_a_fence_blocked_command_for_an_unattended_session(job_client):
    client, started = job_client
    r = client.post('/api/project/p1/agent/s-unatt/job', json={'command': BAD})
    assert r.status_code == 403
    body = r.get_json()
    assert body['fence_blocked'] is True and 'unattended fence' in body['error']
    assert started == []                          # nothing was spawned


def test_job_route_lets_a_benign_command_through_and_a_manual_session_untouched(job_client):
    client, started = job_client
    assert _reached(client.post('/api/project/p1/agent/s-unatt/job', json={'command': GOOD}))
    assert _reached(client.post('/api/project/p1/agent/s-manual/job', json={'command': BAD}))
    assert [k['command'] for k in started] == [GOOD, BAD]


def test_job_route_check_is_off_when_the_fence_setting_is_off(job_client, monkeypatch):
    client, started = job_client
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', False)
    assert _reached(client.post('/api/project/p1/agent/s-unatt/job', json={'command': BAD}))
    assert len(started) == 1


# -- MCP tool level -----------------------------------------------------------

def _host(server):
    return tools.ClayruneToolHost(tools.Gateway('http://127.0.0.1:5199', PID, opener=server))


def _call(host, name, args):
    res = host.call_tool(name, args)
    return res['isError'], json.loads(res['content'][0]['text'])


@pytest.mark.parametrize('name,args', [
    ('clayrune_send_message', {'target_session_id': 'bbbbbbbbbbbb', 'message': 'run: ' + BAD}),
    ('clayrune_dispatch_agent', {'task': 'run: ' + BAD}),
])
def test_tools_refuse_text_the_server_says_the_fence_blocks(name, args):
    server = FakeServer()
    server.fence_reply = (200, {'ok': True, 'armed': True, 'blocked': True, 'reason': 'force push'})
    err, out = _call(_host(server), name, args)
    assert err and 'unattended fence' in out['error'] and out['fence_blocked'] is True
    assert server.action_requests() == []         # the send / dispatch route was never hit


@pytest.mark.parametrize('reply', [(500, {'error': 'boom'}), (200, {'ok': True}), (403, {'ok': False})])
def test_tools_fail_closed_when_the_fence_check_cannot_complete(reply):
    server = FakeServer()
    server.fence_reply = reply
    err, out = _call(_host(server), 'clayrune_dispatch_agent', {'task': 'x'})
    assert err and 'could not be completed' in out['error']
    assert server.action_requests() == []


def test_tools_pass_clear_text_through_and_the_check_sees_the_exact_text():
    server = FakeServer()
    err, _ = _call(_host(server), 'clayrune_send_message',
                   {'target_session_id': 'bbbbbbbbbbbb', 'message': 'hello'})
    assert not err and len(server.action_requests()) == 1
    check = [r for r in server.requests if r[1] == FENCE_PATH][0]
    assert check[3] == {'project_id': PID, 'action': 'send_message', 'text': 'hello',
                        'target_session_id': 'bbbbbbbbbbbb'}


def test_start_job_tool_surfaces_the_routes_fence_403():
    server = FakeServer()
    server.routes[('POST', '/job')] = (403, {'error': 'blocked by the unattended fence: force push',
                                             'fence_blocked': True})
    err, out = _call(_host(server), 'clayrune_start_job', {'command': BAD})
    assert err is True
    assert out['http_status'] == 403 and out['response']['fence_blocked'] is True


# -- fence-check route: server-side verdict, real classifier ------------------

@pytest.fixture()
def fence_client(monkeypatch, tmp_path):
    app = Flask('fence-check-test')
    app.register_blueprint(clayrune_mcp_routes.bp)
    monkeypatch.setattr(clayrune_mcp_routes.project_routes, 'load_project',
                        lambda pid: {'id': pid, 'project_path': str(tmp_path)})
    monkeypatch.setattr(caller_attribution, 'attribute_caller',
                        lambda *_a, **_k: Attribution(ATTRIBUTED, ME, 1, ''))
    sess = {'project_id': PID, 'trigger_type': 'dispatch'}
    monkeypatch.setitem(state.agent_sessions, ME, sess)
    return app.test_client(), sess


@pytest.mark.parametrize('action', ['send_message', 'dispatch_agent'])
def test_fence_check_route_blocks_what_the_curl_way_would_for_unattended(fence_client, action):
    client, _ = fence_client
    base = {'project_id': PID, 'action': action, 'target_session_id': 'bbbbbbbbbbbb'}
    bad = client.post(FENCE_PATH, json={**base, 'text': 'run: ' + BAD}).get_json()
    assert bad['ok'] and bad['armed'] and bad['blocked'] and bad['reason']
    good = client.post(FENCE_PATH, json={**base, 'text': 'please review the diff'}).get_json()
    assert good == {'ok': True, 'armed': True, 'blocked': False, 'reason': ''}
    # parity: identical verdict to classifying the curl form directly
    path = '/agent/send' if action == 'send_message' else '/agent/dispatch'
    body = ({'session_id': 'bbbbbbbbbbbb', 'message': 'run: ' + BAD} if action == 'send_message'
            else {'task': 'run: ' + BAD})
    assert fence_parity.check_request(5199, f'/api/project/{PID}{path}', body)[0] is True


def test_fence_check_route_leaves_manual_alone_and_refuses_unverified_callers(fence_client, monkeypatch):
    client, sess = fence_client
    sess['trigger_type'] = 'manual'
    r = client.post(FENCE_PATH, json={'project_id': PID, 'action': 'dispatch_agent', 'text': BAD}).get_json()
    assert r == {'ok': True, 'armed': False, 'blocked': False, 'reason': ''}
    monkeypatch.setattr(caller_attribution, 'attribute_caller',
                        lambda *_a, **_k: Attribution(caller_attribution.UNATTRIBUTED, detail='x'))
    assert client.post(FENCE_PATH, json={'project_id': PID, 'action': 'dispatch_agent',
                                         'text': BAD}).status_code == 403
    assert client.post(FENCE_PATH, json={'project_id': PID, 'action': 'bogus', 'text': 'x'}).status_code == 400
