"""Secrets vault HTTP surface — mc/blueprints/secrets_routes.py.

The load-bearing assertion here is negative: **no route returns a plaintext
value**. Everything else is ordinary CRUD.
"""

import base64
import os
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    # Hermetic: whoever runs this suite might genuinely be a Claude Code
    # session (this one included), which would otherwise leak a real
    # CLAUDE_CODE_SESSION_ID in and make mc.secrets_store's MC-923
    # unattended-context detection hit the *real* local server. Tests that
    # want to exercise detection mock it explicitly instead.
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    from mc.blueprints import local_auth, secrets_routes
    # Point the local-dashboard-passcode store at a throwaway file so
    # vault-lock tests can configure/verify a passcode without touching a
    # real install's ~/.clayrune-adjacent local_auth.json.
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    secrets_routes._VAULT_LOCK_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    app = Flask(__name__)
    app.register_blueprint(secrets_routes.bp)
    return app.test_client()


def _create(client, **over):
    body = {'name': 'reddit.password', 'value': SECRET,
            'description': 'launch account'}
    body.update(over)
    return client.post('/api/secrets', json=body)


def _set_passcode(passcode='unlock1234'):
    """Configure the local dashboard passcode directly (the local_auth
    blueprint isn't registered on this minimal test app)."""
    from mc.blueprints import local_auth
    local_auth._local_auth_set_passcode(passcode)
    return passcode


def test_create_and_list(client):
    assert _create(client).status_code == 200
    r = client.get('/api/secrets')
    assert r.status_code == 200
    data = r.get_json()
    assert [s['name'] for s in data['secrets']] == ['reddit.password']
    assert data['secrets'][0]['placeholder'] == '{{secret:reddit.password}}'
    assert data['key_backend'] == 'file'
    assert data['key_at_rest_warning']  # file backend must warn


def test_no_route_returns_the_plaintext(client):
    _create(client)
    client.post('/api/secrets/check',
                json={'text': 'x {{secret:reddit.password}}'})
    for path in ('/api/secrets', '/api/secrets/audit'):
        assert SECRET not in client.get(path).get_data(as_text=True), path
    # …including the write and edit responses themselves.
    assert SECRET not in _create(client).get_data(as_text=True)
    patched = client.patch('/api/secrets/reddit.password',
                           json={'description': 'renamed'})
    assert SECRET not in patched.get_data(as_text=True)


def test_username_round_trips_and_survives_an_unrelated_patch(client):
    _create(client, username='u/ron')
    client.patch('/api/secrets/reddit.password', json={'description': 'renamed'})
    entry = client.get('/api/secrets').get_json()['secrets'][0]
    assert entry['username'] == 'u/ron'


def test_check_flags_a_user_reference_with_no_username(client):
    """`{{secret:x}}` resolving is not proof `{{user:x}}` will — the dry run has
    to separate them or the failure lands mid-login."""
    _create(client)
    r = client.post('/api/secrets/check',
                    json={'text': '{{user:reddit.password}}'}).get_json()
    assert r['resolvable'] is False
    assert r['referenced'][0]['reason'] == 'no_username'
    ok = client.post('/api/secrets/check',
                     json={'text': '{{secret:reddit.password}}'}).get_json()
    assert ok['resolvable'] is True


def test_create_rejects_missing_value(client):
    r = client.post('/api/secrets', json={'name': 'a.b'})
    assert r.status_code == 400


def test_create_rejects_bad_name(client):
    r = client.post('/api/secrets', json={'name': 'Bad Name', 'value': 'xxxxxx'})
    assert r.status_code == 400


def test_patch_metadata_preserves_the_value(client):
    _create(client)
    r = client.patch('/api/secrets/reddit.password',
                     json={'scope': 'mission_control', 'allow_unattended': False})
    assert r.status_code == 200
    assert r.get_json()['scope'] == 'mission_control'
    assert r.get_json()['allow_unattended'] is False

    from mc import secrets_store
    assert secrets_store.get_secret_value(
        'reddit.password', consumer='test',
        project_id='mission_control') == SECRET


def test_patch_unknown_is_404(client):
    assert client.patch('/api/secrets/nope.none', json={}).status_code == 404


def test_delete(client):
    _create(client)
    assert client.delete('/api/secrets/reddit.password').status_code == 200
    assert client.delete('/api/secrets/reddit.password').status_code == 404
    assert client.get('/api/secrets').get_json()['secrets'] == []


def test_audit_records_the_write(client):
    _create(client)
    records = client.get('/api/secrets/audit').get_json()['records']
    assert records[0]['event'] == 'set'
    assert records[0]['name'] == 'reddit.password'


