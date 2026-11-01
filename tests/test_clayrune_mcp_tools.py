"""Tool behaviour + the identity boundary for the `clayrune` MCP server (BUILTINS-7a).

The boundary is tested FIRST and across every tool that acts for the caller:
no request reaches an action route unless the server has vouched for the
session, and no argument can name a different session.
"""
from __future__ import annotations

import io
import json
import socket
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from mc.caller_attribution import ATTRIBUTED, UNATTRIBUTED, UNAVAILABLE, Attribution
from mc.clayrune_mcp import identity, tools

PID = 'proj1'
ME = 'aaaaaaaaaaaa'
OTHER = 'bbbbbbbbbbbb'

IDENTITY_PATH = '/api/clayrune-mcp/identity'
FENCE_PATH = '/api/clayrune-mcp/fence-check'


class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._raw = json.dumps(payload).encode()

    def read(self, n=-1):
        return self._raw if n < 0 else self._raw[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeServer:
    """Stands in for urlopen: records every request, answers from a routing table."""

    def __init__(self, identity_reply=None):
        self.requests = []   # (method, path, query, body)
        self.identity_reply = identity_reply or (200, {'ok': True, 'session_id': ME,
                                                       'project_id': PID, 'label': 'Tester'})
        self.routes = {}     # (method, path-suffix) -> (status, payload) | Exception
        # Fence-check answer: manual / clear by default. Tests override it.
        self.fence_reply = (200, {'ok': True, 'armed': False, 'blocked': False, 'reason': ''})

    def __call__(self, req, timeout=None):
        u = urlparse(req.full_url)
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), u.path, parse_qs(u.query), body))
        if u.path == IDENTITY_PATH:
            status, payload = self.identity_reply
        elif u.path == FENCE_PATH:
            status, payload = self.fence_reply
        else:
            hit = next((v for (m, suf), v in self.routes.items()
                        if m == req.get_method() and u.path.endswith(suf)), (200, {'ok': True}))
            if isinstance(hit, Exception):
                raise hit
            status, payload = hit
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, 'err', {},  # type: ignore[arg-type]
                                         io.BytesIO(json.dumps(payload).encode()))
        return FakeResp(status, payload)

    def action_requests(self):
        return [r for r in self.requests if r[1] not in (IDENTITY_PATH, FENCE_PATH)]


def _host(server):
    return tools.ClayruneToolHost(tools.Gateway('http://127.0.0.1:5199', PID, opener=server))


def _call(host, name, args):
    res = host.call_tool(name, args)
    return res['isError'], json.loads(res['content'][0]['text'])


# ── server-side verification (identity.resolve_caller) ───────────────────────

SESSIONS = {ME: {'project_id': PID, 'character': {'agent_name': 'Tobin'}},
            OTHER: {'project_id': 'another'}}


def test_resolve_caller_accepts_attributed_session_in_bound_project():
    status, body = identity.resolve_caller(Attribution(ATTRIBUTED, ME, 1, ''), PID, SESSIONS)
    assert status == 200
    assert (body['session_id'], body['project_id'], body['label']) == (ME, PID, 'Tobin')


@pytest.mark.parametrize('att', [
    Attribution(UNATTRIBUTED, detail='human curl'),
    Attribution(UNAVAILABLE, detail='psutil missing'),
])
def test_resolve_caller_fails_closed_without_attribution(att):
    status, body = identity.resolve_caller(att, PID, SESSIONS)
    assert status == 403 and 'refused' in body['error']


def test_resolve_caller_refuses_a_session_from_another_project():
    status, body = identity.resolve_caller(Attribution(ATTRIBUTED, OTHER, 1, ''), PID, SESSIONS)
    assert status == 403 and 'project' in body['error']


def test_resolve_caller_refuses_a_vanished_session_and_a_missing_project():
    assert identity.resolve_caller(Attribution(ATTRIBUTED, 'gone', 1, ''), PID, SESSIONS)[0] == 403
    assert identity.resolve_caller(Attribution(ATTRIBUTED, ME, 1, ''), '', SESSIONS)[0] == 400


