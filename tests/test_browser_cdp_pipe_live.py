"""The pane's CDP-over-pipe transport against a REAL Chromium, with the default config (backlog 6b313cb6).

The fake-Chromium tests (`test_browser_cdp_pipe.py`, `test_desk_signin_fill_pipe.py`) pin the wiring;
these prove the claims that need a browser: launch / screencast / navigate / read / popup / fit /
named-profile graceful close all work with NO debugging port, a local process cannot attach, and the
Desk sign-in fill still types into a page on a pipe session.

Everything runs against a page served from 127.0.0.1 -- never a third-party site -- in a throwaway
profile root under pytest's tmp_path (the operator's real profiles are never touched). The Chromium is
launched through the pane's own `_launch_browser` and stopped through its own teardown. Skipped when
Chromium or websocket-client is unavailable."""
import http.server
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import pytest
from flask import Flask

pytest.importorskip('websocket')

from mc import state
from mc.blueprints import browser_routes as br
from mc.desk_connect import signin_fill, signin_fill_cdp
from mc.state import browser_sessions

HUMAN = {'Origin': 'http://localhost:5199'}
USER, PW = 'live-pipe-user@example.test', 'Pw-live-pipe-9d41'

FORM = """<!doctype html><title>Sign in</title><body style="margin:0">
<form action="/login" method="post" style="position:absolute;left:10px;top:100px">
  <input name="u" type="text" autocomplete="username"><input name="p" type="password" autocomplete="current-password">
  <button type="submit">Sign in</button></form>
<a id="pop" href="/popup" target="_blank" style="position:absolute;left:10px;top:10px;width:200px;height:50px;background:#cde">popup</a>
</body>"""
OTHER = '<!doctype html><title>Other</title><body><p>pipe-live visible text</p></body>'
POPUP = '<!doctype html><title>Popup</title><body><p>popup page</p></body>'


class _Site:
    def __init__(self):
        self.posts, self.uas = [], {}
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, body):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self):
                site.uas.setdefault(self.path, self.headers.get('User-Agent', ''))
                self._send({'/': FORM, '/other': OTHER, '/popup': POPUP}.get(self.path.split('?')[0], 'ok'))

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                site.posts.append((self.path, dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))))
                self._send('<!doctype html><title>Welcome</title>signed in')

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.srv.shutdown()


@pytest.fixture
def pane(monkeypatch, tmp_path):
    if not br._find_chromium() or br._import_ws() is None:
        pytest.skip('Chromium or websocket-client not available')
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path / 'eph'))
    monkeypatch.setattr(br, '_named_profiles_root', lambda: str(tmp_path / 'named'))
    monkeypatch.setattr(br, '_swept_orphans', True)
    monkeypatch.setattr(br, '_SERVER_PORT', 5199)          # the guard fails closed on loopback until it knows its own port
    monkeypatch.setattr(br, '_register_process', None)
    monkeypatch.setattr(br, '_unregister_process', None)
    monkeypatch.delitem(state.CONFIG, 'browser_cdp_pipe', raising=False)      # the DEFAULT, not a test override
    browser_sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(br.bp)
    launched = []

    def launch(url, **kw):
        session, err = br._launch_browser('live-pipe-test', url, **kw)
        assert err is None, err
        launched.append(session)
        return session
    yield app.test_client(), launch
    for s in launched:
        try:
            br._kill_browser_session(s)
        except Exception:
            pass
    browser_sessions.clear()


def _wait(pred, secs=15, what='condition'):
    end = time.time() + secs
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(f'{what} never became true')


def _eval(session, expr):
    """`Runtime.evaluate` on the pane's page; the page target is not listed for a moment after launch."""
    end = time.time() + 15
    while True:
        ok, val = br._cdp_evaluate(session, expr, timeout=8)
        if ok or val != 'no_page_target' or time.time() > end:
            assert ok, val
            return val
        time.sleep(0.2)


def _tree_of(pid):
    """(pids in the process tree rooted at `pid`, {pid: command line}) -- psutil is not a dependency."""
    if sys.platform == 'win32':
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json'],
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stdout
        rows = [(r['ProcessId'], r['ParentProcessId'], r['CommandLine'] or '') for r in json.loads(out)]
    else:
        out = subprocess.run(['ps', '-eo', 'pid=,ppid=,args='], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).stdout
        rows = [(int(a), int(b), c) for a, b, c in (l.strip().split(None, 2) for l in out.splitlines() if l.strip())]
    kids, cmd = {}, {}
    for p, pp, c in rows:
        kids.setdefault(pp, []).append(p)
        cmd[p] = c
    seen, todo = set(), [pid]
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo.extend(kids.get(p, []))
    return seen, {p: cmd.get(p, '') for p in seen}


def _listening_ports(pids):
    """TCP ports in LISTEN state owned by one of `pids`."""
    if sys.platform == 'win32':
        out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).stdout
        return sorted({int(m.group(1)) for l in out.splitlines()
                       if (m := re.search(r':(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$', l)) and int(m.group(2)) in pids})
    out = subprocess.run(['ss', '-ltnpH'], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).stdout
    return sorted({int(m.group(1)) for l in out.splitlines()
                   if (m := re.search(r':(\d+)\s', l)) and any(f'pid={p},' in l for p in pids)})


