"""Host-only decision table for passkey enrollment (mc/passkeys/host_check.py).

The check is a pure function of (peer address, headers, configured port), so the
whole table is exercised here without a Flask app. Route-level behaviour — that
the refusal lands before the passcode is consulted — is in test_passkeys_routes.py.
"""
from __future__ import annotations

import pytest

from mc.passkeys import host_check

PORT = 5199
GOOD = {'Host': 'localhost:5199', 'Origin': 'http://localhost:5199'}


def code(addr='127.0.0.1', headers=None, **kw):
    return host_check.refusal_code(addr, GOOD if headers is None else headers, PORT, **kw)


def test_host_browser_on_canonical_localhost_is_accepted():
    for addr in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        assert code(addr) is None, addr


@pytest.mark.parametrize('addr', ['192.168.1.20', '10.0.0.5', '8.8.8.8', '', None, 'localhost', 'fe80::1'])
def test_lan_and_odd_peers_are_not_loopback(addr):
    # 'localhost' as a literal remote_addr is not a peer address a real socket
    # produces; the check wants a numeric loopback, so it is refused too.
    assert code(addr) == 'not_loopback'


def test_forged_loopback_headers_from_a_lan_peer_are_refused():
    forged = dict(GOOD, **{'X-Forwarded-For': '127.0.0.1', 'X-Real-IP': '127.0.0.1'})
    assert code('192.168.1.20', forged) == 'not_loopback'
    assert code('192.168.1.20', GOOD) == 'not_loopback'


@pytest.mark.parametrize('extra', [
    {'Cf-Access-Authenticated-User-Email': 'owner@example.com'},
    {'Cf-Access-Jwt-Assertion': 'x.y.z'},
    {'Cf-Ray': 'abc'},
    {'CF-Connecting-IP': '1.2.3.4'},
    {'X-Forwarded-For': '1.2.3.4'},
    {'X-Forwarded-Host': 'evil.example'},
    {'X-Forwarded-Proto': 'https'},
    {'Forwarded': 'for=1.2.3.4'},
    {'Via': '1.1 proxy'},
    {'X-Real-IP': '1.2.3.4'},
    {'True-Client-IP': '1.2.3.4'},
])
def test_tunnel_and_proxy_headers_over_loopback_are_refused(extra):
    # A tunnelled request reaches this server from cloudflared on loopback, so
    # the peer address alone cannot tell it from the owner's browser.
    assert code('127.0.0.1', dict(GOOD, **extra)) == 'proxied'


def test_header_names_are_case_insensitive():
    assert code(headers={'host': 'localhost:5199', 'origin': 'http://localhost:5199',
                         'cf-ray': 'x'}) == 'proxied'
    assert code(headers={'HOST': 'LOCALHOST:5199', 'ORIGIN': 'http://localhost:5199'}) is None


@pytest.mark.parametrize('host', [
    '127.0.0.1:5199', '[::1]:5199', 'localhost', 'localhost:5200', 'localhost:80',
    '192.168.1.5:5199', 'abc.trycloudflare.com', 'localhost.evil.example:5199',
    'evil.example:5199', '', 'localhost:5199.evil.example'])
def test_only_the_exact_configured_host_is_accepted(host):
    assert code(headers={'Host': host, 'Origin': 'http://localhost:5199'}) == 'bad_host'


@pytest.mark.parametrize('origin', [
    '', 'null', 'http://127.0.0.1:5199', 'https://localhost:5199', 'http://localhost',
    'http://localhost:5200', 'http://evil.example', 'http://localhost:5199/',
    'http://localhost:5199.evil.example'])
def test_only_the_exact_configured_origin_is_accepted(origin):
    headers = {'Host': 'localhost:5199'}
    if origin:
        headers['Origin'] = origin
    assert code(headers=headers) == 'bad_origin'


def test_missing_origin_is_refused_for_writes_but_allowed_for_a_status_get():
    headers = {'Host': 'localhost:5199'}
    assert code(headers=headers) == 'bad_origin'
    assert code(headers=headers, require_origin=False) is None
    # A GET that does carry an Origin must still carry the right one.
    assert code(headers=dict(headers, Origin='http://evil.example'), require_origin=False) == 'bad_origin'


def test_cross_site_fetch_metadata_is_refused():
    assert code(headers=dict(GOOD, **{'Sec-Fetch-Site': 'same-origin'})) is None
    for site in ('cross-site', 'same-site', 'none'):
        assert code(headers=dict(GOOD, **{'Sec-Fetch-Site': site})) == 'cross_site', site


def test_port_comes_from_configuration_not_the_request():
    other = host_check.refusal_code('127.0.0.1', {'Host': 'localhost:6000',
                                                  'Origin': 'http://localhost:6000'}, 6000)
    assert other is None
    assert host_check.refusal_code('127.0.0.1', {'Host': 'localhost:6000',
                                                 'Origin': 'http://localhost:6000'}, PORT) == 'bad_host'


def test_every_code_has_a_message_with_the_port_filled_in():
    for c in ('not_loopback', 'proxied', 'bad_host', 'bad_origin', 'cross_site'):
        msg = host_check.refusal_message(c, PORT)
        assert msg and '{port}' not in msg
    assert 'http://localhost:5199' in host_check.refusal_message('bad_host', PORT)
