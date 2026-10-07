from mc import desk_browser_profiles as profiles
from mc import state, desk, desk_accounts, desk_oauth


def test_first_account_per_site_shares_default_second_same_site_separates(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', '')
    assert profiles.select('x', {}, account_id='one') == 'main'
    existing = {'one': {'id': 'one', 'platform': 'x', 'browser_profile': 'main'}}
    assert profiles.select('linkedin', existing, identity='Ron') == 'main'
    assert profiles.select('x', existing, identity='Second') == 'x-second'
    assert profiles.select('x', existing, account_id='one') == 'main'


def test_saved_bindings_and_config_default_are_preserved(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'shared')
    assert profiles.select('youtube', {}) == 'shared'
    for field in ({'browser_profile': 'old'}, {'credentials': {'oauth_profile': 'old'}},
                  {'browser_setup': {'refs': {'browser_profile': 'old'}}}):
        accounts = {'a': {'id': 'a', 'platform': 'x', **field}}
        assert profiles.select('x', accounts, account_id='a') == 'old'


def test_native_dotted_profile_default_is_accepted_by_connect(monkeypatch):
    from mc.desk_connect import browser_setup
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'shared.main')
    assert profiles.shared_default() == 'shared.main'
    assert browser_setup._clean_profile('shared.main', 'x', 'ron') == 'shared.main'


def test_oauth_keeps_its_saved_binding_when_a_browser_route_uses_another_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    with desk._store_lock:
        data = desk._read_store()
        data['accounts']['old'] = {'id': 'old', 'platform': 'x', 'capability': 'direct',
            'browser_profile': 'read-profile', 'browser_setup': {'refs': {'browser_profile': 'browser-login'}},
            'credentials': {'oauth_vault': 'oauth.x.old', 'oauth_profile': 'desk-x-old'}}
        desk._write_store(data)
    assert desk_oauth.profile_name('x', 'old') == 'desk-x-old'


def test_creation_binds_shared_profile_and_oauth_uses_the_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'main')
    monkeypatch.setattr(desk_oauth._profile, 'existed', lambda name: False)
    a = desk_accounts.create_account('x', '@first', account_id='first')
    b = desk_accounts.create_account('x', '@second', account_id='second')
    c = desk_accounts.create_account('linkedin', 'Ron', account_id='third')
    assert a['browser_profile'] == c['browser_profile'] == 'main'
    assert b['browser_profile'] == 'desk-x-second'
    assert desk_oauth.profile_name('x') == 'main'
    assert desk_oauth.profile_name('x', 'second') == 'desk-x-second'


def test_held_second_login_uses_same_profile_before_and_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'main')
    monkeypatch.setattr(desk_oauth._profile, 'existed', lambda name: False)
    desk_accounts.create_account('x', '@first', account_id='first')
    before = desk_oauth.profile_name('x', 'reserved-id')
    saved = desk_accounts.create_account('x', '@other_handle', account_id='reserved-id')
    assert before == saved['browser_profile'] == saved['credentials']['oauth_profile']


def test_engagement_uses_hidden_reader_when_profile_is_open(monkeypatch):
    from mc import desk_engagement
    from mc.blueprints import browser_routes
    from mc.browser_hidden_read import HiddenTabReader
    monkeypatch.setattr(browser_routes, '_session_using_profile', lambda name: {'status': 'running'})
    assert isinstance(desk_engagement._default_page_reader('p', 'main'), HiddenTabReader)


def test_legacy_migration_does_not_rebind_oauth_to_the_read_profile():
    from mc import desk_account_refs
    data = {'accounts': {'old': {'id': 'old', 'platform': 'x', 'capability': 'direct',
                                 'browser_profile': 'read-profile'}}}
    desk_account_refs.bind(data)
    assert data['accounts']['old']['credentials']['oauth_profile'] == 'desk-x'
    assert data['accounts']['old']['browser_profile'] == 'read-profile'


def test_oauth_binding_survives_config_and_read_changes_and_detects_corrupt_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'shared.main')
    desk_accounts.create_account('x', '@first', account_id='first')
    with desk._store_lock:
        data = desk._read_store()
        account = data['accounts']['first']
        assert account['oauth_profile_binding'] == 'shared.main'
        account['browser_profile'] = 'read-only'
        account['credentials']['oauth_profile'] = 'someone-elses-profile'
        desk._write_store(data)
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'new-default')
    assert desk_oauth.profile_name('x') == 'shared.main'
