"""P19 (MC-1179, 2026-11-17, Ron: "Only user should be able to set passcode").

While no dashboard passcode exists, `POST /api/local-auth/set` used to accept the
first one from ANY loopback caller with no proof of anything, and that passcode
then stood for "a human is asking" on every gated route (vault writes, the
vault-lock passphrase, the attend-once grant, install/update). An agent's `curl`
set it first. The first set is now host-only and human-only:

  - a LAN device or a tunnel client: refused, `setup_requires_host` (unchanged
    for LAN; the tunnel used to be allowed);
  - an agent or unattended caller on the host: refused with the human-only
    gate's own 403 `human_only` (no browser Origin, or OS-attributed to a managed
    agent session);
  - the person's browser on the host: accepted, as before.

Changing an existing passcode is unchanged (the current passcode is the proof).
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}
BROWSER = {'Origin': 'http://localhost:5199'}
TUNNEL = {'Origin': 'https://dash.example.invalid',
          'Cf-Access-Authenticated-User-Email': 'owner@example.invalid',
          'Cf-Ray': 'abc123'}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server
    from mc.blueprints import local_auth as la
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(la, '_LOCAL_AUTH_FAILS', {})
    server.app.config['TESTING'] = True
    return server.app.test_client(), la


def _set(client, headers=None, **kw):
    return client.post('/api/local-auth/set', json={'passcode': 'hunter22'},
                       headers=headers or {}, **kw)


def test_agent_curl_without_origin_cannot_set_the_first_passcode(env):
    client, la = env
    r = _set(client)                                   # what an agent's curl sends
    assert r.status_code == 403
    assert r.get_json()['error'] == 'human_only'
    assert not la._local_auth_is_configured()


def test_agent_attributed_to_a_managed_session_cannot_set_it_even_with_an_origin(env, monkeypatch):
    from mc import caller_attribution as ca
    client, la = env
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **k: ca.Attribution(
        ca.ATTRIBUTED, 'sess-1', 4242, 'descends from session pid 4242'))
    r = _set(client, BROWSER)                          # forged browser Origin
    assert r.status_code == 403
    assert r.get_json()['error'] == 'human_only'
    assert not la._local_auth_is_configured()


def test_running_unattended_session_refuses_a_caller_with_no_origin(env, monkeypatch):
    from mc import state
    client, la = env
    monkeypatch.setitem(state.agent_sessions, 'sched-1',
                        {'status': 'running', 'trigger_type': 'schedule'})
    r = _set(client)
    assert r.status_code == 403
    assert r.get_json()['error'] == 'human_only'
    assert not la._local_auth_is_configured()


def test_tunnel_client_cannot_set_the_first_passcode(env):
    client, la = env
    r = _set(client, TUNNEL)                           # loopback peer + Cf-* headers
    assert r.status_code == 403
    assert r.get_json()['error'] == 'setup_requires_host'
    assert not la._local_auth_is_configured()


def test_lan_device_still_cannot_set_it(env):
    client, la = env
    r = _set(client, BROWSER, environ_base=LAN)
    assert r.status_code == 403
    assert r.get_json()['error'] == 'setup_requires_host'
    assert not la._local_auth_is_configured()


def test_owner_browser_on_the_host_can_set_the_first_passcode(env):
    client, la = env
    r = _set(client, BROWSER)
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'configured': True}
    assert la._local_auth_is_configured()
    assert 'mc_local_auth' in r.headers.get('Set-Cookie', '')


def test_changing_an_existing_passcode_is_not_this_gate(env):
    """Once a passcode exists the current one is the proof from every caller;
    the first-set gate must not add a second requirement there."""
    client, la = env
    la._local_auth_set_passcode('hunter22')
    r = client.post('/api/local-auth/set',
                    json={'passcode': 'newpass77', 'current': 'hunter22'})  # no Origin
    assert r.status_code == 200
    assert la._local_auth_verify_passcode('newpass77')


def test_too_short_is_still_a_400_for_the_owner(env):
    client, _la = env
    r = client.post('/api/local-auth/set', json={'passcode': 'abc'}, headers=BROWSER)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'passcode_too_short'
