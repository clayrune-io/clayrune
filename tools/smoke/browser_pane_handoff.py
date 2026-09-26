"""Real Chromium popup handoff regression. No accounts or named profiles.

Run: python tools/smoke/browser_pane_handoff.py
Optional --register-processes registers this run with local Clayrune.
Local fixture uses two origins; it is not an authenticated Google sign-in test.
"""
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mc.blueprints import browser_routes as br
import websocket


def register(pid, name, command):
    base = 'http://localhost:5199' if '--register-processes' in sys.argv else None
    if base:
        req = urllib.request.Request(base + '/api/processes/register',
            data=json.dumps({'pid': pid, 'name': name, 'project_id': 'mission_control',
                             'command': command}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('process registration failed')


def wait_for(fn, label, timeout=12):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = fn()
        if value:
            print('PASS ' + label, flush=True)
            return value
        time.sleep(.05)
    raise AssertionError(label)


class Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        popup_origin = f'http://localhost:{self.server.server_port}'
        root_origin = f'http://127.0.0.1:{self.server.server_port}'
        path = urlsplit(self.path).path
        if path == '/iframe-root':
            body = f'''<title>Iframe opener</title>
            <iframe src="{popup_origin}/iframe" style="position:fixed;inset:0;width:100%;height:100%;border:0"></iframe>
            <script>addEventListener('message',e=>{{
              if(e.origin==={json.dumps(popup_origin)} && e.data==='credential')
                location.href={json.dumps(popup_origin + '/done')};
            }});</script>'''
        elif path == '/iframe':
            body = f'''<button style="position:fixed;inset:0" onclick="window.open(
                '{popup_origin}/popup?iframe=1','iframe-auth','width=500,height=500')">Sign in</button>
            <script>addEventListener('message',e=>{{
              if(e.origin!=={json.dumps(popup_origin)})return;
              parent.postMessage(e.data,{json.dumps(root_origin)});
              e.source.postMessage('ack',e.origin);
            }});</script>'''
        elif path == '/done':
            body = '<title>Callback complete</title><body style="background:#2e7d32">Signed in'
        elif path == '/':
            body = f'''<title>Opener</title><body style="background:#1565c0">
            <button style="position:fixed;inset:0" onclick="window.p=window.open(
              '{popup_origin}/popup','auth','width=500,height=500')">Sign in</button>
            <script>window.received=[];addEventListener('message',e=>{{
              if(e.origin!=={json.dumps(popup_origin)})return;
              received.push(e.data);document.title='Signed in';
              document.body.style.background='#2e7d32';
              e.source.postMessage('ack',e.origin);
            }});</script>'''
        elif path == '/popup':
            callback_origin = popup_origin if parse_qs(urlsplit(self.path).query).get('iframe') else root_origin
            body = f'''<title>Popup</title><body style="background:#fff">
            <button style="position:fixed;inset:0" onclick="opener.postMessage(
                'credential','{callback_origin}');">Complete</button>
            <script>addEventListener('message',e=>{{
              if(e.origin==={json.dumps(callback_origin)} && e.data==='ack')window.close();
            }});</script>'''
        else:
            body = '<title>Second popup</title>Independent popup'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        if path == '/done':
            self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def main():
    register(os.getpid(), 'Popup handoff smoke', 'python tools/smoke/browser_pane_handoff.py')
    tmp = Path(tempfile.mkdtemp(prefix='bp-handoff-'))
    br._profiles_root = lambda: str(tmp / 'profiles')
    br._named_profiles_root = lambda: str(tmp / 'named-unused')
    br._downloads_root = lambda: str(tmp / 'downloads')
    br.wire(register_process_fn=lambda proc, **kw: register(
                proc.pid, 'Popup handoff Chromium', kw['command_preview']),
            unregister_process_fn=lambda pid: None, popen_flags=0,
            startupinfo=None, server_port=1, uploads_dir=str(tmp / 'uploads'))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    session = None
    ws = None
    try:
        url = f'http://127.0.0.1:{srv.server_port}/'
        session, err = br._launch_browser('smoke', url, ephemeral=True, dpr=1)
        assert session and not err, err
        wait_for(lambda: session.get('frame'), 'root frame arrives')
        root = session['root_target_id']
        ver = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{session['port']}/json/version", timeout=3))
        ws = websocket.create_connection(ver['webSocketDebuggerUrl'], timeout=5)
        seq = 0

        def call(method, params=None, sid=None):
            nonlocal seq
            seq += 1
            msg = {'id': seq, 'method': method, 'params': params or {}}
            if sid:
                msg['sessionId'] = sid
            ws.send(json.dumps(msg))
            while True:
                response = json.loads(ws.recv())
                if response.get('id') == seq:
                    assert 'error' not in response, response
                    return response.get('result', {})

        sid = call('Target.attachToTarget', {'targetId': root, 'flatten': True})['sessionId']

        def evaluate(expression):
            result = call('Runtime.evaluate', {'expression': expression,
                          'returnByValue': True, 'userGesture': True}, sid)
            assert 'exceptionDetails' not in result, result
            return result.get('result', {}).get('value')

        def click():
            for kind in ('mousePressed', 'mouseReleased'):
                session['cmd_queue'].put(('Input.dispatchMouseEvent', {
                    'type': kind, 'x': 200, 'y': 200, 'button': 'left', 'clickCount': 1}))

        click()
        popup = wait_for(lambda: next((tid for tid, tab in list(session['tabs'].items())
            if tid != root and tab.get('url', '').endswith('/popup')), None),
            'cross-origin popup attached')
        wait_for(lambda: session.get('active_target_id') == popup and session.get('frame'),
                 'popup has a frame and focus')
        time.sleep(.5)  # allow the real viewport fitter to finish before clicking
        print('OPENER VISIBILITY ' + str(evaluate('document.visibilityState')), flush=True)
        before = session['frame_seq']
        click()
        wait_for(lambda: evaluate('received.length') == 1, 'postMessage reaches opener')
        wait_for(lambda: popup not in session['tabs'], 'window.close removes popup tab')
        wait_for(lambda: session.get('active_target_id') == root, 'focus returns to opener')
        assert session['live_url'] == url, 'closed popup URL leaked into opener frames'
        wait_for(lambda: session.get('frame_seq', 0) > before and session.get('frame'),
                 'opener emits a fresh frame')
        assert evaluate('document.title') == 'Signed in'
        print('STATE ' + json.dumps({k: session.get(k) for k in
              ('status', 'error', 'live_url', 'frame_seq')}), flush=True)

        # GSI-like topology: cross-origin iframe opens the popup, forwards its
        # result to the parent, then the parent navigates across origins/COOP.
        session['cmd_queue'].put(('Page.navigate', {'url': url + 'iframe-root'}))
        wait_for(lambda: evaluate('document.title') == 'Iframe opener', 'iframe opener loaded')
        time.sleep(.5)
        click()
        iframe_popup = wait_for(lambda: next((tid for tid, tab in list(session['tabs'].items())
            if tid != root and '/popup?iframe=1' in tab.get('url', '')), None),
            'iframe-owned popup attached')
        time.sleep(.5)
        click()
        wait_for(lambda: iframe_popup not in session['tabs'], 'iframe-owned popup self-closes')
        wait_for(lambda: session.get('active_target_id') == root, 'iframe flow restores opener')
        wait_for(lambda: evaluate('document.title') == 'Callback complete',
                 'iframe postMessage navigates parent across origin and COOP')
        session['cmd_queue'].put(('Page.navigate', {'url': url}))
        wait_for(lambda: evaluate('document.title') == 'Opener', 'root ready for overlap test')

        # Distinct popups are legitimate concurrent windows, not proof of a retry.
        click()
        first = wait_for(lambda: next((tid for tid in list(session['tabs']) if tid != root), None),
                         'first concurrent popup opens')
        evaluate(f"window.other=window.open('http://localhost:{srv.server_port}/other',"
                 "'other','width=500,height=500');void 0")
        wait_for(lambda: any(t.get('url', '').endswith('/other')
                 for t in list(session['tabs'].values())), 'second concurrent popup opens')
        time.sleep(.5)
        assert first in session['tabs'], 'opening a sibling destroyed the first live popup'
        assert evaluate('!p.closed'), 'first popup WindowProxy was closed'
        print('PASS concurrent popups preserved', flush=True)
        session['cmd_queue'].put(('_activate_tab', {'target_id': first}))
        wait_for(lambda: session.get('active_target_id') == first, 'return to first live popup')
        click()
        wait_for(lambda: evaluate('received.length') == 1, 'preserved popup still delivers result')
        wait_for(lambda: first not in session['tabs'], 'preserved popup still self-closes')
        assert any(t.get('url', '').endswith('/other') for t in session['tabs'].values())
        print('PASS independent sibling survives completed handoff', flush=True)
        print('ALL PASS', flush=True)
    finally:
        if ws:
            ws.close()
        if session:
            br._kill_browser_session(session)
        srv.shutdown()
        srv.server_close()


if __name__ == '__main__':
    main()
