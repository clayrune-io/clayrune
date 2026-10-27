"""GET /api/desk/connect/status?account_id=: the X wizard on an account card reads THAT
account's sign-in, not the legacy singleton `oauth.x` (which a per-account sign-in never writes).

Real temporary store/vault, no live calls.
"""
from __future__ import annotations

import time

import pytest

from mc import desk_account_refs, desk_oauth, secrets_store
from tests.test_desk_connect_x_account_attach import saved
from tests.test_desk_oauth_hold import api, env, profiles, _clean_flows   # noqa: F401


def _two_accounts():
    """First X account takes the legacy names; the second gets `oauth.x.<suffix>`."""
    first, _ = saved('first')
    second, _ = saved('second')
    return first, second


def _sign_in(account_arg, **over):
    rec = {'access_token': 'at-' + str(account_arg), 'refresh_token': 'rt-1', 'expires_at': int(time.time()) + 3600,
           'client_id': 'x-client-id-1234', **over}
    desk_oauth._store_record('x', rec, account_arg)


def test_account_scoped_status_reads_that_accounts_vault_entry(api):
    client, _ = api
    first, second = _two_accounts()
    arg = desk_account_refs.require_oauth_arg(second)
    assert arg == second                                   # the second account has its own sign-in name
    _sign_in(arg)
    assert desk_oauth.vault_name('x', arg) in {s['name'] for s in secrets_store.list_secrets()}
    assert client.get('/api/desk/connect/status').get_json()['x']['state'] == 'not_connected'   # the old, account-less read
    scoped = client.get('/api/desk/connect/status', query_string={'account_id': second})
    assert scoped.status_code == 200
    assert scoped.get_json()['x']['state'] == 'connected'


def test_account_with_no_sign_in_is_not_connected_even_if_another_is(api):
    client, _ = api
    first, second = _two_accounts()
    _sign_in(desk_account_refs.require_oauth_arg(second))
    out = client.get('/api/desk/connect/status', query_string={'account_id': first}).get_json()
    assert out['x']['state'] == 'not_connected'


def test_needs_signin_hint_of_the_account_is_reported(api):
    client, _ = api
    _, second = _two_accounts()
    _sign_in(desk_account_refs.require_oauth_arg(second), needs_signin=True)
    out = client.get('/api/desk/connect/status', query_string={'account_id': second}).get_json()
    assert out['x']['state'] == 'needs_signin'


def test_legacy_holder_account_resolves_to_the_singleton_entry(api):
    client, _ = api
    first, _ = _two_accounts()
    assert desk_account_refs.require_oauth_arg(first) is None
    _sign_in(None)                                         # the legacy `oauth.x`
    out = client.get('/api/desk/connect/status', query_string={'account_id': first}).get_json()
    assert out['x']['state'] == 'connected'


def test_unknown_account_is_a_404_and_makes_no_vault_entry(api):
    client, _ = api
    before = sorted(s['name'] for s in secrets_store.list_secrets())
    r = client.get('/api/desk/connect/status', query_string={'account_id': 'acct-nope'})
    assert r.status_code == 404 and r.get_json()['code'] == 'unknown_account'
    assert sorted(s['name'] for s in secrets_store.list_secrets()) == before


def test_account_less_overview_is_unchanged(api):
    client, _ = api
    out = client.get('/api/desk/connect/status').get_json()
    assert set(out) >= {'higgsfield', 'x', 'openai', 'gemini'}
    assert out['x']['state'] == 'not_connected'


def test_account_scoped_status_is_read_only(api, monkeypatch):
    client, _ = api
    _, second = _two_accounts()
    _sign_in(desk_account_refs.require_oauth_arg(second))
    audit = secrets_store.audit_path()
    size = audit.stat().st_size if audit.exists() else 0
    for name in ('set_secret', 'delete_secret', 'get_secret_value'):
        monkeypatch.setattr(secrets_store, name, lambda *a, _n=name, **k: pytest.fail(f'status called {_n}'))
    assert client.get('/api/desk/connect/status', query_string={'account_id': second}).get_json()['x']['state'] == 'connected'
    assert (audit.stat().st_size if audit.exists() else 0) == size
