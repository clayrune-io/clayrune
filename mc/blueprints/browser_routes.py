"""Browser pane endpoints — Part B of the browser-pairing feature.

A visible, interactive browser rendered inside a Clayrune tab. A headless
Chromium (the one already on disk from the Playwright MCP install) is launched
with remote debugging and driven over CDP (Chrome DevTools Protocol) via a
websocket:

  - a reader thread receives `Page.screencastFrame` events into the session's
    latest-frame buffer (base64 JPEG) and acks them;
  - `/api/browser/stream` re-streams those frames to the pane over SSE;
  - `/api/browser/input` queues mouse/key/scroll/navigate commands, which the
    same CDP thread dispatches (single sender → no cross-thread ws races).

Mirrors the terminal pop-out (mc/blueprints/terminal_routes.py): a late-wired
blueprint, a session registry in mc.state, and process-ledger registration.

OPTIONAL / LAZY BY DESIGN. `websocket-client` is imported lazily and Chromium is
located at call time; neither is a core Clayrune dependency, so a stranger's
install isn't bloated. If either is missing, the endpoints return 501 with a
one-line install hint instead of failing the app.

Security: same posture as /api/terminal/* — protected by the app-wide
local_auth_gate before_request (loopback + CF-tunnel exempt, LAN needs the
passcode). Chromium runs headless with a per-session throwaway user-data-dir.
"""

import glob
import json
import os
import queue
import socket
import subprocess
import threading
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

from mc.state import browser_sessions, browser_lock

bp = Blueprint('browser_routes', __name__)

# Fixed render viewport — the pane scales displayed coords back to these.
VIEW_W, VIEW_H = 1280, 800

# ── wired by server.py ───────────────────────────────────────────────────────
_register_process: Callable[..., Any] = None  # type: ignore[assignment]
_unregister_process: Callable[..., Any] = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None


def wire(*, register_process_fn, unregister_process_fn, popen_flags, startupinfo):
    global _register_process, _unregister_process, _POPEN_FLAGS, _STARTUPINFO
    _register_process = register_process_fn
    _unregister_process = unregister_process_fn
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo


def _find_chromium():
    """Locate the Chromium binary from the Playwright browser cache (or an
    explicit override). Returns None if not found."""
    override = os.environ.get('MC_BROWSER_CHROMIUM')
    if override and os.path.exists(override):
        return override
    root = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'ms-playwright')
    if os.name != 'nt':
        root = os.path.join(os.path.expanduser('~'), '.cache', 'ms-playwright')
    pats = [
        os.path.join(root, 'chromium-*', 'chrome-win64', 'chrome.exe'),
        os.path.join(root, 'chromium-*', 'chrome-win', 'chrome.exe'),
        os.path.join(root, 'chromium-*', 'chrome-linux', 'chrome'),
        os.path.join(root, 'chromium-*', 'chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'),
    ]
    found = []
    for p in pats:
        found.extend(glob.glob(p))
    if not found:
        return None
    # Highest chromium-<N> build wins.
    def _rev(path):
        try:
            seg = [s for s in path.split(os.sep) if s.startswith('chromium-')][0]
            return int(seg.split('-')[1])
        except Exception:
            return 0
    return sorted(found, key=_rev)[-1]


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _import_ws():
    try:
        import websocket  # websocket-client
        return websocket
    except Exception:
        return None


