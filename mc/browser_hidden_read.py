"""Read a page in a hidden background tab of a profile that is already open in the pane
(backlog b1e1b23c, MC-1059 piece C).

`ProfilePageReader` refuses `profile_in_use` when the profile's Chromium is already running
in someone's pane: reading there would navigate the tab the user is looking at, and a second
Chromium on the same profile directory corrupts it. The default profile is nearly always open,
so that refusal made the agent-read route useless in practice.

`HiddenTabReader` reads in the SAME Chromium instead, in a tab of its own:

  1. `Target.createTarget {background: true}` on a browser-level connection, with a one-off URL
     (`about:blank#clayrune-hidden-read-<random>`) registered on the pane session first.
     The pane's CDP thread closes every page it did not ask for (`_page_disposition`, a restored
     tab); it recognises this URL and leaves the tab alone: not attached, not made the active
     tab, not in `session['tabs']`. A popup the page opens is closed, not shown.
  2. A page-scoped connection to that target runs `Page.navigate` and the same read template
     `ProfilePageReader` uses. Nothing is ever sent to the user's tab.
  3. `close()` closes the target and both connections. A browser that has gone away, or one
     that cannot create the tab, ends in `profile_in_use` as before: that refusal is kept only
     for the cases this cannot work.

`Emulation.setFocusEmulationEnabled` is set on the hidden tab only: a background tab reports
`visibilityState: hidden`, and measured 2026-10-06 that is all it takes for a page's own
scripts to behave differently from the visible tab; with it the tab reads as visible and
focused, and the user's tab is untouched (the override is per target).

Live-verified against a real Chromium in tests/test_browser_agent_read_hidden.py.
"""
from __future__ import annotations

import itertools
import json
import threading
import time as _time
import uuid

from mc.blueprints import browser_routes as br

HIDDEN_URL_PREFIX = 'about:blank#clayrune-hidden-read-'
_CALL_TIMEOUT_S = 8


class _Conn:
    """One CDP connection with its own ids; `call` returns a result dict or raises."""

    def __init__(self, conn):
        self.conn = conn
        self._ids = itertools.count(1)

    def call(self, method, params=None, timeout=_CALL_TIMEOUT_S):
        i = next(self._ids)
        self.conn.send(json.dumps({'id': i, 'method': method, 'params': params or {}}))
        end = _time.time() + timeout
        while _time.time() < end:
            r = json.loads(self.conn.recv())
            if r.get('id') != i:
                continue
            if r.get('error'):
                raise RuntimeError(str((r['error'] or {}).get('message') or 'cdp error'))
            return r.get('result') or {}
        raise TimeoutError(method)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


class HiddenTabReader(br.ProfilePageReader):
    """`ProfilePageReader`'s settle-and-read loop, run in a background tab of the pane's
    own Chromium. Same `read()` / `close()` contract and the same error bodies."""

    def __init__(self, project_id, profile):
        super().__init__(project_id, profile)
        self._pane = None
        self._browser = None
        self._page = None
        self._target_id = None
        self._marker = None
        self._lock = threading.Lock()

    def _cannot(self, why):
        return self._fail('profile_in_use',
                          f"profile '{self.profile}' is open in a live browser pane and a "
                          f"background tab could not be opened in it ({why})")

    def _page_entry(self, pane):
        """The `/json/list`-shaped entry `_cdp_page_conn` takes for the new tab: a pipe session
        needs only the id, a port session the page's own websocket address."""
        tid = self._target_id
        if br._cdp_transport(pane) is not None:
            return {'id': tid, 'webSocketDebuggerUrl': 'pipe:' + tid}
        return {'id': tid, 'webSocketDebuggerUrl':
                f"ws://127.0.0.1:{pane.get('port')}/devtools/page/{tid}"}

    def _open(self, url):
        if not br.named_profile_exists(self.profile):
            return self._fail('no_profile', f"no saved browser profile '{self.profile}'")
        pane = br._session_using_profile(self.profile)
        if not pane or pane.get('status') != 'running':
            return self._cannot('the pane is not running')
        if br._import_ws() is None:
            return self._cannot('websocket-client is not installed')
        marker = HIDDEN_URL_PREFIX + uuid.uuid4().hex
        # Registered BEFORE the tab exists: the pane's CDP thread hears `targetCreated` before
        # this call returns, and an unregistered blank page is closed on sight.
        pane.setdefault('hidden_read_urls', set()).add(marker)
        self._pane, self._marker = pane, marker
        try:
            self._browser = _Conn(br._cdp_browser_conn(pane, br._cdp_version(pane), timeout=5))
            self._target_id = self._browser.call(
                'Target.createTarget', {'url': marker, 'background': True})['targetId']
            self._page = _Conn(br._cdp_page_conn(pane, self._page_entry(pane), timeout=5))
            self._page.call('Emulation.setFocusEmulationEnabled', {'enabled': True})
            self._page.call('Page.navigate', {'url': url})
        except Exception as e:
            self.close()
            return self._cannot(f'{type(e).__name__}')
        # The tab starts at the marker URL: until it leaves it, navigation has not happened.
        self._last_href = marker
        self._session = {'status': 'running', 'error': None}
        return None

    def _navigate(self, url):
        self._page.call('Page.navigate', {'url': url})

    def _evaluate(self, expression, timeout, recv_rounds):
        page = self._page
        if page is None:
            return False, 'no_page_target'
        websocket = br._import_ws()
        try:
            page.conn.settimeout(timeout)
            i = next(page._ids)
            page.conn.send(json.dumps({'id': i, 'method': 'Runtime.evaluate', 'params': {
                'expression': expression, 'returnByValue': True, 'awaitPromise': False}}))
            for _ in range(recv_rounds):
                try:
                    raw = page.conn.recv()
                except Exception as e:
                    if isinstance(e, getattr(websocket, 'WebSocketTimeoutException', ())):
                        return False, 'timeout'
                    return False, f'cdp_error:{e}'
                r = json.loads(raw)
                if r.get('id') != i:
                    continue
                if r.get('error'):
                    return False, f"cdp_error:{(r['error'] or {}).get('message')}"
                if r.get('result', {}).get('exceptionDetails'):
                    return False, f"eval_exception:{r['result']['exceptionDetails']}"
                result = r.get('result', {}).get('result', {}) or {}
                if result.get('subtype') == 'error':
                    return False, f'eval_exception:{result.get("description")}'
                return True, result.get('value')
            return False, 'timeout'
        except Exception as e:
            return False, f'cdp_error:{e}'

    def _ended(self):
        pane = self._pane
        if not pane or pane.get('status') != 'running':
            return 'the browser pane this read was using has closed'
        return None

    def close(self):
        """Close the hidden tab and its connections. Never touches the user's tab or the
        pane's Chromium. Safe to call twice."""
        with self._lock:
            tid, browser, page = self._target_id, self._browser, self._page
            pane, marker = self._pane, self._marker
            self._target_id = self._browser = self._page = self._pane = self._marker = None
            self._session = None
        if browser is not None and tid:
            try:
                browser.call('Target.closeTarget', {'targetId': tid}, timeout=3)
            except Exception:
                pass
        for c in (page, browser):
            if c is not None:
                c.close()
        if pane is not None:
            if marker:
                pane.get('hidden_read_urls', set()).discard(marker)
            if tid:
                (pane.get('page_disposition') or {}).pop(tid, None)
