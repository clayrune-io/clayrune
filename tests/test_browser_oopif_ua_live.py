"""MC-992: a cross-site iframe must present the same User-Agent as its page.

Cloudflare Turnstile runs in a cross-site iframe (challenges.cloudflare.com),
which Chromium puts in its own target (an OOPIF). The UA override is per
target, and until 2026-09-28 only page targets got it: the iframe and its
worker said "HeadlessChrome" in navigator.userAgent and in the User-Agent
header while the page said "Chrome", and Turnstile failed every human click
with 600010 "Bot behavior detected" (nopecha.com challenge 0/6, Ron's
cjdropshipping login 0/2; with the fix 6/6 and 2/2).

The live test drives the REAL pane launch (_launch_browser + reader + UA
guard) against its OWN Chromium (throwaway user-data-dir under tmp_path, its
own debugging port, killed by its own PID). Pages come from 127.0.0.1 with the
iframe on localhost, which is a different site, so Chromium isolates it
exactly as it does the Turnstile frame. Never a third-party site.
"""
import http.server
import json
import threading
import time

import pytest

from mc.blueprints import browser_routes as br


# ---- pure: which UA an attaching target gets --------------------------------

_DESKTOP = {'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36'}


def test_iframe_gets_desktop_override_on_a_desktop_pane():
    s = {'ua_override': _DESKTOP, 'device_mode': 'desktop'}
    assert br._guard_ua_override(s, 'iframe') == _DESKTOP
    assert br._guard_ua_override(s, 'page') == _DESKTOP


def test_iframe_and_page_follow_mobile_mode():
    s = {'ua_override': _DESKTOP, 'device_mode': 'mobile'}
    for kind in ('iframe', 'page'):
        ua = br._guard_ua_override(s, kind)['userAgent']
        assert 'Mobile' in ua and 'Chrome/153.0.0.0' in ua
    # A page used to keep the desktop override "because the reader applies mobile
    # itself" -- but the guard is attached first and its override wins, so every
    # request of a mobile pane said Windows (Ron, 2026-10-02: Google served the
    # desktop site). The guard must carry the mode itself.


def test_desktop_site_keeps_the_desktop_ua_in_mobile_mode():
    s = {'ua_override': _DESKTOP, 'device_mode': 'mobile', 'desktop_site': True}
    assert br._guard_ua_override(s, 'page') == _DESKTOP
    assert br._guard_ua_override(s, 'iframe') == _DESKTOP


def test_workers_are_released_never_overridden():
    assert 'worker' not in br._UA_OVERRIDE_TYPES
    assert set(br._UA_OVERRIDE_TYPES) == {'page', 'iframe'}


# ---- live -------------------------------------------------------------------

_REPORTS = []

_PAGE = """<!doctype html><title>{who}</title><body>{extra}<script>
fetch('/report', {{method: 'POST', body: JSON.stringify({{who: '{who}', ua: navigator.userAgent}})}});
new Worker('/w.js').onmessage = m => fetch('/report', {{method: 'POST',
  body: JSON.stringify({{who: '{who}:worker', ua: m.data}})}});
</script></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        b = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == '/top':
            self._send(_PAGE.format(who='top', extra=(
                f'<iframe src="http://localhost:{port}/frame"></iframe>')), 'text/html')
        elif self.path == '/frame':
            self._send(_PAGE.format(who='frame', extra=''), 'text/html')
        elif self.path == '/w.js':
            self._send('postMessage(navigator.userAgent)', 'application/javascript')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        d = json.loads(self.rfile.read(n) or b'{}')
        d['header'] = self.headers.get('User-Agent')
        _REPORTS.append(d)
        self._send('{}', 'application/json')


@pytest.fixture
def pane(tmp_path, monkeypatch):
    if not br._find_chromium() or not br._import_ws():
        pytest.skip('Chromium or websocket-client unavailable')
    # Throwaway profiles under tmp_path, never the live server's root.
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _REPORTS.clear()
    session, err = br._launch_browser(
        'test_mc992', f'http://127.0.0.1:{srv.server_address[1]}/top', ephemeral=True)
    assert not err, err
    try:
        yield session
    finally:
        br._kill_browser_session(session)
        with br.browser_lock:
            br.browser_sessions.pop(session['session_id'], None)
        srv.shutdown()


def _wait_reports(n, timeout=20):
    end = time.time() + timeout
    while time.time() < end and len(_REPORTS) < n:
        time.sleep(0.2)
    return {r['who']: r for r in _REPORTS}


def test_cross_site_iframe_and_its_worker_do_not_say_headless(pane):
    got = _wait_reports(4)
    assert set(got) >= {'top', 'top:worker', 'frame', 'frame:worker'}, got
    for who, r in got.items():
        assert 'Headless' not in r['ua'], (who, r['ua'])
        assert 'Headless' not in (r['header'] or ''), (who, r['header'])
    # And the frame matches its page exactly -- a mismatch is a tell too.
    assert got['frame']['ua'] == got['top']['ua']
