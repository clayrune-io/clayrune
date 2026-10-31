"""Instagram / YouTube / TikTok connected from the Connect wizard become ACCOUNTS (2026-10-16).

Before: the generic browser-profile setup saved them as a `services` record, which Where's tray and
the assisted-post button (accounts only) never see.
"""
import pytest

from mc import desk, desk_accounts, desk_services
from mc.desk_connect import browser_connections as c, type_view
from mc.desk_connect.browser_setup import SetupError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    c._done.clear()
    return tmp_path


def _draft(name, url, profile='main'):
    return c.clean_draft({'name': name, 'url': url, 'browser_profile': profile})


@pytest.mark.parametrize('url,platform,capability', [
    ('https://www.instagram.com', 'instagram', 'assisted'),
    ('https://youtube.com', 'youtube', 'assisted'),
    ('https://www.tiktok.com', 'tiktok', 'none')])
def test_pane_site_becomes_an_account_not_a_service(store, url, platform, capability):
    result, duplicate = c.commit('request-1234', _draft('@clayrune', url, 'ig-login'))
    assert not duplicate and not result['unchanged'] and result['kind'] == 'account'
    accounts = desk_accounts.list_accounts()
    assert [(a['platform'], a['identity'], a['capability'], a['browser_profile']) for a in accounts] == [(platform, 'clayrune', capability, 'ig-login')]
    assert result['account_id'] == accounts[0]['id'] and result['browser_profile'] == 'ig-login'
    assert not desk_services.list_services()
    # grants nothing beyond the platform's own default: no read setting is chosen, nothing is posted
    assert 'read_via' not in accounts[0] and not result['read'] and not result['post'] and not result['executes']


def test_instagram_account_reaches_the_tray_and_is_connected(store):
    c.commit('request-1234', _draft('clayrune', 'https://instagram.com'))
    (acc,) = desk_accounts.list_accounts()
    assert acc['connected'] is True and acc['capability'] == 'assisted'      # Where lists it as a placeable source


def test_replay_and_repeat_do_not_make_a_second_account(store):
    clean = _draft('clayrune', 'https://www.instagram.com')
    result, _ = c.commit('request-1234', clean)
    assert c.commit('request-1234', clean)[1] is True
    again, duplicate = c.commit('request-5678', clean)
    assert not duplicate and again['unchanged'] is True and again['account_id'] == result['account_id']
    assert len(desk_accounts.list_accounts()) == 1


def test_same_handle_on_a_different_profile_is_not_replaced(store):
    c.commit('request-1234', _draft('clayrune', 'https://www.instagram.com', 'main'))
    with pytest.raises(SetupError) as e:
        c.commit('request-5678', _draft('clayrune', 'https://www.instagram.com', 'other'))
    assert e.value.status == 409
    assert desk_accounts.list_accounts()[0]['browser_profile'] == 'main'


def test_a_handle_is_required(store):
    with pytest.raises(SetupError):
        c.commit('request-1234', _draft('@', 'https://www.instagram.com'))
    assert not desk_accounts.list_accounts()


def test_types_say_which_services_make_an_account():
    for sid, want in (('instagram', True), ('youtube', True), ('tiktok', True), ('reddit', False), ('x', False)):
        assert type_view.project_service(sid)['service']['makes_account'] is want
    assert type_view.project(None)['service']['makes_account'] is False
