"""A LOCKED vault is not a dead sign-in (Ron, 2026-10-06: the Higgsfield connection made the
day before had to be made again after the idle relock). Real vault, rooted in tmp_path and
really passphrase-locked; only the provider's HTTP is scripted. Pinned:

  * `desk_oauth.status` answers `vault_locked` for a good sign-in the locked vault cannot
    open, and `connected` again after the unlock with the record untouched (no re-sign-in);
  * a sign-in the vendor really rejected, or one whose key changed, still says `needs_signin`;
  * `access_token` / `_refresh` on a locked vault raise `vault_locked`, call the provider
    never, and never write `needs_signin` into the saved record;
  * the same call is made for the other Desk states that asked `is_readable` (an account's
    publish state, an engine's connection, a pasted key's credential state, route setup);
  * asking for the lock does not go through `is_readable` (its failure path fires the
    "vault locked" push, which a status poll must not do).
"""
import json
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk_accounts as accounts  # noqa: E402
from mc import desk_engines as engines  # noqa: E402
from mc import desk_oauth as oauth  # noqa: E402
from mc import desk_vault_lock as vault_lock  # noqa: E402
from mc import secrets_store  # noqa: E402
from mc.desk_connect import route_readiness, verification  # noqa: E402
from mc.desk_connect.providers.key_paste import KeyPaste  # noqa: E402

_REAL_META = oauth._meta       # conftest blanks `_meta` for every test; this file reads the real (tmp) vault
PASSPHRASE = 'correct horse battery staple'
ACCESS = 'access-token-SENTINEL-aaaaaaaaaaaaaaaaaaaa'
REFRESH = 'refresh-token-SENTINEL-bbbbbbbbbbbbbbbbbbb'
NEW_ACCESS = 'access-token-SENTINEL-cccccccccccccccccccc'


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    monkeypatch.setattr(oauth, '_meta', _REAL_META)
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    secrets_store._last_key_use = None
    secrets_store.set_passphrase(PASSPHRASE)           # configured and unlocked
    yield secrets_store
    secrets_store._unlocked_key = None


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url))
        return 200, {}, json.dumps({'access_token': NEW_ACCESS, 'refresh_token': REFRESH, 'expires_in': 3600}).encode()

    monkeypatch.setattr(oauth, '_http', fake)
    return calls


def _sign_in(*, left=3600, hint_state=None, refresh=REFRESH):
    rec = {'v': 1, 'access_token': ACCESS, 'refresh_token': refresh, 'expires_at': int(time.time()) + left,
           'client_id': 'cid-1', 'token_endpoint': 'https://clerk.higgsfield.ai/oauth/token',
           'revocation_endpoint': None, 'resource': 'https://mcp.higgsfield.ai/mcp', 'scope': 'openid',
           'needs_signin': hint_state == 'needs_signin'}
    oauth._store_record('higgsfield', rec)


def _record():
    return json.loads(secrets_store.get_secret_value('oauth.higgsfield', consumer='t', internal=True))


def test_a_locked_vault_reads_as_vault_locked_and_unlocking_restores_connected(vault):
    _sign_in(left=-60)                                  # lapsed, but refreshable: the exact shape Ron had
    assert oauth.status('higgsfield')['state'] == 'connected'
    vault.lock_now()
    st = oauth.status('higgsfield')
    assert st['state'] == 'vault_locked'
    assert 'Unlock' in st['reason'] and 'still saved' in st['reason'] and 'sign in again' not in st['reason']
    assert oauth.overview()['higgsfield']['state'] == 'vault_locked'
    vault.unlock_with_passphrase(PASSPHRASE)
    assert oauth.status('higgsfield') == {'state': 'connected', 'reason': None}
    assert _record()['access_token'] == ACCESS and _record()['needs_signin'] is False


def test_a_sign_in_the_vendor_rejected_still_needs_a_new_one_while_locked(vault):
    _sign_in(hint_state='needs_signin')
    vault.lock_now()
    assert oauth.status('higgsfield')['state'] == 'needs_signin'


def test_a_lapsed_sign_in_with_no_refresh_token_still_needs_a_new_one_while_locked(vault):
    _sign_in(left=-60, refresh='')
    vault.lock_now()
    assert oauth.status('higgsfield')['state'] == 'needs_signin'


def test_an_entry_whose_key_changed_still_needs_a_new_sign_in_when_unlocked(vault, monkeypatch):
    _sign_in()
    monkeypatch.setattr(secrets_store, 'is_readable', lambda name: False)
    assert oauth.status('higgsfield')['state'] == 'needs_signin'


