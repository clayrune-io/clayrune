"""MC-1113: saved sources remain live, policy-bound, and absent from responses/logs."""
import base64
import json
import time
from types import SimpleNamespace

import pytest

from tests.test_secrets_store import vault  # noqa: F401
from tests.test_desk_connect import env, PASSCODE  # noqa: F401
from mc import secret_field_refs as refs, secrets_store as store, desk_oauth as oauth
from mc.desk_connect.providers.x import XProvider
from mc.desk_connect.undo import UndoStack

VALUE = 'field-value-SENTINEL-8a9bc672'
USER = 'field-user-SENTINEL-6e726d6d'
CID = 'app-id-SENTINEL-73ae9856'


def ref(name='app', part='value'):
    return {'name': name, 'part': part}


@pytest.mark.parametrize('part,expected', [('value', VALUE), ('username', USER)])
def test_resolve_parts_and_never_copy(vault, part, expected):
    vault.set_secret('app', VALUE, username=USER)
    refs.create('canonical', ref(part=part))
    assert vault.get_secret_value('canonical', consumer='test') == expected
    record = vault._load_store()['secrets']['canonical']
    assert record['field_refs'] == {'value': ref(part=part)}
    assert 'ciphertext' not in record
    assert expected not in json.dumps(record)
    assert VALUE not in json.dumps(vault.list_secrets())


def test_rotation_applies_at_next_use_and_source_policy_is_not_copied(vault):
    vault.set_secret('app', VALUE)
    refs.create('canonical', ref())
    vault.set_secret('app', VALUE + '-rotated', allow_unattended=False)
    assert vault.get_secret_value('canonical', consumer='test') == VALUE + '-rotated'
    with pytest.raises(refs.ReferenceError):
        vault.get_secret_value('canonical', consumer='test', unattended=True)
    vault.set_secret('app', VALUE, scope='another-project')
    with pytest.raises(refs.ReferenceError):
        vault.get_secret_value('canonical', consumer='test')


def test_nested_resolution_preserves_source_use_counters(vault):
    vault.set_secret('app', VALUE)
    refs.create('canonical', ref())
    before = vault._load_store()['secrets']['app']['use_count']
    vault.get_secret_value('canonical', consumer='test')
    assert vault._load_store()['secrets']['app']['use_count'] == before + 1


def test_source_exception_text_never_enters_resolver_logs(vault, monkeypatch):
    lines = []
    vault.set_secret('app', VALUE)
    monkeypatch.setattr(refs, '_log', lambda msg, **kw: lines.append(msg))
    monkeypatch.setattr(vault, 'get_secret_value', lambda *a, **kw: (_ for _ in ()).throw(vault.SecretsError(VALUE)))
    with pytest.raises(refs.ReferenceError) as failure:
        refs.resolve(ref())
    assert VALUE not in json.dumps(lines) + str(failure.value)


def test_private_authorization_pane_reuses_profile_without_returning_url(monkeypatch):
    from queue import Queue
    from mc import desk_oauth_private_pane
    from mc.blueprints import browser_routes
    session = {'session_id': 'pane-opaque', 'url': 'https://old.example', 'cmd_queue': Queue()}
    calls = []
    def launch(project, url, **kw):
        assert url == 'about:blank'
        calls.append(kw)
        return session, None
    monkeypatch.setattr(browser_routes, '_launch_browser', launch)
    url = 'https://x.example/authorize?client_id=' + VALUE
    assert desk_oauth_private_pane.open_signin(url, 'saved-profile') == 'pane-opaque'
    assert calls == [{'profile': 'saved-profile'}]
    assert session['cmd_queue'].get_nowait() == ('Page.navigate', {'url': url})
    lines = []
    monkeypatch.setattr(desk_oauth_private_pane, '_log', lambda msg, **kw: lines.append(msg))
    monkeypatch.setattr(browser_routes, '_launch_browser', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError(VALUE)))
    with pytest.raises(oauth.OAuthError) as failure:
        desk_oauth_private_pane.open_signin(url, 'saved-profile')
    assert VALUE not in json.dumps(lines) + str(failure.value)


def test_rebinding_existing_details_restores_prior_record_on_rollback(vault):
    vault.set_secret('app', VALUE)
    vault.set_secret('canonical', 'prior-typed-value')
    undo = UndoStack()
    refs.create('canonical', ref(), undo=undo)
    assert vault.get_secret_value('canonical', consumer='test') == VALUE
    assert not undo.unwind()
    assert vault.get_secret_value('canonical', consumer='test') == 'prior-typed-value'