def _run_cdp(session):
    """Single CDP thread: connect, drive screencast, dispatch queued commands.

    Owns the websocket end-to-end so there is exactly one sender — input/nav
    handlers enqueue onto session['cmd_queue'] and this loop sends them."""
    import urllib.request
    websocket = _import_ws()
    if websocket is None:
        session['status'] = 'error'
        session['error'] = 'websocket-client not installed'
        return
    port = session['port']
    _id = [100]

    def _next_id():
        _id[0] += 1
        return _id[0]

    try:
        # Wait for the devtools endpoint + a page target.
        page = None
        for _ in range(75):
            if session['status'] != 'running':
                return
            try:
                targets = json.load(urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/list', timeout=1))
                page = next((t for t in targets if t.get('type') == 'page'), None)
                if page and page.get('webSocketDebuggerUrl'):
                    break
            except Exception:
                pass
            _time.sleep(0.2)
        if not page:
            session['status'] = 'error'
            session['error'] = 'Chromium devtools endpoint did not come up'
            return

        # connect timeout is generous; the per-recv poll timeout is set below.
        ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                         max_size=None, timeout=5)
        # 0.1s recv poll: the loop drains queued input at the TOP of each
        # iteration, so this timeout bounds click/scroll/key dispatch latency —
        # 0.5s felt sluggish, 0.1s is snappy. The old black-pane risk (a short
        # timeout firing mid-frame, corrupting the ws and silently killing the
        # thread) is now covered by the consecutive-error tolerance below, and
        # measured safe: a 0.1s reader sustains 50fps with 0 recv errors.
        ws.settimeout(0.1)
        session['ws'] = ws

        def send(method, params=None):
            ws.send(json.dumps({'id': _next_id(), 'method': method, 'params': params or {}}))

        def start_screencast():
            send('Page.startScreencast',
                 {'format': 'jpeg', 'quality': 55, 'maxWidth': VIEW_W,
                  'maxHeight': VIEW_H, 'everyNthFrame': 1})

        send('Page.enable')
        send('Emulation.setDeviceMetricsOverride',
             {'width': VIEW_W, 'height': VIEW_H, 'deviceScaleFactor': 1, 'mobile': False})
        # Present a normal (non-headless) User-Agent. Chromium's --headless=new
        # advertises "HeadlessChrome/…", which sites like Hacker News block with
        # a "Sorry." page. Derive from the real browser UA (so the Chrome version
        # always matches) and just strip the Headless marker; also drop the
        # navigator.webdriver bot flag on every new document. Set BEFORE navigate
        # so the first request already carries the clean UA.
        try:
            ver = json.load(urllib.request.urlopen(
                f'http://127.0.0.1:{port}/json/version', timeout=2))
            ua = (ver.get('User-Agent') or '').replace('HeadlessChrome', 'Chrome')
            if ua:
                send('Network.setUserAgentOverride', {'userAgent': ua})
        except Exception as e:
            session['error'] = f'UA override failed: {e}'
        send('Page.addScriptToEvaluateOnNewDocument',
             {'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'})
        send('Page.navigate', {'url': session['url']})
        start_screencast()

        q = session['cmd_queue']
        errors = 0  # consecutive non-timeout recv errors before we give up
        while session['status'] == 'running':
            # 1. drain queued outbound commands (input / navigate)
            try:
                while True:
                    method, params = q.get_nowait()
                    try:
                        send(method, params)
                    except Exception as e:
                        session['error'] = f'send failed: {e}'
            except queue.Empty:
                pass
            # 2. read one inbound message (poll timeout so we loop back to send)
            try:
                raw = ws.recv()
                errors = 0
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                session['status'] = 'error'
                session['error'] = 'CDP websocket closed'
                break
            except Exception as e:
                # Transient recv error — do NOT die silently (that froze the
                # pane on a blank frame with status still 'running'). Tolerate a
                # few in a row, then surface an error so the SSE can end.
                errors += 1
                session['error'] = f'recv error: {e}'
                if errors >= 10:
                    session['status'] = 'error'
                    break
                continue
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            method = msg.get('method')
            if method == 'Page.screencastFrame':
                p = msg['params']
                session['frame'] = p.get('data')
                session['frame_seq'] = session.get('frame_seq', 0) + 1
                try:
                    ws.send(json.dumps({'id': _next_id(),
                                        'method': 'Page.screencastFrameAck',
                                        'params': {'sessionId': p['sessionId']}}))
                except Exception as e:
                    session['status'] = 'error'
                    session['error'] = f'ack failed: {e}'
                    break
            elif method == 'Page.frameStoppedLoading':
                # A cross-document navigation (typed URL, clicked link, or a
                # queued Page.navigate) STOPS the active screencast on this ws.
                # Re-arm it on every load, else the pane freezes/blacks after
                # the first navigation away from the launch page.
                try:
                    start_screencast()
                except Exception as e:
                    session['error'] = f'rearm failed: {e}'
    except Exception as e:
        session['status'] = 'error'
        session['error'] = str(e)
    finally:
        # If we fell out of the loop for any reason other than an explicit
        # /stop, make it observable — never leave status 'running' with a dead
        # reader (that is exactly the silent black-pane failure).
        if session.get('status') == 'running':
            session['status'] = 'error'
            session.setdefault('error', 'CDP reader exited unexpectedly')
        try:
            if session.get('ws'):
                session['ws'].close()
        except Exception:
            pass


