"""Real-Chromium proof that the discovery pane's network confinement holds on the routes an
HTTP proxy cannot see (docs/DESK_CONNECT_BY_URL_SPEC.md, slice 3). The unit tests in
tests/test_desk_connect_discovery.py fake the pane; these launch the real thing.

UDP (WebRTC). A hostile page builds an RTCPeerConnection whose stun:/turn: iceServers point
at UDP listeners on loopback, the LAN address and 0.0.0.0. Chromium sends the STUN/TURN
datagrams itself, outside the CONNECT proxy, unless told not to. The `--force-webrtc-ip-
handling-policy` flag this module once shipped is not a Chromium switch (24 datagrams reached
the listeners); `--webrtc-ip-handling-policy=disable_non_proxied_udp` is (0). The tests below
pin that, and a NEGATIVE CONTROL (the old flag, no init script) must still leak, so a "0" can
never be an artefact of the test setup.

Devtools. The proxy bypass leaves the pane's own debugging port reachable, so the launch
narrows `--remote-allow-origins`: a websocket handshake carrying any other Origin is refused.

Each test launches its OWN Chromium (throwaway profile, its own debugging port, closed by its
own handle) and skips when Chromium or websocket-client is missing. The public host name is a
test stand-in (resolver and connector below route it to a local TLS server; the certificate
is generated here and trusted only through a test-only Chromium flag). No real site is touched.
"""
from __future__ import annotations

import datetime
import http.server
import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.request

import pytest

from mc.blueprints import browser_routes as br
from mc.desk_connect import guard_proxy, pane_reader

HOST = 'attacker.example.com'
PUBLIC_STANDIN = '93.184.216.34'

pytestmark = pytest.mark.skipif(not br._find_chromium() or not br._import_ws(),
                                reason='Chromium or websocket-client unavailable')


def _lan_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))          # no packet is sent: this only picks the outbound interface
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith('127.') else ip


class _Udp:
    """UDP listeners that count every datagram that reaches them."""

    def __init__(self):
        self.socks: list[tuple[socket.socket, str, int]] = []
        self.hits: list[tuple[str, int]] = []
        self._stop = False

    def listen(self, addr: str, port: int = 0, family=socket.AF_INET) -> int | None:
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            s.bind((addr, port))
        except OSError:
            s.close()
            return None
        s.settimeout(0.2)
        bound = s.getsockname()[1]
        self.socks.append((s, addr, bound))
        threading.Thread(target=self._loop, args=(s, addr, bound), daemon=True).start()
        return bound

    def _loop(self, s, addr, port):
        while not self._stop:
            try:
                s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            self.hits.append((addr, port))

    def close(self):
        self._stop = True
        for s, _a, _p in self.socks:
            s.close()


def _certificate(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .sign(key, hashes.SHA256()))
    cp, kp = tmp_path / 'c.pem', tmp_path / 'k.pem'
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()))
    return str(cp), str(kp)