def _identity_client():
    from flask import Flask
    from mc.blueprints import clayrune_mcp_routes
    app = Flask(__name__)
    app.register_blueprint(clayrune_mcp_routes.bp)
    return app.test_client()


def test_identity_route_derives_session_from_attribution_not_body(monkeypatch):
    from mc import caller_attribution, state
    monkeypatch.setitem(state.agent_sessions, ME, {'project_id': PID})
    att = {'v': Attribution(ATTRIBUTED, ME, 1, '')}
    monkeypatch.setattr(caller_attribution, 'attribute_caller', lambda *a, **k: att['v'])
    client = _identity_client()
    # A body that names another session is simply ignored.
    r = client.post(IDENTITY_PATH, json={'project_id': PID, 'session_id': OTHER})
    assert r.status_code == 200 and r.get_json()['session_id'] == ME
    # Same caller, but this server was launched for a different project.
    assert client.post(IDENTITY_PATH, json={'project_id': 'another'}).status_code == 403
    # Unattributed caller (a human's own curl).
    att['v'] = Attribution(UNATTRIBUTED, detail='x')
    assert client.post(IDENTITY_PATH, json={'project_id': PID}).status_code == 403


def test_identity_route_real_attribution_refuses_a_peerless_request():
    """No monkeypatching: a Flask test-client request has no real peer socket, so
    the true attribution code path must land on a refusal, never an accept."""
    assert _identity_client().post(IDENTITY_PATH, json={'project_id': PID}).status_code == 403


# ── client-side boundary: arguments can never claim an identity ──────────────

ACTING = {
    'clayrune_send_message': {'target_session_id': OTHER, 'message': 'hi'},
    'clayrune_dispatch_agent': {'task': 't'},
    'clayrune_coord_publish': {'type': 'intention', 'summary': 's'},
    'clayrune_start_job': {'command': 'echo hi'},
    'clayrune_job_status': {'job_id': 'job1'},
}


@pytest.mark.parametrize('name', sorted(ACTING))
@pytest.mark.parametrize('forged', ['session_id', 'project_id', 'notify_session', 'agent'])
def test_identity_arguments_are_rejected_and_nothing_is_sent(name, forged):
    server = FakeServer()
    is_err, body = _call(_host(server), name, {**ACTING[name], forged: OTHER})
    assert is_err and 'identity' in body['error']
    assert server.requests == [], 'a rejected call must not touch the network at all'


@pytest.mark.parametrize('name', sorted(ACTING))
@pytest.mark.parametrize('reply', [
    (403, {'error': 'identity refused: caller not attributed'}),
    (200, {'ok': True, 'session_id': OTHER, 'project_id': 'another'}),   # wrong project
    (200, {'ok': True, 'project_id': PID}),                              # no session
    (200, {'ok': False}),
])
def test_acting_tools_send_no_action_request_unless_server_vouches(name, reply):
    server = FakeServer(identity_reply=reply)
    is_err, body = _call(_host(server), name, ACTING[name])
    assert is_err and body['error'].startswith('refused')
    assert server.action_requests() == []
    assert len(server.requests) == 1, 'only the identity probe may have gone out'


def test_every_request_is_scoped_to_the_launch_bound_project():
    server = FakeServer()
    host = _host(server)
    for name, args in ACTING.items():
        _call(host, name, args)
    for name in ('clayrune_list_agents', 'clayrune_coord_events', 'clayrune_delivery_status'):
        _call(host, name, {})
    assert len(server.action_requests()) == len(ACTING) + 3
    for _, path, _, body in server.action_requests():
        assert path.startswith(f'/api/project/{PID}/'), path
        assert 'project_id' not in (body or {})


# ── individual tools ─────────────────────────────────────────────────────────

