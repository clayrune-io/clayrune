"""`signin_fill_cdp.connect` on a pipe session (backlog 6b313cb6).

The Desk sign-in fill used to dial `session['port']` itself, so a pane launched with
`--remote-debugging-pipe` (no port at all) could not be filled and the pipe had to stay off by default.
`connect` now goes through the pane's transport-neutral helpers. These run the real `PipeTransport`
against a scripted fake Chromium (no browser); `test_desk_signin_fill_pipe_live.py` repeats the claim
against a real one."""
import urllib.request

import pytest

pytest.importorskip('websocket')

from mc.desk_connect import signin_fill_cdp
from tests.test_browser_cdp_pipe import FakeChromium, _answer

LOGIN = 'https://www.linkedin.com/login'


def _chromium():
    def script(msg, emit):
        m = msg['method']
        if m == 'Target.getTargets':
            _answer(msg, emit, {'targetInfos': [
                {'targetId': 'U1', 'type': 'browser_ui', 'url': 'chrome://x', 'title': ''},
                {'targetId': 'P1', 'type': 'page', 'url': LOGIN, 'title': 'Sign in'}]})
        elif m == 'Target.attachToTarget':
            _answer(msg, emit, {'sessionId': 'S1'})
        elif m == 'Page.getFrameTree':
            _answer(msg, emit, {'frameTree': {'frame': {'id': 'F1', 'url': LOGIN,
                                                        'securityOrigin': 'https://www.linkedin.com'}}})
        elif m == 'Page.createIsolatedWorld':
            _answer(msg, emit, {'executionContextId': 7})
        elif m == 'Runtime.evaluate':
            _answer(msg, emit, {'result': {'type': 'object', 'value': {'state': 'filled'}}})
    return FakeChromium(script)


def test_connect_reads_and_types_through_the_pipe_with_no_port_and_no_http(monkeypatch):
    def no_http(*a, **k):
        raise AssertionError('a pipe session must not open an HTTP connection')
    monkeypatch.setattr(urllib.request, 'urlopen', no_http)
    fc = _chromium()
    session = {'cdp': fc.transport, 'port': None, 'live_url': LOGIN}

    with signin_fill_cdp.connect(session, timeout=2) as link:
        assert link.top() == {'url': LOGIN, 'origin': 'https://www.linkedin.com',
                              'frame_id': 'F1', 'unreachable': False}
        assert link.evaluate('1+1') == (True, {'state': 'filled'})

    attach = fc.wait_for('Target.attachToTarget')
    assert attach['params']['targetId'] == 'P1', 'attached to the page, not the browser_ui target'
    ev = fc.wait_for('Runtime.evaluate')
    assert ev['sessionId'] == 'S1' and ev['params']['contextId'] == 7 and ev['params']['expression'] == '1+1'
    world = fc.wait_for('Page.createIsolatedWorld')
    assert world['params']['frameId'] == 'F1' and world['params']['grantUniveralAccess'] is False


def test_connect_on_a_pipe_session_with_no_page_target_is_a_link_error():
    def script(msg, emit):
        if msg['method'] == 'Target.getTargets':
            _answer(msg, emit, {'targetInfos': [{'targetId': 'U1', 'type': 'browser_ui', 'url': 'chrome://x', 'title': ''}]})
    session = {'cdp': FakeChromium(script).transport, 'port': None}
    with pytest.raises(signin_fill_cdp.LinkError) as e:
        signin_fill_cdp.connect(session, timeout=2)
    assert str(e.value) == 'no_page_target'


def test_connect_on_a_port_session_still_goes_through_the_port(monkeypatch):
    """The Desk discovery pane stays on a port; the helper must still reach it there."""
    from mc.blueprints import browser_routes as br
    seen = {}

    def targets(session, timeout=2):
        seen['targets'] = session
        return [{'id': 'P1', 'type': 'page', 'url': LOGIN, 'webSocketDebuggerUrl': 'ws://127.0.0.1:9/devtools/page/P1'}]

    def conn(session, page, timeout=5):
        seen['conn'] = (session, page['id'], timeout)
        return object()
    monkeypatch.setattr(br, '_cdp_targets', targets)
    monkeypatch.setattr(br, '_cdp_page_conn', conn)
    session = {'cdp': None, 'port': 9, 'live_url': LOGIN}
    signin_fill_cdp.connect(session, timeout=3)
    assert seen['targets'] is session and seen['conn'] == (session, 'P1', 3)
