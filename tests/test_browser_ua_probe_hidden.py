"""The UA guard's probe tab must never be read as "the page" (2026-10-29).

`_start_ua_guard` opens a throwaway background tab (Chromium's own devtools page on a port
session, chrome://version on a pipe one) to read the native client hints, on the reader
thread, right after `_launch_browser` returns. `ProfilePageReader.read()` starts polling at
that same moment, and `_pick_page_target` prefers any non-blank page over the root tab's
about:blank, so a poll that landed inside the probe's lifetime evaluated the probe tab. The
Desk discovery pane then failed with `own_origin_blocked` on
http://127.0.0.1:<cdp port>/json/version: test_desk_connect_real_chromium's first test
failed 2 of 5 runs after three other Chromium test files, 0 of 6 alone.

The real window is ~250-550 ms, so these tests widen it to 2.5 s to make the race certain
instead of a matter of timing, and assert that the widening really happened inside it.
Real Chromium, throwaway profile, its own port, closed by its own handle.
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

from mc import browser_default_ua, state
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions

pytestmark = pytest.mark.skipif(not br._find_chromium() or not br._import_ws(),
                                reason='Chromium or websocket-client unavailable')

PAGE = b'<!doctype html><title>probe</title><body><p>the real page</p></body>'


class _ThrowawayReader(br.ProfilePageReader):
    SETTLE_TIMEOUT_S = 15.0

    def _open(self, url):
        session, err = br._launch_browser(self.project_id, url, ephemeral=True)
        if err or session is None:
            return self._fail('launch_failed', err or 'browser failed to start')
        self._session = session
        return None


@pytest.fixture
def page_url():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{httpd.server_address[1]}/'
    finally:
        httpd.shutdown()


@pytest.mark.parametrize('transport', ['port', 'pipe'])
def test_a_read_during_the_ua_probe_reads_the_real_page(transport, page_url, tmp_path, monkeypatch):
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path))
    monkeypatch.setattr(br, '_swept_orphans', True)
    monkeypatch.setattr(br, '_register_process', None)
    monkeypatch.setattr(br, '_unregister_process', None)
    monkeypatch.setattr(br, '_SERVER_PORT', 5199)          # the page's own port is not Clayrune's
    monkeypatch.setitem(state.CONFIG, 'browser_cdp_pipe', transport == 'pipe')
    widened = []
    real_fill = browser_default_ua.fill_high_entropy

    def slow_fill(*a, **k):                                 # runs while the probe tab is open
        widened.append(time.time())
        time.sleep(2.5)
        return real_fill(*a, **k)
    monkeypatch.setattr(browser_default_ua, 'fill_high_entropy', slow_fill)

    reader = _ThrowawayReader('test_ua_probe', '')
    try:
        body = reader.read(page_url)
        session_error = reader._session and reader._session.get('error')
    finally:
        reader.close()
        browser_sessions.clear()
    assert widened, 'the probe window was never widened: this run did not exercise the race'
    assert body.get('ok'), body
    assert body['final_url'] == page_url
    assert 'the real page' in body['content']['text']
    assert not session_error, session_error                 # the probe itself still got the hints


def test_the_probe_url_is_hidden_from_target_pickers():
    session = {'hidden_read_urls': {'http://127.0.0.1:9/json/version' + br._UA_PROBE_FRAGMENT}}
    targets = [
        {'id': 'probe', 'type': 'page', 'url': 'http://127.0.0.1:9/json/version' + br._UA_PROBE_FRAGMENT,
         'webSocketDebuggerUrl': 'ws://x/probe'},
        {'id': 'root', 'type': 'page', 'url': 'about:blank', 'webSocketDebuggerUrl': 'ws://x/root'},
    ]
    before = br._pick_page_target(targets)
    after = br._pick_page_target(br._without_hidden_reads(session, targets))
    assert before is not None and before['id'] == 'probe'   # what every poll used to see
    assert after is not None and after['id'] == 'root'
