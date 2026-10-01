"""Pop-out chat window: slot registry (mc/blueprints/popout_routes.py) and the
pywebview bridge (mc/popout_windows.py). MC-1027.

The registry caps concurrently popped conversations at POPOUT_MAX so popped
windows cannot starve Chromium's 6-connections-per-origin limit. These tests
use a bare Flask app + the blueprint (no server import, no real windows).
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import popout_routes as pr  # noqa: E402
from mc.popout_windows import PopoutApi  # noqa: E402


@pytest.fixture()
def client():
    pr._reset_for_tests()
    app = Flask(__name__)
    app.register_blueprint(pr.bp)
    yield app.test_client()
    pr._reset_for_tests()


def _claim(c, sid):
    return c.post('/api/popout/claim', json={'session_id': sid})


def test_claim_then_release(client):
    r = _claim(client, 's1')
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'existing': False, 'count': 1, 'max': 4}
    r = client.post('/api/popout/release', json={'session_id': 's1'})
    assert r.get_json()['count'] == 0


def test_reclaim_same_session_is_existing_not_a_new_slot(client):
    _claim(client, 's1')
    r = _claim(client, 's1')
    assert r.status_code == 200
    assert r.get_json()['existing'] is True
    assert r.get_json()['count'] == 1


def test_fifth_conversation_is_refused_with_a_plain_message(client):
    for i in range(pr.POPOUT_MAX):
        assert _claim(client, f's{i}').status_code == 200
    r = _claim(client, 'one-too-many')
    assert r.status_code == 409
    body = r.get_json()
    assert body['ok'] is False and body['max'] == pr.POPOUT_MAX
    assert 'already popped out' in body['message']
    # A conversation that already holds a slot can still heartbeat at the cap.
    assert _claim(client, 's0').status_code == 200


def test_release_frees_a_slot_for_another_conversation(client):
    for i in range(pr.POPOUT_MAX):
        _claim(client, f's{i}')
    client.post('/api/popout/release', json={'session_id': 's2'})
    assert _claim(client, 'new').status_code == 200


def test_release_is_idempotent(client):
    r = client.post('/api/popout/release', json={'session_id': 'never-claimed'})
    assert r.status_code == 200 and r.get_json()['count'] == 0


def test_stale_slot_ages_out(client, monkeypatch):
    # A window that died without releasing (crash, killed app) must not hold a
    # slot forever: advance the clock past the TTL and the slot is reclaimable.
    clock = [1000.0]
    monkeypatch.setattr(pr.time, 'monotonic', lambda: clock[0])
    for i in range(pr.POPOUT_MAX):
        _claim(client, f's{i}')
    assert _claim(client, 'x').status_code == 409
    clock[0] += pr.POPOUT_TTL_SEC + 1
    assert _claim(client, 'x').status_code == 200


@pytest.mark.parametrize('body', [None, {}, {'session_id': ''}, {'session_id': 5}, []])
def test_bad_body_is_400(client, body):
    kw = {'json': body} if body is not None else {'data': 'not json'}
    assert client.post('/api/popout/claim', **kw).status_code == 400
    assert client.post('/api/popout/release', **kw).status_code == 400


# ── pywebview bridge ──────────────────────────────────────────────────────────

class _FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class _FakeWindow:
    def __init__(self, url, title):
        self.url, self.title = url, title
        self.events = type('E', (), {'closed': _FakeEvent()})()
        self.shown = 0
        self.destroyed = False

    def restore(self):
        pass

    def show(self):
        self.shown += 1

    def destroy(self):
        self.destroyed = True


class _FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, title, url=None, **kw):
        w = _FakeWindow(url, title)
        self.windows.append(w)
        return w


def test_bridge_opens_one_window_per_session_and_focuses_on_repeat():
    wv = _FakeWebview()
    api = PopoutApi(wv, 5199, log=lambda *_: None)
    assert api.open_chat_window('p1', 's1', 'T') == {'ok': True, 'existing': False}
    assert wv.windows[0].url == 'http://127.0.0.1:5199/?popout=1&p=p1&s=s1'
    assert api.open_chat_window('p1', 's1') == {'ok': True, 'existing': True}
    assert len(wv.windows) == 1 and wv.windows[0].shown == 1
    assert api.open_chat_window('p1', 's2')['existing'] is False
    assert len(wv.windows) == 2


def test_bridge_forgets_a_closed_window_so_the_next_open_is_fresh():
    wv = _FakeWebview()
    api = PopoutApi(wv, 5199, log=lambda *_: None)
    api.open_chat_window('p1', 's1')
    for fn in wv.windows[0].events.closed.handlers:
        fn()
    assert api.open_chat_window('p1', 's1')['existing'] is False
    assert len(wv.windows) == 2


def test_bridge_never_takes_a_url_and_rejects_non_strings():
    wv = _FakeWebview()
    api = PopoutApi(wv, 5199, log=lambda *_: None)
    assert api.open_chat_window('', 's1')['ok'] is False
    assert api.open_chat_window('p1', None)['ok'] is False
    assert not wv.windows
    # Hostile ids are encoded into the query, never into the host or path.
    api.open_chat_window('p&x=1', 's/../../evil')
    assert wv.windows[0].url.startswith('http://127.0.0.1:5199/?popout=1&')
    assert '/evil' not in wv.windows[0].url.split('?')[0]


def test_bridge_close_all_destroys_every_window():
    wv = _FakeWebview()
    api = PopoutApi(wv, 5199, log=lambda *_: None)
    api.open_chat_window('p1', 's1')
    api.open_chat_window('p1', 's2')
    api.close_all()
    assert all(w.destroyed for w in wv.windows)