def test_check_reports_resolvability_and_actually_tries_decryption(client):
    _create(client, scope='alpha')
    r = client.post('/api/secrets/check',
                    json={'text': 'login {{secret:reddit.password}} '
                                  'and {{secret:absent.one}}',
                          'project_id': 'alpha'})
    data = r.get_json()
    assert data['resolvable'] is False
    by_name = {x['name']: x for x in data['referenced']}
    assert by_name['reddit.password']['ok'] is True
    assert by_name['absent.one']['reason'] == 'not_found'
    # A check must not count as a use, even though it now decrypts for real.
    assert client.get('/api/secrets').get_json()['secrets'][0]['use_count'] == 0


def test_check_flags_undecryptable_entry_not_just_existence(client):
    """2026-09-14 regression: the old dry-run only confirmed a name existed,
    so it reported an entry orphaned by a silent master-key remint as fine.
    Corrupt the key file in place (same effect as a remint) and confirm the
    check now catches it instead of reporting ok=True."""
    _create(client)
    from mc import secrets_store as vault
    vault.key_file_path().write_text(
        base64.b64encode(os.urandom(32)).decode('ascii'), encoding='utf-8')
    r = client.post('/api/secrets/check',
                    json={'text': '{{secret:reddit.password}}'})
    data = r.get_json()
    assert data['resolvable'] is False
    assert data['referenced'][0]['reason'] == 'undecryptable'


def test_list_exposes_unreadable_count(client):
    _create(client)
    r = client.get('/api/secrets')
    assert r.get_json()['unreadable_count'] == 0

    from mc import secrets_store as vault
    vault.key_file_path().write_text(
        base64.b64encode(os.urandom(32)).decode('ascii'), encoding='utf-8')

    r = client.get('/api/secrets')
    data = r.get_json()
    assert data['unreadable_count'] == 1
    assert data['secrets'][0]['readable'] is False
    assert SECRET not in r.get_data(as_text=True)


def test_authenticator_import_previews_without_storing_or_leaking_seeds(client):
    from tests.test_totp import _make_migration_uri
    uri = _make_migration_uri([(b'12345678901234567890', 'ron', 'GitHub', 2)])
    r = client.post('/api/secrets/import-authenticator', json={'uri': uri})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    data = r.get_json()
    assert data['count'] == 1
    assert data['accounts'][0]['issuer'] == 'GitHub'
    # The preview must not carry seeds, and must not have stored anything yet.
    assert 'secret' not in data['accounts'][0]
    assert 'GEZDGNBVGY' not in body     # base32 of "12345678901234567890"
    assert client.get('/api/secrets').get_json()['secrets'] == []


def test_authenticator_import_commits_selected_accounts(client):
    from tests.test_totp import _make_migration_uri
    uri = _make_migration_uri([
        (b'12345678901234567890', 'ron', 'GitHub', 2),
        (b'09876543210987654321', 'ron', 'Reddit', 2),
    ])
    r = client.post('/api/secrets/import-authenticator', json={
        'uri': uri, 'commit': True,
        'names': {'github.ron.totp': 'gh.totp', 'reddit.ron.totp': ''},
    })
    data = r.get_json()
    assert data['imported'] == ['gh.totp']       # the blank name means "skip"
    assert data['skipped'] == ['reddit.ron.totp']
    stored = client.get('/api/secrets').get_json()['secrets']
    assert [s['name'] for s in stored] == ['gh.totp']
    assert stored[0]['kind'] == 'totp'
    assert stored[0]['placeholder'] == '{{totp:gh.totp}}'


def test_authenticator_import_rejects_a_plain_otpauth_uri(client):
    r = client.post('/api/secrets/import-authenticator',
                    json={'uri': 'otpauth://totp/x?secret=JBSWY3DPEHPK3PXP'})
    assert r.status_code == 400


def test_totp_probe_confirms_without_revealing_the_code(client):
    from mc import totp
    seed = 'JBSWY3DPEHPK3PXP'
    client.post('/api/secrets', json={'name': 'gh.totp', 'value': seed,
                                      'kind': 'totp'})
    good = totp.generate(seed)
    r = client.post('/api/secrets/totp/gh.totp', json={'code': good})
    data = r.get_json()
    assert data['match'] is True
    # The response must never contain a usable code — only the verdict.
    assert good not in r.get_data(as_text=True)

    bad = client.post('/api/secrets/totp/gh.totp', json={'code': '000000'})
    assert bad.get_json()['match'] is False


def test_patching_metadata_does_not_downgrade_a_totp_secret(client):
    client.post('/api/secrets', json={'name': 'gh.totp', 'kind': 'totp',
                                      'value': 'JBSWY3DPEHPK3PXP'})
    client.patch('/api/secrets/gh.totp', json={'description': 'renamed'})
    stored = client.get('/api/secrets').get_json()['secrets'][0]
    assert stored['kind'] == 'totp'
    assert stored['placeholder'] == '{{totp:gh.totp}}'


def test_totp_probe_on_unknown_secret_is_404(client):
    r = client.post('/api/secrets/totp/nope.totp', json={'code': '123456'})
    assert r.status_code == 404


