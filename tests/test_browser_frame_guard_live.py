"""MC-1083 follow-up: a mobile pane's frame is the size its override declares.

Ron, 2026-10-10, phone: x.com at a third of the pane, left-aligned, black
around it (411/1280). Measured: a window resize that lands after
Emulation.setDeviceMetricsOverride leaves the page laid out at the override
but the screencast captured at the window's size, and nothing re-asserted it.

Drives the REAL pane launch (_launch_browser + reader) on its OWN Chromium
(throwaway profile under tmp_path, killed by its own PID) and a local page.
Runs once with the guard and once with it disabled, so the test shows the
split exists here and that the guard is what removes it. (The launch-time
race that showed it in the harness against x.com, 3/3, does not reproduce
against a local page that loads before the window pin lands, so it is not
tested here: a test that passes with the guard off proves nothing.)
"""
import http.server
import threading
import time

import pytest

from mc import browser_frame_guard as fg
from mc.blueprints import browser_routes as br

VIEW = (411, 754)
_PAGE = (b'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
         b'<title>fg</title><body style="margin:0;background:#2e7d32"></body>')


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)


@pytest.fixture
def pane(tmp_path, monkeypatch):
    if not br._find_chromium() or not br._import_ws():
        pytest.skip('Chromium or websocket-client unavailable')
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    made = []

    def launch():
        session, err = br._launch_browser(
            'test_frame_guard', f'http://127.0.0.1:{srv.server_address[1]}/',
            ephemeral=True, dpr=2, view=VIEW, mobile=True)
        assert not err, err
        made.append(session)
        return session
    yield launch
    for s in made:
        br._kill_browser_session(s)
        with br.browser_lock:
            br.browser_sessions.pop(s['session_id'], None)
    srv.shutdown()


def _frame(session, settle=6.0):
    """The frame size the pane would draw, once it has stopped changing."""
    end, last, since = time.time() + 30, None, time.time()
    while time.time() < end:
        cur = (session.get('frame_seq'), session.get('frame_w'), session.get('frame_h'))
        if cur[0] and cur[1:] != last:
            last, since = cur[1:], time.time()
        if last and time.time() - since >= settle:
            return last
        time.sleep(0.1)
    return last


def _resize_window_after_override(session):
    """What a restored profile window, a floor correction or a fit does: the
    window changes size while the override stays put."""
    keep = session['view']
    session['view'] = (1280, 800)
    try:
        br._fit_windows(session)
    finally:
        session['view'] = keep


@pytest.mark.parametrize('guard', [False, True])
def test_window_resize_after_override(pane, monkeypatch, guard):
    if not guard:
        monkeypatch.setattr(fg, 'due', lambda *a, **k: None)
    session = pane()
    _frame(session)
    _resize_window_after_override(session)
    got = _frame(session)
    if guard:
        assert got == VIEW, f'frame {got} is not the override {VIEW}'
    else:
        assert got != VIEW, 'the split did not reproduce; this test no longer measures the guard'


def test_mobile_popup_opens_at_the_override(pane, monkeypatch):
    """A window.open() popup in a mobile pane comes up at the override with
    the guard off: the attach path's override alone sizes the frame, so the
    popup needs no window fit (which is skipped in mobile mode, as apply_view
    already does). A pin, not a reproduction: the old fit-after-attach also
    passed this 3/3, because _fit_windows skips a window whose innerWidth
    already equals the view."""
    monkeypatch.setattr(fg, 'due', lambda *a, **k: None)
    session = pane()
    _frame(session)
    root = session.get('active_target_id')
    session['cmd_queue'].put(('Runtime.evaluate', {'expression': "window.open('/popup')",
                                                   'userGesture': True}))
    end = time.time() + 15
    while time.time() < end and session.get('active_target_id') == root:
        time.sleep(0.1)
    assert session.get('active_target_id') != root, 'popup never became the active tab'
    got = _frame(session)
    assert got == VIEW, f'popup frame {got} is not the override {VIEW}'
