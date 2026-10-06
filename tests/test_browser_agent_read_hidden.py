"""MC-1059 piece C (backlog b1e1b23c): read while the pane is open.

`read-digest` on a profile already open in someone's pane used to refuse `profile_in_use`, and the
default profile is nearly always open. `HiddenTabReader` (mc/browser_hidden_read.py) reads in a NEW
background tab of that same Chromium instead: never the user's tab, closed afterwards, and kept out of
the pane's tab strip and its page targets.

Two halves:
  * fake-pane tests pin the wiring: which reader the route picks, what it refuses, and that
    `profile_in_use` survives for the cases a hidden tab cannot work;
  * live tests run a REAL headless Chromium (pipe and port transport), a page served from 127.0.0.1,
    in a throwaway profile root under tmp_path. Skipped when Chromium or websocket-client is missing.
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

from tests.test_browser_agent_read import (  # noqa: F401  (fixtures are used by name)
    FEED, FakeBrowser, _answer, _digest, _enable, env, wired)

PAGE = ('<!doctype html><title>Feed</title><body><p id=v></p><p>hello from the hidden read</p>'
        '<script>document.getElementById("v").textContent="visibility=" + document.visibilityState'
        ' + " focus=" + document.hasFocus();</script></body>')


# ── wiring (no Chromium) ─────────────────────────────────────────────────────

def _pane_session(env, **extra):
    """A live pane session holding profile `li`, as the pane registers it."""
    s = {'session_id': 'pane1', 'status': 'running', 'profile': 'li', 'port': 1, 'cdp': None,
         'project_id': 'mission_control', 'tabs': {}, **extra}
    env.br.browser_sessions['pane1'] = s
    return s


@pytest.fixture()
def pane_open(env):
    yield lambda **kw: _pane_session(env, **kw)
    env.br.browser_sessions.pop('pane1', None)


def test_a_profile_open_in_a_pane_is_read_in_a_hidden_tab_not_refused(env, wired, pane_open, monkeypatch):
    from mc.blueprints import browser_agent_read_routes as routes
    from mc import browser_hidden_read as hidden
    fb, fm = wired()
    _enable(env)
    pane_open()
    opened = []

    class FakeHidden(hidden.HiddenTabReader):
        def _open(self, url):
            opened.append(url)
            return self._fail('profile_in_use', 'fake: cannot open a hidden tab')
    monkeypatch.setattr(routes, '_AllowListedHiddenReader',
                        type('R', (routes._AllowList, FakeHidden), {}))
    r = _digest(env)
    assert opened == [FEED]                                   # the hidden reader was the one asked
    assert r.status_code == 409 and r.get_json()['error'] == 'profile_in_use'   # kept where it cannot work
    assert fb.launched == 0 and fb.navigated == []            # the pane's Chromium was never launched into


def test_a_profile_not_open_anywhere_still_launches_its_own_chromium(env, wired):
    fb, _ = wired()
    _enable(env)
    r = _digest(env)
    assert r.status_code == 200 and fb.launched == 1


def test_the_hidden_reader_refuses_when_the_pane_has_gone(env, wired, pane_open):
    from mc.browser_hidden_read import HiddenTabReader
    wired()
    s = pane_open()
    s['status'] = 'stopped'
    reader = HiddenTabReader('mission_control', 'li')
    body = reader.read(FEED)
    assert body['ok'] is False and body['error'] == 'profile_in_use'
    reader.close()


def test_the_pane_leaves_a_registered_hidden_tab_alone_and_closes_a_stranger(env):
    br = env.br
    s = {'root_target_id': 'root', 'hidden_read_urls': {'about:blank#clayrune-hidden-read-abc'}}
    info = {'type': 'page', 'targetId': 'T1', 'url': 'about:blank#clayrune-hidden-read-abc'}
    assert br._page_disposition(s, info) == 'hidden'
    # a popup the hidden page opens must not reach the pane
    assert br._page_disposition(s, {'type': 'page', 'targetId': 'T2', 'url': 'https://x', 'openerId': 'T1'}) == 'close'
    # an unregistered blank page is still the restored-tab case: closed
    assert br._page_disposition(s, {'type': 'page', 'targetId': 'T3', 'url': 'about:blank'}) == 'close'
    # a popup opened by the user's own tab is still shown
    assert br._page_disposition(s, {'type': 'page', 'targetId': 'T4', 'url': 'https://x', 'openerId': 'root'}) == 'focus'


def test_hidden_tabs_are_not_in_the_target_list_the_pane_picks_from(env):
    br = env.br
    s = {'hidden_read_urls': {'about:blank#clayrune-hidden-read-abc'}, 'page_disposition': {'T9': 'hidden'}}
    listed = [{'id': 'T0', 'type': 'page', 'url': 'https://user.example/', 'webSocketDebuggerUrl': 'x'},
              {'id': 'T9', 'type': 'page', 'url': 'https://www.linkedin.com/feed/', 'webSocketDebuggerUrl': 'x'},
              {'id': 'T8', 'type': 'page', 'url': 'about:blank#clayrune-hidden-read-abc', 'webSocketDebuggerUrl': 'x'}]
    assert [t['id'] for t in br._without_hidden_reads(s, listed)] == ['T0']
    assert br._without_hidden_reads({}, listed) == listed


# ── live Chromium ────────────────────────────────────────────────────────────

class _Site:
    def __init__(self):
        self.hits = []
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                site.hits.append(self.path)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(PAGE.encode())

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'


@pytest.fixture(params=['pipe', 'port'])
def live_pane(request, monkeypatch, tmp_path):
    pytest.importorskip('websocket')
    from mc import state
    from mc.blueprints import browser_routes as br
    from mc.state import browser_sessions
    if not br._find_chromium():
        pytest.skip('Chromium not available')
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path / 'eph'))
    monkeypatch.setattr(br, '_named_profiles_root', lambda: str(tmp_path / 'named'))
    monkeypatch.setattr(br, '_swept_orphans', True)
    monkeypatch.setattr(br, '_SERVER_PORT', 5199)
    monkeypatch.setattr(br, '_register_process', None)
    monkeypatch.setattr(br, '_unregister_process', None)
    monkeypatch.setitem(state.CONFIG, 'browser_cdp_pipe', request.param == 'pipe')
    browser_sessions.clear()
    site = _Site()
    session, err = br._launch_browser('live-hidden-test', site.base + '/user-tab', profile='main')
    assert err is None, err
    yield br, session, site
    try:
        br._kill_browser_session(session)
    except Exception:
        pass
    browser_sessions.clear()
    site.srv.shutdown()


def _wait(pred, secs=15, what='condition'):
    end = time.time() + secs
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(f'{what} never became true')


def _pane_href(br, session):
    ok, val = br._cdp_evaluate(session, 'location.href', timeout=8)
    return val if ok else None


def _raw_page_targets(br, session):
    """Every page target Chromium has, bypassing the filter the pane's own callers get."""
    t = br._cdp_transport(session)
    if t is not None:
        return [x for x in t.targets() if x.get('type') == 'page']
    import json
    import urllib.request
    return [x for x in json.load(urllib.request.urlopen(
        f"http://127.0.0.1:{session['port']}/json/list", timeout=3)) if x.get('type') == 'page']


