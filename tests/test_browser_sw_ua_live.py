"""MC-1083: a reload served through a service worker keeps the pane's UA.

Ron, 2026-10-10, on his phone: the pane showed a site's mobile version, then
after a refresh the desktop one. Measured on a local site with a pass-through
service worker: the first load's document request carried the mobile UA, but
once the worker controlled the page every reload was fetched BY the worker,
and the worker is its own target that the UA guard released without an
override -- the request said "Windows NT 10.0 ... HeadlessChrome" while
Sec-CH-UA-Mobile still said ?1. Same leak on a desktop pane (HeadlessChrome).

Drives the REAL pane launch (_launch_browser + reader + UA guard) against its
OWN Chromium (throwaway profile under tmp_path, killed by its own PID) and a
local server. Never a third-party site.
"""
import http.server
import threading
import time

import pytest

from mc.blueprints import browser_routes as br

_HITS = []

# Navigations go through the worker, which fetches them with a marker so the
# server can tell a worker-served request from a direct one.
_SW = ("self.addEventListener('install', e => self.skipWaiting());"
       "self.addEventListener('activate', e => e.waitUntil(clients.claim()));"
       "self.addEventListener('fetch', e => { if (e.request.mode === 'navigate')"
       " e.respondWith(fetch(e.request.url.split('?')[0] + '?via=sw')); });")
_PAGE = ("<!doctype html><title>sw</title><body><script>"
         "navigator.serviceWorker.register('/sw.js');</script></body>")


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == '/sw.js':
            body, ctype = _SW, 'application/javascript'
        elif self.path.startswith('/page'):
            _HITS.append({'path': self.path, 'ua': self.headers.get('User-Agent') or '',
                          'mobile': self.headers.get('Sec-CH-UA-Mobile')})
            body, ctype = _PAGE, 'text/html'
        else:
            self.send_response(404)
            self.end_headers()
            return
        b = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def _launch(tmp_path, monkeypatch, mobile):
    if not br._find_chromium() or not br._import_ws():
        pytest.skip('Chromium or websocket-client unavailable')
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _HITS.clear()
    session, err = br._launch_browser(
        'test_mc1083', f'http://127.0.0.1:{srv.server_address[1]}/page',
        ephemeral=True, view=(412, 780), mobile=mobile)
    assert not err, err
    return session, srv


def _teardown(session, srv):
    br._kill_browser_session(session)
    with br.browser_lock:
        br.browser_sessions.pop(session['session_id'], None)
    srv.shutdown()


def _reload_until_worker_serves(session, timeout=30):
    """Reload (as POST /api/browser/input {type:'reload'} does) until a
    document request arrives through the worker; return that hit."""
    end = time.time() + timeout
    while time.time() < end:
        n = len(_HITS)
        session['cmd_queue'].put(('Page.reload', {}))
        t = time.time() + 5
        while time.time() < t and len(_HITS) == n:
            time.sleep(0.1)
        via_sw = [h for h in _HITS[n:] if h['path'].endswith('?via=sw')]
        if via_sw:
            return via_sw[0]
    raise AssertionError(f'no worker-served reload in {timeout}s: {_HITS}')


@pytest.mark.parametrize('mobile', [True, False], ids=['mobile', 'desktop'])
def test_worker_served_reload_keeps_the_pane_ua(tmp_path, monkeypatch, mobile):
    session, srv = _launch(tmp_path, monkeypatch, mobile)
    try:
        end = time.time() + 20
        while time.time() < end and not _HITS:
            time.sleep(0.1)
        assert _HITS, 'first load never reached the server'
        first = _HITS[0]
        hit = _reload_until_worker_serves(session)
        assert 'Headless' not in hit['ua'], hit
        assert hit['ua'] == first['ua'], (first, hit)
        if mobile:
            # (No Sec-CH-UA-Mobile check: the worker's own fetch of a NEW
            # Request carries no client hints at all, override or not.)
            assert 'Android' in hit['ua'] and 'Mobile Safari' in hit['ua'], hit
    finally:
        _teardown(session, srv)
