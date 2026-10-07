"""Capture settings, path/provenance confinement and the real Chromium path."""
import io
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parent.parent))
from mc import caller_attribution, desk, desk_capture as capture, desk_pieces
from mc.blueprints import desk_capture_routes as routes, desk_routes, browser_routes
from mc.desk_capture_network import CaptureProxy, address, origin
from mc.desk_connect.net_guard import Blocked

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64
LOCAL = 'http://127.0.0.1:5199/'
PROJECTS = [{'id': 'mission_control', 'name': 'Clayrune'}, {'id': 'alpha', 'name': 'Alpha'}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk_pieces, 'UPLOADS_ROOT', tmp_path / 'uploads')
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: next((p for p in PROJECTS if p['id'] == pid), None))
    monkeypatch.setattr(routes, '_local_url', lambda: LOCAL)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    return app.test_client()


@pytest.mark.parametrize('url', ['file:///tmp/a', 'ftp://host/a', 'javascript:alert(1)', 'https://user:pass@host', '//host', 'http://', 'http://a:bad', 'http://a\\b', 'http://a\n.test'])
def test_only_web_addresses(url):
    with pytest.raises(ValueError):
        address(url)


def test_server_pages_and_unknown_product(env):
    info = env.get('/api/desk/capture/projects/mission_control').get_json()
    assert info['app_address'] == LOCAL and len(info['pages']) == 5
    assert env.get('/api/desk/capture/projects/missing').status_code == 404
    assert env.post('/api/desk/capture', json={'project_id': '../secret', 'page': '/'}).status_code == 400
    assert env.post('/api/desk/capture', json={'project_id': 'mission_control', 'page': {}}).status_code == 400


def test_missing_address_prompts_and_never_starts_browser(env, monkeypatch):
    monkeypatch.setattr(capture, 'screenshot', lambda *a, **kw: pytest.fail('browser started'))
    assert env.get('/api/desk/capture/projects/alpha').get_json()['app_address'] == ''
    r = env.post('/api/desk/capture', json={'project_id': 'alpha', 'page': '/'})
    assert r.status_code == 409 and 'address' in r.get_json()['error']


def test_private_setting_requires_human_provenance_not_body_flag(env, monkeypatch):
    monkeypatch.setattr(routes, '_human_typed', lambda: False)
    r = env.put('/api/desk/capture/projects/alpha', json={'app_address': 'http://127.0.0.1:3000', 'user_entered': True})
    assert r.status_code == 400
    assert not desk.STORE_PATH.exists()
    monkeypatch.setattr(routes, '_human_typed', lambda: True)
    assert env.put('/api/desk/capture/projects/alpha', json={'app_address': 'http://127.0.0.1:3000'}).status_code == 200
    url, allowed, page = capture.target('alpha', '/pricing', load_project=desk_routes.load_project, local_url=LOCAL)
    assert url == 'http://127.0.0.1:3000/pricing' and ('127.0.0.1', 3000) in allowed and page is None
    assert desk._read_store()['capture_apps']['alpha']['user_entered'] is True


def test_agent_cannot_claim_a_browser_origin_for_private_exception(env, monkeypatch):
    monkeypatch.setattr(caller_attribution, 'attribute_caller', lambda *a, **kw: caller_attribution.Attribution(caller_attribution.ATTRIBUTED, session_id='agent'))
    r = env.put('/api/desk/capture/projects/alpha', json={'app_address': 'http://127.0.0.1:3000'}, headers={'Origin': LOCAL})
    assert r.status_code == 400 and not desk.STORE_PATH.exists()


def test_uncertain_attribution_never_grants_private_exception(env, monkeypatch):
    monkeypatch.setattr(caller_attribution, 'attribute_caller', lambda *a, **kw: caller_attribution.Attribution(caller_attribution.UNAVAILABLE))
    r = env.put('/api/desk/capture/projects/alpha', json={'app_address': 'http://127.0.0.1:3000'}, headers={'Origin': LOCAL})
    assert r.status_code == 400


def test_only_the_dashboard_origin_can_grant_a_typed_exception(env, monkeypatch):
    monkeypatch.setattr(caller_attribution, 'attribute_caller', lambda *a, **kw: caller_attribution.Attribution(caller_attribution.UNATTRIBUTED))
    data = {'app_address': 'http://127.0.0.1:3000'}
    assert env.put('/api/desk/capture/projects/alpha', json=data, headers={'Origin': 'http://attacker.test'}).status_code == 400
    assert env.put('/api/desk/capture/projects/alpha', json=data, headers={'Origin': 'http://localhost'}).status_code == 200
    assert env.put('/api/desk/capture/projects/alpha', json=data, base_url='http://my-tunnel.test', headers={'Origin': 'https://my-tunnel.test'}).status_code == 200


@pytest.mark.parametrize('page', ['//10.0.0.1/x', '/\\10.0.0.1/x', 'http://10.0.0.1/', 'file:///tmp/a', None, {}, '/\nxyz'])
def test_page_cannot_change_origin_or_scheme(env, monkeypatch, page):
    monkeypatch.setattr(routes, '_human_typed', lambda: True)
    env.put('/api/desk/capture/projects/alpha', json={'app_address': 'http://127.0.0.1:3000'})
    r = env.post('/api/desk/capture', json={'project_id': 'alpha', 'page': page})
    assert r.status_code == 400