def _launch_browser(project_id, url):
    chromium = _find_chromium()
    if not chromium:
        return None, 'Chromium not found (install: pip install playwright && playwright install chromium)'
    if not _import_ws():
        return None, 'websocket-client not installed (pip install websocket-client)'
    sid = uuid.uuid4().hex[:12]
    port = _free_port()
    udd = os.path.join(os.path.expanduser('~'), '.clayrune', 'browser_profiles', sid)
    os.makedirs(udd, exist_ok=True)
    args = [
        chromium, '--headless=new', f'--remote-debugging-port={port}',
        '--remote-allow-origins=*', f'--user-data-dir={udd}',
        '--no-first-run', '--no-default-browser-check', '--disable-gpu',
        f'--window-size={VIEW_W},{VIEW_H}', 'about:blank',
    ]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
    except Exception as e:
        return None, f'failed to launch Chromium: {e}'
    session = {
        'session_id': sid, 'project_id': project_id, 'proc': proc, 'port': port,
        'url': url or 'about:blank', 'status': 'running', 'frame': None,
        'frame_seq': 0, 'cmd_queue': queue.Queue(), 'ws': None, 'error': None,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    with browser_lock:
        browser_sessions[sid] = session
    if _register_process:
        try:
            _register_process(proc.pid, name=f'browser pane ({url or "about:blank"})',
                              type='browser', session_id=sid, project_id=project_id,
                              command_preview=f'chromium --headless (browser pane) :{port}',
                              proc=proc)
        except Exception:
            pass
    t = threading.Thread(target=_run_cdp, args=(session,), daemon=True)
    session['thread'] = t
    t.start()
    return session, None


def _kill_browser_session(session):
    session['status'] = 'stopped'
    proc = session.get('proc')
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
        if _unregister_process:
            try:
                _unregister_process(proc.pid)
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


@bp.route('/api/browser/launch', methods=['POST'])
def browser_launch():
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    url = (data.get('url') or 'about:blank').strip()
    if url and not url.startswith(('http://', 'https://', 'about:')):
        url = 'https://' + url
    session, err = _launch_browser(project_id, url)
    if err:
        return jsonify({'error': err}), 501
    return jsonify({'session_id': session['session_id'], 'url': session['url'],
                    'view': {'w': VIEW_W, 'h': VIEW_H}}), 201


@bp.route('/api/browser/stream')
def browser_stream():
    sid = request.args.get('session_id')
    session = browser_sessions.get(sid)
    if not session:
        return jsonify({'error': 'unknown session'}), 404

    def gen():
        last = -1
        idle = 0
        while session['status'] == 'running':
            seq = session.get('frame_seq', 0)
            if seq != last and session.get('frame'):
                last = seq
                idle = 0
                payload = json.dumps({'seq': seq, 'img': session['frame'],
                                      'url': session.get('url')})
                yield f'data: {payload}\n\n'
            else:
                idle += 1
                if idle % 60 == 0:  # ~2s heartbeat keeps the SSE open
                    yield ': ping\n\n'
                _time.sleep(0.033)  # ~30fps delivery cap (was 20fps at 0.05s)
        # final status frame
        yield f'data: {json.dumps({"status": session["status"], "error": session.get("error")})}\n\n'

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/browser/input', methods=['POST'])
def browser_input():
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'unknown or stopped session'}), 404
    q = session['cmd_queue']
    kind = data.get('type')
    try:
        if kind == 'mouse':
            # x,y are already in VIEW_W×VIEW_H page coords (pane scales them)
            q.put(('Input.dispatchMouseEvent', {
                'type': data['action'],  # mousePressed | mouseReleased | mouseMoved
                'x': float(data['x']), 'y': float(data['y']),
                'button': data.get('button', 'left'),
                'clickCount': int(data.get('clickCount', 1)),
                'buttons': int(data.get('buttons', 0)),
            }))
        elif kind == 'wheel':
            q.put(('Input.dispatchMouseEvent', {
                'type': 'mouseWheel', 'x': float(data['x']), 'y': float(data['y']),
                'deltaX': float(data.get('deltaX', 0)), 'deltaY': float(data.get('deltaY', 0)),
            }))
        elif kind == 'text':
            q.put(('Input.insertText', {'text': data.get('text', '')}))
        elif kind == 'key':
            base = {'key': data.get('key', ''), 'code': data.get('code', ''),
                    'windowsVirtualKeyCode': int(data.get('keyCode', 0))}
            q.put(('Input.dispatchKeyEvent', {'type': 'keyDown', **base}))
            q.put(('Input.dispatchKeyEvent', {'type': 'keyUp', **base}))
        elif kind == 'navigate':
            url = (data.get('url') or '').strip()
            if url and not url.startswith(('http://', 'https://', 'about:')):
                url = 'https://' + url
            session['url'] = url
            q.put(('Page.navigate', {'url': url}))
        elif kind == 'back':
            # navigate history: use Page.goBack via CDP (needs the entry id) —
            # simplest is JS history.back through Runtime.evaluate.
            q.put(('Runtime.evaluate', {'expression': 'history.back()'}))
        elif kind == 'forward':
            q.put(('Runtime.evaluate', {'expression': 'history.forward()'}))
        elif kind == 'reload':
            q.put(('Page.reload', {}))
        else:
            return jsonify({'error': f'unknown input type: {kind}'}), 400
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({'error': f'bad input payload: {e}'}), 400
    return jsonify({'ok': True})


