"""The Desk's vendor transport names itself, and a refused Higgsfield client
registration is visible (Ron 2026-10-05: "Sign in with Higgsfield" failed with
"Higgsfield did not accept this app's sign-in request", nothing in the log).

Measured 2026-10-05: Cloudflare in front of clerk.higgsfield.ai answers HTTP 403
"error code: 1010" to urllib's default `Python-urllib/3.x` User-Agent on
/oauth/register, /oauth/token and /oauth/token/revoke, while its /.well-known
documents answer 200. So discovery passed and registration failed. NO REAL
PROVIDER CALL here: `desk_engines._OPENER` is a fake that behaves like that edge,
and the request goes through the real `desk_oauth._http` -> `_http_request` chain.
"""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk_engines as eng  # noqa: E402
from mc import desk_oauth as oauth  # noqa: E402

CF_1010 = {'type': 'https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/',
           'title': 'Error 1010: Access denied', 'status': 403, 'error_code': 1010}


class _Resp:
    def __init__(self, status, obj):
        self.status = status
        self._data = json.dumps(obj).encode()
        self.headers = {'Content-Type': 'application/json'}

    def read(self, n=-1):
        return self._data

    def getcode(self):
        return self.status

    def close(self):
        pass


class CloudflareEdge:
    """A fake `_OPENER`: Higgsfield's discovery documents for anyone; /oauth/* refused
    with 1010 unless the request names itself (urllib adds `Python-urllib/x` itself
    when the request carries no User-Agent)."""

    def __init__(self):
        self.seen: list[tuple[str, str, str | None]] = []

    def open(self, req, timeout=None):
        ua = req.get_header('User-agent')
        url = req.full_url
        self.seen.append((req.get_method(), url, ua))
        path = urllib.parse.urlsplit(url).path
        if path.startswith('/.well-known/oauth-protected-resource'):
            return _Resp(200, {'resource': 'https://mcp.higgsfield.ai/mcp',
                               'authorization_servers': ['https://clerk.higgsfield.ai'],
                               'scopes_supported': ['openid', 'email', 'offline_access']})
        if path.startswith('/.well-known/oauth-authorization-server'):
            return _Resp(200, {'issuer': 'https://clerk.higgsfield.ai',
                               'authorization_endpoint': 'https://clerk.higgsfield.ai/oauth/authorize',
                               'token_endpoint': 'https://clerk.higgsfield.ai/oauth/token',
                               'registration_endpoint': 'https://clerk.higgsfield.ai/oauth/register',
                               'code_challenge_methods_supported': ['S256']})
        if not ua or ua.startswith('Python-urllib'):
            return _Resp(403, CF_1010)
        return _Resp(201, {'client_id': 'cid-ua', 'token_endpoint_auth_method': 'none'})


@pytest.fixture
def edge(monkeypatch):
    e = CloudflareEdge()
    monkeypatch.setattr(eng, '_OPENER', e)
    return e


@pytest.fixture
def logs(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(oauth, '_log', lambda msg, *a, **k: lines.append(str(msg)))
    return lines


@pytest.fixture(autouse=True)
def _clean_flows():
    yield
    with oauth._lock:
        servers = [f.get('listener') for f in oauth._flows.values()]
        oauth._flows.clear()
    for s in servers:
        if s is not None:
            s.shutdown()
            s.server_close()


def test_vendor_calls_name_clayrune_not_python_urllib(edge):
    eng._http_request('GET', 'https://clerk.higgsfield.ai/oauth/register')
    assert edge.seen[-1][2] == eng.USER_AGENT
    assert not eng.USER_AGENT.startswith('Python-urllib')


def test_a_caller_set_user_agent_is_kept(edge):
    eng._http_request('GET', 'https://example.test/x', headers={'user-agent': 'Other/1'})
    assert edge.seen[-1][2] == 'Other/1'


def test_higgsfield_sign_in_registers_through_the_cloudflare_edge(edge, logs):
    out = oauth.start('higgsfield')
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(out['auth_url']).query))
    assert q['client_id'] == 'cid-ua'
    assert all(ua == eng.USER_AGENT for _m, _u, ua in edge.seen)


def test_a_refused_registration_is_logged_with_its_status_and_body(edge, logs, monkeypatch):
    monkeypatch.setattr(eng, 'USER_AGENT', 'Python-urllib/3.14')     # the edge refuses this
    with pytest.raises(oauth.OAuthError) as e:
        oauth.start('higgsfield')
    assert e.value.code == 'register_failed'
    assert 'HTTP 403' in str(e.value)
    refused = [ln for ln in logs if 'registration refused' in ln]
    assert len(refused) == 1 and 'HTTP 403' in refused[0] and '1010' in refused[0]
    assert len(refused[0]) < 400
    assert oauth._flows == {}