def test_send_message_binds_callback_to_server_derived_session_and_reports_honestly():
    server = FakeServer()
    server.routes[('POST', '/agent/send')] = (200, {'ok': True, 'queued': True, 'session_id': OTHER, 'route': 'queue'})
    is_err, body = _call(_host(server), 'clayrune_send_message',
                         {'target_session_id': OTHER, 'message': 'hello'})
    method, path, _, sent = server.action_requests()[0]
    assert (method, path) == ('POST', f'/api/project/{PID}/agent/send')
    assert sent == {'session_id': OTHER, 'message': 'hello', 'notify_session': ME}
    assert not is_err and body['queued'] is True and body['route'] == 'queue'
    assert 'not confirmation' in body['note']
    assert len(server.action_requests()) == 1, 'no retry'


def test_send_message_without_reply_omits_the_callback():
    server = FakeServer()
    _call(_host(server), 'clayrune_send_message',
          {'target_session_id': OTHER, 'message': 'fyi', 'request_reply': False})
    assert 'notify_session' not in server.action_requests()[0][3]


def test_send_message_to_self_is_refused():
    server = FakeServer()
    is_err, body = _call(_host(server), 'clayrune_send_message',
                         {'target_session_id': ME, 'message': 'x'})
    assert is_err and 'itself' in body['error'] and server.action_requests() == []


def test_send_message_flags_a_fresh_dispatch_route():
    server = FakeServer()
    server.routes[('POST', '/agent/send')] = (200, {'ok': True, 'session_id': 'new', 'route': 'dispatch'})
    _, body = _call(_host(server), 'clayrune_send_message', {'target_session_id': 'ghost', 'message': 'x'})
    assert 'started or revived' in body['note']


def test_http_error_from_server_is_an_error_result_with_the_body():
    server = FakeServer()
    server.routes[('POST', '/agent/send')] = (404, {'error': 'project not found'})
    is_err, body = _call(_host(server), 'clayrune_send_message', {'target_session_id': OTHER, 'message': 'x'})
    assert is_err and body['http_status'] == 404 and body['response'] == {'error': 'project not found'}


def test_timeout_reports_unknown_outcome_and_does_not_retry():
    server = FakeServer()
    server.routes[('POST', '/agent/send')] = socket.timeout()
    is_err, body = _call(_host(server), 'clayrune_send_message', {'target_session_id': OTHER, 'message': 'x'})
    assert is_err and body['outcome'] == 'unknown' and 'NOT retried' in body['error']
    assert len(server.action_requests()) == 1


def test_unreachable_server_reports_nothing_sent():
    server = FakeServer()
    server.routes[('POST', '/agent/send')] = urllib.error.URLError(ConnectionRefusedError())
    _, body = _call(_host(server), 'clayrune_send_message', {'target_session_id': OTHER, 'message': 'x'})
    assert body['outcome'] == 'not_sent'


def test_dispatch_agent_binds_callback_and_forwards_only_existing_options():
    server = FakeServer()
    _call(_host(server), 'clayrune_dispatch_agent',
          {'task': 'do it', 'character': 'project:code-reviewer', 'effort': 'high'})
    method, path, _, sent = server.action_requests()[0]
    assert (method, path) == ('POST', f'/api/project/{PID}/agent/dispatch')
    assert sent == {'task': 'do it', 'notify_session': ME,
                    'character': 'project:code-reviewer', 'effort': 'high'}
    assert 'source' not in sent and 'incognito' not in sent


def test_coord_publish_stamps_server_derived_identity():
    server = FakeServer()
    _call(_host(server), 'clayrune_coord_publish',
          {'type': 'completion', 'summary': 'landed', 'commit_sha': 'abc', 'targets': ['a.py']})
    sent = server.action_requests()[0][3]
    assert sent == {'type': 'completion', 'summary': 'landed', 'agent': 'Tester',
                    'session_id': ME, 'targets': ['a.py'], 'commit_sha': 'abc'}