def test_live_a_hidden_tab_reads_the_page_and_never_touches_the_users_tab(live_pane):
    from mc.browser_hidden_read import HiddenTabReader
    br, session, site = live_pane
    user_url = site.base + '/user-tab'
    _wait(lambda: _pane_href(br, session) == user_url, what='pane tab to load')
    tabs_before = dict(session['tabs'])
    active_before = session.get('active_target_id')
    pages_before = {t['id'] for t in _raw_page_targets(br, session)}
    hits_before = list(site.hits)

    reader = HiddenTabReader('live-hidden-test', 'main')
    reader.POLL_S = 0.2
    body = reader.read(site.base + '/hidden-page')
    assert body['ok'] is True, body
    text = body['content']['text']
    assert 'hello from the hidden read' in text
    # a background tab reports hidden unless focus emulation is on: the page must see a normal tab
    assert 'visibility=visible' in text and 'focus=true' in text
    assert body['final_url'] == site.base + '/hidden-page'

    # while the hidden tab is still open: the pane's own view of the world has not changed
    during = {t['id'] for t in _raw_page_targets(br, session)} - pages_before
    assert len(during) == 1                                              # one extra page: ours
    assert _pane_href(br, session) == user_url                           # `_cdp_evaluate` still lands in the user's tab
    assert dict(session['tabs']) == tabs_before and session.get('active_target_id') == active_before
    assert all(t['id'] not in during for t in br._cdp_targets(session))  # not in anything the pane lists
    assert site.hits.count('/user-tab') == hits_before.count('/user-tab')   # the user's tab was not reloaded

    reader.close()
    _wait(lambda: {t['id'] for t in _raw_page_targets(br, session)} == pages_before, what='hidden tab to close')
    assert session['status'] == 'running'                                # the pane's Chromium is still up
    assert _pane_href(br, session) == user_url
    assert dict(session['tabs']) == tabs_before
    assert not session.get('hidden_read_urls')                           # the registration did not leak
    reader.close()                                                       # twice is safe


def test_live_the_route_reader_closes_the_tab_even_when_the_read_is_refused(live_pane):
    from mc.blueprints import browser_agent_read_routes as routes
    br, session, site = live_pane
    _wait(lambda: _pane_href(br, session) == site.base + '/user-tab', what='pane tab to load')
    pages_before = {t['id'] for t in _raw_page_targets(br, session)}
    reader = routes._AllowListedHiddenReader('live-hidden-test', 'main', ['example.com'])
    reader.POLL_S = 0.2
    body = reader.read(site.base + '/hidden-page')
    reader.close()
    assert body['ok'] is False and body['error'] == 'redirect_off_list'    # off the allowed list: refused unread
    _wait(lambda: {t['id'] for t in _raw_page_targets(br, session)} == pages_before, what='hidden tab to close')
    assert _pane_href(br, session) == site.base + '/user-tab'


def test_live_a_failed_open_leaves_nothing_behind(live_pane, monkeypatch):
    from mc.browser_hidden_read import HiddenTabReader
    br, session, site = live_pane
    _wait(lambda: _pane_href(br, session) == site.base + '/user-tab', what='pane tab to load')
    pages_before = {t['id'] for t in _raw_page_targets(br, session)}
    monkeypatch.setattr(br, '_cdp_page_conn', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    reader = HiddenTabReader('live-hidden-test', 'main')
    body = reader.read(site.base + '/hidden-page')
    assert body['ok'] is False and body['error'] == 'profile_in_use'       # the refusal that remains
    assert 'could not be opened' in body['detail']
    _wait(lambda: {t['id'] for t in _raw_page_targets(br, session)} == pages_before, what='hidden tab to close')
    assert not session.get('hidden_read_urls')
