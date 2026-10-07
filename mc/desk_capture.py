"""Real product screenshots, saved as ordinary Studio library images."""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit

from mc import browser_cdp_pipe, desk, desk_pieces
from mc.blueprints import browser_routes
from mc.core import _log
from mc.desk_capture_network import CaptureProxy, address, origin
from mc.desk_connect.pane_reader import chromium_args, WEBRTC_OFF_JS
from mc.desk_connect.net_guard import Blocked

Error = desk_pieces.PieceError
_slots = threading.BoundedSemaphore(2)
PAGES = ({'id': 'dashboard', 'label': 'Projects'}, {'id': 'floor', 'label': 'Floor'},
         {'id': 'desk', 'label': 'Desk'}, {'id': 'studio', 'label': 'Studio'},
         {'id': 'connections', 'label': 'Connections'})


def settings(project_id: str, *, load_project, local_url: str) -> dict:
    if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', project_id):
        raise Error('Choose an existing product.')
    p = load_project(project_id)
    if not p or p.get('_is_incognito_project'):
        raise Error('Choose an existing product.', 404)
    own = project_id == 'mission_control'
    with desk._store_lock:
        saved = (desk._read_store().get('capture_apps') or {}).get(project_id) or {}
    return {'project_id': project_id, 'name': p.get('name') or project_id,
            'app_address': local_url if own else saved.get('address') or p.get('app_address') or p.get('app_url') or '',
            'pages': list(PAGES) if own else [], 'clayrune': own}


def save_address(project_id: str, raw, *, load_project, local_url: str, human_typed: bool) -> dict:
    info = settings(project_id, load_project=load_project, local_url=local_url)
    if info['clayrune']:
        raise Error('Clayrune uses the address of this server.')
    try:
        url = address(raw)
        # Without human provenance this setting never grants a private exception.
        CaptureProxy(allowed=(origin(url),) if human_typed else ()).addresses(*origin(url))
    except (ValueError, Blocked) as e:
        raise Error(str(e)) from None
    with desk._store_lock:
        if desk.STORE_PATH is None:
            raise Error('The material library is not connected.', 503)
        if desk.STORE_PATH.exists():
            try:
                if not isinstance(json.loads(desk.STORE_PATH.read_text(encoding='utf-8')), dict):
                    raise ValueError('not an object')
            except Exception as e:
                _log(f'[desk_capture] app address store unreadable: {type(e).__name__}', flush=True)
                raise Error('The saved app addresses could not be read. Nothing was changed.', 503) from None
        store = desk._read_store()
        store.setdefault('capture_apps', {})[project_id] = {'address': url, 'user_entered': human_typed}
        desk._write_store(store)
    return settings(project_id, load_project=load_project, local_url=local_url)


def target(project_id: str, page, *, load_project, local_url: str) -> tuple[str, set, str | None]:
    info = settings(project_id, load_project=load_project, local_url=local_url)
    base = info['app_address']
    if not base:
        raise Error('What is the address where you open this app?', 409)
    if info['clayrune']:
        if not isinstance(page, str) or page not in {p['id'] for p in PAGES}:
            raise Error('Choose one of Clayrune’s pages.')
        return local_url, {origin(local_url)}, page
    if (not isinstance(page, str) or len(page) > 2000 or not page.startswith('/') or page.startswith('//')
            or '\\' in page or any(ord(c) < 32 for c in page)):
        raise Error('Enter a page path starting with /, such as /pricing.')
    try:
        url = address(urljoin(base.rstrip('/') + '/', page))
        if origin(url) != origin(base) or urlsplit(url).scheme != urlsplit(base).scheme:
            raise ValueError('Choose a page at this product’s app address.')
    except (ValueError, Blocked) as e:
        raise Error(str(e)) from None
    with desk._store_lock:
        saved = (desk._read_store().get('capture_apps') or {}).get(project_id) or {}
    allowed = {origin(base)} if saved.get('address') == base and saved.get('user_entered') is True else set()
    # Only this server's loopback address is intrinsically allowed.
    server_port = origin(local_url)[1]
    allowed.update({('127.0.0.1', server_port), ('localhost', server_port)})
    return url, allowed, None


