"""MC-1187 / DCW-C2: every path that creates a Desk account starts it denied.

Five paths make an account: the X developer-app connection, the browser sign-in
setup, the purpose-bindings Save, the pane wizard (Instagram, YouTube, TikTok) and
the saved-site attach. Two used to initialize the Read/Post policy; three left the
account with no policy, which `permission_check` and `post_consent` read as "no
consent needed". The initialization now lives in `desk_accounts.create_account`, so a
sixth path cannot miss it. Existing accounts with no policy (legacy) are not touched.
"""
from __future__ import annotations

import pytest

from mc import desk, desk_accounts
from mc.desk_connect import browser_connections as pane, permission_policy, saved_site_account as sites

from tests.test_desk_connect_browser_setup import env as _bs_env, _save as _bs_save, _draft as _bs_draft  # noqa: F401
# fixtures for the paths that need a real Flask client / vault (not collected as tests here)
from tests.test_desk_connect import env as _connect_env          # noqa: F401
from tests.test_desk_connect_providers import net, _post, _x_draft  # noqa: F401
from tests.test_desk_purposes import env as _purposes_env, pane as _purposes_pane, _draft as _bind_draft, _b, _save as _bind_save  # noqa: F401


def _rec(account_id):
    with desk._store_lock:
        return dict(desk._read_store()['accounts'][account_id])


def _assert_denied(rec, kind):
    state = permission_policy.read_policy(rec, account_id=rec['id'], account_kind=kind)
    assert state['state'] == 'explicit', state
    assert state['read'] is False and state['post'] is False and state['scopes'] == []


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    pane._done.clear()
    return tmp_path


# -- the five paths -----------------------------------------------------------

def test_x_developer_app_account_starts_denied(_connect_env, net):
    client = _connect_env[0]
    r = _post(client, _x_draft('@first'))
    assert r.status_code == 201, r.get_json()
    _assert_denied(_rec(r.get_json()['account_id']), 'account')


def test_purpose_bindings_new_account_starts_denied(_purposes_env, _purposes_pane):
    client = _purposes_env[0]
    d = _bind_draft('linkedin', {'new': {'identity': 'Ron Levy'}}, 'member',
                    [_b('read_own', 'linkedin-browser', ['own_posts'], browser_profile='li-ron')])
    r = _bind_save(client, d)
    assert r.status_code == 201, r.get_json()
    _assert_denied(_rec(r.get_json()['account_id']), 'member')


@pytest.mark.parametrize('url,platform', [('https://www.instagram.com', 'instagram'),
                                          ('https://youtube.com', 'youtube'),
                                          ('https://www.tiktok.com', 'tiktok')])
def test_pane_wizard_account_starts_denied(store, url, platform):
    result, _ = pane.commit('request-1234', pane.clean_draft({'name': '@clayrune', 'url': url,
                                                             'browser_profile': 'main'}))
    _assert_denied(_rec(result['account_id']), 'account')


@pytest.mark.parametrize('service,kind,identity', [('x', 'account', 'ron'), ('linkedin', 'member', 'Ron Levy'),
                                                   ('linkedin', 'organization', 'Clayrune')])
def test_browser_setup_new_account_starts_denied(_bs_env, service, kind, identity):
    r = _bs_save(_bs_env[0], _bs_draft(service, {'new': {'identity': identity}}, kind))
    assert r.status_code == 201, r.get_json()
    _assert_denied(_rec(r.get_json()['account_id']), kind)


def test_saved_site_attach_new_account_starts_denied(store):
    with desk._store_lock:
        s = desk._read_store()
        s['services'] = {'old': {'id': 'old', 'name': 'www.instagram.com', 'link': 'https://www.instagram.com',
                                 'credential': '', 'created_at': 'then',
                                 'browser_connection': {'service': None, 'browser_profile': 'p', 'read': False,
                                                        'post': False, 'signin_method': 'other'}}}
        desk._write_store(s)
    out = sites.attach('old', {'identity': '@example'})
    _assert_denied(_rec(out['account_id']), 'account')


# -- the seam itself, and what it must not touch ------------------------------

@pytest.mark.parametrize('platform,kind', [('x', 'account'), ('instagram', 'account'),
                                           ('youtube', 'account'), ('tiktok', 'account'),
                                           ('linkedin', 'member'), ('linkedin', 'organization')])
def test_create_account_initializes_default_deny(store, platform, kind):
    acc = desk_accounts.create_account(platform, 'someone', account_kind=kind)
    _assert_denied(_rec(acc['id']), kind)


def test_a_platform_with_no_permission_profile_is_left_alone(store):
    acc = desk_accounts.create_account('blog', 'my-blog')
    assert 'permission_policy' not in _rec(acc['id'])


def test_create_account_does_not_overwrite_an_existing_policy(store):
    acc = desk_accounts.create_account('x', 'ron')
    with desk._store_lock:
        s = desk._read_store()
        before = dict(s['accounts'][acc['id']]['permission_policy'])
    with pytest.raises(desk_accounts.AccountError):
        desk_accounts.create_account('x', 'ron')          # re-saving an existing identity is refused
    assert _rec(acc['id'])['permission_policy'] == before


def test_a_legacy_account_with_no_policy_still_reads_as_legacy(store):
    with desk._store_lock:
        s = desk._read_store()
        s['accounts']['old'] = {'id': 'old', 'platform': 'x', 'identity': 'old', 'label': 'X @old',
                                'capability': 'direct', 'created_at': 'then'}
        desk._write_store(s)
    state = permission_policy.read_policy(_rec('old'), account_id='old', account_kind='account')
    assert state['state'] == 'legacy' and state['read'] is None and state['post'] is None
    assert 'permission_policy' not in _rec('old')
    desk_accounts.create_account('x', 'another')              # creating a new account leaves it untouched
    assert 'permission_policy' not in _rec('old')