def _read_page_selection(session):
    """Read the page's current text selection via a short-lived CDP connection,
    kept OFF the reader thread's single-sender websocket to avoid races."""
    import urllib.request
    websocket = _import_ws()
    if websocket is None:
        return ''
    port = session.get('port')
    try:
        targets = json.load(urllib.request.urlopen(
            f'http://127.0.0.1:{port}/json/list', timeout=2))
        page = next((t for t in targets if t.get('type') == 'page'), None)
        if not page or not page.get('webSocketDebuggerUrl'):
            return ''
        ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                         max_size=None, timeout=3)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                'expression': 'window.getSelection().toString()', 'returnByValue': True}}))
            for _ in range(20):
                r = json.loads(ws.recv())
                if r.get('id') == 1:
                    return (r.get('result', {}).get('result', {}) or {}).get('value') or ''
        finally:
            ws.close()
    except Exception:
        return ''
    return ''


@bp.route('/api/browser/selection', methods=['POST'])
def browser_selection():
    """Return the page's current text selection (for copy-out to the host
    clipboard). Paste-in needs no endpoint — the pane inserts host-clipboard
    text via the existing 'text' input type."""
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'unknown or stopped session'}), 404
    return jsonify({'text': _read_page_selection(session)})


@bp.route('/api/browser/stop', methods=['POST'])
def browser_stop():
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session:
        return jsonify({'error': 'unknown session'}), 404
    _kill_browser_session(session)
    with browser_lock:
        browser_sessions.pop(sid, None)
    return jsonify({'ok': True})


@bp.route('/api/project/<project_id>/browser/status')
def browser_status(project_id):
    out = []
    for sid, s in list(browser_sessions.items()):
        if s.get('project_id') == project_id:
            out.append({'session_id': sid, 'url': s.get('url'),
                        'status': s.get('status'), 'started_at': s.get('started_at')})
    return jsonify({'sessions': out})
