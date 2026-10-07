"""Auth trace privacy, routing, lifecycle and real-reader wiring (no login)."""
import base64
import json
import queue

import pytest
from flask import Flask

from mc import browser_auth_diagnostics as diag
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions


SECRET = 'SENSITIVE_token_123456789'


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params=None, session_id=None):
        self.calls.append((method, params or {}, session_id))
        return len(self.calls)


def event(trace, sender, method, params, sid=None):
    msg = {'method': method, 'params': params}
    if sid:
        msg['sessionId'] = sid
    trace.handle(msg, sender)


def request_event(trace, sender, rid='r', url='https://jf.x.com/onboarding/web', sid=None):
    event(trace, sender, 'Network.requestWillBeSent', {
        'requestId': rid, 'request': {'url': url, 'method': 'POST',
                                    'headers': {'Cookie': SECRET, 'Authorization': SECRET},
                                    'postData': SECRET}}, sid)


@pytest.mark.parametrize('host', sorted(diag.HOSTS))
def test_request_projection_drops_secrets_and_query(host):
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender, url=f'https://user:{SECRET}@{host}/onboarding/web?token={SECRET}#{SECRET}')
    snapshot = trace.snapshot()
    assert snapshot['events'][0]['url'] == f'https://{host}/onboarding/web'
    assert snapshot['events'][0]['method'] == 'POST'
    saved = repr(vars(trace))
    for forbidden in (SECRET, 'Cookie', 'Authorization', 'postData', '?token=', 'user:'):
        assert forbidden not in saved


@pytest.mark.parametrize('url', ['https://example.com/login', 'https://x.com.evil.com/login',
                                'https://evilx.com/login', 'data:text/html,secret', 'http://['])
def test_other_origins_and_invalid_urls_are_not_retained(url):
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender, url=url)
    assert trace.snapshot()['events'] == []
    assert not trace.requests


def test_path_credentials_are_masked_too():
    assert diag.safe_url(f'https://x.com/i/flow/{SECRET}?code=foo') == 'https://x.com/i/flow/[redacted]'
    assert diag.safe_url('http://x.com:12345/login?token=foo') == 'http://x.com:12345/login'


@pytest.mark.parametrize('body', [
    json.dumps({'errors': [{'code': 32, 'message': f'Could not authenticate you token={SECRET}',
                           'token': SECRET, 'email': SECRET}], 'token': SECRET}),
    f'<html><script>token="{SECRET}"</script><p>Authentication failed token={SECRET}</p></html>',
    f'Jetfuel error: invalid credential {SECRET} https://x.com/callback?code={SECRET}',
    f'Authentication failed token="error" password=1234 email=person@example.com',
])
def test_failure_body_redaction_including_non_json(body):
    output = json.dumps(diag.error_body(body))
    assert SECRET not in output
    assert '1234' not in output and 'person@example.com' not in output
    assert 'authentication' in output or 'authenticate' in output or 'credential' in output
    if body.startswith('{'):
        assert diag.error_body(body)['errors'][0]['code'] == 32


@pytest.mark.parametrize('encoded', [False, True])
def test_failed_x_body_requested_only_after_finish_and_routed_to_popup(encoded):
    trace, sender = diag.AuthTrace(), Sender()
    trace.attach(sender, None, 'https://x.com/', 'root')
    trace.attach(sender, 'popup-sid', 'https://jf.x.com/onboarding/web', 'popup')
    request_event(trace, sender, sid='popup-sid')
    event(trace, sender, 'Network.responseReceived', {
        'requestId': 'r', 'response': {'url': 'https://jf.x.com/onboarding/web?token=' + SECRET,
                                     'status': 403, 'headers': {'Set-Cookie': SECRET}}}, 'popup-sid')
    assert not any(c[0] == 'Network.getResponseBody' for c in sender.calls)
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'}, 'popup-sid')
    assert sender.calls[-1] == ('Network.getResponseBody', {'requestId': 'r'}, 'popup-sid')
    body = json.dumps({'errors': [{'code': 32, 'message': 'Authentication failed ' + SECRET}], 'token': SECRET})
    payload = base64.b64encode(body.encode()).decode() if encoded else body
    trace.handle({'id': len(sender.calls), 'result': {'body': payload, 'base64Encoded': encoded}}, sender)
    last = trace.snapshot()['events'][-1]
    assert last['kind'] == 'failure_body' and last['status'] == 403
    assert last['body']['errors'][0]['code'] == 32
    assert SECRET not in repr(vars(trace))


