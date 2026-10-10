"""Explicit X 2xx diagnostic capture: eligibility, projection and revocation."""
import base64
import json
import queue

import pytest
from flask import Flask

from mc import browser_auth_diagnostics as diag
from mc import browser_auth_body_projection as projection
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions
from tests.test_browser_auth_diagnostics import Sender, event, request_event


ALLOWED = ('https://jf.x.com/onboarding/web/credential',
           'https://api.x.com/1.1/flow/task.json')


def finish(trace, sender, url, status=200, sid='popup', rid='r'):
    before = len(sender.calls)
    request_event(trace, sender, url=url, sid=sid, rid=rid)
    event(trace, sender, 'Network.responseReceived', {
        'requestId': rid, 'response': {'url': url, 'status': status}}, sid)
    assert len(sender.calls) == before
    event(trace, sender, 'Network.loadingFinished', {'requestId': rid}, sid)


@pytest.mark.parametrize('url', ALLOWED)
def test_default_does_not_request_allowed_success_bodies(url):
    trace, sender = diag.AuthTrace(), Sender()
    finish(trace, sender, url)
    assert sender.calls == []
    assert trace.snapshot()['x_success_bodies'] is False


@pytest.mark.parametrize('url', ALLOWED)
@pytest.mark.parametrize('status', [200, 201, 204, 299])
@pytest.mark.parametrize('encoded', [False, True])
def test_opt_in_requests_only_after_finish_and_projects_2xx(url, status, encoded):
    trace, sender = diag.AuthTrace(x_success_bodies=True), Sender()
    body = json.dumps({'data': {'subtasks': [{'errors': [{'code': 32,
        'message': 'An unexpected error occurred. Please try again.'}],
        'flow_token': 'SECRET', 'user_id': 123456789}]}})
    finish(trace, sender, url, status)
    assert sender.calls == [('Network.getResponseBody', {'requestId': 'r'}, 'popup')]
    payload = base64.b64encode(body.encode()).decode() if encoded else body
    trace.handle({'id': 1, 'result': {'body': payload, 'base64Encoded': encoded}}, sender)
    last = trace.snapshot()['events'][-1]
    assert last['kind'] == 'success_body' and last['status'] == status
    assert last['body']['errors'][0]['code'] == 32
    assert 'unexpected error' in last['body']['errors'][0]['message']
    assert 'SECRET' not in repr(vars(trace)) and '123456789' not in repr(vars(trace))


@pytest.mark.parametrize('url', [
    'https://jf.x.com/onboarding/web', 'https://jf.x.com/onboarding/web-other/task',
    'https://jf.x.com/other/web/task', 'https://api.x.com/1.1/flow',
    'https://api.x.com/1.1/flow-other/task', 'https://api.x.com/2/flow/task',
    'https://x.com/onboarding/web/task', 'https://accounts.google.com/onboarding/web/task',
    'https://jf.x.com.evil.test/onboarding/web/task', 'http://jf.x.com/onboarding/web/task',
    'https://jf.x.com:8443/onboarding/web/task', 'https://user@jf.x.com/onboarding/web/task',
    'https://jf.x.com/onboarding/%77eb/task', 'https://api.x.com/1.1/%66low/task',
])
def test_flag_does_not_expand_body_capture_to_other_urls(url):
    trace, sender = diag.AuthTrace(x_success_bodies=True), Sender()
    finish(trace, sender, url)
    assert sender.calls == []


@pytest.mark.parametrize('status', [100, 199, 300, 302, 399])
def test_non_success_non_failure_responses_are_not_read(status):
    trace, sender = diag.AuthTrace(x_success_bodies=True), Sender()
    finish(trace, sender, ALLOWED[0], status)
    assert sender.calls == []


def test_response_url_controls_capture_redirect_and_attachment_isolation():
    trace, sender = diag.AuthTrace(x_success_bodies=True), Sender()
    request_event(trace, sender, url=ALLOWED[0], sid='popup')
    event(trace, sender, 'Network.responseReceived', {'requestId': 'r', 'response': {
        'url': 'https://accounts.google.com/signin', 'status': 200}}, 'popup')
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'}, 'popup')
    assert sender.calls == []
    finish(trace, sender, ALLOWED[1])
    request_event(trace, sender, url='https://api.x.com/other', sid='popup')
    event(trace, sender, 'Network.responseReceivedExtraInfo', {'requestId': 'r', 'statusCode': 200}, 'popup')
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'}, 'popup')
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'}, 'root')
    assert len(sender.calls) == 1
    assert ('popup', 'r') not in trace.success_requests


def test_projection_redacts_contacts_secret_keys_and_unknown_identifiers():
    body = json.dumps({'errors': [{'code': 'invalid_token', 'message':
        'Authentication failed person@example.com +1 (555) 123-4567 user_123 csrf=SECRET '
        'token="error" cookie="invalid" flow_token=SECRET randomOpaqueString',
        'email': 'person@example.com', 'phone': '+15551234567', 'id': 'SECRET'}],
        'flow_token': 'error', 'csrf': 'invalid', 'token': 'success',
        'cookies': {'message': 'SECRET'}, 'account': {'errors': [{'code': 123456}]}})
    output = json.dumps(projection.success_body(body))
    for secret in ('person@example.com', '555', '123-4567', 'user_123', 'SECRET',
                   'randomOpaqueString', 'flow_token', 'cookies', '123456'):
        assert secret not in output
    assert 'authentication failed' in output and 'invalid_token' in output


