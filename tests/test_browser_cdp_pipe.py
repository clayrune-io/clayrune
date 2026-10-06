"""CDP over a pipe (mc/browser_cdp_pipe.py) and its wiring in the browser pane.

Backlog 6b313cb6 / Wren's P2b re-audit R1: a pane Chromium launched with a TCP
debugging port lets any local process read a filled sign-in credential. The
transport tests run against a scripted fake Chromium on a pair of OS pipes (no
browser); `test_browser_cdp_pipe_live.py` repeats the claim against a real one.
"""
import json
import os
import threading
import time

import pytest

websocket = pytest.importorskip('websocket')

from mc import browser_cdp_pipe as bp
from mc import state
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions


class FakeChromium:
    """The far end of the pipe: reads NUL-framed JSON, answers per ``script``.

    ``script(msg, emit)`` is called for every command; ``emit(obj)`` writes a
    message back to the transport (responses, events) in whatever order the
    test wants.
    """

    def __init__(self, script):
        self._c_r, self._c_w = os.pipe()     # transport writes _c_w, we read _c_r
        self._e_r, self._e_w = os.pipe()     # we write _e_w, transport reads _e_r
        self.script = script
        self.seen = []
        self.transport = bp.PipeTransport(self._e_r, self._c_w)
        threading.Thread(target=self._loop, daemon=True).start()

    def emit(self, obj):
        os.write(self._e_w, json.dumps(obj).encode() + b'\0')

    def eof(self):
        os.close(self._e_w)

    def wait_for(self, method, secs=2):
        """The first command with this method the fake has read off the pipe."""
        end = time.time() + secs
        while time.time() < end:
            for m in list(self.seen):
                if m.get('method') == method:
                    return m
            time.sleep(0.01)
        raise AssertionError(f'{method} never reached Chromium')

    def _loop(self):
        buf = b''
        while True:
            try:
                chunk = os.read(self._c_r, 65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b'\0' in buf:
                raw, buf = buf.split(b'\0', 1)
                msg = json.loads(raw)
                self.seen.append(msg)
                self.script(msg, self.emit)


def _answer(msg, emit, result=None):
    emit({'id': msg['id'], **({'sessionId': msg['sessionId']} if 'sessionId' in msg else {}),
          'result': result or {}})


def _recv_until(conn, pred, n=20):
    for _ in range(n):
        m = json.loads(conn.recv())
        if pred(m):
            return m
    raise AssertionError('message never arrived')


def test_two_clients_using_the_same_id_each_get_their_own_answer():
    def script(msg, emit):
        _answer(msg, emit, {'for': msg['method']})
    fc = FakeChromium(script)
    a, b = fc.transport.browser_conn(timeout=2), fc.transport.browser_conn(timeout=2)
    a.send(json.dumps({'id': 1, 'method': 'A.m'}))
    b.send(json.dumps({'id': 1, 'method': 'B.m'}))
    ra, rb = json.loads(a.recv()), json.loads(b.recv())
    assert (ra['id'], ra['result']) == (1, {'for': 'A.m'})
    assert (rb['id'], rb['result']) == (1, {'for': 'B.m'})
    # and Chromium saw two DIFFERENT ids on the wire
    assert len({m['id'] for m in fc.seen}) == 2


def test_page_conn_scopes_commands_and_unscopes_events():
    def script(msg, emit):
        if msg['method'] == 'Target.attachToTarget':
            _answer(msg, emit, {'sessionId': 'S1'})
        elif msg['method'] == 'Page.enable':
            _answer(msg, emit)
            emit({'method': 'Page.loadEventFired', 'sessionId': 'S1', 'params': {}})
            emit({'method': 'Page.screencastFrame', 'sessionId': 'CHILD', 'params': {}})
    fc = FakeChromium(script)
    # a child session owned by this page (auto-attach under S1)
    page = fc.transport.page_conn('T1', timeout=2)
    fc.emit({'method': 'Target.attachedToTarget', 'sessionId': 'S1',
             'params': {'sessionId': 'CHILD', 'targetInfo': {'targetId': 'T2', 'type': 'page'}}})
    page.send(json.dumps({'id': 7, 'method': 'Page.enable'}))
    # a command with no sessionId was given the page's
    assert fc.wait_for('Page.enable')['sessionId'] == 'S1'
    resp = _recv_until(page, lambda m: m.get('id') == 7)
    assert 'sessionId' not in resp, 'a page-level websocket carries no sessionId'
    ev = _recv_until(page, lambda m: m.get('method') == 'Page.loadEventFired')
    assert 'sessionId' not in ev
    child = _recv_until(page, lambda m: m.get('method') == 'Page.screencastFrame')
    assert child['sessionId'] == 'CHILD', 'a child session keeps its sessionId'


def test_events_go_only_to_the_connection_that_owns_the_session():
    def script(msg, emit):
        if msg['method'] == 'Target.attachToTarget':
            _answer(msg, emit, {'sessionId': 'S-' + msg['params']['targetId']})
    fc = FakeChromium(script)
    p1 = fc.transport.page_conn('T1', timeout=0.3)
    p2 = fc.transport.page_conn('T2', timeout=0.3)
    fc.emit({'method': 'Page.loadEventFired', 'sessionId': 'S-T1', 'params': {}})
    assert json.loads(p1.recv())['method'] == 'Page.loadEventFired'
    with pytest.raises(websocket.WebSocketTimeoutException):
        p2.recv()


def test_browser_level_auto_attach_events_go_to_the_connection_that_enabled_it():
    def script(msg, emit):
        _answer(msg, emit, {'sessionId': 'X1'} if msg['method'] == 'Target.attachToTarget' else {})
    fc = FakeChromium(script)
    guard = fc.transport.browser_conn(timeout=0.3)
    other = fc.transport.browser_conn(timeout=0.3)
    guard.send(json.dumps({'id': 1, 'method': 'Target.setAutoAttach',
                           'params': {'autoAttach': True, 'flatten': True}}))
    json.loads(guard.recv())
    fc.emit({'method': 'Target.attachedToTarget',
             'params': {'sessionId': 'A1', 'targetInfo': {'targetId': 'N1', 'type': 'page'}}})
    assert json.loads(guard.recv())['params']['sessionId'] == 'A1'
    with pytest.raises(websocket.WebSocketTimeoutException):
        other.recv()


def test_an_explicit_attach_event_goes_to_the_requester_not_the_auto_attach_holder():
    def script(msg, emit):
        if msg['method'] == 'Target.attachToTarget':
            # event first, response second: the order Chromium uses
            emit({'method': 'Target.attachedToTarget',
                  'params': {'sessionId': 'X1', 'targetInfo': {'targetId': 'T9', 'type': 'page'}}})
        _answer(msg, emit, {'sessionId': 'X1'} if msg['method'] == 'Target.attachToTarget' else {})
    fc = FakeChromium(script)
    guard = fc.transport.browser_conn(timeout=0.3)
    guard.send(json.dumps({'id': 1, 'method': 'Target.setAutoAttach',
                           'params': {'autoAttach': True, 'flatten': True}}))
    json.loads(guard.recv())
    asker = fc.transport.browser_conn(timeout=1)
    asker.send(json.dumps({'id': 1, 'method': 'Target.attachToTarget',
                           'params': {'targetId': 'T9', 'flatten': True}}))
    seen = [json.loads(asker.recv()) for _ in range(2)]
    assert {m.get('method') or 'resp' for m in seen} == {'Target.attachedToTarget', 'resp'}
    with pytest.raises(websocket.WebSocketTimeoutException):
        guard.recv()


@pytest.mark.parametrize('event_first', [True, False])
def test_the_transports_own_attach_is_not_shown_to_the_page_connection(event_first):
    def script(msg, emit):
        if msg['method'] != 'Target.attachToTarget':
            return
        ev = {'method': 'Target.attachedToTarget',
              'params': {'sessionId': 'S1', 'targetInfo': {'targetId': 'T1', 'type': 'page'}}}
        if event_first:
            emit(ev)
        _answer(msg, emit, {'sessionId': 'S1'})
        if not event_first:
            emit(ev)
    fc = FakeChromium(script)
    page = fc.transport.page_conn('T1', timeout=0.3)
    fc.emit({'method': 'Page.loadEventFired', 'sessionId': 'S1', 'params': {}})
    # the first thing the page sees is ITS event, not a stray attach for itself
    assert json.loads(page.recv())['method'] == 'Page.loadEventFired'


def test_closing_a_page_connection_detaches_its_session():
    def script(msg, emit):
        if msg['method'] == 'Target.attachToTarget':
            _answer(msg, emit, {'sessionId': 'S1'})
    fc = FakeChromium(script)
    page = fc.transport.page_conn('T1', timeout=1)
    page.close()
    assert fc.wait_for('Target.detachFromTarget')['params'] == {'sessionId': 'S1'}
    with pytest.raises(websocket.WebSocketConnectionClosedException):
        page.send('{}')


def test_pipe_eof_ends_every_connection_with_the_websocket_close_exception():
    fc = FakeChromium(lambda msg, emit: None)
    conn = fc.transport.browser_conn(timeout=2)
    fc.eof()
    with pytest.raises(websocket.WebSocketConnectionClosedException):
        conn.recv()
    with pytest.raises(websocket.WebSocketConnectionClosedException):
        conn.recv()                      # and stays closed
    with pytest.raises(bp.PipeClosed):
        fc.transport.request('Browser.getVersion', timeout=1)


def test_recv_times_out_with_the_websocket_timeout_exception():
    fc = FakeChromium(lambda msg, emit: None)
    conn = fc.transport.browser_conn(timeout=0.05)
    with pytest.raises(websocket.WebSocketTimeoutException):
        conn.recv()
    conn.settimeout(0.01)
    with pytest.raises(websocket.WebSocketTimeoutException):
        conn.recv()


def test_targets_and_version_have_the_json_endpoint_shape():
    def script(msg, emit):
        if msg['method'] == 'Target.getTargets':
            _answer(msg, emit, {'targetInfos': [
                {'targetId': 'U1', 'type': 'browser_ui', 'url': 'chrome://x', 'title': ''},
                {'targetId': 'P1', 'type': 'page', 'url': 'https://a.example/', 'title': 'A'}]})
        elif msg['method'] == 'Browser.getVersion':
            _answer(msg, emit, {'product': 'Chrome/1', 'userAgent': 'UA', 'protocolVersion': '1.3',
                                'jsVersion': '9', 'revision': 'r'})
    fc = FakeChromium(script)
    targets = fc.transport.targets()
    assert br._pick_page_target(targets, 'https://a.example/')['id'] == 'P1'
    ver = fc.transport.version()
    assert ver['User-Agent'] == 'UA' and ver['Browser'] == 'Chrome/1'


# ---- wiring in browser_routes ------------------------------------------------

@pytest.fixture
def pipe_cfg(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_cdp_pipe', True)


def test_the_pipe_is_off_unless_configured(monkeypatch):
    monkeypatch.delitem(state.CONFIG, 'browser_cdp_pipe', raising=False)
    assert br._cdp_transport_wanted(None, False) is False


def test_the_pipe_is_chosen_when_configured_but_not_for_the_port_aware_desk_pane(pipe_cfg):
    assert br._cdp_transport_wanted(None, False) is True
    # the discovery pane hands Chromium a proxy bypass for the debugging port
    assert br._cdp_transport_wanted(lambda p: [], True) is False
    assert br._cdp_transport_wanted(lambda p: [], False) is False
    assert br._cdp_transport_wanted(None, True) is False


class _FakeProc:
    pid = 4242

    def poll(self):
        return None


class _NoThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def _stub_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path / 'eph'))
    monkeypatch.setattr(br, '_named_profiles_root', lambda: str(tmp_path / 'named'))
    monkeypatch.setattr(br, '_swept_orphans', True)
    monkeypatch.setattr(br, '_find_chromium', lambda: 'C:/fake/chrome.exe')
    monkeypatch.setattr(br, '_import_ws', lambda: object())
    monkeypatch.setattr(br.threading, 'Thread', _NoThread)
    monkeypatch.setattr(br, '_register_process', None)
    browser_sessions.clear()
    got = {}

    def popen(args, **kw):
        got['popen'] = args
        return _FakeProc()

    def spawn(args, kw):
        got['spawn'] = args
        return _FakeProc(), 'TRANSPORT'
    monkeypatch.setattr(br.subprocess, 'Popen', popen)
    monkeypatch.setattr(bp, 'spawn', spawn)
    return got


