"""mc/update_ping.py + its routes. No test here opens a socket: every send goes
through an injected post_fn, and a socket guard makes any stray connect fail loudly."""
import json
import socket

import pytest
from flask import Flask

from mc import state, update_ping
from mc.blueprints import update_ping_routes

DAY = 24 * 3600


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('a test tried to open a network connection')
    monkeypatch.setattr(socket.socket, 'connect', boom)
    monkeypatch.setattr(socket, 'create_connection', boom)
    for k in update_ping.ENV_OFF_VARS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def ping(tmp_path):
    cfg = {'update_ping_enabled': True, 'update_ping_disclosed': True}
    update_ping.wire(tmp_path / 'state.json', config_fn=lambda: cfg,
                     version_fn=lambda: 'v2.3.0 build 180', ping_id_path=tmp_path / 'ping_id')
    calls = []

    def post(url, payload):
        calls.append((url, payload))
        return {'latest_version': 'v2.4.0'}
    return type('P', (), {'cfg': cfg, 'calls': calls, 'post': staticmethod(post), 'dir': tmp_path})


def _assert_silent(ping, **kw):
    r = update_ping.maybe_ping(now=1_000_000, post_fn=ping.post, **kw)
    assert r['sent'] is False, r
    assert ping.calls == []
    assert not (ping.dir / 'ping_id').exists(), 'id must not be created when nothing is sent'
    return r


def test_zero_requests_when_disabled(ping):
    ping.cfg['update_ping_enabled'] = False
    assert _assert_silent(ping)['reason'] == 'disabled'


def test_zero_requests_before_disclosure(ping):
    ping.cfg['update_ping_disclosed'] = False
    assert _assert_silent(ping)['reason'] == 'not_disclosed'


@pytest.mark.parametrize('var', ['DO_NOT_TRACK', 'CLAYRUNE_NO_PING'])
@pytest.mark.parametrize('val', ['1', 'true', 'YES'])
def test_zero_requests_with_env_off(ping, var, val):
    assert _assert_silent(ping, environ={var: val})['reason'] == 'env_off'


def test_env_off_beats_enabled_and_disclosed(ping):
    assert ping.cfg['update_ping_enabled'] and ping.cfg['update_ping_disclosed']
    _assert_silent(ping, environ={'DO_NOT_TRACK': '1'})


def test_env_zero_does_not_disable(ping):
    r = update_ping.maybe_ping(now=1_000_000, post_fn=ping.post, environ={'DO_NOT_TRACK': '0'})
    assert r['sent'] is True


@pytest.mark.parametrize('url', ['http://example.com/ping', 'ftp://x/y', 'javascript:1', '//x/y'])
def test_zero_requests_for_non_https_url(ping, url):
    ping.cfg['update_ping_url'] = url
    assert _assert_silent(ping)['reason'] == 'bad_url'


def test_loopback_http_is_allowed(ping):
    ping.cfg['update_ping_url'] = 'http://127.0.0.1:8787/api/ping'
    assert update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)['sent'] is True


def test_default_enabled_when_key_missing(ping):
    del ping.cfg['update_ping_enabled']
    assert update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)['sent'] is True


def test_non_true_enabled_values_are_off(ping):
    for v in (False, None, 'true', 1, 0, ''):
        ping.cfg['update_ping_enabled'] = v
        assert update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)['sent'] is False, v
    assert ping.calls == []


def test_payload_is_exactly_three_fields_and_no_hostname(ping, monkeypatch):
    monkeypatch.setattr(socket, 'gethostname', lambda: 'rons-secret-laptop')
    update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)
    (url, payload), = ping.calls
    assert url == update_ping.DEFAULT_URL
    assert set(payload) == {'install_id', 'version', 'os'}
    assert payload['version'] == 'v2.3.0 build 180'
    assert payload['os'] in ('windows', 'darwin', 'linux', 'other')
    assert len(payload['install_id']) == 32 and int(payload['install_id'], 16) >= 0
    assert 'secret' not in json.dumps(payload)


def test_id_is_stable_and_not_the_project_sync_id(ping, monkeypatch):
    from mc import project_sync
    monkeypatch.setattr(project_sync, '_install_id_path', lambda: ping.dir / 'install_id')
    update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)
    update_ping.maybe_ping(now=1_000_000 + DAY + 1, post_fn=ping.post)
    ids = {c[1]['install_id'] for c in ping.calls}
    assert len(ids) == 1
    assert (ping.dir / 'ping_id').read_text() == ids.pop()
    assert ping.calls[0][1]['install_id'] != project_sync.get_install_id()


