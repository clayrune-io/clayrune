"""MC-1079: real pipe/port targets, plus handle routing and cleanup failures."""
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from mc import browser_agent_tabs as tabs
from mc.blueprints import browser_routes as br
from tests.test_browser_agent_read_hidden import (live_pane, _wait, _pane_href, _raw_page_targets)  # noqa: F401


@pytest.fixture(autouse=True)
def _own_port(monkeypatch):
    # The own-origin guard reads br._SERVER_PORT, which importing server.py
    # under the test config wires to MC_PORT=0; pin it so localhost:5199
    # stays "own origin" whatever ran first.
    monkeypatch.setattr(br, '_SERVER_PORT', 5199)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(br.bp)
    return app.test_client()


def test_live_adoption_opens_two_owned_tabs_without_focus_steal_and_stop_closes_only_one(live_pane, client):
    br, pane, site = live_pane
    user_url = site.base + '/user-tab'
    _wait(lambda: _pane_href(br, pane) == user_url)
    active = pane['active_target_id']
    before = {t['id'] for t in _raw_page_targets(br, pane)}
    handles = []
    try:
        for _ in range(2):
            r = client.post('/api/browser/launch', json={
                'project_id': 'agent-project', 'profile': 'main', 'url': user_url})
            assert r.status_code == 200, r.get_json()
            body = r.get_json()
            assert body['agent_tab'] and body['session_id'] != pane['session_id']
            handles.append(body['session_id'])
        assert len(set(handles)) == 2
        assert pane['active_target_id'] == active and _pane_href(br, pane) == user_url
        assert len(_raw_page_targets(br, pane)) == len(before) + 2
        r = client.post('/api/browser/input', json={
            'session_id': handles[0], 'type': 'navigate', 'url': site.base + '/agent-page'})
        assert r.status_code == 200
        tab = tabs._tabs[handles[0]]
        _wait(lambda: tab.evaluate('location.href') == (True, site.base + '/agent-page'))
        r = client.post('/api/browser/read', json={'session_id': handles[0]})
        assert r.status_code == 200 and r.get_json()['url'] == site.base + '/agent-page'
        assert r.get_json()['content']['warning'] and 'hello from the hidden read' in r.get_json()['content']['text']
        assert _pane_href(br, pane) == user_url and pane['active_target_id'] == active
        target = tab.target_id
        assert client.post('/api/browser/stop', json={'session_id': handles[0]}).status_code == 200
        _wait(lambda: target not in {t['id'] for t in _raw_page_targets(br, pane)})
        assert pane['status'] == 'running' and tabs._tabs[handles[1]].running()
        assert client.post('/api/browser/read', json={'session_id': handles[0]}).status_code == 404
        assert pane['active_target_id'] == active and _pane_href(br, pane) == user_url
    finally:
        for h in handles:
            if h in tabs._tabs:
                tabs._tabs[h].close()


def test_human_adoption_keeps_current_navigation_behavior(client, monkeypatch):
    pane = {'session_id': 'pane', 'url': 'https://old.example', 'reused': True,
            'status': 'running', 'cmd_queue': queue.Queue()}
    monkeypatch.setattr(br, '_launch_browser', lambda *a, **k: (pane, None))
    r = client.post('/api/browser/launch', headers={'Origin': 'http://localhost:5199'},
                    json={'profile': 'main', 'url': 'https://new.example'})
    assert r.status_code == 200 and r.get_json()['session_id'] == 'pane'
    assert pane['cmd_queue'].get_nowait() == ('Page.navigate', {'url': 'https://new.example'})


def test_concurrent_launches_do_not_consume_each_others_reuse_flag(monkeypatch):
    pane = {'session_id': 'pane', 'status': 'running', 'cmd_queue': queue.Queue()}
    barrier = threading.Barrier(2)
    def launch(*a, reuse_result, **kw):
        pane['reused'] = True
        reuse_result['reused'] = True
        barrier.wait(timeout=5)
        return pane, None
    monkeypatch.setattr(br, '_launch_browser', launch)
    monkeypatch.setattr(tabs, 'adopt', lambda p, url, pid: {'agent_tab': True, 'url': url})
    app = Flask(__name__)
    app.register_blueprint(br.bp)
    def request(url):
        with app.test_client() as caller:
            response = caller.post('/api/browser/launch', json={'profile': 'main', 'url': url})
            return response.status_code, response.get_json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(request, ['https://one.example', 'https://two.example']))
    assert all(status == 200 and body.get('agent_tab') for status, body in replies)
    assert pane['cmd_queue'].empty()


def test_tab_input_rejects_own_origin_and_never_queues_to_pane(client):
    tab = tabs.AgentTab({'status': 'running', 'cmd_queue': queue.Queue()}, 'p')
    tab.page = object()
    tabs._tabs[tab.handle] = tab
    try:
        r = client.post('/api/browser/input', json={'session_id': tab.handle,
                        'type': 'navigate', 'url': 'http://localhost:5199/'})
        assert r.status_code == 403 and tab.pane['cmd_queue'].empty()
    finally:
        tabs._tabs.pop(tab.handle)