def test_a_pipe_launch_has_no_debugging_port_anywhere(pipe_cfg, monkeypatch, tmp_path):
    got = _stub_launch(monkeypatch, tmp_path)
    session, err = br._launch_browser('proj', 'https://example.com', ephemeral=True)
    assert err is None
    assert 'popen' not in got, 'a pipe launch must not go through the plain Popen'
    assert not [a for a in got['spawn'] if 'remote-debugging' in a or 'remote-allow-origins' in a], got['spawn']
    assert session['port'] is None and session['cdp'] == 'TRANSPORT'
    browser_sessions.clear()


def test_a_port_launch_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.delitem(state.CONFIG, 'browser_cdp_pipe', raising=False)
    got = _stub_launch(monkeypatch, tmp_path)
    session, err = br._launch_browser('proj', 'https://example.com', ephemeral=True)
    assert err is None and 'spawn' not in got
    assert f'--remote-debugging-port={session["port"]}' in got['popen']
    assert '--remote-allow-origins=*' in got['popen']
    assert session['cdp'] is None
    browser_sessions.clear()


def test_the_desk_discovery_pane_stays_on_its_port_with_the_pipe_on(pipe_cfg, monkeypatch, tmp_path):
    got = _stub_launch(monkeypatch, tmp_path)
    session, err = br._launch_browser('proj', 'https://example.com', ephemeral=True,
                                      extra_args=lambda port: [f'--proxy-bypass-list=127.0.0.1:{port}'],
                                      narrow_remote_origins=True)
    assert err is None and 'spawn' not in got
    assert session['port'] and f'--remote-allow-origins=http://127.0.0.1:{session["port"]}' in got['popen']
    browser_sessions.clear()