@pytest.mark.parametrize('host,status', [('x.com', 200), ('x.com', 302), ('accounts.google.com', 403)])
def test_success_and_google_auth_bodies_never_requested(host, status):
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender, url=f'https://{host}/login')
    event(trace, sender, 'Network.responseReceived', {
        'requestId': 'r', 'response': {'url': f'https://{host}/login', 'status': status,
                                     'headers': {'Set-Cookie': SECRET}}})
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'})
    trace.handle({'id': 999, 'result': {'body': SECRET}}, sender)
    assert sender.calls == []
    assert SECRET not in repr(vars(trace))


@pytest.mark.parametrize('response,early', [(False, False), (True, False), (False, True), (True, True)])
def test_cookie_block_reasons_names_only_and_extra_info_order(response, early):
    trace, sender = diag.AuthTrace(), Sender()
    params = {'requestId': 'r', 'statusCode': 403, 'headers': {'Cookie': SECRET, 'Set-Cookie': SECRET}}
    params['blockedCookies' if response else 'associatedCookies'] = [
        {'cookie': {'name': 'auth_token', 'value': SECRET, 'domain': 'x.com'},
         'cookieLine': 'auth_token=' + SECRET, 'blockedReasons': ['DomainMismatch']},
        {'cookie': {'name': 'ct0', 'value': SECRET}, 'blockedReasons': []},
    ]
    method = 'Network.responseReceivedExtraInfo' if response else 'Network.requestWillBeSentExtraInfo'
    if early:
        event(trace, sender, method, params)
    request_event(trace, sender)
    if not early:
        event(trace, sender, method, params)
    rows = [e for e in trace.snapshot()['events'] if e['kind'] == 'blocked_cookies']
    assert rows[0]['cookies'] == [{'name': 'auth_token', 'reasons': ['DomainMismatch']}]
    assert SECRET not in repr(vars(trace))
    if response:
        event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'})
        assert sender.calls[-1][0] == 'Network.getResponseBody'


def test_cookie_name_fallback_never_saves_raw_set_cookie_line():
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender)
    event(trace, sender, 'Network.responseReceivedExtraInfo', {
        'requestId': 'r', 'blockedCookies': [{'cookieLine': f'ct0={SECRET}; Secure',
                                             'blockedReasons': ['SecureOnly']}]})
    assert trace.snapshot()['events'][-1]['cookies'][0]['name'] == 'ct0'
    assert SECRET not in repr(vars(trace))


@pytest.mark.parametrize('reason', ['PortMismatch', 'SchemeMismatch', 'AnonymousContext', 'NoCookieContent'])
def test_current_protocol_cookie_reasons_survive(reason):
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender)
    event(trace, sender, 'Network.responseReceivedExtraInfo', {
        'requestId': 'r', 'blockedCookies': [{'cookieLine': f'ct0={SECRET}', 'blockedReasons': [reason]}]})
    assert trace.snapshot()['events'][-1]['cookies'][0]['reasons'] == [reason]


def test_network_failure_keeps_known_error_enum_without_arbitrary_text():
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender)
    event(trace, sender, 'Network.loadingFailed', {'requestId': 'r', 'errorText': 'net::ERR_FAILED'})
    assert trace.snapshot()['events'][-1]['message'] == 'net::ERR_FAILED'
    event(trace, sender, 'Network.loadingFailed', {'requestId': 'r', 'errorText': 'error token=' + SECRET})
    assert SECRET not in repr(vars(trace))