def test_a_refresh_on_a_locked_vault_raises_vault_locked_and_marks_nothing(vault, provider):
    _sign_in(left=-60)                                  # due for a refresh
    vault.lock_now()
    with pytest.raises(oauth.OAuthError) as e:
        oauth.access_token('higgsfield', consumer='t')
    assert e.value.code == 'vault_locked' and e.value.status == 409
    assert provider == []                               # the vendor was never asked
    vault.unlock_with_passphrase(PASSPHRASE)
    assert _record()['needs_signin'] is False and _record()['access_token'] == ACCESS
    assert oauth.status('higgsfield')['state'] == 'connected'
    assert oauth.access_token('higgsfield', consumer='t') == NEW_ACCESS      # and it refreshes normally now
    assert len(provider) == 1


def test_the_lock_is_asked_without_is_readable_so_no_push_fires(vault, monkeypatch):
    _sign_in()
    vault.lock_now()

    def boom(name):
        raise AssertionError('is_readable was asked of a locked vault')

    monkeypatch.setattr(secrets_store, 'is_readable', boom)
    assert vault_lock.probe('oauth.higgsfield') == vault_lock.LOCKED


def test_a_lock_that_lapses_inside_the_probe_still_reads_as_locked(vault, monkeypatch):
    """Idle expiry clears the key inside `is_readable` itself: the first lock check says no,
    `is_readable` says False, the second lock check says yes."""
    _sign_in()
    answers = iter([False, True])
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: next(answers))
    monkeypatch.setattr(secrets_store, 'is_readable', lambda name: False)
    assert vault_lock.probe('oauth.higgsfield') == vault_lock.LOCKED


def test_an_x_account_with_a_saved_sign_in_is_vault_locked_not_unsigned(vault):
    oauth._store_record('x', {'v': 1, 'access_token': ACCESS, 'refresh_token': REFRESH,
                              'expires_at': int(time.time()) + 3600, 'client_id': 'c',
                              'token_endpoint': 'https://api.x.com/2/oauth2/token', 'needs_signin': False})
    acc = {'id': 'a1', 'platform': 'x', 'capability': 'direct'}
    assert accounts.publish_state(acc)['ready'] is True
    vault.lock_now()
    pub = accounts.publish_state(acc)
    assert pub['ready'] is False and pub['vault_locked'] is True
    assert 'Unlock' in pub['reason'] and 'sign in again' not in pub['reason']
    assert route_readiness._api_publish(acc)['setup'] == 'vault_locked'


def test_a_pasted_x_token_on_a_locked_vault_is_vault_locked(vault):
    vault.set_secret('x.oauth-token', 'pasted-token-SENTINEL-zzzzzzzzzzzz')
    acc = {'id': 'a1', 'platform': 'x', 'capability': 'direct'}
    assert accounts.publish_state(acc)['ready'] is True
    vault.lock_now()
    pub = accounts.publish_state(acc)
    assert pub['ready'] is False and pub['vault_locked'] is True and 'Unlock' in pub['reason']


def test_an_engine_connection_says_vault_locked(vault):
    _sign_in()
    assert engines.connection('higgsfield_mcp')['ready'] is True
    vault.lock_now()
    c = engines.connection('higgsfield_mcp')
    assert c['ready'] is False and c['state'] == 'vault_locked' and c['exists'] is True
    assert 'Unlock' in c['reason']
    # a key-based engine too
    vault.unlock_with_passphrase(PASSPHRASE)
    vault.set_secret('gemini-api', 'gemkey-SENTINEL-eeeeeeeeeeeeeeeeeeeeeeee')
    assert engines.connection('google')['ready'] is True
    vault.lock_now()
    c = engines.connection('google')
    assert c['ready'] is False and c['state'] == 'vault_locked' and 'Unlock' in c['reason']


def test_a_pasted_key_credential_state_says_vault_locked_and_verification_labels_it(vault):
    kp = KeyPaste(vault='gemini-api', secret_label='Gemini key', hint='')
    vault.set_secret('gemini-api', 'gemkey-SENTINEL-eeeeeeeeeeeeeeeeeeeeeeee')
    assert kp.state()['state'] == 'key_stored'
    vault.lock_now()
    assert kp.state() == {'state': 'vault_locked', 'entry': 'gemini-api'}
    assert verification.LABELS['vault_locked'] == 'Vault locked'


def test_user_facing_lock_copy_has_no_em_dash():
    for text in (vault_lock.SIGNIN_REASON, vault_lock.KEY_REASON, vault_lock.TOKEN_REASON):
        assert '—' not in text and '–' not in text