def test_key_pair_fields_can_both_reference_saved_parts(vault):
    from mc.desk_connect.providers.key_paste import KeyPaste
    vault.set_secret('app', VALUE, username=USER)
    provider = KeyPaste(vault='higgsfield', secret_label='Secret', hint='', user_label='Key ID')
    fields = provider.fields(set())
    assert fields[0]['saved_entry'] is True
    clean = provider.clean({'key_id': ref(part='username'), 'secret': ref()}, set())
    provider.apply(clean, UndoStack(), set())
    assert vault.get_secret_value('higgsfield', consumer='test') == VALUE
    assert vault.get_username('higgsfield') == USER


def test_source_edit_invalidates_a_previously_verified_key(env, monkeypatch):
    from mc.desk_connect import verification
    from mc.desk_connect.providers.base import Probe
    from mc.desk_connect.providers.openai_api import OpenAiProvider
    store.set_secret('app', VALUE)
    refs.create('openai-api', ref())
    provider = OpenAiProvider()
    monkeypatch.setattr(provider, 'verify', lambda *a: Probe(True, 'Accepted'))
    assert verification.verify('openai', 'api_key', provider=provider)['state'] == 'verified'
    store.set_secret('app', VALUE + '-rotated')
    assert verification.status('openai', 'api_key', provider=provider)['state'] == 'key_stored'
    store.delete_secret('app')
    status = verification.status('openai', 'api_key', provider=provider)
    assert status['state'] == 'key_unreadable' and status['message'] == refs.RECONNECT


def test_broken_x_source_does_not_use_legacy_token_on_card(vault, monkeypatch):
    from mc import desk_accounts, desk_account_refs
    vault.set_secret('app', VALUE)
    refs.create('x.client-id', ref())
    vault.set_secret('x.oauth-token', 'legacy-token')
    vault.delete_secret('app')
    monkeypatch.setattr(desk_account_refs, 'oauth_arg', lambda *a: None)
    state = desk_accounts.publish_state({'platform': 'x', 'capability': 'direct'})
    assert state['ready'] is False and state['reason'] == refs.RECONNECT


@pytest.mark.parametrize('part,setup', [('value', 'missing'), ('username', 'empty'), ('value', 'scope'), ('value', 'locked')])
def test_start_and_save_refuse_broken_sources_without_leaking(env, monkeypatch, part, setup):
    from mc.blueprints import desk_held_signin_routes
    client, _, _ = env
    client.application.register_blueprint(desk_held_signin_routes.bp)
    store.set_secret('app', VALUE)
    if setup == 'missing':
        store.delete_secret('app')
    elif setup == 'scope':
        store.set_secret('app', VALUE, scope='another-project')
    elif setup == 'locked':
        monkeypatch.setattr(store, 'is_locked', lambda: True)
    fields = {'client_id': ref(part=part)}
    start = client.post('/api/desk/connect/x/start-held', json={'passcode': PASSCODE, 'hold': fields})
    assert start.status_code == 409
    saved = client.post('/api/desk/connect/commit', json={'passcode': PASSCODE, 'request_id': 'broken-save-1113',
        'draft': {'url': 'https://x.com', 'method': 'oauth', 'fields': {**fields, 'identity': 'example'}}})
    assert saved.status_code == 409
    assert VALUE not in start.get_data(as_text=True) + saved.get_data(as_text=True)


@pytest.mark.parametrize('broken', ['missing', 'empty_user', 'scope', 'locked', 'corrupt'])
def test_broken_sources_fail_closed_and_report_plain_card_status(vault, monkeypatch, broken):
    lines = []
    monkeypatch.setattr(refs, '_log', lambda msg, **kw: lines.append(msg))
    vault.set_secret('app', VALUE, username=USER)
    refs.create('canonical', ref(part='username' if broken == 'empty_user' else 'value'))
    if broken == 'missing':
        vault.delete_secret('app')
    elif broken == 'empty_user':
        vault.set_secret('app', VALUE)
    elif broken == 'scope':
        vault.set_secret('app', VALUE, scope='other')
    elif broken == 'locked':
        monkeypatch.setattr(vault, 'is_locked', lambda: True)
        monkeypatch.setattr(vault, 'load_master_key', lambda: (_ for _ in ()).throw(vault.VaultLocked('locked')))
    else:
        data = vault._load_store()
        data['secrets']['app']['ciphertext'] = 'invalid'
        vault._save_store(data)
    with pytest.raises(refs.ReferenceError):
        vault.get_secret_value('canonical', consumer='test')
    issue = refs.health('canonical')
    assert issue['state'] == ('vault_locked' if broken == 'locked' else 'key_unreadable')
    assert issue['reason'] in (refs.UNLOCK, refs.RECONNECT)
    assert lines
    assert VALUE not in json.dumps([issue, lines]) and USER not in json.dumps([issue, lines])