def test_console_exception_keeps_type_and_sanitized_description():
    trace, sender = diag.AuthTrace(), Sender()
    trace.attach(sender, None, 'https://x.com/login', 'root')
    event(trace, sender, 'Runtime.exceptionThrown', {'exceptionDetails': {
        'text': 'Uncaught', 'exception': {'description': 'TypeError: Failed to fetch token=' + SECRET}}})
    assert trace.snapshot()['events'][-1]['message'] == 'uncaught typeerror failed to fetch [redacted]'


def test_target_request_ids_do_not_collide():
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender, url='https://x.com/login')
    request_event(trace, sender, url='https://accounts.google.com/signin', sid='popup')
    event(trace, sender, 'Network.responseReceived', {
        'requestId': 'r', 'response': {'url': 'https://accounts.google.com/signin', 'status': 403}}, 'popup')
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'})
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'}, 'popup')
    assert sender.calls == []


def test_already_attached_google_iframe_is_observed_without_new_attachment():
    trace, sender = diag.AuthTrace(), Sender()
    trace.attach(sender, None, 'https://x.com/login', 'root')
    event(trace, sender, 'Target.attachedToTarget', {'sessionId': 'frame', 'targetInfo': {
        'targetId': 'frame-target', 'type': 'iframe', 'url': 'https://accounts.google.com/gsi/button?client_id=' + SECRET}})
    request_event(trace, sender, sid='frame', url='https://accounts.google.com/gsi/button')
    last = trace.snapshot()['events'][-1]
    assert last['target'] == 'target_2' and last['url'] == 'https://accounts.google.com/gsi/button'
    assert not any(c[0] == 'Target.attachToTarget' for c in sender.calls)
    assert SECRET not in repr(vars(trace))


def test_stopped_capture_cannot_be_rearmed_by_stale_queued_enable():
    sender = Sender()
    old = diag.AuthTrace()
    new = diag.AuthTrace()
    session = {'auth_trace': new, 'tabs': {'root': {'session_id': None, 'url': 'https://x.com/login'}}}
    diag.control(session, old, True, sender)
    assert sender.calls == []
    diag.control(session, new, True, sender)
    assert len(sender.calls) == 4


def test_error_body_unavailable_is_visible_and_never_logs_payload(capsys):
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender)
    event(trace, sender, 'Network.responseReceived', {'requestId': 'r', 'response': {
        'url': 'https://jf.x.com/onboarding/web', 'status': 403}})
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'})
    trace.handle({'id': len(sender.calls), 'error': {'message': 'Request failed ' + SECRET}}, sender)
    assert trace.snapshot()['events'][-1]['kind'] == 'observer_error'
    assert SECRET not in repr(vars(trace)) and SECRET not in capsys.readouterr().out


def test_redirect_to_unlisted_host_cannot_retain_prior_failure_body():
    trace, sender = diag.AuthTrace(), Sender()
    request_event(trace, sender)
    event(trace, sender, 'Network.responseReceived', {'requestId': 'r', 'response': {
        'url': 'https://jf.x.com/onboarding/web', 'status': 403}})
    request_event(trace, sender, url='https://example.com/' + SECRET)
    event(trace, sender, 'Network.loadingFinished', {'requestId': 'r'})
    assert sender.calls == []
    assert SECRET not in repr(vars(trace))


def test_popup_lifecycle_blank_initial_url_and_navigation():
    trace, sender = diag.AuthTrace(), Sender()
    trace.attach(sender, None, 'https://x.com/login', 'root')
    event(trace, sender, 'Target.targetCreated', {'targetInfo': {
        'targetId': 'popup', 'openerId': 'root', 'type': 'page', 'url': 'about:blank'}})
    trace.attach(sender, 'p', 'about:blank', 'popup')
    event(trace, sender, 'Page.frameNavigated', {'frame': {'url': 'https://accounts.google.com/signin?token=' + SECRET}}, 'p')
    event(trace, sender, 'Target.targetDestroyed', {'targetId': 'popup'})
    event(trace, sender, 'Target.detachedFromTarget', {'sessionId': 'p', 'targetId': 'popup'})
    rows = [e for e in trace.snapshot()['events'] if e['kind'].startswith('popup_')]
    assert [r['kind'] for r in rows] == ['popup_open', 'popup_close']
    assert rows[-1]['url'] == 'https://accounts.google.com/signin'
    assert 'p' not in trace.sids and SECRET not in repr(vars(trace))