def _probe_page(udp_targets: list[str]) -> bytes:
    urls = json.dumps(udp_targets)
    return f"""<!doctype html><title>probe</title><body><pre id="o">x</pre><script>
const t = typeof RTCPeerConnection;
document.getElementById('o').textContent = 'rtc=' + t;
if (t === 'function') {{
  const pc = new RTCPeerConnection({{iceServers: [{{urls: {urls}, username: 'u', credential: 'c'}}]}});
  pc.createDataChannel('x');
  pc.createOffer().then(o => pc.setLocalDescription(o));
}}
</script></body>""".encode()


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A local TLS server standing in for one public host, UDP listeners on every local
    address a hostile page might aim at, and a guard proxy in front of the pane."""
    monkeypatch.setattr(br, '_profiles_root', lambda: str(tmp_path))
    udp = _Udp()
    targets: list[str] = []
    p = udp.listen('127.0.0.1')
    targets += [f'stun:127.0.0.1:{p}', f'turn:127.0.0.1:{p}?transport=udp']
    p = udp.listen('0.0.0.0')
    targets += [f'stun:0.0.0.0:{p}']
    p = udp.listen('127.0.0.1', 3478)                 # the standard STUN/TURN port, when free
    if p:
        targets += ['stun:127.0.0.1:3478']
    lan = _lan_ip()
    if lan:
        p = udp.listen(lan)
        if p:
            targets += [f'stun:{lan}:{p}']
    p6 = udp.listen('::1', 0, socket.AF_INET6)
    if p6:
        targets += [f'stun:[::1]:{p6}']
    page = _probe_page(targets)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    cert, key = _certificate(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    srv_port = httpd.server_address[1]

    def resolver(host, port, type=0):
        if host == HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_STANDIN, port))]
        return socket.getaddrinfo(host, port, type=type)

    def connector(addr, port, timeout):
        if addr == PUBLIC_STANDIN:                    # test only: the stand-in for the public host
            return socket.create_connection(('127.0.0.1', srv_port), timeout)
        return socket.create_connection((addr, port), timeout)

    proxy = guard_proxy.GuardProxy(own_hosts=(), resolver=resolver, connector=connector)
    proxy.start()
    real_args = pane_reader.chromium_args
    monkeypatch.setattr(pane_reader, 'chromium_args',
                        lambda *a, **k: real_args(*a, **k) + ['--ignore-certificate-errors'])
    try:
        yield udp, proxy, targets
    finally:
        proxy.close()
        httpd.shutdown()
        udp.close()


def _visit(proxy, wait_s=4.0):
    """Open the probe page in the real discovery pane, give ICE gathering time, then close."""
    pane = pane_reader.DiscoveryPane('test_desk_connect', proxy.port)
    try:
        body = pane.read(f'https://{HOST}/')
        time.sleep(wait_s)
        return body
    finally:
        pane.close()


def test_the_discovery_pane_sends_no_udp_to_local_listeners(lab):
    udp, proxy, targets = lab
    assert len(targets) >= 3, targets
    body = _visit(proxy)
    assert body.get('ok'), body
    assert body['content']['text'].strip() == 'rtc=undefined'          # the init script removed WebRTC
    assert udp.hits == [], f'{len(udp.hits)} datagram(s) reached {sorted(set(udp.hits))}'


def test_the_webrtc_policy_flag_alone_closes_the_udp_route(lab, monkeypatch):
    """No init script: the page DOES have RTCPeerConnection and uses it, and still 0 datagrams."""
    udp, proxy, targets = lab
    monkeypatch.setattr(pane_reader, 'WEBRTC_OFF_JS', None)
    body = _visit(proxy)
    assert body.get('ok'), body
    assert body['content']['text'].strip() == 'rtc=function'
    assert udp.hits == [], f'{len(udp.hits)} datagram(s) reached {sorted(set(udp.hits))}'


def test_negative_control_the_old_flag_name_leaks(lab, monkeypatch):
    """The flag the module used to carry is not a real switch. With it, and no init script,
    the same page DOES reach the listeners: so a 0 above is the policy, not the setup."""
    udp, proxy, targets = lab
    monkeypatch.setattr(pane_reader, 'WEBRTC_OFF_JS', None)
    good = pane_reader.chromium_args

    def old(*a, **k):
        return [x.replace('--webrtc-ip-handling-policy=', '--force-webrtc-ip-handling-policy=')
                for x in good(*a, **k)]
    monkeypatch.setattr(pane_reader, 'chromium_args', old)
    body = _visit(proxy)
    assert body.get('ok'), body
    assert body['content']['text'].strip() == 'rtc=function'
    assert len(udp.hits) > 0, 'the control did not leak: this machine cannot show the difference'


def test_a_devtools_websocket_needs_the_pane_own_origin(lab):
    """The pane's debugging port stays reachable (the proxy bypass), but only the harness's
    own origin may open a websocket to it; a page's origin is refused. The pane still works."""
    udp, proxy, targets = lab
    websocket = br._import_ws()
    pane = pane_reader.DiscoveryPane('test_desk_connect', proxy.port)
    try:
        body = pane.read(f'https://{HOST}/')
        assert body.get('ok'), body                                    # the harness itself still connects
        port = pane._session['port']
        ver = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=3))
        ws_url = ver['webSocketDebuggerUrl']
        for origin in (f'https://{HOST}', 'http://evil.example', 'null'):
            with pytest.raises(Exception) as e:
                websocket.create_connection(ws_url, origin=origin, timeout=5).close()
            assert '403' in str(e.value) or 'Forbidden' in str(e.value) or 'Handshake' in str(e.value), (origin, str(e.value))
        ok = websocket.create_connection(ws_url, origin=f'http://127.0.0.1:{port}', timeout=5)
        ok.close()                                                      # its own origin is accepted
    finally:
        pane.close()


def test_an_ordinary_pane_keeps_its_wildcard_origin_and_has_no_init_script():
    """The new launch options are opt-in: nothing else's launch changed."""
    import inspect
    sig = inspect.signature(br._launch_browser)
    assert sig.parameters['narrow_remote_origins'].default is False
    assert sig.parameters['init_script'].default is None
    src = inspect.getsource(br._launch_browser)
    assert '"*"' in src                                                 # the default branch is still '*'