def test_reference_scope_and_username_semantics(vault):
    vault.set_secret('app', VALUE, username=USER, scope='project-a', allow_unattended=False)
    assert refs.resolve(ref(part='username'), project_id='project-a', unattended=True) == USER
    with pytest.raises(refs.ReferenceError):
        refs.resolve(ref(), project_id='project-b')
    with pytest.raises(refs.ReferenceError):
        refs.resolve(ref(), project_id='project-a', unattended=True)
    with pytest.raises(refs.ReferenceError):
        refs.create('canonical', ref())  # Desk-wide setup cannot borrow a project-only entry.


def test_mixed_key_pair_and_rollback(vault):
    vault.set_secret('app', VALUE, username=USER)
    undo = UndoStack()
    refs.create('canonical', 'typed-secret', username=ref(part='value'), undo=undo,
                entry_type=vault.ENTRY_API_KEY_PAIR)
    assert vault.get_secret_value('canonical', consumer='test') == 'typed-secret'
    assert vault.get_username('canonical') == VALUE
    assert VALUE not in json.dumps(vault.list_secrets())
    assert not undo.unwind()
    assert 'canonical' not in {s['name'] for s in vault.list_secrets()}


def test_reference_cycle_and_invalid_parts_are_refused(vault):
    vault.set_secret('app', VALUE)
    refs.create('canonical', ref())
    refs.create('app', ref('canonical'), undo=UndoStack())
    with pytest.raises(refs.ReferenceError):
        vault.get_secret_value('canonical', consumer='test')
    for raw in ({'name': 'app', 'part': 'bad'}, {'name': 'app', 'part': []}, {'name': 'app', 'part': 'value', 'value': VALUE}):
        with pytest.raises(refs.ReferenceError):
            refs.clean(raw)


@pytest.mark.parametrize('damaged', [None, {'unknown': {'name': 'app', 'part': 'value'}}])
def test_damaged_binding_fails_with_plain_message_instead_of_type_error(vault, damaged):
    vault.set_secret('app', VALUE)
    refs.create('canonical', ref())
    records = vault._load_store()
    records['secrets']['canonical']['field_refs'] = damaged
    vault._save_store(records)
    with pytest.raises(refs.ReferenceError, match='Reconnect'):
        vault.get_secret_value('canonical', consumer='test')
    assert refs.health('canonical')['reason'] == refs.RECONNECT


def test_backup_preserves_bindings_without_snapshotting_source(vault):
    vault.set_secret('app', VALUE)
    refs.create('canonical', ref())
    archive = vault.export_all_for_backup('backup-passphrase', consumer='test')
    vault.delete_secret('canonical')
    vault.import_all_from_backup(archive, 'backup-passphrase', consumer='test')
    assert vault._load_store()['secrets']['canonical']['field_refs'] == {'value': ref()}
    vault.set_secret('app', 'rotated')
    assert vault.get_secret_value('canonical', consumer='test') == 'rotated'


def test_metadata_edit_keeps_source_live_and_cannot_silently_copy(env):
    from mc.blueprints import secrets_routes
    client, _, _ = env
    client.application.register_blueprint(secrets_routes.bp)
    store.set_secret('app', VALUE)
    refs.create('canonical', ref())
    response = client.patch('/api/secrets/canonical', json={'passcode': PASSCODE, 'description': 'Updated description'})
    assert response.status_code == 200
    assert VALUE not in response.get_data(as_text=True)
    store.set_secret('app', 'rotated')
    assert store.get_secret_value('canonical', consumer='test') == 'rotated'
    response = client.patch('/api/secrets/canonical', json={'passcode': PASSCODE, 'value': 'different'})
    assert response.status_code == 400
    assert 'original entry' in response.get_json()['error']
    assert store._load_store()['secrets']['canonical']['field_refs'] == {'value': ref()}


