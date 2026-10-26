"""MC-1113 audit: probes do not dispense; crafted references cannot bypass Setup."""
import base64
import json
import os
from pathlib import Path

import pytest

from tests.test_secrets_store import vault  # noqa: F401
from tests.test_desk_connect import env, PASSCODE  # noqa: F401
from mc import secret_field_refs as refs, secrets_store as store, desk_oauth as oauth
from mc.desk_connect.undo import UndoStack


def ref(name='app', part='value'):
    return {'name': name, 'part': part}


@pytest.mark.parametrize('part', ['value', 'username'])
def test_probes_never_dispense_write_or_register(vault, monkeypatch, part):
    from mc import desk_oauth_app_refs, desk_engines
    from mc.desk_connect.providers.key_paste import KeyPaste
    monkeypatch.setattr(oauth, '_meta', lambda service, account_id=None: next(
        (s for s in vault.list_secrets() if s['name'] == oauth.vault_name(service, account_id)), None))
    vault.set_secret('app', 'secret-sentinel', username='client-id')
    refs.create('x.client-id', ref(part=part))
    refs.create('openai-api', ref(part=part))
    oauth._store_record('x', {'access_token': 'access-sentinel', 'client_id': 'client-id' if part == 'username' else 'secret-sentinel'})
    before = vault._load_store()
    # Snapshot all files, including the audit log, not just public counters.
    home = Path(os.environ['CLAYRUNE_HOME'])
    files = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    def forbidden(*a, **kw):
        pytest.fail('a status probe dispensed, registered, audited or wrote a record')
    for attr in ('get_secret_value', 'get_username', 'register_dispensed', '_save_store', '_audit'):
        monkeypatch.setattr(vault, attr, forbidden)
    monkeypatch.setattr(oauth, '_read_record', forbidden)
    original_open = vault._open
    def checked_open(name, rec):
        assert name != oauth.vault_name('x'), 'status decrypted the token record beyond the readability check'
        return original_open(name, rec)
    # The ordinary lock probe is permitted one _open readability check of tokens.
    monkeypatch.setattr(oauth._vault_lock, 'probe', lambda name: oauth._vault_lock.OK)
    monkeypatch.setattr(vault, '_open', checked_open)
    for _ in range(3):
        assert refs.health('x.client-id') is None
        assert vault.is_readable('openai-api')
        assert desk_oauth_app_refs.problem() is None
        assert oauth.status('x')['state'] == 'connected'
        assert KeyPaste(vault='openai-api', secret_label='Key', hint='').state()['state'] == 'key_stored'
        assert desk_engines.connection('openai')['ready'] is True
    assert vault._load_store() == before
    assert {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()} == files


@pytest.mark.parametrize('damage', ['missing', 'scope', 'totp', 'corrupt', 'empty_username', 'unattended'])
def test_failed_probe_is_also_non_dispensing(vault, monkeypatch, damage):
    vault.set_secret('app', 'value', username='user')
    refs.create('canonical', ref(part='username' if damage == 'empty_username' else 'value'))
    records = vault._load_store()
    app = records['secrets']['app']
    if damage == 'missing':
        records['secrets'].pop('app')
    elif damage == 'scope':
        app['scope'] = 'private'
    elif damage == 'totp':
        app['kind'] = vault.KIND_TOTP
    elif damage == 'corrupt':
        app['ciphertext'] = 'bad'
    elif damage == 'empty_username':
        app['username'] = ''
    else:
        app['allow_unattended'] = False
    vault._save_store(records)
    before = vault._load_store()
    def forbidden(*a, **kw):
        pytest.fail('failed probe caused a dispense side effect')
    for attr in ('get_secret_value', 'get_username', 'register_dispensed', '_save_store', '_audit'):
        monkeypatch.setattr(vault, attr, forbidden)
    assert refs.health('canonical', unattended=damage == 'unattended')['state'] == 'key_unreadable'
    assert vault._load_store() == before