def test_at_most_once_per_24h_and_resumes_after(ping):
    assert update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)['sent'] is True
    assert update_ping.maybe_ping(now=1_000_000 + DAY - 1, post_fn=ping.post)['reason'] == 'not_due'
    assert len(ping.calls) == 1
    assert update_ping.maybe_ping(now=1_000_000 + DAY, post_fn=ping.post)['sent'] is True
    assert len(ping.calls) == 2


def test_failed_attempt_still_consumes_the_day_and_never_raises(ping):
    def bad(url, payload):
        ping.calls.append(payload)
        raise OSError('connection refused')
    r = update_ping.maybe_ping(now=1_000_000, post_fn=bad)
    assert r['sent'] is True and r['ok'] is False
    assert update_ping.maybe_ping(now=1_000_000 + 3600, post_fn=bad)['reason'] == 'not_due'
    assert len(ping.calls) == 1


def test_state_survives_rewire(ping):
    update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)
    update_ping.wire(ping.dir / 'state.json', config_fn=lambda: ping.cfg,
                     version_fn=lambda: 'v2.3.0', ping_id_path=ping.dir / 'ping_id')
    assert update_ping.maybe_ping(now=1_000_000 + 60, post_fn=ping.post)['reason'] == 'not_due'


@pytest.mark.parametrize('reply,kept', [
    ({'latest_version': 'v2.4.0'}, 'v2.4.0'),
    ({'latest_version': '<script>alert(1)</script>'}, ''),
    ({'latest_version': 'x' * 100}, ''),
    ({'latest_version': 5}, ''),
    ({}, ''),
])
def test_latest_version_is_validated(ping, reply, kept):
    update_ping.maybe_ping(now=1_000_000, post_fn=lambda u, p: reply)
    assert update_ping.status()['latest_version'] == kept


def test_status_update_available_and_withheld_when_off(ping):
    update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)
    s = update_ping.status()
    assert (s['latest_version'], s['update_available']) == ('v2.4.0', True)
    ping.cfg['update_ping_enabled'] = False
    s = update_ping.status()
    assert (s['latest_version'], s['update_available'], s['enabled']) == ('', False, False)


@pytest.mark.parametrize('latest,cur,newer', [
    ('v2.4.0', 'v2.3.0 build 180', True), ('v2.3.0', 'v2.3.0 build 180', False),
    ('v2.10.0', 'v2.9.9', True), ('v1.0', 'v2.0', False),
    ('v2.4.0', 'untagged (abc1234)', False), ('', 'v1', False),
])
def test_is_newer(latest, cur, newer):
    assert update_ping.is_newer(latest, cur) is newer


def test_status_reports_env_off(ping, monkeypatch):
    update_ping.maybe_ping(now=1_000_000, post_fn=ping.post)
    monkeypatch.setenv('DO_NOT_TRACK', '1')
    s = update_ping.status()
    assert s['env_disabled'] is True and s['latest_version'] == ''


def test_real_transport_refuses_redirects():
    import urllib.request
    h = update_ping._NoRedirect()
    req = urllib.request.Request('https://a.example/x')
    assert h.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/') is None


def test_server_defaults_on_but_undisclosed():
    import server
    assert server.CONFIG['update_ping_enabled'] is True
    assert server.CONFIG['update_ping_disclosed'] is False
    assert server.CONFIG['update_ping_url'] == update_ping.DEFAULT_URL
    assert 'update_ping_enabled' in server._CONFIG_EDITABLE_KEYS
    assert 'update_ping_disclosed' not in server._CONFIG_EDITABLE_KEYS
    assert 'update_ping_url' not in server._CONFIG_EDITABLE_KEYS


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({'keep': 1}), encoding='utf-8')
    live = {'update_ping_enabled': True, 'update_ping_disclosed': False}
    monkeypatch.setattr(state, 'CONFIG', live)
    update_ping.wire(tmp_path / 's.json', config_fn=lambda: state.CONFIG,
                     version_fn=lambda: 'v2.3.0', ping_id_path=tmp_path / 'ping_id')
    update_ping_routes.wire(config_path=cfg_path)
    app = Flask(__name__)
    app.register_blueprint(update_ping_routes.bp)
    return app.test_client(), cfg_path, live


def test_seen_sets_disclosed_only(client):
    c, path, live = client
    assert c.post('/api/update-ping/seen').status_code == 200
    assert json.loads(path.read_text()) == {'keep': 1, 'update_ping_disclosed': True}
    assert live['update_ping_enabled'] is True


def test_choice_off_persists_and_blocks_ping(client):
    c, path, live = client
    r = c.post('/api/update-ping/choice', json={'enabled': False})
    assert r.status_code == 200 and r.get_json()['enabled'] is False
    assert json.loads(path.read_text())['update_ping_enabled'] is False
    calls = []
    update_ping.maybe_ping(now=1, post_fn=lambda u, p: calls.append(p) or {})
    assert calls == []


