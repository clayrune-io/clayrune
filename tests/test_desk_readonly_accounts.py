"""Read-only pane accounts and the end of the LinkedIn API read option (44712cf4 follow-up).

Pinned:

  * a YouTube / Instagram / TikTok Desk account can be created (the sites in
    `desk_pane_pages.PANE_ONLY_PLATFORMS`), always with capability `none`: a `direct`
    one is refused, `none` on any other site is refused, an unknown site still 400s;
  * `publish_state` refuses such an account whatever the vault holds, so approval and
    the tick (both go through it) refuse it with the same reason;
  * a read-only account lifted from a presence copy is `none`, not `manual` (which
    publish_state would call ready);
  * `read_via: 'api'` is X only, through the account PATCH, the legacy presence read
    route and the store function; a LinkedIn account already stored as `api` is NOT
    rewritten, still reads as `api`, and can be moved to the pane by the user;
  * the legacy presence read route takes a YouTube account's profile.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402
from mc.desk_pane_pages import PANE_ONLY_PLATFORMS  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
READ_ONLY = ('youtube', 'instagram', 'tiktok')


@pytest.fixture
def env(tmp_path, monkeypatch):
    # a vault that HAS every token: the refusal must not depend on a missing one
    monkeypatch.setattr(_accounts.secrets_store, 'list_secrets',
                        lambda *a, **k: [{'name': n, 'allow_unattended': True} for n in
                                         ('x.oauth-token', 'linkedin.oauth-token', 'youtube.oauth-token')])
    monkeypatch.setattr(_accounts.secrets_store, 'is_readable', lambda name: True)
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: False)
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def _create(client, **body):
    return client.post('/api/desk/accounts', json=body)


def _by_id(client):
    return {a['id']: a for a in client.get('/api/desk/accounts').get_json()}


def test_the_read_only_sites_are_the_defaults_table_minus_x_and_linkedin():
    assert tuple(sorted(PANE_ONLY_PLATFORMS)) == tuple(sorted(READ_ONLY))
    assert set(READ_ONLY) <= set(_accounts.ACCOUNT_PLATFORMS)


@pytest.mark.parametrize('plat', READ_ONLY)
def test_create_a_read_only_account(env, plat):
    r = _create(env, platform=plat, identity='clayrune', id=f'ch-{plat}')
    assert r.status_code == 201, r.get_json()
    acc = r.get_json()
    assert acc['capability'] == 'none' and acc['platform'] == plat
    assert acc['label'].endswith(' · clayrune') and acc['label'] != ' · clayrune'
    assert acc['publish']['ready'] is False and 'read-only' in acc['publish']['reason']
    assert _by_id(env)[f'ch-{plat}']['publish']['ready'] is False


@pytest.mark.parametrize('plat', READ_ONLY)
def test_a_read_only_site_never_takes_a_publishing_capability(env, plat):
    for cap in ('direct', 'manual'):
        r = _create(env, platform=plat, identity='clayrune', capability=cap)
        assert r.status_code == 400 and 'read-only' in r.get_json()['error'], (plat, cap)
    assert _create(env, platform=plat, identity='clayrune', capability='none').status_code == 201
    assert _create(env, platform=plat, identity='CLAYRUNE').status_code == 409


def test_capability_none_is_for_read_only_sites_only(env):
    for plat in ('x', 'linkedin', 'blog'):
        r = _create(env, platform=plat, identity='a', capability='none')
        assert r.status_code == 400, plat
    assert _create(env, platform='forum', identity='a').status_code == 400
    assert _create(env, platform='discord', identity='a').status_code == 400


@pytest.mark.parametrize('plat', READ_ONLY)
def test_publish_state_refuses_a_read_only_account_whatever_the_vault_holds(env, plat):
    pub = _accounts.publish_state({'platform': plat, 'capability': 'none', 'identity': 'a'})
    assert pub['ready'] is False and 'read-only' in pub['reason']
    assert _accounts.publish_state({'platform': plat, 'capability': 'none', 'credentials': {}})['ready'] is False


def test_a_lifted_read_only_presence_account_is_none_not_manual(env):
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-yt', 'platform': 'youtube', 'identity': 'UC1'},
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'A'}]})
    got = _by_id(env)
    assert got['ch-yt']['capability'] == 'none' and got['ch-yt']['publish']['ready'] is False
    assert got['ch-li']['capability'] == 'manual'          # unchanged for the sites that publish


def test_the_legacy_presence_read_route_takes_a_read_only_account(env):
    r = env.patch('/api/desk/presence/alpha/accounts/ch-yt/read',
                  json={'platform': 'youtube', 'read_via': 'pane', 'browser_profile': 'main'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['browser_profile'] == 'main'
    ws = _by_id(env)['ch-yt']
    assert ws['capability'] == 'none' and ws['platform'] == 'youtube' and ws['browser_profile'] == 'main'
    bad = env.patch('/api/desk/presence/alpha/accounts/ch-yt/read', json={'read_via': 'api'})
    assert bad.status_code == 400 and 'only X' in bad.get_json()['error']


def test_an_unknown_site_is_still_refused_on_the_legacy_route(env):
    r = env.patch('/api/desk/presence/alpha/accounts/ch-f/read', json={'platform': 'forum', 'read_via': 'pane'})
    assert r.status_code == 400


# -- LinkedIn has no API read ---------------------------------------------------

def test_linkedin_cannot_be_set_to_an_api_read(env):
    _create(env, platform='linkedin', identity='Clayrune', id='ch-li')
    _create(env, platform='x', identity='@ron', id='ch-x')
    r = env.patch('/api/desk/accounts/ch-li', json={'read_via': 'api'})
    assert r.status_code == 400 and 'only X' in r.get_json()['error']
    assert 'read_via' not in _by_id(env)['ch-li']
    r = env.patch('/api/desk/presence/alpha/accounts/ch-li/read', json={'platform': 'linkedin', 'read_via': 'api'})
    assert r.status_code == 400 and 'only X' in r.get_json()['error']
    assert env.patch('/api/desk/accounts/ch-x', json={'read_via': 'api'}).status_code == 200     # X keeps its choice
    assert _by_id(env)['ch-x']['read_via'] == 'api'


def test_the_store_function_applies_the_same_rule_to_a_stored_account(env):
    _create(env, platform='linkedin', identity='Clayrune', id='ch-li')
    _desk.set_account_read_settings('alpha', 'ch-li', platform='linkedin', read_via='pane')
    with pytest.raises(ValueError, match='only X'):
        _desk.set_account_read_settings('alpha', 'ch-li', read_via='api')          # platform read from the store
    with pytest.raises(ValueError, match='only X'):
        _desk.set_account_read_settings('alpha', 'ch-new', platform='youtube', read_via='api')


def test_a_stored_linkedin_api_setting_is_not_flipped_and_the_user_can_choose(env):
    _create(env, platform='linkedin', identity='Clayrune', id='ch-li')
    with _desk._store_lock:                      # the record as saved before the option was removed
        store = _desk._read_store()
        store['accounts']['ch-li']['read_via'] = 'api'
        _desk._write_store(store)
    assert _by_id(env)['ch-li']['read_via'] == 'api'                    # shown as stored: not moved quietly
    assert _desk.account_read_via({'read_via': 'api'}) == 'api'
    # an unrelated edit leaves it as it was
    assert env.patch('/api/desk/accounts/ch-li', json={'label': 'Clay page'}).status_code == 200
    assert _by_id(env)['ch-li']['read_via'] == 'api'
    # the user's own choice moves it
    assert env.patch('/api/desk/accounts/ch-li', json={'read_via': 'pane'}).status_code == 200
    assert _by_id(env)['ch-li']['read_via'] == 'pane'