@pytest.mark.parametrize('url,method,target,fields', [
    ('https://openai.com', 'api_key', 'openai-api', {'secret': ref()}),
    ('https://x.com', 'oauth', 'x.client-id', {'identity': 'example', 'client_id': ref()}),
])
def test_save_replacement_requires_explicit_confirmation_and_keeps_policy(env, monkeypatch, url, method, target, fields):
    client, _, _ = env
    store.set_secret('app', 'new-value')
    store.set_secret(target, 'prior-value', scope='project-a', allow_unattended=False)
    monkeypatch.setattr(oauth, 'start', lambda *a, **kw: {})
    before = store._load_store()['secrets'][target]
    def save(fields, request_id):
        return client.post('/api/desk/connect/commit', json={'passcode': PASSCODE, 'request_id': request_id,
            'draft': {'url': url, 'method': method, 'fields': fields}})
    refused = save(fields, 'refused-replace')
    assert refused.status_code == 409
    assert refused.get_json()['code'] == 'replace_confirmation_required'
    assert store._load_store()['secrets'][target] == before
    for invalid in ('true', 1):
        assert save({**fields, 'confirm_replace': invalid}, 'invalid-' + str(invalid)).status_code == 400
    saved = save({**fields, 'confirm_replace': True}, 'confirmed-replace')
    assert saved.status_code == 201, saved.get_json()
    rec = store._load_store()['secrets'][target]
    assert rec['scope'] == 'project-a' and rec['allow_unattended'] is False
    assert store.is_readable(target)
    with pytest.raises(store.SecretDenied):
        store.get_secret_value(target, consumer='test', project_id='other-project')
    with pytest.raises(store.SecretDenied):
        store.get_secret_value(target, consumer='test', project_id='project-a', unattended=True)
    assert store.get_secret_value(target, consumer='test', project_id='project-a') == 'new-value'


def test_direct_create_cannot_replace_with_only_undo(vault):
    vault.set_secret('app', 'new')
    vault.set_secret('canonical', 'old')
    with pytest.raises(vault.SecretExists):
        refs.create('canonical', ref(), undo=UndoStack())
    assert vault.get_secret_value('canonical', consumer='test') == 'old'


@pytest.mark.parametrize('bad', ['totp', 'scope', 'oauth', 'internal'])
def test_crafted_body_refuses_ineligible_source(env, bad):
    client, _, _ = env
    name = 'oauth.hidden' if bad in ('oauth', 'internal') else 'app'
    store.set_secret(name, 'JBSWY3DPEHPK3PXP' if bad == 'totp' else 'value',
                     kind=store.KIND_TOTP if bad == 'totp' else store.KIND_PASSWORD,
                     scope='private' if bad == 'scope' else 'global')
    with pytest.raises(refs.FieldRefError):
        refs.clean(ref(name))
    result = client.post('/api/desk/connect/commit', json={'passcode': PASSCODE, 'request_id': 'crafted-' + bad,
        'draft': {'url': 'https://openai.com', 'method': 'api_key', 'fields': {'secret': ref(name)}}})
    assert result.status_code in (400, 409)
    assert 'openai-api' not in store._load_store()['secrets']


def archive(vault, entries):
    salt, nonce = b'0123456789abcdef', b'0123456789ab'
    key = vault._scrypt_key('passphrase', salt)
    ct = vault._aesgcm(key).encrypt(nonce, json.dumps({'entries': entries}).encode(), vault._EXPORT_AAD)
    return json.dumps({k: base64.b64encode(v).decode() for k, v in
                       {'salt': salt, 'nonce': nonce, 'ciphertext': ct}.items()}).encode()


