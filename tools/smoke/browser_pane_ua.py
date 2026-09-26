"""MC-976 Google-sign-in guard: every tab of the browser pane must present as
the ordinary Chromium it is -- including a window.open() popup, and including
that popup's FIRST request.

What Ron hit (2026-09-26): "Sign in with Google" opened a popup, and Google
answered "Couldn't sign you in - This browser or app may not be secure". The
popup's User-Agent said HeadlessChrome (the override was root-tab only), and
even once overridden on attach, the sign-in page's own request had already
gone out as HeadlessChrome -- which alone was enough for Google to refuse.

Checks, against a local page server (never Google), REAL Chromium through the
REAL blueprint, temp profile dirs only:
  1. every request the server sees -- root tab and popup, first request
     included -- has no "Headless" in User-Agent and carries Sec-CH-UA;
  2. in both tabs: navigator.userAgent has no "Headless",
     navigator.userAgentData.brands is non-empty, navigator.webdriver is false.

RUN: python tools/smoke/browser_pane_ua.py   (exit 0 = all pass)
"""
import http.server
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mc.blueprints import browser_routes as br  # noqa: E402

import websocket  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix='bp-ua-'))
br._profiles_root = lambda: str(tmp / 'p')
br._named_profiles_root = lambda: str(tmp / 'pn')
br._downloads_root = lambda: str(tmp / 'd')
br.wire(register_process_fn=lambda *a, **k: None, unregister_process_fn=lambda *a: None,
        popen_flags=0, startupinfo=None, server_port=1, uploads_dir=str(tmp / 'u'))

requests = []


class Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(404)
            self.end_headers()
            return
        requests.append((self.path, self.headers.get('User-Agent') or '',
                         self.headers.get('Sec-CH-UA')))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        body = (b"<button onclick=\"window.open('/popup','p','width=500,height=600')\" "
                b"style='position:fixed;inset:0'>open</button>" if self.path == '/'
                else b'<p>popup</p>')
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Pages)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fails = []


def check(cond, label):
    print(('✅ ' if cond else '❌ FAIL — ') + label)
    if not cond:
        fails.append(label)


session, err = br._launch_browser('smoke', f'http://127.0.0.1:{srv.server_address[1]}/', dpr=1)
if err or not session:
    print(f'❌ FAIL — launch: {err}')
    sys.exit(1)
try:
    q = session['cmd_queue']
    t0 = time.time()
    while not any(p == '/' for p, _, _ in requests) and time.time() - t0 < 20:
        time.sleep(0.2)
    time.sleep(1)
    for kind in ('mousePressed', 'mouseReleased'):
        q.put(('Input.dispatchMouseEvent', {'type': kind, 'x': 200, 'y': 200,
                                            'button': 'left', 'clickCount': 1}))
    t0 = time.time()
    while not any(p == '/popup' for p, _, _ in requests) and time.time() - t0 < 15:
        time.sleep(0.2)
    time.sleep(1)

    check(any(p == '/popup' for p, _, _ in requests), 'popup opened and requested its page')
    for path, ua, ch in requests:
        check('Headless' not in ua and ch and 'Headless' not in ch,
              f'request {path}: UA {ua[-40:]!r}, Sec-CH-UA {ch!r}')

    ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{session['port']}/json/version"))
    ws = websocket.create_connection(ver['webSocketDebuggerUrl'], timeout=10)
    n = [0]

    def call(method, params=None, sid=None):
        n[0] += 1
        msg = {'id': n[0], 'method': method, 'params': params or {}}
        if sid:
            msg['sessionId'] = sid
        ws.send(json.dumps(msg))
        while True:
            r = json.loads(ws.recv())
            if r.get('id') == n[0]:
                return r.get('result', r.get('error'))

    pages = [t for t in call('Target.getTargets')['targetInfos']
             if t['type'] == 'page' and str(srv.server_address[1]) in t['url']]
    check(len(pages) == 2, f'two tabs to inspect (found {len(pages)})')
    for t in pages:
        sid = call('Target.attachToTarget', {'targetId': t['targetId'], 'flatten': True})['sessionId']
        v = call('Runtime.evaluate', {'returnByValue': True, 'expression': (
            'JSON.stringify({ua: navigator.userAgent, wd: String(navigator.webdriver),'
            ' brands: navigator.userAgentData ? navigator.userAgentData.brands : null})')},
            sid)['result']['value']
        v = json.loads(v)
        where = t['url'].rsplit('/', 1)[-1] or 'root'
        check('Headless' not in v['ua'], f'{where}: navigator.userAgent {v["ua"][-40:]!r}')
        check(bool(v['brands']), f'{where}: navigator.userAgentData.brands {v["brands"]}')
        check(v['wd'] == 'false', f'{where}: navigator.webdriver {v["wd"]}')
    ws.close()
finally:
    br._kill_browser_session(session)
    srv.shutdown()

print(f'\n{len(fails)} FAILED' if fails else '\nALL PASS')
sys.exit(1 if fails else 0)