def test_console_and_fedcm_projection_never_saves_account_data():
    trace, sender = diag.AuthTrace(), Sender()
    trace.attach(sender, 'p', 'https://accounts.google.com/signin', 'popup')
    event(trace, sender, 'Runtime.exceptionThrown', {'exceptionDetails': {
        'text': f'Uncaught TypeError {SECRET}', 'url': f'https://accounts.google.com/signin?token={SECRET}',
        'exception': {'objectId': SECRET}, 'stackTrace': {'secret': SECRET}}}, 'p')
    event(trace, sender, 'Runtime.consoleAPICalled', {'type': 'error', 'args': [
        {'type': 'string', 'value': f'Authentication failed token={SECRET}'},
        {'type': 'object', 'description': SECRET, 'objectId': SECRET}]}, 'p')
    event(trace, sender, 'FedCm.dialogShown', {'dialogId': SECRET, 'dialogType': 'AccountChooser',
                                             'title': SECRET, 'accounts': [{'email': SECRET}]}, 'p')
    event(trace, sender, 'FedCm.dialogClosed', {'dialogId': SECRET}, 'p')
    rows = trace.snapshot()['events']
    assert len(rows) == 4
    assert rows[0]['message'] == 'uncaught typeerror [redacted]'
    assert rows[-2]['dialog_type'] == 'AccountChooser'
    assert SECRET not in repr(vars(trace))
    assert ('FedCm.enable', {'disableRejectionDelay': False}, 'p') in sender.calls


def test_bound_all_event_and_request_buffers():
    trace, sender = diag.AuthTrace(), Sender()
    for i in range(diag.LIMIT + 20):
        request_event(trace, sender, rid=str(i))
        event(trace, sender, 'Network.requestWillBeSentExtraInfo', {
            'requestId': f'extra{i}', 'associatedCookies': [{'cookie': {'name': 'ct0', 'value': SECRET},
                                                          'blockedReasons': ['DomainMismatch']}]})
    assert len(trace.events) == len(trace.requests) == len(trace.extra) == diag.LIMIT
    assert trace.snapshot()['dropped'] == 20


def test_stop_ignores_inflight_secrets_then_disables_every_attached_target():
    trace, sender = diag.AuthTrace(), Sender()
    trace.attach(sender, None, 'https://x.com/login', 'root')
    trace.attach(sender, 'p', 'https://accounts.google.com/signin', 'popup')
    request_event(trace, sender)
    trace.stop()
    trace.handle({'id': 1, 'result': {'body': SECRET}}, sender)
    trace.stop(sender)
    assert len([c for c in sender.calls if c[0].endswith('.disable')]) == 8
    assert not trace.on and not trace.requests and not trace.pending and not trace.sids
    assert SECRET not in repr(vars(trace))


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(br.bp)
    browser_sessions.clear()
    browser_sessions['pane'] = {'status': 'running', 'cmd_queue': queue.Queue()}
    browser_sessions['sibling'] = {'status': 'running', 'cmd_queue': queue.Queue()}
    yield app.test_client()
    browser_sessions.clear()


def test_routes_default_off_and_one_session_only(client):
    assert client.get('/api/browser/auth-trace?session_id=pane').get_json()['on'] is False
    assert 'auth_trace' not in browser_sessions['pane']
    assert client.post('/api/browser/auth-trace', json={'session_id': 'pane', 'on': True}).status_code == 202
    assert 'auth_trace' not in browser_sessions['sibling']
    assert browser_sessions['sibling']['cmd_queue'].empty()
    assert client.post('/api/browser/auth-trace', json={'session_id': 'pane', 'on': True}).status_code == 200
    assert browser_sessions['pane']['cmd_queue'].qsize() == 1
    assert client.post('/api/browser/auth-trace', json={'session_id': 'pane', 'on': False}).status_code == 202
    assert client.get('/api/browser/auth-trace?session_id=pane').get_json()['on'] is False


