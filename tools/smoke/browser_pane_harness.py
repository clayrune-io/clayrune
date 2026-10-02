#!/usr/bin/env python
"""Real-backend harness for browser-pane smokes — NOT a Clayrune server.

Every other browser-pane smoke stubs /api/browser/* and feeds hand-written
SSE, which is exactly how MC-976's black pane shipped: the stubs said frames
arrive, the real backend sent none. This serves the REAL browser_routes
blueprint (real headless Chromium, real CDP reader, real SSE) plus the real
static/js/browser-pane.js, on its own port, with every profile/download root
redirected into a temp dir so it can never touch a real signed-in profile.

It deliberately does NOT import server.py (that would start schedulers,
stewards, the reaper...). Only the blueprint.

  python tools/smoke/browser_pane_harness.py <port> <tmpdir>

Also serves two test pages from a SECOND port (the pane refuses its own
origin): /page.html (a coloured page) and /popup.html (window.open()).
Prints `READY <port> <page_port>` once listening. Kills every Chromium it
launched on exit (SIGTERM/Ctrl-C/stdin EOF).
"""
import http.server
import os
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

port = int(sys.argv[1])
tmp = Path(sys.argv[2])
tmp.mkdir(parents=True, exist_ok=True)

from flask import Flask  # noqa: E402
from mc.blueprints import browser_routes as br  # noqa: E402
from mc.state import browser_sessions  # noqa: E402

br._profiles_root = lambda: str(tmp / 'profiles')
br._named_profiles_root = lambda: str(tmp / 'profiles_named')
br._downloads_root = lambda: str(tmp / 'downloads')

registered = {}


def _register(proc, name, proc_type, session_id, project_id, command_preview=''):
    # Same signature as agent_routes._register_process on purpose: a caller
    # that does not match it fails here the way it fails in production.
    registered[proc.pid] = {'name': name, 'type': proc_type, 'session_id': session_id}


def _unregister(pid):
    registered.pop(pid, None)


br.wire(register_process_fn=_register, unregister_process_fn=_unregister,
        popen_flags=0, startupinfo=None, server_port=port, uploads_dir=str(tmp / 'uploads'))

app = Flask(__name__, static_folder=str(REPO / 'static'), static_url_path='/static')
app.register_blueprint(br.bp)


@app.route('/_harness/crash/<sid>', methods=['POST'])
def _crash(sid):
    # Simulates a pane Chromium dying under a live session (what a lost
    # profile lock or a crash looks like to the pane). Only ever a process
    # this harness itself launched.
    s = browser_sessions.get(sid)
    if not s:
        return {'error': 'unknown session'}, 404
    s['proc'].kill()
    return {'ok': True}


@app.route('/_harness/session/<sid>')
def _session_state(sid):
    # What a failing smoke needs to say WHY: the view the server is fitting
    # to, what it measured, and the last error it swallowed.
    s = browser_sessions.get(sid)
    if not s:
        return {'error': 'unknown session'}, 404
    return {'view': s.get('view'), 'frame': [s.get('frame_w'), s.get('frame_h')],
            'page_scale': s.get('page_scale'),
            'desktop_site': s.get('desktop_site'), 'device_mode': s.get('device_mode'),
            'mobile': s.get('mobile'), 'ua': (s.get('ua_override') or {}).get('userAgent'),
            'window_chrome': {str(k): v for k, v in (s.get('window_chrome') or {}).items()},
            'error': s.get('error'), 'dpr': s.get('dpr')}


@app.route('/_harness/registered')
def _registered():
    return {'registered': [dict(v, pid=k) for k, v in registered.items()]}


@app.route('/')
def _shell():
    # The viewport meta is what the real app's index.html carries: without it a
    # phone WebView lays the shell out ~980px wide and `_bpIsMobile()` reads
    # false. (Playwright desktop/hasTouch smokes are unaffected by it.)
    return """<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
    <body style="margin:0;background:#333">
    <div id="modal-layer"></div><div id="minimized-tray"></div>
    <script>window.nextModalZ = 100;</script>
    <script type="module" src="/static/js/browser-pane.js"></script></body>"""


