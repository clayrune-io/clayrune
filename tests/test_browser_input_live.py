"""Browser-pane input path: request -> CDP translation, proven on a real page.

Written 2026-09-21 after an agent could not drive a signed-in pane: Ctrl+K and
Alt+ArrowDown never took effect and single `mouse` calls clicked nothing,
because the route dropped modifiers and treated a click as two requests.

The live tests launch their OWN headless Chromium (dedicated user-data-dir and
debugging port, killed by its own PID) against a page served from 127.0.0.1. Never a
third-party site: the point is to prove the input path, not to touch anyone's
account. Skipped when Chromium or websocket-client is unavailable.
"""
import http.server
import json
import threading
import subprocess
import tempfile
import time
import urllib.request

import pytest

from mc.blueprints import browser_routes as br


# ---- pure translation -------------------------------------------------------

def test_combo_string_carries_the_modifier():
    down, up = br._input_commands({'type': 'key', 'key': 'Ctrl+K'})
    assert down[1]['modifiers'] == 2
    assert down[1]['key'] == 'k' and down[1]['code'] == 'KeyK'
    assert down[1]['windowsVirtualKeyCode'] == 75
    assert 'text' not in down[1]  # a shortcut must not also type a 'k'
    assert up[1]['type'] == 'keyUp' and up[1]['modifiers'] == 2


def test_alt_arrow_gets_keycode_and_modifier():
    down, _ = br._input_commands({'type': 'key', 'key': 'Alt+ArrowDown'})
    assert down[1]['modifiers'] == 1 and down[1]['windowsVirtualKeyCode'] == 40


def test_modifier_list_and_booleans_combine():
    down, _ = br._input_commands({'type': 'key', 'key': 'k',
                                  'modifiers': ['shift'], 'ctrl': True})
    assert down[1]['modifiers'] == 10


def test_pane_style_key_is_unchanged():
    # What static/js/browser-pane.js sends for a named key.
    down, up = br._input_commands({'type': 'key', 'key': 'Enter',
                                   'code': 'Enter', 'keyCode': 13})
    assert down[1]['windowsVirtualKeyCode'] == 13 and down[1]['modifiers'] == 0
    assert up[1]['type'] == 'keyUp'


def test_unknown_modifier_is_refused():
    with pytest.raises(ValueError):
        br._input_commands({'type': 'key', 'key': 'Hyper+K'})


def test_mouse_without_action_is_a_full_click():
    cmds = br._input_commands({'type': 'mouse', 'x': 10, 'y': 20})
    assert [c[1]['type'] for c in cmds] == ['mouseMoved', 'mousePressed', 'mouseReleased']
    assert cmds[1][1]['buttons'] == 1 and cmds[1][1]['button'] == 'left'


def test_pane_style_press_is_a_single_event():
    cmds = br._input_commands({'type': 'mouse', 'action': 'mousePressed',
                               'x': 1, 'y': 2, 'buttons': 1})
    assert len(cmds) == 1 and cmds[0][1]['type'] == 'mousePressed'


def test_unknown_type_is_refused():
    with pytest.raises(ValueError):
        br._input_commands({'type': 'teleport'})


# ---- live: a real Chromium, a local page ------------------------------------

PAGE = """<!doctype html><html><body style="margin:0">
<a id="go" href="/landed" style="position:absolute;left:0;top:0;width:200px;height:100px;display:block">go</a>
<script>
window.log = [];
document.addEventListener('keydown', e => window.log.push(
  (e.ctrlKey ? 'Ctrl+' : '') + (e.altKey ? 'Alt+' : '') + e.key + ':' + e.keyCode));
</script></body></html>"""


@pytest.fixture
def chromium():
    exe = br._find_chromium()
    ws_mod = br._import_ws()
    if not exe or ws_mod is None:
        pytest.skip('Chromium or websocket-client not available')
    port = br._free_port()
    udd = tempfile.mkdtemp(prefix='mc-input-test-')
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (PAGE if self.path == '/' else '<p>landed</p>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{srv.server_port}/'
    proc = subprocess.Popen([exe, '--headless=new', f'--remote-debugging-port={port}',
                             '--remote-allow-origins=*', f'--user-data-dir={udd}',
                             '--no-first-run', '--disable-gpu', 'about:blank'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(100):
            try:
                targets = json.load(urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/list', timeout=1))
                page = next((t for t in targets if t.get('type') == 'page'), None)
                if page:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert page, 'devtools endpoint never came up'
        ws = ws_mod.create_connection(page['webSocketDebuggerUrl'], timeout=5)
        ids = iter(range(1, 10_000))

        def call(method, params=None):
            i = next(ids)
            ws.send(json.dumps({'id': i, 'method': method, 'params': params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get('id') == i:
                    return msg

        def evaluate(expr):
            return call('Runtime.evaluate', {'expression': expr, 'returnByValue': True}
                        )['result']['result'].get('value')

        call('Page.navigate', {'url': url})  # as _run_cdp does
        for _ in range(50):
            if (evaluate('location.pathname') == '/'
                    and evaluate('document.readyState') == 'complete'):
                break
            time.sleep(0.1)
        yield call, evaluate
        ws.close()
    finally:
        proc.kill()  # our own PID only
        proc.wait(timeout=10)
        srv.shutdown()


def _send(call, data):
    for method, params in br._input_commands(data):
        assert 'error' not in call(method, params)


def test_one_mouse_call_clicks_a_link_and_navigates(chromium):
    call, evaluate = chromium
    assert evaluate('location.pathname') == '/'
    _send(call, {'type': 'mouse', 'x': 50, 'y': 50})
    for _ in range(50):  # a real cross-document navigation, not a hash change
        if evaluate('location.pathname') == '/landed':
            break
        time.sleep(0.1)
    assert evaluate('location.pathname') == '/landed'


def test_key_combos_reach_the_page_with_modifiers(chromium):
    call, evaluate = chromium
    _send(call, {'type': 'mouse', 'x': 400, 'y': 400})  # focus the page body
    _send(call, {'type': 'key', 'key': 'Ctrl+K'})
    _send(call, {'type': 'key', 'key': 'Alt+ArrowDown'})
    log = evaluate('window.log')
    assert 'Ctrl+k:75' in log
    assert 'Alt+ArrowDown:40' in log