def test_choice_on_then_ping_is_allowed(client):
    c, _, _ = client
    c.post('/api/update-ping/choice', json={'enabled': True})
    calls = []
    update_ping.maybe_ping(now=1_000_000, post_fn=lambda u, p: calls.append(p) or {})
    assert len(calls) == 1


@pytest.mark.parametrize('body', [{}, {'enabled': 'yes'}, {'enabled': 1}, {'enabled': None}])
def test_choice_rejects_non_bool(client, body):
    c, path, _ = client
    assert c.post('/api/update-ping/choice', json=body).status_code == 400
    assert json.loads(path.read_text()) == {'keep': 1}


def test_unattended_caller_refused(client, monkeypatch):
    c, path, live = client
    monkeypatch.setattr(update_ping_routes, 'is_unattended_caller', lambda *a, **k: True)
    assert c.post('/api/update-ping/choice', json={'enabled': False}).status_code == 403
    assert c.post('/api/update-ping/seen').status_code == 403
    assert json.loads(path.read_text()) == {'keep': 1}
    assert live['update_ping_disclosed'] is False


# ── Fenn review 2026-11-05: regressions (each fails on 82619240) ──────────────

import threading
import time

from mc import provider_preferences


def _run(fn):
    out = {}

    def target():
        out['r'] = fn()
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, out


def test_seen_cannot_overwrite_a_concurrent_opt_out(client, monkeypatch):
    """P1: /seen read enabled=true, paused before its write; /choice {false} saved OFF;
    /seen then wrote the stale copy back. Disk said ON while the live dict said OFF."""
    c0, path, live = client
    app = Flask(__name__)
    app.register_blueprint(update_ping_routes.bp)
    reached, release = threading.Event(), threading.Event()
    real = update_ping_routes.write_json_atomic

    def paused_write(p, obj, **kw):
        if threading.current_thread().name == 'seen-writer' and not reached.is_set():
            reached.set()
            release.wait(5)
        return real(p, obj, **kw)
    monkeypatch.setattr(update_ping_routes, 'write_json_atomic', paused_write)

    seen_t = threading.Thread(target=lambda: app.test_client().post('/api/update-ping/seen'),
                              name='seen-writer', daemon=True)
    seen_t.start()
    assert reached.wait(5)
    choice_t = threading.Thread(
        target=lambda: app.test_client().post('/api/update-ping/choice', json={'enabled': False}),
        daemon=True)
    choice_t.start()
    time.sleep(0.3)              # lets an unserialized /choice run to completion first
    release.set()
    seen_t.join(5)
    choice_t.join(5)
    assert json.loads(path.read_text())['update_ping_enabled'] is False
    assert live['update_ping_enabled'] is False
    sent = []
    update_ping.maybe_ping(now=1_000_000, post_fn=lambda u, p: sent.append(p) or {})
    assert sent == []


def test_routes_take_the_settings_config_lock(client):
    """P1: the routes must serialize with PUT /api/config, i.e. the shared lock."""
    c, path, _ = client
    done = threading.Event()
    with provider_preferences._write_lock:       # what PUT /api/config holds while it writes
        t, _out = _run(lambda: (c.post('/api/update-ping/choice', json={'enabled': False}), done.set()))
        assert not done.wait(0.4), 'choice wrote while another config writer held the lock'
        assert json.loads(path.read_text()) == {'keep': 1}
    assert done.wait(5)
    assert json.loads(path.read_text())['update_ping_enabled'] is False


def test_opt_out_during_version_lookup_sends_nothing(ping):
    """P2: OFF saved while the (git describe) version callback is running must stop the POST."""
    entered, release = threading.Event(), threading.Event()

    def slow_version():
        entered.set()
        release.wait(5)
        return 'v2.3.0'
    update_ping.wire(ping.dir / 'state.json', config_fn=lambda: ping.cfg,
                     version_fn=slow_version, ping_id_path=ping.dir / 'ping_id')
    t, out = _run(lambda: update_ping.maybe_ping(now=1_000_000, post_fn=ping.post))
    assert entered.wait(5)
    ping.cfg['update_ping_enabled'] = False      # the user's opt-out lands now
    release.set()
    t.join(5)
    assert ping.calls == []
    assert out['r'] == {'sent': False, 'reason': 'disabled'}
    assert not (ping.dir / 'ping_id').exists()
    # nothing was sent, so the day is not consumed: opting back in pings immediately
    ping.cfg['update_ping_enabled'] = True
    assert update_ping.maybe_ping(now=1_000_001, post_fn=ping.post)['sent'] is True