# ---- the default is the pipe, and nothing can attach -------------------------

def test_default_launch_has_no_port_and_a_local_process_cannot_attach(pane, site):
    client, launch = pane
    s = launch(site.base + '/other')
    assert s['port'] is None and s['cdp'] is not None
    pids, cmd = _tree_of(s['proc'].pid)
    assert [c for c in cmd.values() if 'remote-debugging-port' in c or 'remote-allow-origins' in c] == []
    assert [c for c in cmd.values() if '--remote-debugging-pipe' in c], 'the pane Chromium should be on the pipe'
    # the attacker's recipe: any listener owned by the browser, ask it for /json/version
    assert _listening_ports(pids) == []


def test_control_the_same_attack_works_on_a_port_launch(pane, site, monkeypatch):
    """Without the pipe the debugging port is discoverable from the command line and a second client attaches."""
    client, launch = pane
    monkeypatch.setitem(state.CONFIG, 'browser_cdp_pipe', False)
    s = launch(site.base + '/other')
    _pids, cmd = _tree_of(s['proc'].pid)
    ports = {int(m.group(1)) for c in cmd.values() for m in [re.search(r'--remote-debugging-port=(\d+)', c)] if m}
    assert s['port'] in ports
    ver = json.load(urllib.request.urlopen(f'http://127.0.0.1:{s["port"]}/json/version', timeout=3))
    assert 'webSocketDebuggerUrl' in ver


# ---- the pane works on the pipe ----------------------------------------------

def test_screencast_navigate_read_popup_and_fit_on_the_default_pipe(pane, site):
    client, launch = pane
    s = launch(site.base + '/other')
    sid = s['session_id']
    _wait(lambda: s.get('frame_seq', 0) > 0 and s.get('frame'), what='first screencast frame')

    r = client.post('/api/browser/read', json={'session_id': sid}, headers=HUMAN)
    assert r.status_code == 200 and 'pipe-live visible text' in json.dumps(r.get_json())

    frames = s['frame_seq']
    client.post('/api/browser/input', json={'session_id': sid, 'type': 'navigate', 'url': site.base + '/'})
    _wait(lambda: s.get('live_url', '').rstrip('/').endswith(f':{site.srv.server_port}') and
          _eval(s, 'document.title') == 'Sign in', what='navigation to the form page')
    _wait(lambda: s['frame_seq'] > frames, what='frames after navigation (screencast re-armed)')

    client.post('/api/browser/input', json={'session_id': sid, 'type': 'viewport', 'w': 640, 'h': 480})
    _wait(lambda: _eval(s, '[innerWidth, innerHeight]') == [640, 480], what='fit to 640x480')

    client.post('/api/browser/input', json={'session_id': sid, 'type': 'mouse', 'x': 50, 'y': 30})
    _wait(lambda: len(s.get('tabs') or {}) == 2, what='popup in the tab strip')
    _wait(lambda: '/popup' in site.uas, what='popup request')
    assert 'HeadlessChrome' not in site.uas['/popup'], site.uas['/popup']
    assert 'HeadlessChrome' not in site.uas['/'], site.uas['/']


def test_named_profile_is_saved_by_the_graceful_pipe_close(pane, site):
    client, launch = pane
    s = launch(site.base + '/other', profile='pipe-live')
    _wait(lambda: s.get('frame_seq', 0) > 0, what='first frame')
    _eval(s, "document.cookie = 'k=v; max-age=86400'; document.cookie")
    assert br._graceful_close(s) is True, 'Browser.close over the pipe should end the process by itself'
    s['status'] = 'stopped'
    browser_sessions.pop(s['session_id'], None)
    s2 = launch(site.base + '/other', profile='pipe-live')
    _wait(lambda: s2.get('frame_seq', 0) > 0, what='first frame, second launch')
    assert 'k=v' in _eval(s2, 'document.cookie'), 'the profile did not keep its cookie across a close'


# ---- the Desk sign-in fill types on a pipe session ---------------------------

def test_signin_fill_types_the_login_into_a_pipe_session(pane, site, monkeypatch):
    client, launch = pane
    s = launch(site.base + '/', profile='pipe-fill')
    _wait(lambda: _eval(s, 'document.title') == 'Sign in', what='the form page')
    assert s['port'] is None

    origin = site.base
    monkeypatch.setattr(signin_fill, '_declared',          # the https-only gate is URL logic with its own tests;
                        lambda top, origins: top.get('url', '').startswith(origin) and top.get('origin') == origin)
    monkeypatch.setattr(signin_fill._vault, 'get_username', lambda *a, **k: USER)
    monkeypatch.setattr(signin_fill._vault, 'get_secret_value', lambda *a, **k: PW)
    page = signin_fill_cdp.connect(s, timeout=8)
    try:
        # the BROWSER's reading of the top frame, over the pipe
        top = page.top()
        assert top['url'] == origin + '/' and top['origin'] == origin
        out = signin_fill._run(page, [origin], {'name': 'live.login'}, None, False, time.sleep)
    finally:
        page.close()
    assert out['ok'] and out['state'] == 'submitted', out
    posted = _wait(lambda: site.posts, what='the form post')
    assert posted == [('/login', {'u': USER, 'p': PW})], posted
    assert PW not in json.dumps(out) and USER not in json.dumps(out)