def test_read_redirect_to_own_origin_is_refused(client, monkeypatch):
    tab = tabs.AgentTab({'status': 'running'}, 'p')
    tab.page = object()
    tabs._tabs[tab.handle] = tab
    monkeypatch.setattr(tab, 'evaluate', lambda expr: (True, {'url': 'http://localhost:5199/', 'runs': [{'text': 'private'}]}))
    try:
        r = client.post('/api/browser/read', json={'session_id': tab.handle})
        assert r.status_code == 403 and r.get_json()['error'] == 'own_origin_blocked'
        assert 'private' not in r.get_data(as_text=True) and r.get_json()['guidance']
    finally:
        tabs._tabs.pop(tab.handle)


def test_failed_close_retains_handle_for_retry_and_never_closes_browser(client):
    class Conn:
        def __init__(self):
            self.fail = True
            self.calls = []
        def call(self, method, params=None, **kw):
            self.calls.append((method, params))
            if self.fail:
                raise TimeoutError('close timeout')
            return {'success': True}
        def close(self):
            pass
    pane = {'status': 'running', 'page_disposition': {'owned': 'hidden'}, 'hidden_read_urls': set()}
    tab = tabs.AgentTab(pane, 'p')
    tab.target_id = 'owned'
    tab.browser = Conn()
    tab.page = None                      # failed open can still close its target
    conn = tab.browser
    tabs._tabs[tab.handle] = tab
    try:
        # A failed-open handle remains stoppable; reading it must not trigger
        # browser-wide sibling cleanup while the pane is still running.
        r = client.post('/api/browser/read', json={'session_id': tab.handle})
        assert r.status_code == 404 and conn.calls == [] and tab.handle in tabs._tabs
        r = client.post('/api/browser/stop', json={'session_id': tab.handle})
        assert r.status_code == 502 and tab.handle in tabs._tabs
        assert pane['status'] == 'running' and pane['page_disposition']['owned'] == 'hidden'
        conn.fail = False
        assert client.post('/api/browser/stop', json={'session_id': tab.handle}).status_code == 200
        assert tab.handle not in tabs._tabs and pane['status'] == 'running'
        assert {method for method, _ in conn.calls} == {'Target.closeTarget'}
    finally:
        tabs._tabs.pop(tab.handle, None)


# ---- SF33: input is checked against where the tab IS, not what was typed ----

class _FakePage:
    """Stands in for the tab's CDP page connection: tracks an address, honours
    Page.navigate (optionally redirecting), answers Runtime.evaluate with it."""
    def __init__(self, href, redirects=None, fail_eval=False):
        self.href = href
        self.redirects = redirects or {}
        self.fail_eval = fail_eval
        self.calls = []
    def call(self, method, params=None, **kw):
        self.calls.append((method, params))
        if method == 'Page.navigate':
            self.href = self.redirects.get(params['url'], params['url'])
            return {'frameId': 'f'}
        if method == 'Runtime.evaluate' and params.get('expression') == 'location.href':
            if self.fail_eval:
                raise TimeoutError('no context')
            return {'result': {'value': self.href}}
        return {}
    def input_calls(self):
        return [c for c in self.calls if c[0].startswith('Input.')]


@pytest.fixture
def fake_tab():
    made = []
    def make(href, **kw):
        tab = tabs.AgentTab({'status': 'running'}, 'p')
        tab.page = _FakePage(href, **kw)
        tabs._tabs[tab.handle] = tab
        made.append(tab)
        return tab
    yield make
    for t in made:
        tabs._tabs.pop(t.handle, None)


_INPUTS = [
    {'type': 'mouse', 'x': 10, 'y': 10},
    {'type': 'key', 'key': 'a'},
    {'type': 'text', 'text': 'hello'},
    {'type': 'wheel', 'x': 10, 'y': 10, 'dy': 40},
]


@pytest.mark.parametrize('payload', _INPUTS, ids=[p['type'] for p in _INPUTS])
def test_input_refused_when_tab_is_on_own_origin_and_tab_is_moved_off(client, fake_tab, payload):
    tab = fake_tab('http://localhost:5199/dashboard')
    r = client.post('/api/browser/input', json={'session_id': tab.handle, **payload})
    assert r.status_code == 403
    assert tab.page.input_calls() == []
    assert tab.page.href == 'about:blank'
    assert tab.url == 'about:blank'


def test_redirect_onto_own_origin_is_refused_and_tab_moved_off(client, fake_tab):
    tab = fake_tab('about:blank', redirects={'https://redirector.example/go': 'http://localhost:5199/'})
    r = client.post('/api/browser/input', json={
        'session_id': tab.handle, 'type': 'navigate', 'url': 'https://redirector.example/go'})
    assert r.status_code == 403
    assert tab.page.href == 'about:blank'
    assert tab.url == 'about:blank'
    assert tab.page.input_calls() == []


def test_external_page_still_accepts_input(client, fake_tab):
    tab = fake_tab('https://example.com/')
    for payload in _INPUTS:
        r = client.post('/api/browser/input', json={'session_id': tab.handle, **payload})
        assert r.status_code == 200, (payload, r.get_json())
    assert tab.page.href == 'https://example.com/'
    assert len(tab.page.input_calls()) >= len(_INPUTS)


def test_unreadable_address_fails_closed(client, fake_tab):
    tab = fake_tab('https://example.com/', fail_eval=True)
    r = client.post('/api/browser/input', json={'session_id': tab.handle, 'type': 'key', 'key': 'a'})
    assert r.status_code == 502
    assert tab.page.input_calls() == []
