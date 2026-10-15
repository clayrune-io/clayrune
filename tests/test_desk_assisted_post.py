"""Assisted posting (mc/desk_assisted_post.py): the pane is filled in, a person presses Share.

Pinned:
  * a route that could click a final-submit button does not load, and the engine refuses one
    at run time even if the page hands back "Share" where the step wanted "Next";
  * only the files and text of the ONE approved version are used: an unapproved version, a
    body edited after approval, a path outside data/uploads, no media, or a network with no
    route are all refused before the browser is touched;
  * the route is human-only and maps refusals to their status;
  * live (a real Chromium on a local page, skipped when Chromium is missing): every file
    arrives, the text is typed, the Share button is present and was never pressed.

Everything that says "live" runs against tools/smoke/MOCK_instagram_compose.html, a
hand-written stand-in. NONE of this has run against instagram.com: the live Instagram run is
pending a signed-in profile.
"""
from __future__ import annotations

import http.server
import threading
import time
import types
from pathlib import Path

import pytest
from flask import Flask

from mc import desk as _desk
from mc import desk_assisted_instagram as ig
from mc import desk_assisted_post as ap
from mc import desk_pieces as _pieces
from mc.blueprints import desk_assisted_post_routes as routes
from mc.blueprints import desk_routes

MOCK_PAGE = Path(__file__).resolve().parents[1] / 'tools' / 'smoke' / 'MOCK_instagram_compose.html'
BODY = 'A caption for the MOCK run #clay'


class FakeDriver:
    """Records every call; `labels` maps the label asked for to the label the page offers."""

    def __init__(self, offer=None, attach_ok=True, caption_ok=True):
        self.calls = []
        self.offer = offer or {}
        self.attach_ok, self.caption_ok = attach_ok, caption_ok

    def navigate(self, url): self.calls.append(('navigate', url))
    def wait(self, css, timeout_s): self.calls.append(('wait', css)); return True

    def locate(self, css, labels):
        self.calls.append(('locate', labels[0]))
        return {'x': 1.0, 'y': 2.0, 'label': self.offer.get(labels[0], labels[0])}

    def click_at(self, x, y): self.calls.append(('click', x, y))
    def attach(self, css, paths): self.calls.append(('attach', list(paths))); return self.attach_ok
    def insert_text(self, css, text): self.calls.append(('text', text)); return self.caption_ok
    def present(self, css, label): self.calls.append(('present', label)); return True
    def close(self): self.calls.append(('close',))


def _post(media=('a.png',)):
    return {'route': ig, 'profile': 'main', 'body': BODY, 'media': list(media), 'account_id': 'acc-ig'}


# ── the final button is never pressed ────────────────────────────────────────

def test_the_instagram_route_loads_and_names_no_final_submit_click():
    ap.validate_route(ig)
    clicks = [s for s in ig.STEPS if s['op'] == 'click']
    assert [c['label'] for c in clicks] == ['Create', 'Post', 'Next', 'Next']
    assert not any(ap.is_final_submit(x) for c in clicks for x in (c['label'], *c.get('aliases', ())))
    assert ig.FINAL_SUBMIT['label'] == 'Share'


@pytest.mark.parametrize('step', [
    {'op': 'click', 'css': 'div', 'label': 'Share'},
    {'op': 'click', 'css': 'div', 'label': ' share '},
    {'op': 'click', 'css': 'div', 'label': 'Publish'},
    {'op': 'click', 'css': 'div', 'label': 'Next', 'aliases': ('Share',)},
])
def test_a_route_that_clicks_a_final_submit_word_does_not_load(step):
    bad = types.SimpleNamespace(**{**vars(ig), 'STEPS': ig.STEPS + (step,)})
    with pytest.raises(ValueError, match='final-submit'):
        ap.validate_route(bad)


def test_a_route_may_not_navigate_off_its_network():
    bad = types.SimpleNamespace(**{**vars(ig), 'STEPS': ({'op': 'navigate', 'url': 'https://evil.example/'},)})
    with pytest.raises(ValueError, match='outside the network'):
        ap.validate_route(bad)


def test_the_run_fills_in_the_post_and_stops_before_the_final_button():
    d = FakeDriver()
    out = ap.run(_post(('a.png', 'b.mp4')), d)
    assert out['ok'] and out['state'] == 'ready_for_human' and out['final_button_present'] is True
    clicked = [c[1] for c in d.calls if c[0] == 'locate']
    assert clicked == ['Create', 'Post', 'Next', 'Next']
    assert ('attach', ['a.png', 'b.mp4']) in d.calls and ('text', BODY) in d.calls
    assert d.calls[-1] == ('present', 'Share')
    assert 'Share' not in clicked and sum(1 for c in d.calls if c[0] == 'click') == 4


def test_a_screen_that_offers_share_where_next_was_expected_is_refused_not_clicked():
    d = FakeDriver(offer={'Next': 'Share'})
    with pytest.raises(ap.AssistedError) as e:
        ap.run(_post(), d)
    assert e.value.code == 'final_submit_refused' and e.value.status == 403
    # Create and Post were clicked; nothing was clicked once the page offered Share.
    assert sum(1 for c in d.calls if c[0] == 'click') == 2