def test_graceful_close_of_a_pipe_session_sends_browser_close_down_the_pipe():
    def script(msg, emit):
        _answer(msg, emit)
    fc = FakeChromium(script)

    class _Proc:
        def wait(self, timeout=None):
            return 0
    session = {'proc': _Proc(), 'cdp': fc.transport, 'session_id': 's'}
    assert br._graceful_close(session) is True
    fc.wait_for('Browser.close')


def test_teardown_closes_the_pipe_so_chromium_sees_eof():
    fc = FakeChromium(lambda msg, emit: _answer(msg, emit))
    conn = fc.transport.browser_conn(timeout=1)

    class _Proc:
        pid = 1

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0
    session = {'proc': _Proc(), 'cdp': fc.transport, 'profile': None, 'user_data_dir': None}
    br._kill_browser_session(session)
    assert fc.transport.closed
    with pytest.raises(websocket.WebSocketConnectionClosedException):
        conn.recv()


def test_cdp_helpers_route_a_pipe_session_through_its_transport_not_http(monkeypatch):
    import urllib.request

    def no_http(*a, **k):
        raise AssertionError('a pipe session must not open an HTTP connection')
    monkeypatch.setattr(urllib.request, 'urlopen', no_http)

    class _T:
        def targets(self):
            return [{'id': 'P1', 'type': 'page', 'url': 'about:blank', 'webSocketDebuggerUrl': 'pipe:P1'}]

        def version(self):
            return {'User-Agent': 'UA'}

        def page_conn(self, tid, timeout=None):
            return ('page', tid)

        def browser_conn(self, timeout=None):
            return ('browser',)
    s = {'cdp': _T(), 'port': None}
    assert br._cdp_targets(s)[0]['id'] == 'P1'
    assert br._cdp_version(s)['User-Agent'] == 'UA'
    assert br._cdp_page_conn(s, br._cdp_targets(s)[0]) == ('page', 'P1')
    assert br._cdp_browser_conn(s, {}) == ('browser',)