def test_corrupt_store_is_not_overwritten(env, monkeypatch):
    desk.STORE_PATH.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(routes, '_human_typed', lambda: True)
    r = env.put('/api/desk/capture/projects/alpha', json={'app_address': 'http://127.0.0.1:3000'})
    assert r.status_code == 503 and desk.STORE_PATH.read_text() == '{broken'


def test_real_file_shape_library_and_recent(env, monkeypatch):
    monkeypatch.setattr(capture, 'screenshot', lambda *a, **kw: PNG)
    r = env.post('/api/desk/capture', json={'project_id': 'mission_control', 'page': 'studio'})
    assert r.status_code == 200
    item = r.get_json()['item']
    assert item['kind'] == 'image' and item['path'].startswith('desk/library/image/Studio/')
    assert (desk_pieces.UPLOADS_ROOT / item['path']).read_bytes() == PNG
    materials = desk_pieces.materials()
    assert materials['library']['image'][0]['items'][0]['path'] == item['path']
    assert materials['recent'][0]['id'] == item['path']


def test_failure_creates_no_file_and_slot_is_released(env, monkeypatch):
    def refused(*a, **kw):
        raise capture.Error('app did not open', 502)
    monkeypatch.setattr(capture, 'screenshot', refused)
    assert env.post('/api/desk/capture', json={'project_id': 'mission_control', 'page': 'floor'}).status_code == 502
    assert not desk_pieces.UPLOADS_ROOT.exists()
    monkeypatch.setattr(capture, 'screenshot', lambda *a, **kw: PNG)
    assert env.post('/api/desk/capture', json={'project_id': 'mission_control', 'page': 'floor'}).status_code == 200


def test_dns_mixed_rebinding_and_exact_origin_boundary():
    def dns(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port)) for ip in ('8.8.8.8', '10.0.0.1')]
    proxy = CaptureProxy(allowed=[('local.test', 3000)], resolver=dns)
    assert proxy.addresses('local.test', 3000) == ['8.8.8.8', '10.0.0.1']
    for host, port in [('other.test', 3000), ('local.test', 3001), ('127.0.0.1', 5199)]:
        with pytest.raises(Blocked):
            proxy.addresses(host, port)


def test_proxy_pins_ip_and_denies_redirect_destination():
    called = []
    class Upstream:
        def sendall(self, value):
            called.append(value)
        def close(self):
            pass
    def connector(ip, port, timeout):
        called.append((ip, port))
        return Upstream()
    proxy = CaptureProxy(resolver=lambda host, port, **kw: [(2, 1, 6, '', ('8.8.8.8', port))], connector=connector)
    proxy._pump = lambda *args: None
    a, b = socket.socketpair()
    try:
        b.sendall(b'GET http://product.test/page HTTP/1.1\r\nHost: fake.test\r\n\r\n')
        proxy._serve(a)
        assert called[0] == ('8.8.8.8', 80)
        assert b'Host: product.test' in called[1] and b'Connection: close' in called[1]
    finally:
        b.close()
    blocked = CaptureProxy(connector=lambda *a: pytest.fail('private socket opened'))
    a, b = socket.socketpair()
    try:
        b.sendall(b'CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n')
        blocked._serve(a)
        assert b'403' in b.recv(1000)
        assert blocked.refused[0]['code'] == 'private_address'
    finally:
        b.close()


def test_real_chromium_capture_guard_and_cleanup(env, monkeypatch):
    if not browser_routes._find_chromium():
        pytest.skip('Chromium is not installed')
    hits, forbidden_hits = [], []
    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            forbidden_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    sink = ThreadingHTTPServer(('127.0.0.1', 0), Sink)
    sink_thread = threading.Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()
    forbidden_url = f'http://127.0.0.1:{sink.server_port}/secret'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', forbidden_url)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(('<html><body style="background:#ee8844"><h1>Actual product page</h1>'
                             f'<img src="{forbidden_url}"><script>fetch("{forbidden_url}").catch(()=>{{}})</script></body></html>').encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    processes = []
    def register(proc, **kw):
        processes.append(proc)
        # Hermetic process-manager seam; pytest forbids live-state writes.
        assert kw['proc_type'] == 'browser' and kw['project_id'] == 'alpha'
    monkeypatch.setattr(browser_routes, '_register_process', register)
    url = f'http://127.0.0.1:{server.server_port}/product'
    try:
        png = capture.screenshot(url, {origin(url)}, project_id='alpha')
        from PIL import Image
        image = Image.open(io.BytesIO(png))
        assert image.size == (1440, 900)
        assert image.getpixel((700, 700))[:3] == (238, 136, 68)
        assert '/product' in hits
        assert forbidden_hits == []  # Subresources and JS fetches cannot leave the chosen origin for another private port.
        with pytest.raises(capture.Error) as error:
            capture.screenshot(url.replace('/product', '/redirect'), {origin(url)}, project_id='alpha')
        assert error.value.status == 502 and forbidden_hits == []
        assert all(p.poll() is not None for p in processes)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        sink.shutdown()
        sink.server_close()
        sink_thread.join(timeout=3)