def test_a_different_control_is_a_plain_stop_not_a_click():
    d = FakeDriver(offer={'Next': 'Discard'})
    with pytest.raises(ap.AssistedError) as e:
        ap.run(_post(), d)
    assert e.value.code == 'step_failed' and e.value.extra['op'] == 'click'
    assert 'finish this post by hand' in str(e.value)


@pytest.mark.parametrize('kw,op', [({'attach_ok': False}, 'attach'), ({'caption_ok': False}, 'caption')])
def test_a_missing_field_stops_with_how_far_it_got(kw, op):
    with pytest.raises(ap.AssistedError) as e:
        ap.run(_post(), FakeDriver(**kw))
    assert e.value.code == 'step_failed' and e.value.extra['op'] == op and e.value.status == 502
    assert 0 < e.value.extra['steps_done'] < e.value.extra['steps_total']


# ── only the approved post's files and text ──────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'a.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    (uploads / 'b.mp4').write_bytes(b'0' * 64)
    (tmp_path / 'secret.png').write_bytes(b'0' * 8)               # a real file OUTSIDE uploads
    desk_routes.wire(load_projects_fn=lambda: [], load_project_fn=lambda pid: None,
                     store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'sig.jsonl',
                     uploads_root=uploads)
    return uploads, tmp_path


def _store(assets, *, platform='instagram', state='approved', body=BODY, approved_body=None, stamp=True):
    ver = {'id': 'v1', 'account_id': 'acc-ig', 'state': state, 'body': body, 'revision': 0,
           'approved': ({'at': 'now', 'by': 'human', 'account_id': 'acc-ig',
                         'body_sha': _pieces._body_sha(approved_body if approved_body is not None else body)}
                        if stamp else None)}
    s = _desk._read_store()
    s['accounts'] = {'acc-ig': {'id': 'acc-ig', 'platform': platform, 'browser_profile': 'main'}}
    s['pieces'] = {'p1': {'id': 'p1', 'kind': 'post', 'body': '', 'assets': assets, 'versions': [ver]}}
    _desk._write_store(s)


def _asset(path, kind='image', aid='a1'):
    return {'id': aid, 'kind': kind, 'title': aid, 'path': path}


def test_the_approved_posts_own_files_and_text_are_used(env):
    uploads, _ = env
    _store([_asset('a.png'), _asset('b.mp4', 'video', 'a2')])
    post = ap.approved_post('p1', 'v1')
    assert post['body'] == BODY and post['profile'] == 'main'
    assert post['media'] == [str((uploads / 'a.png').resolve()), str((uploads / 'b.mp4').resolve())]


@pytest.mark.parametrize('kw,code', [
    ({'state': 'needs_review'}, 'not_approved'),
    ({'stamp': False}, 'not_approved'),
    ({'approved_body': 'what was approved'}, 'changed_after_approval'),
    ({'platform': 'x'}, 'no_route'),
    ({'body': ''}, 'no_text'),
    ({'body': 'x' * 2201}, 'text_too_long'),
])
def test_an_unapproved_or_edited_or_unrouted_version_is_refused(env, kw, code):
    _store([_asset('a.png')], **kw)
    with pytest.raises(ap.AssistedError) as e:
        ap.approved_post('p1', 'v1')
    assert e.value.code == code


@pytest.mark.parametrize('assets,code', [
    ([], 'no_media'),
    ([_asset('../secret.png')], 'bad_media'),                   # exists on disk, outside data/uploads
    ([_asset('missing.png')], 'bad_media'),
    ([_asset('a.png', 'article')], 'bad_media'),
    ([_asset('a.png', aid=f'a{i}') for i in range(11)], 'bad_media'),
])
def test_media_outside_the_post_or_uploads_is_refused(env, assets, code):
    _store(assets)
    with pytest.raises(ap.AssistedError) as e:
        ap.approved_post('p1', 'v1')
    assert e.value.code == code


def test_an_unknown_piece_or_version_is_a_404(env):
    _store([_asset('a.png')])
    for args in (('nope', 'v1'), ('p1', 'nope')):
        with pytest.raises(ap.AssistedError) as e:
            ap.approved_post(*args)
        assert e.value.status == 404


def test_nothing_touches_the_browser_when_the_gate_refuses(env):
    _store([], state='needs_review')
    made = []
    with pytest.raises(ap.AssistedError):
        ap.prepare('p1', 'v1', driver_factory=lambda profile: made.append(profile))
    assert made == []


def test_prepare_closes_the_driver_and_frees_the_profile_even_when_a_step_fails(env):
    _store([_asset('a.png')])
    d = FakeDriver(attach_ok=False)
    with pytest.raises(ap.AssistedError):
        ap.prepare('p1', 'v1', driver_factory=lambda profile: d)
    assert d.calls[-1] == ('close',)
    d2 = FakeDriver()
    assert ap.prepare('p1', 'v1', driver_factory=lambda profile: d2)['state'] == 'ready_for_human'


