"""P3 transport policy, vault scope and nonredirecting read evidence, offline."""
import io
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from mc import secrets_store
from mc.desk_impact_github_transport import GitHubTransport, _NoRedirect
from tests.desk_impact_github_fakes import TARGET, offline

URL = 'https://api.github.com/repos/example/repo/stargazers'
HEADERS = {'Accept': 'application/vnd.github.star+json'}


class Reply(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}


class Opener:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return Reply(self.result if self.result is not None else b'[]')


def setup_transport(**patch):
    connection = {'id': 'conn-1', 'project_id': 'proj1', 'target': TARGET,
                   'read_enabled': True, 'vault_ref': 'chosen-reference', **patch}
    calls = []
    opener = Opener()
    def secret(name, **kwargs):
        calls.append((name, kwargs))
        return 'opaque-fixture-value'
    transport = GitHubTransport('proj1', lambda _: connection, unattended=True,
                                 opener=opener, secret_reader=secret)
    return transport, opener, calls


def test_transport_uses_only_selected_vault_reference_and_existing_scope_checks():
    transport, opener, calls = setup_transport()
    result = transport.get(URL, connection_ref='conn-1', headers=HEADERS, params={'page': '1', 'per_page': '100'})
    assert result.body == b'[]'
    assert calls == [('chosen-reference', {'consumer': 'desk-impact-github', 'project_id': 'proj1', 'unattended': True})]
    request, timeout = opener.calls[0]
    assert timeout == 10 and request.get_method() == 'GET'
    assert request.get_header('Authorization') == 'Bearer opaque-fixture-value'
    assert request.full_url.endswith('?page=1&per_page=100')


@pytest.mark.parametrize('patch', [{'project_id': 'other'}, {'id': 'other'},
                                  {'read_enabled': False}, {'vault_ref': None}])
def test_foreign_disabled_or_unconfigured_connection_cannot_dispense(patch):
    transport, opener, calls = setup_transport(**patch)
    with pytest.raises(PermissionError):
        transport.get(URL, connection_ref='conn-1', headers=HEADERS)
    assert calls == [] and opener.calls == []


@pytest.mark.parametrize('url', ['https://evil.invalid/steal',
    'http://api.github.com/repos/example/repo/stargazers',
    'https://api.github.com.evil.invalid/repos/example/repo/stargazers',
    'https://api.github.com/repos/other/repo/stargazers',
    'https://api.github.com/repos/example/repo/stargazers?token=bad',
    'https://user@api.github.com/repos/example/repo/stargazers',
    'https://api.github.com/repos/example/repo/issues',
    'https://api.github.com/repos/example/repo/releases/assets/0',
    'https://api.github.com/repos/example/repo/stargazers#hidden'])
def test_url_policy_precedes_secret_and_network(url):
    transport, opener, calls = setup_transport()
    with pytest.raises(PermissionError):
        transport.get(url, connection_ref='conn-1', headers=HEADERS)
    assert calls == [] and opener.calls == []


@pytest.mark.parametrize('params,headers', [({'url': 'https://elsewhere'}, HEADERS),
    ({'per': 'week'}, HEADERS), ({'page': '0'}, HEADERS),
    ({}, {'Accept': 'text/html'}), ({}, {**HEADERS, 'Authorization': 'anything'})])
def test_header_query_injection_is_refused(params, headers):
    transport, opener, calls = setup_transport()
    with pytest.raises(PermissionError):
        transport.get(URL, connection_ref='conn-1', headers=headers, params=params)
    assert not calls and not opener.calls


def test_redirect_handler_refuses_every_new_host_and_same_host():
    handler = _NoRedirect()
    for newurl in ('https://evil.invalid', URL):
        assert handler.redirect_request(Request(URL), None, 302, '', {}, newurl) is None


def test_default_opener_installs_no_redirect_policy():
    transport = GitHubTransport('proj1', lambda _: None, unattended=True)
    assert any(isinstance(h, _NoRedirect) for h in transport.opener.handlers)


def test_http_failure_discards_body_and_unsafelist_headers():
    transport, opener, calls = setup_transport()
    body = io.BytesIO(b'raw body with a credential')
    opener.result = HTTPError(URL, 429, 'raw error', {'Retry-After': '60', 'Authorization': 'hidden'}, body)
    result = transport.get(URL, connection_ref='conn-1', headers=HEADERS)
    assert result.status == 429 and result.body == b''
    assert result.headers == {'Retry-After': '60'} and body.closed


@pytest.mark.parametrize('error', [secrets_store.SecretDenied('private-message'),
    secrets_store.SecretNotFound('private-message'), secrets_store.SecretsUnavailable('private-message')])
def test_vault_errors_are_safe_and_do_not_open_network(error):
    transport, opener, calls = setup_transport()
    def refuse(*args, **kwargs):
        raise error
    transport.secret_reader = refuse
    with pytest.raises(PermissionError, match='vault access refused') as exc:
        transport.get(URL, connection_ref='conn-1', headers=HEADERS)
    assert 'private-message' not in str(exc.value) and opener.calls == []


@pytest.mark.parametrize('reason,kind', [(TimeoutError('raw'), TimeoutError), ('raw', OSError)])
def test_url_errors_do_not_leak_raw_details(reason, kind):
    transport, opener, calls = setup_transport()
    opener.result = URLError(reason)
    with pytest.raises(kind) as exc:
        transport.get(URL, connection_ref='conn-1', headers=HEADERS)
    assert 'raw' not in str(exc.value)


def test_response_body_is_bounded(monkeypatch):
    import mc.desk_impact_github_transport as module
    monkeypatch.setattr(module, 'MAX_BODY_BYTES', 5)
    transport, opener, calls = setup_transport()
    opener.result = b'123456'
    with pytest.raises(OSError, match='size limit'):
        transport.get(URL, connection_ref='conn-1', headers=HEADERS)