@pytest.mark.parametrize('bad', ['totp', 'scope', 'oauth', 'cycle'])
@pytest.mark.parametrize('reverse', [False, True])
def test_crafted_backup_checks_final_sources_before_writing(vault, bad, reverse):
    name = 'oauth.hidden' if bad == 'oauth' else 'app'
    entries = [{'name': 'canonical', 'value': '', 'field_refs': {'value': ref(name)}},
               {'name': name, 'value': 'value', 'kind': vault.KIND_TOTP if bad == 'totp' else vault.KIND_PASSWORD,
                'scope': 'private' if bad == 'scope' else 'global'}]
    if bad == 'cycle':
        entries[1]['field_refs'] = {'value': ref('canonical')}
    if reverse:
        entries.reverse()
    with pytest.raises(refs.FieldRefError):
        vault.import_all_from_backup(archive(vault, entries), 'passphrase', consumer='test')
    assert vault.list_secrets() == []


def test_backup_missing_source_restores_broken_binding(vault):
    data = [{'name': 'canonical', 'value': '', 'field_refs': {'value': ref('absent')}}]
    assert vault.import_all_from_backup(archive(vault, data), 'passphrase', consumer='test')['imported'] == ['canonical']
    assert refs.health('canonical')['reason'] == refs.RECONNECT


def test_create_walks_multiple_existing_links_before_rebinding(vault):
    vault.set_secret('app', 'value')
    refs.create('b', ref())
    refs.create('c', ref('b'))
    before = vault._load_store()
    with pytest.raises(refs.FieldRefError):
        refs.create('app', ref('c'), undo=UndoStack(), confirm_replace=True)
    assert vault._load_store() == before


def test_create_preserves_source_dispense_counters(vault):
    vault.set_secret('app', 'value')
    before = vault._load_store()['secrets']['app']['use_count']
    refs.create('canonical', ref())
    assert vault._load_store()['secrets']['app']['use_count'] == before + 1


def test_server_internal_predicate_is_enforced_independently_of_oauth_prefix(vault, monkeypatch):
    vault.set_secret('server-only', 'value')
    monkeypatch.setattr(vault, 'is_server_internal', lambda name: name == 'server-only')
    with pytest.raises(refs.FieldRefError):
        refs.clean(ref('server-only'))
    with pytest.raises(refs.FieldRefError):
        vault.import_all_from_backup(archive(vault, [
            {'name': 'canonical', 'value': '', 'field_refs': {'value': ref('server-only')}}
        ]), 'passphrase', consumer='test')


@pytest.mark.parametrize('state', ['connected', 'changed', 'locked', 'legacy'])
def test_real_x_status_readability_check_has_no_dispense_side_effects(vault, monkeypatch, state):
    monkeypatch.setattr(oauth, '_meta', lambda service, account_id=None: next(
        (s for s in vault.list_secrets() if s['name'] == oauth.vault_name(service, account_id)), None))
    vault.set_secret('app', 'client-id')
    refs.create('x.client-id', ref())
    oauth._store_record('x', {'access_token': 'access', 'client_id': 'client-id'})
    if state == 'changed':
        vault.set_secret('app', 'new-client-id')
    if state == 'legacy':
        records = vault._load_store()
        records['secrets'][oauth.vault_name('x')]['hint'] = ''
        vault._save_store(records)
    if state == 'locked':
        monkeypatch.setattr(vault, 'is_locked', lambda: True)
    before = vault._load_store()
    home = Path(os.environ['CLAYRUNE_HOME'])
    files = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    def forbidden(*a, **kw):
        pytest.fail('status dispensed a source or token')
    for attr in ('get_secret_value', 'get_username', 'register_dispensed', '_save_store', '_audit'):
        monkeypatch.setattr(vault, attr, forbidden)
    monkeypatch.setattr(oauth, '_read_record', forbidden)
    status = oauth.status('x')
    assert status['state'] == {'connected': 'connected', 'changed': 'needs_signin',
                               'locked': 'vault_locked', 'legacy': 'needs_signin'}[state]
    assert vault._load_store() == before
    assert {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()} == files
