"""Desk connect-by-URL, slice 2 / M2: one X sign-in per Desk account
(docs/DESK_CONNECT_BY_URL_SPEC.md "Multiple accounts", Dave 2026-10-03).

Pinned:

  * the FIRST direct X account keeps the legacy singleton names (`oauth.x`,
    profile `desk-x`), every later one gets names of its own, derived from its id;
  * binding is deterministic (oldest first) and idempotent, it runs when a store
    written before this existed is read, and deleting the account that holds the
    legacy names does NOT hand them to the next one (that would inherit someone
    else's sign-in);
  * an account with names of its own never reads the singleton's token or the
    hand-pasted `x.oauth-token`, and is "not signed in" until its own sign-in is
    made, even while the singleton is connected;
  * a sign-in made for an account lands in that account's vault entry only, in
    that account's browser profile;
  * Higgsfield (one engine sign-in for the workspace) takes no account;
  * the Desk account id reaches desk_publish, so a post goes out under the
    account it was written for.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import desk as _desk  # noqa: E402
from mc import desk_account_refs as refs  # noqa: E402
from mc import desk_accounts as accounts  # noqa: E402
from mc import desk_oauth as oauth  # noqa: E402
from mc import desk_publish  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402
# the fake vault, fake provider and loopback helpers the sign-in tests already use
from tests.test_desk_oauth import (ACCESS_1, REFRESH_1, _clean_flows, _hit, _state_of, _x_app,  # noqa: E402,F401
                                   provider, vault, x_port)

LEGACY = {'oauth_vault': 'oauth.x', 'oauth_profile': 'desk-x'}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    return tmp_path / 'desk.json'


def _x(i, created, **over):
    rec = {'id': i, 'platform': 'x', 'identity': f'@{i}', 'label': i, 'capability': 'direct',
           'voice': '', 'created_at': created}
    rec.update(over)
    return rec


# -- binding -----------------------------------------------------------------------

def test_bind_gives_the_oldest_x_account_the_legacy_names():
    data = {'accounts': {'b': _x('b', '2026-02-01'), 'a': _x('a', '2026-01-01'), 'c': _x('c', '2026-03-01')}}
    refs.bind(data)
    assert data['accounts']['a']['credentials'] == LEGACY
    assert data['accounts']['b']['credentials'] == {'oauth_vault': 'oauth.x.b', 'oauth_profile': 'desk-x-b'}
    assert data['accounts']['c']['credentials'] == {'oauth_vault': 'oauth.x.c', 'oauth_profile': 'desk-x-c'}
    assert data[refs.LEGACY_FLAG] == 'a'            # the flag names the account that holds the legacy names


def test_bind_is_idempotent_and_never_rewrites_a_bound_account():
    data = {'accounts': {'a': _x('a', '2026-01-01'), 'b': _x('b', '2026-02-01')}}
    refs.bind(data)
    once = json.loads(json.dumps(data))
    refs.bind(data)
    assert data == once


def test_only_a_direct_non_preview_x_account_has_a_sign_in():
    data = {'accounts': {'m': _x('m', '2026-01-01', capability='manual'),
                         'p': _x('p', '2026-01-02', preview=True),
                         'l': {**_x('l', '2026-01-03'), 'platform': 'linkedin'},
                         'b': {**_x('b', '2026-01-04', capability='manual'), 'platform': 'blog'}}}
    refs.bind(data)
    assert not any('credentials' in a for a in data['accounts'].values())
    assert refs.LEGACY_FLAG not in data


def test_a_store_written_before_this_is_bound_when_read(store):
    store.write_text(json.dumps({'version': 2, 'accounts': {
        'new': _x('new', '2026-05-01'), 'old': _x('old', '2026-01-01')}}), encoding='utf-8')
    got = _desk._read_store()['accounts']
    assert got['old']['credentials'] == LEGACY and got['new']['credentials']['oauth_vault'] == 'oauth.x.new'
    assert _desk._read_store()['accounts'] == got                  # a second read agrees


def test_create_account_binds_in_order(store, vault):
    a = accounts.create_account('x', '@first')
    b = accounts.create_account('x', '@second')
    m = accounts.create_account('x', '@manual', capability='manual')
    assert a['credentials'] == LEGACY
    assert b['credentials'] == {'oauth_vault': f"oauth.x.{b['id'].lower()}", 'oauth_profile': f"desk-x-{b['id'].lower()}"}
    assert 'credentials' not in m


def test_deleting_the_legacy_holder_does_not_hand_its_sign_in_to_the_next_account(store, vault):
    a = accounts.create_account('x', '@first')
    assert accounts.delete_account(a['id']) is True
    b = accounts.create_account('x', '@second')
    assert b['credentials']['oauth_vault'] != 'oauth.x'
    assert refs.oauth_arg_for(b['id']) == b['id']


def test_oauth_arg(store, vault):
    a = accounts.create_account('x', '@first')
    b = accounts.create_account('x', '@second')
    assert refs.oauth_arg_for(a['id']) is None and refs.oauth_arg_for(b['id']) == b['id']
    assert refs.oauth_arg_for(None) is None and refs.oauth_arg_for('nope') is None
    with pytest.raises(LookupError):
        refs.require_oauth_arg('nope')
    blog = accounts.create_account('blog', 'my blog')
    with pytest.raises(LookupError):
        refs.require_oauth_arg(blog['id'])


# -- names -------------------------------------------------------------------------

def test_names_derive_from_the_account_id():
    assert oauth.vault_name('x') == 'oauth.x' and oauth.profile_name('x') == 'desk-x'
    assert oauth.vault_name('x', 'acct-7') == 'oauth.x.acct-7' and oauth.profile_name('x', 'acct-7') == 'desk-x-acct-7'
    from mc import secrets_store
    assert secrets_store.valid_name(oauth.vault_name('x', 'Acct_7-b')) if hasattr(secrets_store, 'valid_name') else True


@pytest.mark.parametrize('bad', ['', 'a b', 'a/b', '../x', 'a' * 81, 'é'])
def test_a_bad_account_id_is_refused(bad):
    with pytest.raises(oauth.OAuthError) as e:
        oauth.vault_name('x', bad)
    assert e.value.code == 'bad_account'


def test_higgsfield_takes_no_account():
    with pytest.raises(oauth.OAuthError) as e:
        oauth.vault_name('higgsfield', 'acct-1')
    assert e.value.code == 'not_per_account'


# -- sign-ins stay apart -------------------------------------------------------------

def _sign_in(provider, vault, account_id, access, refresh):
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': access, 'refresh_token': refresh,
                                                          'expires_in': 7200}))
    out = oauth.start('x', account_id)
    _hit(out['redirect_uri'], code='c', state=_state_of(out['auth_url'])['state'])
    return out


def test_a_sign_in_for_an_account_lands_in_its_own_entry_and_profile(provider, vault, x_port):
    _x_app(vault)
    out = _sign_in(provider, vault, 'acct-2', ACCESS_1, REFRESH_1)
    assert out['profile'] == 'desk-x-acct-2'
    assert 'oauth.x.acct-2' in vault['entries'] and 'oauth.x' not in vault['entries']
    assert oauth.status('x', 'acct-2')['state'] == 'connected'
    assert oauth.status('x')['state'] == 'not_connected'
    assert oauth.x_token(consumer='t', account_id='acct-2') == ACCESS_1


def test_two_accounts_hold_two_tokens_and_disconnect_removes_only_its_own(provider, vault, x_port):
    _x_app(vault)
    _sign_in(provider, vault, None, 'tok-legacy-AAAAAAAAAAAAAAAAAAAA', REFRESH_1)
    _sign_in(provider, vault, 'acct-2', 'tok-second-BBBBBBBBBBBBBBBBBBBB', 'ref-second-CCCCCCCCCCCCCCCC')
    assert oauth.x_token(consumer='t') == 'tok-legacy-AAAAAAAAAAAAAAAAAAAA'
    assert oauth.x_token(consumer='t', account_id='acct-2') == 'tok-second-BBBBBBBBBBBBBBBBBBBB'
    provider.on('POST', 'api.x.com/2/oauth2/revoke', (200, {}))
    provider.on('POST', 'api.x.com/2/oauth2/revoke', (200, {}))
    assert oauth.disconnect('x', 'acct-2')['deleted'] is True
    assert 'oauth.x' in vault['entries'] and 'oauth.x.acct-2' not in vault['entries']


def test_an_account_with_its_own_name_never_falls_back_to_the_singleton(provider, vault, x_port):
    _x_app(vault)
    _sign_in(provider, vault, None, ACCESS_1, REFRESH_1)           # the singleton is connected
    vault['entries']['x.oauth-token'] = {'value': 'pasted-token-DDDDDDDDDDDDDDDD', 'hint': '', 'scope': 'global',
                                         'allow_unattended': True, 'entry_type': 'token'}
    with pytest.raises(oauth.OAuthError) as e:
        oauth.x_token(consumer='t', account_id='acct-2')
    assert e.value.code == 'not_connected'


# -- publish state -------------------------------------------------------------------

def test_the_second_account_is_not_signed_in_while_the_first_is(store, provider, vault, x_port):
    _x_app(vault)
    first = accounts.create_account('x', '@first')
    second = accounts.create_account('x', '@second')
    _sign_in(provider, vault, None, ACCESS_1, REFRESH_1)
    by_id = {a['id']: a for a in accounts.list_accounts()}
    assert by_id[first['id']]['publish']['ready'] is True
    assert by_id[first['id']]['publish']['secret'] == 'oauth.x'
    p = by_id[second['id']]['publish']
    assert p['ready'] is False and 'not signed in' in p['reason'] and p['secret'] is None
    _sign_in(provider, vault, second['id'], 'tok-second-BBBBBBBBBBBBBBBBBBBB', 'ref-second-CCCCCCCCCCCCCCCC')
    by_id = {a['id']: a for a in accounts.list_accounts()}
    assert by_id[second['id']]['publish']['ready'] is True
    assert by_id[second['id']]['publish']['secret'] == oauth.vault_name('x', second['id'])


def test_an_account_view_names_its_credentials_but_holds_no_value(store, vault):
    a = accounts.create_account('x', '@first')
    assert accounts.get_account(a['id'])['credentials'] == LEGACY


# -- the post goes out under the account it was written for -----------------------------

def test_publish_and_verify_use_the_accounts_own_sign_in(store, tmp_path, monkeypatch, vault):
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', tmp_path / 'receipts.json')
    first = accounts.create_account('x', '@first')
    second = accounts.create_account('x', '@second')
    seen = []

    def fake_token(*, consumer, project_id=None, unattended=False, account_id=None):
        seen.append(account_id)
        return 'tok'
    monkeypatch.setattr(desk_publish._oauth, 'x_token', fake_token)
    monkeypatch.setattr(desk_publish, '_send_x', lambda token, body, reply, account_key='': ('1', 'https://x.com/a/status/1'))
    monkeypatch.setattr(desk_publish, '_get_tweet', lambda token, pid: {'data': {'id': pid}})
    desk_publish.publish({'id': 'v1', 'platform': 'x', 'body': 'hi', 'account_id': second['id']})
    desk_publish.publish({'id': 'v2', 'platform': 'x', 'body': 'hi', 'account_id': first['id']})
    desk_publish.publish({'id': 'v3', 'platform': 'x', 'body': 'hi'})
    assert desk_publish.verify_post('x', '1', account_id=second['id']) is True
    assert seen == [second['id'], None, None, second['id']]


# -- routes -----------------------------------------------------------------------------

def test_the_start_route_refuses_an_account_that_is_not_there(store, provider, vault, x_port, monkeypatch):
    _x_app(vault)
    monkeypatch.setattr(desk_routes, '_human_only', lambda *a, **k: None)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_routes.bp)
    c = app.test_client()
    r = c.post('/api/desk/connect/x/start', json={'account_id': 'ghost'})
    assert r.status_code == 404 and r.get_json()['code'] == 'unknown_account'
    assert 'oauth.x.ghost' not in vault['entries'] and oauth._flows == {}
    a = accounts.create_account('x', '@first')
    b = accounts.create_account('x', '@second')
    r = c.post('/api/desk/connect/x/start', json={'account_id': b['id']})
    assert r.status_code == 201 and r.get_json()['profile'] == oauth.profile_name('x', b['id'])
    r = c.post('/api/desk/connect/x/start', json={'account_id': a['id']})     # the first account: legacy profile
    assert r.status_code == 201 and r.get_json()['profile'] == 'desk-x'
    r = c.post('/api/desk/connect/x/start', json={})                           # no account: the singleton, as before
    assert r.status_code == 201 and r.get_json()['profile'] == 'desk-x'


# -- review 2026-10-03 (Dave): three faults found in the first cut ----------------------

def test_bind_never_raises_for_an_id_the_desk_can_hold(store):
    """x-main then x.second broke every Desk read: the ids the Desk accepts (a '.',
    up to 80 characters) must all get a name, and a name nobody can make must be skipped."""
    ids = ['x-main', 'x.second', 'a' * 80, 'b' * 41]
    data = {'accounts': {i: _x(i, f'2026-01-0{n + 1}') for n, i in enumerate(ids)}}
    refs.bind(data)
    from mc import secrets_store
    for i in ids:
        cred = data['accounts'][i]['credentials']
        assert secrets_store.valid_name(cred['oauth_vault']) and len(cred['oauth_profile']) <= 64
    # and it survives a store read, which is where it raised
    store.write_text(json.dumps({'version': 2, 'accounts': {i: _x(i, f'2026-01-0{n + 1}') for n, i in enumerate(ids)}}), encoding='utf-8')
    assert set(_desk._read_store()['accounts']) == set(ids)


def test_bind_skips_and_logs_an_id_it_cannot_name(monkeypatch):
    logged = []
    monkeypatch.setattr(refs, '_log', lambda m, **k: logged.append(m))
    data = {'accounts': {'ok': _x('ok', '2026-01-01'), 'bad id': _x('bad id', '2026-02-01'), 'next': _x('next', '2026-03-01')}}
    refs.bind(data)                              # does not raise
    assert 'credentials' not in data['accounts']['bad id'] and logged
    assert data['accounts']['ok']['credentials'] == LEGACY
    assert data['accounts']['next']['credentials']['oauth_vault'] == 'oauth.x.next'    # one bad id does not stop the rest


def test_two_account_ids_never_share_a_vault_or_profile_name():
    ids = ['Acct-B', 'acct-b', 'ACCT-B', 'x.second', 'x-second', 'x_second', 'a' * 41, 'a' * 40, 'A' * 40]
    vaults = [oauth.vault_name('x', i) for i in ids]
    profiles = [oauth.profile_name('x', i) for i in ids]
    assert len(set(vaults)) == len(ids) and len(set(profiles)) == len(ids)
    from mc import secrets_store
    assert all(secrets_store.valid_name(v) for v in vaults)
    assert oauth.vault_name('x', 'acct-b') == 'oauth.x.acct-b'    # a plain lowercase id keeps its readable name


def test_restoring_the_legacy_holder_with_undo_gives_it_the_legacy_names_back(store, vault):
    a = accounts.create_account('x', '@first', account_id='first')
    assert a['credentials'] == LEGACY
    assert accounts.delete_account('first') is True
    b = accounts.create_account('x', '@second', account_id='second')
    assert b['credentials']['oauth_vault'] == 'oauth.x.second'     # a stranger does not inherit it
    again = accounts.create_account('x', '@first', account_id='first')      # Undo re-posts the same id
    assert again['credentials'] == LEGACY
    assert refs.oauth_arg_for('first') is None and refs.oauth_arg_for('second') == 'second'
    assert _desk._read_store()['accounts']['second']['credentials']['oauth_vault'] == 'oauth.x.second'


def test_an_old_true_flag_is_resolved_to_the_account_that_holds_the_legacy_names():
    data = {refs.LEGACY_FLAG: True, 'accounts': {'a': _x('a', '2026-01-01', credentials=dict(LEGACY)), 'b': _x('b', '2026-02-01')}}
    refs.bind(data)
    assert data[refs.LEGACY_FLAG] == 'a' and data['accounts']['b']['credentials']['oauth_vault'] == 'oauth.x.b'