@pytest.mark.parametrize('data', [None, [], {}, {'session_id': 'pane', 'on': 'true'},
                               {'session_id': 'pane', 'on': 1}, {'session_id': 1, 'on': True}])
def test_invalid_opt_in_cannot_start_capture(client, data):
    assert client.post('/api/browser/auth-trace', json=data).status_code == 400
    assert 'auth_trace' not in browser_sessions['pane']


def test_unknown_stopped_and_ended_sessions(client):
    assert client.get('/api/browser/auth-trace?session_id=unknown').status_code == 404
    assert client.post('/api/browser/auth-trace', json={'session_id': 'unknown', 'on': True}).status_code == 404
    client.post('/api/browser/auth-trace', json={'session_id': 'pane', 'on': True})
    browser_sessions['pane']['status'] = 'stopped'
    assert client.get('/api/browser/auth-trace?session_id=pane').get_json()['on'] is False
    assert client.post('/api/browser/auth-trace', json={'session_id': 'pane', 'on': True}).status_code == 404


def test_reader_missing_dependency_also_turns_capture_off(monkeypatch):
    trace = diag.AuthTrace()
    session = {'status': 'running', 'auth_trace': trace}
    monkeypatch.setattr(br, '_import_ws', lambda: None)
    br._run_cdp(session)
    assert session['status'] == 'error'
    assert trace.on is False


@pytest.mark.parametrize('opt_in', [False, True])
def test_actual_cdp_reader_default_off_popup_enable_and_auto_off(monkeypatch, opt_in):
    session = {'status': 'running', 'port': None, 'url': 'https://x.com/login',
               'cmd_queue': queue.Queue()}
    trace = None
    if opt_in:
        trace = diag.AuthTrace()
        session['auth_trace'] = trace
        session['cmd_queue'].put(('_auth_trace', {'trace': trace, 'on': True}))
    messages = iter([
        {'method': 'Target.targetCreated', 'params': {'targetInfo': {
            'targetId': 'popup', 'openerId': 'root', 'type': 'page', 'url': 'https://accounts.google.com/signin'}}},
        {'method': 'Target.attachedToTarget', 'params': {'sessionId': 'p', 'targetInfo': {
            'targetId': 'popup', 'openerId': 'root', 'type': 'page', 'url': 'https://accounts.google.com/signin'}}},
        {'method': 'Network.requestWillBeSent', 'sessionId': 'p', 'params': {'requestId': 'r', 'request': {
            'url': 'https://accounts.google.com/signin?token=' + SECRET, 'method': 'POST', 'postData': SECRET}}},
    ])

    class Socket:
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(json.loads(text))

        def recv(self):
            msg = next(messages, None)
            if msg is None:
                session['status'] = 'stopped'
                return ''
            return json.dumps(msg)

        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    sock = Socket()
    monkeypatch.setattr(br, '_cdp_targets', lambda *a, **k: [{'id': 'root', 'type': 'page', 'webSocketDebuggerUrl': 'fake'}])
    monkeypatch.setattr(br, '_cdp_page_conn', lambda *a, **k: sock)
    monkeypatch.setattr(br, '_pin_root_window', lambda *a: None)
    monkeypatch.setattr(br, '_start_ua_guard', lambda *a: None)
    monkeypatch.setattr(br, '_fit_and_rearm', lambda *a, **k: None)
    br._run_cdp(session)
    enables = [m for m in sock.sent if m['method'] in {'Network.enable', 'Runtime.enable', 'Log.enable', 'FedCm.enable'}]
    if opt_in:
        assert len(enables) == 8
        assert len([m for m in enables if m.get('sessionId') == 'p']) == 4
        assert trace.on is False
        assert any(e['kind'] == 'request' for e in trace.events)
        assert SECRET not in repr(vars(trace))
    else:
        assert enables == []
        assert 'auth_trace' not in session