def test_start_job_and_job_status_use_the_callers_own_session_path():
    server = FakeServer()
    host = _host(server)
    _call(host, 'clayrune_start_job', {'command': 'sleep 1', 'timeout_minutes': 2, 'shell': 'bash'})
    _call(host, 'clayrune_job_status', {'job_id': 'j-1'})
    (m1, p1, _, b1), (m2, p2, _, _) = server.action_requests()
    assert (m1, p1) == ('POST', f'/api/project/{PID}/agent/{ME}/job')
    assert b1 == {'command': 'sleep 1', 'timeout_minutes': 2, 'shell': 'bash'}
    assert (m2, p2) == ('GET', f'/api/project/{PID}/agent/{ME}/job/j-1')


def test_start_job_passes_through_existing_sandbox_refusal():
    server = FakeServer()
    server.routes[('POST', '/job')] = (403, {'error': 'this session is running under the Codex unattended sandbox'})
    is_err, body = _call(_host(server), 'clayrune_start_job', {'command': 'x'})
    assert is_err and body['http_status'] == 403 and 'sandbox' in body['response']['error']


def test_reads_do_not_require_identity_and_list_agents_marks_you():
    server = FakeServer(identity_reply=(403, {'error': 'no'}))
    server.routes[('GET', '/coord/roster')] = (200, {'enabled': False, 'live_agents': []})
    is_err, body = _call(_host(server), 'clayrune_list_agents', {})
    assert not is_err and body['you'] is None and body['response']['live_agents'] == []
    _call(_host(server), 'clayrune_coord_events', {'limit': 5})
    assert server.action_requests()[-1][2] == {'limit': ['5']}


def test_list_agents_marks_the_caller_when_identity_is_confirmed():
    server = FakeServer()
    _, body = _call(_host(server), 'clayrune_list_agents', {})
    assert body['you'] == ME


def test_delivery_status_event_vs_list_and_bounds():
    server = FakeServer()
    host = _host(server)
    _call(host, 'clayrune_delivery_status', {'event_id': 'ev1'})
    _call(host, 'clayrune_delivery_status', {'limit': 7})
    reqs = server.action_requests()
    assert reqs[0][1].endswith('/agent/delegation/status') and reqs[0][2] == {'event_id': ['ev1']}
    assert reqs[1][1].endswith('/status-list') and reqs[1][2] == {'limit': ['7'], 'offset': ['0']}
    is_err, _ = _call(host, 'clayrune_delivery_status', {'limit': 1000})
    assert is_err
    assert len(server.action_requests()) == 2


def test_argument_validation():
    server = FakeServer()
    host = _host(server)
    for name, args in [('clayrune_send_message', {'message': 'x'}),                       # missing target
                       ('clayrune_send_message', {'target_session_id': '../x', 'message': 'x'}),
                       ('clayrune_send_message', {'target_session_id': OTHER, 'message': 'x' * 20001}),
                       ('clayrune_coord_publish', {'type': 'bogus', 'summary': 's'}),
                       ('clayrune_start_job', {'command': 'x', 'shell': 'zsh'}),
                       ('clayrune_job_status', {'job_id': 'a/b'}),
                       ('clayrune_dispatch_agent', {'task': 5})]:
        is_err, _ = _call(host, name, args)
        assert is_err, (name, args)
    assert server.requests == []


def test_oversized_server_reply_is_discarded():
    class Big:
        status = 200

        def read(self, n=-1):
            return b'x' * (tools.MAX_RESPONSE_BYTES + 1)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    gw = tools.Gateway('http://127.0.0.1:5199', PID, opener=lambda req, timeout=None: Big())
    is_err, body = _call(tools.ClayruneToolHost(gw), 'clayrune_coord_events', {})
    assert is_err and 'exceeded' in body['error']


def test_gateway_only_accepts_loopback_http():
    for bad in ('https://127.0.0.1:5199', 'http://example.com:5199', 'http://127.0.0.1'):
        with pytest.raises(ValueError):
            tools.Gateway(bad, PID)
    with pytest.raises(ValueError):
        tools.Gateway('http://127.0.0.1:5199', '../etc')