def test_plain_text_redacts_quoted_json_secret_values_and_identifier_assignments():
    result = projection.success_body('Authentication failed csrf="error invalid" '
        'phone="error expired" user_id="success denied" '
        '"flow_token": "login successful" "cookie": "consent required"')
    assert result['message'] == 'authentication failed [redacted]'


def test_known_flow_denial_is_projected_without_retaining_arbitrary_subtask_ids():
    result = projection.success_body(json.dumps({'subtasks': [
        {'subtask_id': 'DenyLoginSubtask'}, {'subtask_id': 'LoginSuccessSubtask'},
        {'subtask_id': 'SECRET_user_123'}]}))
    assert result['errors'] == [{'subtask_id': 'login denied'},
                                {'subtask_id': 'login successful'},
                                {'subtask_id': '[redacted]'}]


def test_failure_capture_remains_available_with_success_flag():
    trace, sender = diag.AuthTrace(x_success_bodies=True), Sender()
    finish(trace, sender, 'https://x.com/login', 403)
    assert sender.calls[-1][0] == 'Network.getResponseBody'
    trace.handle({'id': 1, 'result': {'body': '{"message":"authentication failed"}'}}, sender)
    assert trace.events[-1]['kind'] == 'failure_body'


@pytest.mark.parametrize('body', [
    json.dumps({'errors': [{'message': 'error ' * 300, 'detail': 'failed ' * 300,
                           'title': 'invalid ' * 300}] * 8}),
    'error ' * (projection.BODY_LIMIT // 2),
    json.dumps({'token': 'error ' * projection.BODY_LIMIT}),
    '{"token": "error invalid success"',
    '[' * 2000 + '0' + ']' * 2000,
    json.dumps({'errors': [{'message': '\u2603' * projection.BODY_LIMIT}]}),
], ids=['json_output_cap', 'text_input_cap', 'cut_secret_json', 'malformed_secret_json',
        'deep_json', 'unicode_input_cap'])
def test_body_projection_has_byte_cap_even_when_cut_or_unparseable(body):
    result = projection.success_body(body)
    assert len(json.dumps(result).encode('utf-8')) <= projection.SUCCESS_BODY_LIMIT
    assert isinstance(result['truncated'], bool)
    if body.startswith('{"token"'):
        assert result['errors'] == []


def test_success_eligibility_storage_is_bounded_and_stop_resets_flag():
    trace, sender = diag.AuthTrace(x_success_bodies=True), Sender()
    for i in range(diag.LIMIT + 20):
        request_event(trace, sender, url=ALLOWED[0], rid=str(i))
        event(trace, sender, 'Network.responseReceived', {'requestId': str(i),
            'response': {'url': ALLOWED[0], 'status': 200}})
    assert len(trace.success_requests) == diag.LIMIT
    trace.stop()
    assert not trace.x_success_bodies and not trace.success_requests


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(br.bp)
    browser_sessions.clear()
    browser_sessions['pane'] = {'status': 'running', 'cmd_queue': queue.Queue()}
    yield app.test_client()
    browser_sessions.clear()


def test_route_default_flag_on_repeat_enable_revocation_off_and_fresh_start(client):
    assert client.get('/api/browser/auth-trace?session_id=pane').get_json()['x_success_bodies'] is False
    url = '/api/browser/auth-trace'
    enabled = {'session_id': 'pane', 'on': True, 'x_success_bodies': True}
    assert client.post(url, json=enabled).get_json()['x_success_bodies'] is True
    trace = browser_sessions['pane']['auth_trace']
    sender = Sender()
    finish(trace, sender, ALLOWED[0])
    assert client.post(url, json={'session_id': 'pane', 'on': True}).status_code == 200
    trace.handle({'id': 1, 'result': {'body': '{"message":"error"}'}}, sender)
    assert not any(e['kind'] == 'success_body' for e in trace.events)
    assert client.post(url, json=enabled).get_json()['x_success_bodies'] is True
    assert browser_sessions['pane']['auth_trace'] is trace
    assert browser_sessions['pane']['cmd_queue'].qsize() == 1
    assert client.post(url, json={'session_id': 'pane', 'on': False,
                                 'x_success_bodies': True}).get_json()['x_success_bodies'] is False
    assert not trace.pending and not trace.success_requests
    assert client.post(url, json={'session_id': 'pane', 'on': True}).status_code == 202
    assert browser_sessions['pane']['auth_trace'] is not trace
    assert client.get(url + '?session_id=pane').get_json()['x_success_bodies'] is False


@pytest.mark.parametrize('value', ['true', 1, 0, None, [], {}])
def test_route_rejects_non_boolean_flag_without_changing_capture(client, value):
    response = client.post('/api/browser/auth-trace', json={
        'session_id': 'pane', 'on': True, 'x_success_bodies': value})
    assert response.status_code == 400
    assert 'auth_trace' not in browser_sessions['pane']
