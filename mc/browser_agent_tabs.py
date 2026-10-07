"""MC-1079: handles for background tabs in an already-running pane browser.

These are targets, never browser/process sessions. The existing hidden-tab
disposition keeps them and their popups away from the pane's focus/stream.
No Origin header uses the existing agent routing signal; it grants no authority.
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from mc.core import _log

_tabs: dict[str, AgentTab] = {}
_lock = threading.RLock()


class AdoptionError(RuntimeError):
    """A failed open with a retained target the caller can still Stop."""
    def __init__(self, handle: str):
        super().__init__('agent tab open failed; cleanup needs retry')
        self.handle = handle


def _br():
    from mc.blueprints import browser_routes
    return browser_routes


class AgentTab:
    def __init__(self, pane: dict, project_id: str | None):
        self.pane = pane
        self.project_id = project_id
        self.handle = 'agent-tab-' + uuid.uuid4().hex[:12]
        self.marker = 'about:blank#clayrune-hidden-read-' + uuid.uuid4().hex
        self.target_id: str | None = None
        self.browser: Any = None
        self.page: Any = None
        self.url = self.marker
        self.lock = threading.RLock()

    def open(self, url: str) -> None:
        from mc.browser_hidden_read import _Conn
        br = _br()
        self.pane.setdefault('hidden_read_urls', set()).add(self.marker)
        try:
            self.browser = _Conn(br._cdp_browser_conn(self.pane, br._cdp_version(self.pane), timeout=5))
            self.target_id = self.browser.call('Target.createTarget', {
                'url': self.marker, 'background': True})['targetId']
            # Pin the disposition before navigating away from the marker.
            self.pane.setdefault('page_disposition', {})[self.target_id] = 'hidden'
            transport = br._cdp_transport(self.pane)
            address = ('pipe:' + str(self.target_id) if transport is not None else
                       f"ws://127.0.0.1:{self.pane.get('port')}/devtools/page/{self.target_id}")
            self.page = _Conn(br._cdp_page_conn(self.pane, {
                'id': self.target_id, 'webSocketDebuggerUrl': address}, timeout=5))
            self.page.call('Page.enable')
            ua = (br._mobile_ua_override(self.pane) if self.pane.get('device_mode') == 'mobile'
                  else self.pane.get('ua_override'))
            if ua:
                self.page.call('Network.setUserAgentOverride', ua)
            if self.pane.get('device_mode') == 'mobile':
                vw, vh = self.pane.get('view') or (br.VIEW_W, br.VIEW_H)
                method, params = br._mobile_metrics_cmd(vw, vh, self.pane.get('dpr', 1))
                self.page.call(method, params)
            self.page.call('Emulation.setFocusEmulationEnabled', {'enabled': True})
            self.page.call('Page.navigate', {'url': url})
            self.url = url
        except Exception:
            try:
                self.close()
            except Exception as e:
                _log(f'[browser-agent-tab] failed-open cleanup failed: {type(e).__name__}', flush=True)
            raise

    def running(self) -> bool:
        return self.pane.get('status') == 'running' and self.page is not None

    def evaluate(self, expression: str) -> tuple[bool, Any]:
        with self.lock:
            try:
                result = self.page.call('Runtime.evaluate', {
                    'expression': expression, 'returnByValue': True, 'awaitPromise': False})
                if result.get('exceptionDetails') or result.get('result', {}).get('subtype') == 'error':
                    return False, 'eval_exception'
                return True, result.get('result', {}).get('value')
            except Exception as e:
                return False, f'cdp_error:{type(e).__name__}'

    def input(self, data: dict) -> None:
        br = _br()
        with self.lock:
            if data.get('type') == 'navigate':
                url = (data.get('url') or '').strip()
                if url and not url.startswith(('http://', 'https://', 'about:')):
                    url = 'https://' + url
                if br._is_clayrune_own_origin(url):
                    raise PermissionError("the browser pane may not navigate to Clayrune's own origin")
                self.page.call('Page.navigate', {'url': url})
                self.url = url
            else:
                for method, params in br._input_commands(data):
                    self.page.call(method, params)

    def close(self) -> None:
        with self.lock:
            if self.browser is not None and self.target_id:
                # On failure retain the handle and disposition for a retry; never
                # fall back to closing Chromium or removing its saved profile.
                if self.pane.get('status') == 'running':
                    result = self.browser.call('Target.closeTarget', {'targetId': self.target_id}, timeout=3)
                    if result.get('success') is False:
                        raise RuntimeError('target could not be closed')
            for conn in (self.page, self.browser):
                if conn is not None:
                    conn.close()
            self.page = self.browser = None
            self.pane.get('hidden_read_urls', set()).discard(self.marker)
            self.pane.get('page_disposition', {}).pop(self.target_id, None)
            with _lock:
                _tabs.pop(self.handle, None)


def adopt(pane: dict, url: str, project_id: str | None) -> dict:
    tab = AgentTab(pane, project_id)
    with _lock:
        _tabs[tab.handle] = tab
    try:
        tab.open(url)
    except Exception as e:
        with _lock:
            retained = tab.handle in _tabs
        if retained:
            raise AdoptionError(tab.handle) from e
        raise
    return {'session_id': tab.handle, 'browser_session_id': pane['session_id'],
            'target_id': tab.target_id, 'agent_tab': True, 'reused': True,
            'url': url, 'profile': pane.get('profile')}


def browser_closed(pane: dict) -> None:
    with _lock:
        tabs = [t for t in _tabs.values() if t.pane is pane]
    for tab in tabs:
        try:
            tab.close()
        except Exception as e:
            _log(f'[browser-agent-tab] browser-close cleanup failed: {type(e).__name__}', flush=True)


def route(operation: str, data: dict):
    """None for a browser session; a Flask response for an agent-tab handle."""
    from flask import jsonify
    sid = data.get('session_id')
    if not isinstance(sid, str) or not sid.startswith('agent-tab-'):
        return None
    with _lock:
        tab = _tabs.get(sid)
    if tab is None or (operation != 'stop' and not tab.running()):
        if tab is not None and tab.pane.get('status') != 'running':
            browser_closed(tab.pane)
        body, status = _br()._read_error('unknown_session', 'unknown or stopped agent tab', 404)
        return jsonify(body), status
    br = _br()
    try:
        if operation == 'stop':
            tab.close()
            return jsonify({'ok': True})
        if operation == 'input':
            tab.input(data)
            return jsonify({'ok': True})
        selector = data.get('selector')
        if selector is not None and not isinstance(selector, str):
            return jsonify(br._read_error('bad_request', 'selector must be a string', 400)[0]), 400
        expression = br._READ_JS_TEMPLATE.replace('__SEL__', json.dumps(selector)).replace(
            '__CAP__', json.dumps(br._JS_SAFETY_CHAR_CAP))
        expression = '(() => {const value = (' + expression.strip().rstrip(';') + '); value.url = location.href; return value;})()'
        ok, value = tab.evaluate(expression)
        if not ok:
            body, status = br._read_error('cdp_error', str(value), 502)
        elif isinstance(value, dict) and br._is_clayrune_own_origin(value.get('url') or tab.url):
            body, status = br._read_error('own_origin_blocked', "the browser pane may not read Clayrune's own origin", 403)
        else:
            final_url = (value.get('url') or tab.url) if isinstance(value, dict) else tab.url
            body, status = br._build_read_envelope(final_url, value)
        _log(f'[browser-agent-tab] read handle={sid} project={tab.project_id} status={status}', flush=True)
        return jsonify(body), status
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (KeyError, ValueError, TypeError) as e:
        if operation == 'read':
            body, status = br._read_error('cdp_error', 'unexpected agent tab read result', 502)
            return jsonify(body), status
        return jsonify({'error': f'bad input payload: {e}'}), 400
    except Exception as e:
        _log(f'[browser-agent-tab] {operation} failed: {type(e).__name__}', flush=True)
        if operation == 'read':
            body, status = br._read_error('cdp_error', 'agent tab read failed', 502)
            return jsonify(body), status
        return jsonify({'error': 'agent tab command failed'}), 502