def test_env_off_during_version_lookup_sends_nothing(ping):
    entered, release = threading.Event(), threading.Event()

    def slow_version():
        entered.set()
        release.wait(5)
        return 'v2.3.0'
    update_ping.wire(ping.dir / 'state.json', config_fn=lambda: ping.cfg,
                     version_fn=slow_version, ping_id_path=ping.dir / 'ping_id')
    env = {}
    t, out = _run(lambda: update_ping.maybe_ping(now=1_000_000, post_fn=ping.post, environ=env))
    assert entered.wait(5)
    env['DO_NOT_TRACK'] = '1'
    release.set()
    t.join(5)
    assert ping.calls == [] and out['r']['reason'] == 'env_off'


def test_update_ping_routes_wire_guard(tmp_path):
    """pyright: CONFIG_PATH may be None; _persist must say so rather than open(None)."""
    saved = update_ping_routes.CONFIG_PATH
    update_ping_routes.CONFIG_PATH = None
    try:
        with pytest.raises(RuntimeError):
            update_ping_routes._persist({'x': 1})
    finally:
        update_ping_routes.CONFIG_PATH = saved


# ── opt-out vs the gate-to-send transition (Fenn re-review, 2026-11-05) ─────

def _choice_off_thread(events, app):
    """POST /api/update-ping/choice {enabled:false} through the real route."""
    def go():
        r = app.test_client().post('/api/update-ping/choice', json={'enabled': False})
        events.append(('off_response', r.status_code))
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


@pytest.fixture
def route_app(client, monkeypatch):
    c, path, live = client
    live['update_ping_disclosed'] = True
    monkeypatch.setattr(update_ping_routes, 'is_unattended_caller', lambda: False)
    app = Flask(__name__)
    app.register_blueprint(update_ping_routes.bp)
    return app, path, live


def test_opt_out_during_id_file_step_sends_nothing(route_app, monkeypatch):
    """Fenn's repro: the sender pauses in get_ping_id (after the early check, before the
    final gate); OFF is saved through the real route and returns 200. No POST may follow."""
    app, path, live = route_app
    reached, release, events = threading.Event(), threading.Event(), []
    real_id = update_ping.get_ping_id

    def slow_id():
        reached.set()
        assert release.wait(10)
        return real_id()
    monkeypatch.setattr(update_ping, 'get_ping_id', slow_id)

    def post(url, payload):
        events.append(('post', live['update_ping_enabled']))
        return {}
    t, out = _run(lambda: update_ping.maybe_ping(now=1_000_000, post_fn=post, environ={}))
    assert reached.wait(5)
    off = _choice_off_thread(events, app)
    off.join(5)
    assert ('off_response', 200) in events          # OFF succeeded while the sender was paused
    release.set()
    t.join(10)
    assert [e for e in events if e[0] == 'post'] == [], events
    assert out['r'] == {'sent': False, 'reason': 'disabled'}


def test_opt_out_after_the_final_gate_waits_for_the_post(route_app, monkeypatch):
    """OFF fired the instant the final gate passes (before transport). The save may not
    return success until the in-flight request is over, and no second request may start."""
    app, path, live = route_app
    events, off_holder = [], []
    real_gate = update_ping.blocked_reason
    armed = {'on': False}

    def gate(cfg, environ=None):
        r = real_gate(cfg, environ)
        if armed['on'] and r is None and not off_holder:
            off_holder.append(_choice_off_thread(events, app))
            time.sleep(0.4)                           # lets an unserialized OFF finish first
        return r
    monkeypatch.setattr(update_ping, 'blocked_reason', gate)

    def post(url, payload):
        events.append(('post', live['update_ping_enabled']))
        return {}
    # the first pass through the gate is maybe_ping's own cheap one; arm it after that,
    # and skip the early _recheck so the next gate IS the final one
    monkeypatch.setattr(update_ping, '_recheck', lambda environ=None: armed.update(on=True))
    r = update_ping.maybe_ping(now=1_000_000, post_fn=post, environ={})
    off_holder[0].join(5)
    assert r['sent'] is True
    # the request was already past the point of no return; the save came strictly after it
    assert events == [('post', True), ('off_response', 200)], events
    assert live['update_ping_enabled'] is False
    assert json.loads(path.read_text())['update_ping_enabled'] is False
    # and from here on nothing new starts
    assert update_ping.maybe_ping(now=1_000_000 + 2 * DAY, post_fn=post, environ={})['reason'] == 'disabled'
    assert [e for e in events if e[0] == 'post'] == [('post', True)]


def test_http_post_is_bounded_against_a_dripping_server(monkeypatch):
    """The POST now runs under the config lock, so a slow-drip body must not hold it forever."""
    class Drip:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n):
            time.sleep(0.05)
            return b'x'
    class Opener:
        def open(self, req, timeout=None): return Drip()
    monkeypatch.setattr(update_ping.urllib.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(update_ping, 'TIMEOUT_S', 0.3)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        update_ping._http_post('https://example.invalid/ping', {})
    assert time.monotonic() - start < 2