PAGES = {
    '/page.html': b"""<!doctype html><title>Harness page</title>
      <body style="margin:0;background:#1565c0;color:#fff;font:40px sans-serif">
      <h1 id="h">harness page</h1></body>""",
    # NO <meta name=viewport>: under mobile emulation Chromium lays this out
    # 980px wide and draws it zoomed out (the Google-home-page situation).
    '/scale.html': b"""<!doctype html><title>Scale</title>
      <body style="margin:0;background:#222;color:#fff;font:40px sans-serif">
      <textarea id="q" style="position:absolute;left:500px;top:300px;width:300px;height:100px"
        oninput="document.getElementById('out').textContent='typed:'+this.value"></textarea>
      <div id="out" style="position:absolute;left:20px;top:20px">typed:</div>
      <div id="vv" style="position:absolute;left:20px;top:80px;font-size:20px"></div>
      <script>setInterval(function(){var v=window.visualViewport;
        document.getElementById('vv').textContent='vv:'+[v.pageLeft,v.pageTop,v.scale,scrollX,scrollY,innerWidth,innerHeight].map(function(x){return Math.round(x*100)/100}).join(',')},100)</script></body>""",
    # Typing probe: a full-page textarea whose page background changes colour
    # with the number of characters typed, so a screencast frame says exactly how
    # many keystrokes it contains. Has a viewport meta (no page-scale zoom).
    '/type.html': b"""<!doctype html><title>Type</title>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <body style="margin:0;background:rgb(0,0,0)">
      <textarea id="q" autofocus style="position:fixed;inset:0;width:100%;height:100%;border:0;
        outline:0;background:transparent;color:#fff;font:24px sans-serif;resize:none"
        oninput="var n=this.value.length;document.body.style.background=
          'rgb('+(n*37)%256+','+(n*91)%256+','+(n*53)%256+')'"></textarea></body>""",
    '/popup.html': b"""<!doctype html><title>Opener</title>
      <body style="margin:0;background:#2e7d32;color:#fff;font:40px sans-serif">
      <button id="b" style="font-size:40px"
        onclick="var w=window.open('about:blank','p','width=500,height=500');
                 setTimeout(function(){w.location='/page.html'},300)">open popup</button></body>""",
}


# /ua.html answers by User-Agent, the way Google does: a phone UA gets a page
# with a viewport meta, anything else gets the desktop one (NO meta, so it is
# laid out 980px wide). `ua_hits` records which one each request got, in order,
# so a smoke can see a desktop load FOLLOWED BY a mobile one (the reload).
ua_hits = []
ua_strings = []
UA_MOBILE = b"""<!doctype html><title>UA</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <body style="margin:0;background:#00695c;color:#fff;font:40px sans-serif"><h1 id="site">MOBILE-SITE</h1></body>"""
UA_DESKTOP = b"""<!doctype html><title>UA</title>
  <body style="margin:0;background:#4527a0;color:#fff;font:40px sans-serif"><h1 id="site">DESKTOP-SITE</h1>
  <div style="width:980px;height:1400px"></div></body>"""


@app.route('/_harness/uahits')
def _uahits():
    return {'hits': list(ua_hits), 'uas': list(ua_strings)}


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/ua.html':
            mobile = 'Mobile' in (self.headers.get('User-Agent') or '')
            ua_hits.append('mobile' if mobile else 'desktop')
            ua_strings.append(self.headers.get('User-Agent'))
            body = UA_MOBILE if mobile else UA_DESKTOP
        else:
            body = PAGES.get(path)
        self.send_response(200 if body else 404)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body or b'nope')

    def log_message(self, *a):
        pass


pages = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Pages)
threading.Thread(target=pages.serve_forever, daemon=True).start()


def _teardown():
    for s in list(browser_sessions.values()):
        try:
            br._kill_browser_session(s)
        except Exception as e:
            print(f'[harness] teardown failed: {e}', flush=True)


def _stdin_watch():
    # The .mjs parent closes our stdin when it exits, however it exits.
    try:
        sys.stdin.read()
    finally:
        _teardown()
        os._exit(0)


threading.Thread(target=_stdin_watch, daemon=True).start()
print(f'READY {port} {pages.server_address[1]}', flush=True)
try:
    app.run(host='127.0.0.1', port=port, threaded=True, use_reloader=False)
finally:
    _teardown()