def screenshot(url: str, allowed: set, *, project_id: str, page_id=None) -> bytes:
    chrome = browser_routes._find_chromium()
    if not chrome:
        raise Error('Chromium is not installed on this server. Capture is unavailable.', 503)
    proxy = CaptureProxy(allowed=allowed, max_tunnels=512)
    # Preflight and proxy share the same policy; DNS is rechecked at connect time.
    try:
        proxy.addresses(*origin(url))
    except (ValueError, Blocked) as e:
        raise Error(str(e)) from None
    proc = transport = None
    deadline = time.monotonic() + 35
    with tempfile.TemporaryDirectory(prefix='clayrune-capture-') as profile:
        try:
            port = proxy.start()
            args = [chrome, '--headless=new', f'--user-data-dir={profile}', '--no-first-run',
                    '--no-default-browser-check', '--disable-gpu', *chromium_args(port), 'about:blank']
            proc, transport = browser_cdp_pipe.spawn(args, dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                               stderr=subprocess.DEVNULL, creationflags=browser_routes._POPEN_FLAGS))
            register = browser_routes._register_process
            if register is None:
                raise Error('Capture process tracking is not connected.', 503)
            register(proc, name='Studio product capture', proc_type='browser', project_id=project_id,
                     command_preview='chromium --headless (Studio capture) pipe')

            def call(method, params=None, sid=None):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('capture deadline')
                result = transport.request(method, params, session_id=sid, timeout=max(1, min(int(remaining), 8)))
                if result.get('exceptionDetails'):
                    raise Error('The selected page could not be opened.', 502)
                return result

            tid = call('Target.createTarget', {'url': 'about:blank'})['targetId']
            sid = call('Target.attachToTarget', {'targetId': tid, 'flatten': True})['sessionId']
            call('Page.enable', sid=sid)
            call('Browser.setDownloadBehavior', {'behavior': 'deny'})
            call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False}, sid)
            call('Page.addScriptToEvaluateOnNewDocument', {'source': WEBRTC_OFF_JS}, sid)
            nav = call('Page.navigate', {'url': url}, sid)
            if nav.get('errorText'):
                raise Error('The app could not be opened. Check its address and that it is running.', 502)
            ready = False
            while time.monotonic() < deadline:
                value = call('Runtime.evaluate', {'expression': "JSON.stringify({ready:document.readyState,url:location.href,type:document.contentType})", 'returnByValue': True}, sid)
                state = json.loads(value['result'].get('value') or '{}')
                if state.get('ready') == 'complete' and state.get('url') != 'about:blank':
                    if state.get('url', '').startswith('chrome-error:') or state.get('type') not in ('text/html', 'application/xhtml+xml'):
                        raise Error('This address did not open a web page.', 502)
                    ready = True
                    break
                time.sleep(.1)
            if not ready:
                raise TimeoutError('page load')
            if page_id:
                # Fixed first-party navigation commands only. Never execute text
                # supplied by a caller or learned from the page.
                for _ in range(60):
                    v = call('Runtime.evaluate', {'expression': "typeof sidebarNav==='function' && typeof window.deskV1Nav==='function' && typeof _globalConfig!=='undefined' && _globalConfig && typeof _globalConfig.desk_v1==='boolean'", 'returnByValue': True}, sid)
                    if v['result'].get('value'):
                        break
                    time.sleep(.1)
                else:
                    raise Error('Clayrune’s dashboard did not finish opening.', 502)
                if page_id in ('dashboard', 'floor'):
                    script = "sidebarNav(" + json.dumps(page_id) + ")"
                else:
                    script = "(async()=>{await openDesk();if(!document.querySelector('.desk-v1-shell'))throw Error('Desk unavailable');await window.DeskV1Store.load();if(window.DeskV1Store.gate())throw Error('Desk not ready');window.deskV1Nav(" + json.dumps('home' if page_id == 'desk' else page_id) + ");})()"
                call('Runtime.evaluate', {'expression': script, 'awaitPromise': True}, sid)
            # Let app rendering/images settle; no networkidle wait on live SSE.
            call('Runtime.evaluate', {'expression': "new Promise(r=>{let t=setTimeout(r,1500);Promise.all([document.fonts.ready,...Array.from(document.images,i=>i.decode().catch(()=>{}))]).then(()=>{clearTimeout(t);setTimeout(r,350)})})", 'awaitPromise': True}, sid)
            shot = call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False}, sid)
            return base64.b64decode(shot['data'], validate=True)
        except Error:
            raise
        except Exception as e:
            _log(f'[desk_capture] screenshot failed: {type(e).__name__}', flush=True)
            raise Error('Capture did not finish. Check the app address and try again.', 502) from None
        finally:
            if transport is not None:
                try:
                    transport.request('Browser.close', timeout=2)
                except Exception as e:
                    _log(f'[desk_capture] browser close failed: {type(e).__name__}', flush=True)
                transport.close()
            if proc is not None:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()  # This exact Popen is ours, never a name/listening PID.
                    proc.wait(timeout=3)
            proxy.close()


def capture(project_id, page, *, load_project, local_url: str) -> dict:
    url, allowed, page_id = target(project_id, page, load_project=load_project, local_url=local_url)
    if not _slots.acquire(blocking=False):
        raise Error('Two captures are already running. Try again shortly.', 429)
    try:
        png = screenshot(url, allowed, project_id=project_id, page_id=page_id)
        if not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise Error('The browser did not return a picture.', 502)
        name = f'{project_id}-{page_id or "capture"}.png'
        project = load_project(project_id) or {}
        label = next((p['label'] for p in PAGES if p['id'] == page_id), page)
        item = desk_pieces.save_to_library(name, io.BytesIO(png), title=f'{project.get("name") or project_id} · {label}')
        return {'item': item}
    finally:
        _slots.release()