def test_check_flags_out_of_scope_and_unattended(client):
    _create(client, scope='alpha', allow_unattended=False)
    out = client.post('/api/secrets/check',
                      json={'text': '{{secret:reddit.password}}',
                            'project_id': 'beta'}).get_json()
    assert out['referenced'][0]['reason'] == 'out_of_scope'
    un = client.post('/api/secrets/check',
                     json={'text': '{{secret:reddit.password}}',
                           'project_id': 'alpha',
                           'unattended': True}).get_json()
    assert un['referenced'][0]['reason'] == 'unattended_blocked'


# ── vault passphrase lock (MC 503edfe4) ──────────────────────────────────────

def test_vault_lock_lifecycle_over_http(client):
    from mc import secrets_store as vault
    _create(client)
    passcode = _set_passcode()

    state = client.get('/api/secrets/vault-lock').get_json()
    assert state == {'state': 'unconfigured', 'configured': False}

    res = client.post('/api/secrets/vault-lock/set',
                      json={'passphrase': 'a real passphrase', 'passcode': passcode})
    assert res.status_code == 200
    recovery_key = res.get_json()['recovery_key']
    assert client.get('/api/secrets/vault-lock').get_json()['state'] == 'unlocked'

    # Simulate a restart: the in-memory key is gone.
    vault._unlocked_key = None
    assert client.get('/api/secrets/vault-lock').get_json()['state'] == 'locked'

    # Locked list: names still visible (metadata), never a 500, never a value.
    locked_list = client.get('/api/secrets').get_json()
    assert locked_list['locked'] is True
    assert locked_list['secrets'][0]['name'] == 'reddit.password'
    assert SECRET not in str(locked_list)

    # Wrong passphrase: refused, still locked.
    bad = client.post('/api/secrets/vault-lock/unlock',
                      json={'passphrase': 'nope', 'passcode': passcode})
    assert bad.status_code == 403
    assert client.get('/api/secrets/vault-lock').get_json()['state'] == 'locked'

    # Recovery key unlocks; the list reads clean again.
    ok = client.post('/api/secrets/vault-lock/unlock',
                     json={'recovery_key': recovery_key, 'passcode': passcode})
    assert ok.status_code == 200
    assert client.get('/api/secrets/vault-lock').get_json()['state'] == 'unlocked'
    unlocked_list = client.get('/api/secrets').get_json()
    assert unlocked_list['locked'] is False
    assert unlocked_list['secrets'][0]['readable'] is True


def test_vault_lock_set_is_refused_a_second_time(client):
    passcode = _set_passcode()
    client.post('/api/secrets/vault-lock/set',
               json={'passphrase': 'a real passphrase', 'passcode': passcode})
    res = client.post('/api/secrets/vault-lock/set',
                      json={'passphrase': 'a different one', 'passcode': passcode})
    assert res.status_code == 400


def test_vault_lock_set_refused_without_a_configured_passcode(client):
    """No dashboard passcode has ever been set — there is nothing to verify
    the caller against, so the vault lock cannot be configured at all, even
    by a caller `is_unattended_caller()` reads as human (Dave's review of
    d3516a2, MC 503edfe4: worst case was an agent racing to `/set` first and
    owning both the passphrase and the recovery key)."""
    res = client.post('/api/secrets/vault-lock/set',
                      json={'passphrase': 'a real passphrase'})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'passcode_required'
    from mc import secrets_store as vault
    assert vault.lock_state() == 'unconfigured'


def test_vault_lock_set_refused_with_wrong_passcode(client):
    _set_passcode('unlock1234')
    res = client.post('/api/secrets/vault-lock/set',
                      json={'passphrase': 'a real passphrase', 'passcode': 'not-it'})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'bad_passcode'
    from mc import secrets_store as vault
    assert vault.lock_state() == 'unconfigured'


def test_vault_lock_forged_origin_no_longer_sufficient(client):
    """A browser Origin header alone used to make `is_unattended_caller()`
    treat any caller as human. A configured passcode with no correct
    passcode supplied must still refuse the write, Origin or not."""
    _set_passcode('unlock1234')
    for headers in ({'Origin': 'http://localhost:5199'}, {}):
        res = client.post('/api/secrets/vault-lock/set',
                          headers=headers,
                          json={'passphrase': 'a real passphrase'})
        assert res.status_code == 403
    from mc import secrets_store as vault
    assert vault.lock_state() == 'unconfigured'


def test_vault_lock_manual_chat_session_still_needs_the_passcode(client):
    """`is_unattended_caller()` exempts `trigger_type == 'manual'` sessions —
    a real interactive agent chat. That must not be enough on its own to set
    the vault passphrase; the passcode gate applies regardless."""
    from mc.state import agent_sessions
    agent_sessions['manual-1'] = {'status': 'running', 'trigger_type': 'manual',
                                  'project_id': 'mission_control'}
    try:
        res = client.post('/api/secrets/vault-lock/set',
                          json={'passphrase': 'a real passphrase'})
        assert res.status_code == 403
        assert res.get_json()['error'] == 'passcode_required'
    finally:
        agent_sessions.clear()