def test_a_second_run_on_a_busy_profile_is_refused(env):
    _store([_asset('a.png')])
    lock = ap._run_locks.setdefault('main', threading.Lock())
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(ap.AssistedError) as e:
            ap.prepare('p1', 'v1', driver_factory=lambda profile: FakeDriver())
        assert e.value.code == 'busy'
    finally:
        lock.release()


def test_no_open_pane_on_the_profile_is_a_clear_refusal(monkeypatch):
    from mc import desk_assisted_pane as pane
    monkeypatch.setattr(pane.br, '_session_using_profile', lambda name: None)
    with pytest.raises(ap.AssistedError) as e:
        pane.pane_driver_for('main')
    assert e.value.code == 'pane_not_open' and 'sign in first' in str(e.value)


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    from mc import desk_assisted_pane as pane
    d = FakeDriver()
    monkeypatch.setattr(pane, 'pane_driver_for', lambda profile: d)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client(), d


URL = '/api/desk/pieces/p1/versions/v1/assisted-post'


def test_the_route_fills_in_an_approved_post(client):
    c, d = client
    _store([_asset('a.png')])
    r = c.post(URL, json={})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['state'] == 'ready_for_human' and r.get_json()['final_button_present'] is True


def test_the_route_maps_a_refusal_to_its_status_and_code(client):
    c, _ = client
    _store([_asset('a.png')], state='needs_review')
    r = c.post(URL, json={})
    assert r.status_code == 409 and r.get_json()['code'] == 'not_approved'


def test_the_route_is_human_only(client, monkeypatch):
    c, d = client
    _store([_asset('a.png')])
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: True)
    r = c.post(URL, json={})
    assert r.status_code == 403 and d.calls == []


def test_the_route_needs_the_passcode(client, monkeypatch):
    from flask import jsonify
    c, d = client
    _store([_asset('a.png')])
    monkeypatch.setattr(desk_routes, '_require_human_passcode',
                        lambda data: (jsonify({'error': 'passcode_required'}), 403))
    r = c.post(URL, json={})
    assert r.status_code == 403 and d.calls == []


# ── live: MOCK page in a real Chromium ───────────────────────────────────────

class _MockSite:
    def __init__(self):
        page = MOCK_PAGE.read_bytes()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'


@pytest.fixture(params=['pipe', 'port'])
def mock_pane(request, monkeypatch, tmp_path):
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
    site = _MockSite()
    session, err = br._launch_browser('live-assisted-MOCK', 'about:blank', profile='main')
    assert err is None, err
    yield br, session, site
    try:
        br._kill_browser_session(session)
    except Exception:
        pass
    browser_sessions.clear()
    site.srv.shutdown()


def _mock_route(site, query=''):
    steps = tuple({**s, 'url': site.base + '/' + query} if s['op'] == 'navigate' else s for s in ig.STEPS)
    return types.SimpleNamespace(**{**vars(ig), 'HOSTS': ('127.0.0.1',), 'STEPS': steps})


def _wait(pred, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_MOCK_live_the_pane_is_filled_in_and_share_is_never_pressed(mock_pane, tmp_path):
    from mc.desk_assisted_pane import PaneDriver
    br, session, site = mock_pane
    files = [tmp_path / 'one.png', tmp_path / 'two.mp4']
    files[0].write_bytes(b'1' * 11)
    files[1].write_bytes(b'2' * 22)
    post = {**_post([str(f) for f in files]), 'route': _mock_route(site)}
    driver = PaneDriver(session)
    try:
        out = ap.run(post, driver)
    finally:
        driver.close()
    assert out['ok'] and out['final_button_present'] is True, out
    ev = lambda js: br._cdp_evaluate(session, js, timeout=8)[1]   # noqa: E731
    assert ev('window.__stage') == 'stage-caption'
    assert ev('window.__files') == ['one.png:11', 'two.mp4:22']
    assert ev('document.getElementById("caption").innerText') == BODY
    assert ev('window.__shareClicks') == 0


def test_MOCK_live_a_screen_that_swaps_next_for_share_is_not_clicked(mock_pane, tmp_path):
    from mc.desk_assisted_pane import PaneDriver
    br, session, site = mock_pane
    f = tmp_path / 'one.png'
    f.write_bytes(b'1' * 11)
    driver = PaneDriver(session)
    try:
        with pytest.raises(ap.AssistedError) as e:
            ap.run({**_post([str(f)]), 'route': _mock_route(site, '?swap=1')}, driver)
    finally:
        driver.close()
    # The pane driver only offers a control whose label matches, so the swapped button is never
    # even located; the engine's own refusal (fake-driver test above) is the second layer.
    assert e.value.code == 'step_failed' and e.value.extra['op'] == 'click' and e.value.extra['step'] == 7
    ev = lambda js: br._cdp_evaluate(session, js, timeout=8)[1]   # noqa: E731
    assert ev('window.__shareClicks') == 0 and ev('window.__stage') == 'stage-edit'