def test_x_refresh_rotated_secret_and_changed_id_even_with_fresh_token(vault, monkeypatch):
    monkeypatch.setattr(oauth, '_meta', lambda service, account_id=None: next(
        (s for s in vault.list_secrets() if s['name'] == oauth.vault_name(service, account_id)), None))
    vault.set_secret('app', VALUE, username=CID)
    refs.create('x.client-id', ref(part='username'))
    refs.create('x.client-secret', ref())
    record = {'access_token': 'access-SENTINEL', 'refresh_token': 'refresh-SENTINEL',
              'client_id': CID, 'expires_at': time.time() - 10, 'token_endpoint': 'https://example.test/token'}
    oauth._store_record('x', record)
    calls = []
    def token_call(*args, **kw):
        calls.append(kw)
        return 200, {'access_token': 'new-access', 'expires_in': 3600}
    monkeypatch.setattr(oauth, '_call', token_call)
    vault.set_secret('app', VALUE + '-rotated', username=CID)
    assert oauth.access_token('x', consumer='test') == 'new-access'
    assert calls[0]['headers']['Authorization'] == 'Basic ' + base64.b64encode(f'{CID}:{VALUE}-rotated'.encode()).decode()
    vault.set_secret('app', VALUE, username='changed-id')
    assert oauth.status('x')['state'] == 'needs_signin'
    with pytest.raises(oauth.OAuthError, match='sign in again'):
        oauth.access_token('x', consumer='test')
    assert len(calls) == 1


def test_held_and_commit_resolve_on_server_and_return_no_resolved_values(env, monkeypatch):
    client, _, _ = env
    from mc.blueprints import desk_held_signin_routes, secrets_routes
    from mc import desk_oauth_private_pane
    client.application.register_blueprint(desk_held_signin_routes.bp)
    client.application.register_blueprint(secrets_routes.bp)
    store.set_secret('app.id', CID)
    store.set_secret('app', VALUE)
    monkeypatch.setattr(oauth, '_open_listener', lambda *a: SimpleNamespace(server_address=('127.0.0.1', 9876)))
    monkeypatch.setattr(oauth, '_close_listener', lambda *a: None)
    monkeypatch.setattr(oauth.threading, 'Timer', lambda *a, **kw: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(oauth._profile, 'existed', lambda *a: True)
    monkeypatch.setattr(desk_oauth_private_pane, 'open_signin', lambda url, profile: 'pane-opaque')
    lines = []
    monkeypatch.setattr(oauth, '_log', lambda msg, **kw: lines.append(msg))
    monkeypatch.setattr(refs, '_log', lambda msg, **kw: lines.append(msg))
    held = client.post('/api/desk/connect/x/start-held', json={'passcode': PASSCODE,
                      'hold': {'client_id': ref('app.id'), 'client_secret': ref()}})
    assert held.status_code == 201
    assert held.get_json()['pane_session_id'] == 'pane-opaque'
    assert held.get_json()['auth_url'] == ''
    # Save without a held token exercises its ordinary after-commit sign-in too.
    saved = client.post('/api/desk/connect/commit', json={'passcode': PASSCODE, 'request_id': 'saved-ref-1113',
        'draft': {'url': 'https://x.com', 'method': 'oauth',
                  'fields': {'identity': 'example', 'client_id': ref('app.id'), 'client_secret': ref()}}})
    assert saved.status_code == 201
    assert store.get_secret_value('x.client-secret', consumer='test') == VALUE
    metadata = client.get('/api/secrets')
    blob = json.dumps([held.get_json(), saved.get_json(), metadata.get_json(), lines])
    assert VALUE not in blob and CID not in blob
    with oauth._lock:
        oauth._flows.clear()


@pytest.mark.parametrize('part', ['value', 'username'])
def test_shared_key_provider_accepts_each_part(env, part):
    client, _, _ = env
    store.set_secret('app', VALUE, username=USER)
    r = client.post('/api/desk/connect/commit', json={'passcode': PASSCODE, 'request_id': 'shared-key-1113',
        'draft': {'url': 'https://openai.com', 'method': 'api_key', 'fields': {'secret': ref(part=part)}}})
    assert r.status_code == 201
    assert store.get_secret_value('openai-api', consumer='test') == (VALUE if part == 'value' else USER)
    assert VALUE not in r.get_data(as_text=True) and USER not in r.get_data(as_text=True)
