"""/api/terminal/launch must be HOST-only — and "host" excludes the tunnel.

cloudflared runs on the same machine and forwards to the origin over loopback,
so a tunneled request presents remote_addr == 127.0.0.1. Tunnel requests are a
strict SUBSET of loopback requests, which means the original
`if not _is_loopback_request(): 403` guard admitted every caller who had
cleared CF Access — turning dashboard access into arbitrary command execution
via `subprocess.Popen(..., shell=True)`.

Found 2026-08-22 while triaging a CodeAnt scan: the endpoint's own comment
claimed "LAN/tunnel sessions get the dashboard, never raw shell". The LAN half
was true; the tunnel half was not.

The dashboard never calls this endpoint (only stream/stdin/stop/delete), so
excluding the tunnel costs no UI behaviour.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}
HOST = {'REMOTE_ADDR': '127.0.0.1'}
BODY = {'project_id': 'mission_control', 'command': 'echo hi'}


@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


def _set_tunnel(monkeypatch, value: bool):
    """Force the injected CF-tunnel predicate for the duration of a test."""
    from mc.blueprints import terminal_routes as tr
    monkeypatch.setattr(tr, '_is_cf_tunneled_request', lambda: value)


def test_lan_is_refused(client):
    """LAN never reaches the handler.

    It is stopped one layer earlier than the tunnel case: local_auth_gate
    returns 401 auth_required for a non-exempt peer with no passcode cookie.
    Assert "blocked", not a specific code, so this stays true if the gate
    ordering changes.
    """
    r = client.post('/api/terminal/launch', json=BODY, environ_base=LAN)
    assert r.status_code in (401, 403)


def test_tunnel_is_refused_even_though_its_peer_is_loopback(client, monkeypatch):
    """The regression this file exists for."""
    _set_tunnel(monkeypatch, True)
    r = client.post('/api/terminal/launch', json=BODY, environ_base=HOST)
    assert r.status_code == 403, (
        'a CF-tunneled request reached terminal launch — tunnel traffic has a '
        'loopback peer, so the loopback check alone does not exclude it')
    assert r.get_json().get('error') == 'host_only'


def test_real_host_still_gets_past_the_gate(client, monkeypatch):
    """Agents curl this from real localhost; that must keep working.

    Asserts only that the request passes the AUTH gate — a 404 for an unknown
    project is a pass, since it means execution reached the handler body. This
    deliberately avoids spawning a process.
    """
    _set_tunnel(monkeypatch, False)
    r = client.post('/api/terminal/launch',
                    json={'project_id': '__no_such_project__', 'command': 'echo hi'},
                    environ_base=HOST)
    assert r.status_code != 403, 'real host traffic must not be blocked'
    assert r.status_code == 404


def test_guard_survives_unwired_predicate(client, monkeypatch):
    """If wire() is never called the predicate is None — must not crash."""
    from mc.blueprints import terminal_routes as tr
    monkeypatch.setattr(tr, '_is_cf_tunneled_request', None)
    r = client.post('/api/terminal/launch', json=BODY, environ_base=HOST)
    # None predicate must not raise; loopback still passes the gate, so this
    # lands in the handler and 404s on the real-but-irrelevant project lookup.
    assert r.status_code != 500
