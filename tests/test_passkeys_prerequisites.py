"""Slice 2a prerequisites (docs/PASSKEYS_SPEC.md, "Slice 2 prerequisites"):

1. registry integrity: MAC under a key from the unlocked vault (`integrity`, `store`)
2. add and revoke behind a passkey assertion once one exists (`assertion`, routes)
3. host reset for an unusable registry (`recovery`, `/api/passkeys/reset`)
4. the stale-lock double-unlink race (`store._unlink_if_same`)

Registry-level tests need no WebAuthn library. Route-level ones use the software
authenticator and prove protocol logic only, not a real authenticator or biometric.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mc import secrets_store
from mc.blueprints import local_auth
from mc.passkeys import audit, challenges, integrity, recovery, store
from passkeys_vault import TEST_KEY, use_test_key
from tests.test_passkeys_routes import (   # noqa: F401  (fixtures + helpers shared with slice 1)
    ASSERT, FINISH, GOOD, HOST_URL, ORIGIN, OPTIONS, PASSCODE,
    _env as _routes_env, app, authn, browser, call, enroll, passkey_ids, prove, start)

HANDLE = 'aGFuZGxlLWhhbmRsZS1oYW5kbGUtaGFuZGxlLWhhbmRsZQ'


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / 'home'))
    use_test_key(monkeypatch)
    challenges.STORE.clear()
    yield tmp_path / 'home'
    challenges.STORE.clear()


def add(cid='AAAA', **kw):
    args = dict(credential_id=cid, public_key='cHVi', rp_id='localhost',
                origin='http://localhost:5199', transports=['internal'], sign_count=0,
                backup_eligible=False, backup_state=False, aaguid='0' * 8,
                label='Laptop', owner_handle=HANDLE, epoch=0)
    args.update(kw)
    return store.add_credential(**args)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding='utf-8')


class FakeVault:
    """Swaps in the `secrets_store` calls `integrity.mac_key` makes. `load_master_key`
    is a tripwire: it counts as a vault use, so `mac_key` must never call it."""

    def __init__(self, monkeypatch, state='unlocked', backend='passphrase', master=b'm' * 32):
        self.state, self.backend, self.master, self.reads = state, backend, master, 0
        monkeypatch.setattr(integrity, 'mac_key', _real_mac_key)
        monkeypatch.setattr(secrets_store, 'check_idle_lock', lambda: False)
        monkeypatch.setattr(secrets_store, 'lock_state', lambda: self.state)
        monkeypatch.setattr(secrets_store, 'peek_passphrase_key', self._peek)
        monkeypatch.setattr(secrets_store, 'wrapped_key_path',
                            lambda: Path(__file__) if backend == 'passphrase' else Path('no-such-wrapped-key'))
        monkeypatch.setattr(secrets_store, 'load_master_key', self._tripwire)

    def _peek(self):
        self.reads += 1
        return self.master if self.state == 'unlocked' and self.backend == 'passphrase' else None

    @staticmethod
    def _tripwire():
        raise AssertionError('mac_key called load_master_key: that restarts the vault idle clock')


_real_mac_key = integrity.mac_key


# ── 1. registry integrity ────────────────────────────────────────────────────

def test_registry_and_marker_are_signed_and_round_trip():
    add('AAAA')
    reg, marker = read(store.registry_path()), read(store.marker_path())
    assert isinstance(reg['mac'], str) and isinstance(marker['mac'], str)
    assert reg['mac'] != marker['mac']
    assert integrity.verify('registry', reg, TEST_KEY) and integrity.verify('marker', marker, TEST_KEY)
    assert store.active_credential_ids() == ['AAAA']
    assert 'mac' not in store.load()                       # callers never see the MAC


def test_a_registry_edited_without_the_key_is_refused_not_read_as_empty():
    add('AAAA')
    reg = read(store.registry_path())
    reg['credentials'][0]['label'] = 'Attacker laptop'
    write(store.registry_path(), reg)
    with pytest.raises(store.RegistryTampered):
        store.load()
    with pytest.raises(store.RegistryTampered):
        store.active_credential_ids()
    with pytest.raises(store.RegistryTampered):
        add('BBBB')                                         # a write starts with a load
    assert read(store.registry_path())['credentials'][0]['label'] == 'Attacker laptop'


def test_an_attacker_planting_a_credential_with_a_recomputed_mac_needs_the_vault_key():
    add('AAAA')
    reg = read(store.registry_path())
    reg['credentials'].append(dict(reg['credentials'][0], id='EVIL'))
    reg['mac'] = integrity.sign('registry', reg, b'not-the-vault-key-not-the-vault-k')
    write(store.registry_path(), reg)
    with pytest.raises(store.RegistryTampered):
        store.load()


def test_an_unsigned_registry_or_marker_is_refused():
    add('AAAA')
    reg = read(store.registry_path())
    del reg['mac']
    write(store.registry_path(), reg)
    with pytest.raises(store.RegistryUnsigned):
        store.load()
    # a planted unsigned pair (an older build's files, or an attacker's) is no better
    marker = read(store.marker_path())
    del marker['mac']
    write(store.marker_path(), marker)
    with pytest.raises(store.RegistryUnsigned):
        store.load()


def test_an_edited_marker_is_refused_and_macs_are_not_interchangeable():
    add('AAAA')
    marker = read(store.marker_path())
    marker['epoch'] = 0
    marker['enrolled_at'] = 'earlier'
    write(store.marker_path(), marker)
    with pytest.raises(store.RegistryTampered):
        store.load()
    # the registry's MAC grafted onto the marker (and the reverse) must not verify
    add_home = store.registry_path().parent
    reg, mk = read(add_home / 'registry.json'), read(add_home / 'enrolled.json') \
        if (add_home / 'enrolled.json').exists() else read(store.marker_path())
    assert not integrity.verify('marker', dict(mk, mac=reg['mac']), TEST_KEY)
    assert not integrity.verify('registry', dict(reg, mac=mk['mac']), TEST_KEY)


def test_a_registry_signed_under_another_vault_key_is_refused(monkeypatch):
    add('AAAA')
    use_test_key(monkeypatch, b'\x07' * 32)                  # vault rekeyed / registry copied in
    with pytest.raises(store.RegistryTampered):
        store.load()


def test_a_locked_vault_makes_the_registry_unavailable_not_empty(monkeypatch):
    vault = FakeVault(monkeypatch)
    add('AAAA')
    before = store.registry_path().read_bytes()
    vault.state = 'locked'
    for call_ in (store.load, store.active_credential_ids, lambda: add('BBBB'),
                  lambda: store.revoke('AAAA'), store.revoke_all):
        with pytest.raises(store.StoreLocked) as ei:
            call_()
        assert ei.value.reason == 'vault_locked' and ei.value.code == 'passkey_vault_locked'
    assert store.registry_path().read_bytes() == before     # nothing was written
    reads = vault.reads
    assert store.integrity_status() == 'vault_locked' and vault.reads == reads   # no key requested
    vault.state = 'unlocked'
    assert store.active_credential_ids() == ['AAAA'] and store.integrity_status() is None


def test_relocking_takes_effect_on_the_very_next_read(monkeypatch):
    vault = FakeVault(monkeypatch)
    add('AAAA')
    assert store.active_credential_ids() == ['AAAA']
    vault.state = 'locked'                                   # manual or idle relock
    with pytest.raises(store.StoreLocked):
        store.active_credential_ids()


@pytest.mark.parametrize('state,backend', [('unconfigured', 'keyring'), ('unconfigured', 'file'),
                                           ('unlocked', 'keyring')])
def test_a_vault_without_a_passphrase_lock_cannot_back_the_mac(monkeypatch, state, backend):
    vault = FakeVault(monkeypatch, state=state, backend=backend)
    assert store.integrity_status() == 'vault_not_configured'
    assert vault.reads == 0 if state != 'unlocked' else True
    with pytest.raises(store.StoreLocked) as ei:
        add('AAAA')
    assert ei.value.reason == 'vault_not_configured'
    assert not store.registry_path().exists()


def test_a_fresh_install_needs_no_key_to_read_empty(monkeypatch):
    FakeVault(monkeypatch, state='locked')
    assert store.list_credentials() == []                    # nothing enrolled, nothing to verify


def test_mac_key_is_derived_not_the_master_key_and_never_cached(monkeypatch):
    vault = FakeVault(monkeypatch)
    k1 = integrity.mac_key()
    assert k1 != vault.master and len(k1) == 32
    assert k1 == hmac.new(vault.master, b'clayrune-passkey-registry-mac-v1', hashlib.sha256).digest()
    vault.master = b'n' * 32
    assert integrity.mac_key() != k1                         # re-derived from the vault each call


def test_mac_key_never_mints_a_vault_key_on_an_unconfigured_box(monkeypatch):
    vault = FakeVault(monkeypatch, state='unconfigured', backend='keyring')
    with pytest.raises(integrity.IntegrityUnavailable) as ei:
        integrity.mac_key()
    assert ei.value.reason == 'vault_not_configured' and vault.reads == 0


def test_mac_key_does_not_restart_the_vault_idle_clock(monkeypatch):
    """Audit finding 1: GET /api/passkeys derives the key on every poll. Through
    `load_master_key` that counted as a vault use, so a loopback poller held the
    vault unlocked past `vault_idle_lock_minutes`. Real secrets_store, fake clock."""
    monkeypatch.setattr(integrity, 'mac_key', _real_mac_key)
    now = [1000.0]
    monkeypatch.setattr(secrets_store, '_monotonic', lambda: now[0])
    monkeypatch.setattr(secrets_store, '_idle_lock_minutes', lambda: 10.0)
    monkeypatch.setattr(secrets_store, '_unlocked_key', b'K' * 32)
    monkeypatch.setattr(secrets_store, '_last_key_use', now[0])
    monkeypatch.setattr(secrets_store, 'lock_state', lambda: 'unlocked')
    monkeypatch.setattr(secrets_store, 'wrapped_key_path', lambda: Path(__file__))   # passphrase mode
    assert len(integrity.mac_key()) == 32
    for _ in range(4):                                       # a poller, 2 minutes apart (8 of the 10)
        now[0] += 120
        integrity.mac_key()
    assert secrets_store._last_key_use == 1000.0             # never refreshed
    now[0] = 1000.0 + 10 * 60
    with pytest.raises(integrity.IntegrityUnavailable) as ei:
        integrity.mac_key()                                  # idle window ran out despite the polling
    assert ei.value.reason == 'vault_locked' and secrets_store._unlocked_key is None


def test_mac_key_reports_a_relock_between_the_check_and_the_read(monkeypatch):
    FakeVault(monkeypatch)
    monkeypatch.setattr(secrets_store, 'peek_passphrase_key', lambda: None)
    with pytest.raises(integrity.IntegrityUnavailable) as ei:
        integrity.mac_key()
    assert ei.value.reason == 'vault_locked'


def test_registry_without_marker_stays_reportable_while_the_vault_is_locked(monkeypatch):
    vault = FakeVault(monkeypatch)
    add('AAAA')
    store.marker_path().unlink()                             # the crash between the two writes
    vault.state = 'locked'
    with pytest.raises(store.StoreCorrupt) as ei:
        store.load()
    assert not isinstance(ei.value, store.StoreLocked)
    assert 'marker is missing' in str(ei.value)


# ── 4. the stale-lock double-unlink race ─────────────────────────────────────

def _fresh_lock(lock: Path, mtime: float) -> tuple:
    lock.write_text('held', encoding='utf-8')
    os.utime(lock, (mtime, mtime))
    return store._stat_lock(lock)[0]


def test_unlink_if_same_removes_the_lock_it_saw(_home):
    store.passkeys_dir().mkdir(parents=True, exist_ok=True)
    lock = store._lock_path()
    seen = _fresh_lock(lock, time.time() - 60)
    assert store._unlink_if_same(lock, seen) is True
    assert not lock.exists()
    assert not lock.with_name(lock.name + '.break').exists()   # the breaker mutex is released


def test_unlink_if_same_leaves_a_newer_lock_alone(_home):
    """The race: waiter B judged the OLD lock stale, but waiter A removed it and
    took a fresh one first. B's unlink used to delete A's live lock."""
    store.passkeys_dir().mkdir(parents=True, exist_ok=True)
    lock = store._lock_path()
    seen_by_b = _fresh_lock(lock, time.time() - 60)
    lock.unlink()                                            # A breaks the stale lock ...
    _fresh_lock(lock, time.time())                           # ... and takes a new one
    assert store._unlink_if_same(lock, seen_by_b) is False
    assert lock.exists() and lock.read_text(encoding='utf-8') == 'held'


def test_unlink_if_same_on_a_vanished_lock_is_a_no_op(_home):
    store.passkeys_dir().mkdir(parents=True, exist_ok=True)
    lock = store._lock_path()
    seen = _fresh_lock(lock, time.time() - 60)
    lock.unlink()
    assert store._unlink_if_same(lock, seen) is False


def test_a_crashed_breaker_mutex_does_not_wedge_stale_lock_recovery(_home, monkeypatch):
    add('AAAA')
    lock = store._lock_path()
    seen = _fresh_lock(lock, time.time() - 60)
    breaker = lock.with_name(lock.name + '.break')
    breaker.write_text('crashed', encoding='utf-8')
    os.utime(breaker, (time.time() - 60, time.time() - 60))
    assert store._unlink_if_same(lock, seen) is True
    assert not breaker.exists()


def test_a_waiter_that_judged_the_lock_stale_cannot_delete_the_winners_new_lock(_home, monkeypatch):
    """The slice 1 race, replayed deterministically. Waiter B stats the stale lock
    and judges it stale; before B acts, waiter A removes it and takes a fresh one.
    B used to unlink by path, deleting A's live lock so a third writer could enter
    alongside A. B must now notice the lock is not the one it judged and wait."""
    store.passkeys_dir().mkdir(parents=True, exist_ok=True)
    lock = store._lock_path()
    _fresh_lock(lock, time.time() - 60)
    monkeypatch.setattr(store, '_LOCK_TIMEOUT_S', 0.3)     # B's deadline; raised once A has won
    real_stat, taken = store._stat_lock, []

    def stat_then_let_a_win(path):
        out = real_stat(path)                        # B's view: the stale lock
        if not taken:
            taken.append(True)
            path.unlink()                            # A breaks it ...
            _fresh_lock(path, time.time())           # ... and takes a new one
            monkeypatch.setattr(store, '_LOCK_TIMEOUT_S', 30.0)    # A's lock is live, not stale
        return out

    monkeypatch.setattr(store, '_stat_lock', stat_then_let_a_win)
    with pytest.raises(store.StoreError, match='busy'):      # B waits out A's live lock
        with store._write_lock():
            pytest.fail('B entered while A held the lock')
    assert lock.exists() and lock.read_text(encoding='utf-8') == 'held'


# ── 2. add and revoke behind a passkey assertion ─────────────────────────────

def test_first_enrollment_uses_the_passcode_then_add_needs_a_passkey(app, authn):
    c = browser(app)
    a = authn()
    assert enroll(c, a)[2].status_code == 200                # bootstrap: passcode only
    listing = call(c, 'get', '/api/passkeys').get_json()
    assert listing['needs_passkey_to_change'] is True
    for body in ({'passcode': PASSCODE}, {'passcode': 'wrong-wrong'}, {}):
        r = call(c, 'post', OPTIONS, body)
        assert r.status_code == 403 and r.get_json()['error'] == 'proof_required', body
    assert local_auth._LOCAL_AUTH_FAILS == {}                # none of them spent a passcode guess
    b = authn()
    j, cred, fin = enroll(c, b, 'Phone', by=a)
    assert fin.status_code == 200 and cred['id'] in passkey_ids() and len(passkey_ids()) == 2


def test_the_new_credential_cannot_authorize_its_own_enrollment(app, authn):
    c = browser(app)
    a, b = authn(), authn()
    enroll(c, a)
    r = call(c, 'post', ASSERT, {'purpose': 'add'})
    j = r.get_json()
    forged = {'ceremony_id': j['ceremony_id'], 'assertion': b.get(j['options'], ORIGIN)}
    r = call(c, 'post', OPTIONS, {'proof': forged, 'label': 'Mine'})
    assert r.status_code == 403 and r.get_json()['error'] == 'invalid_assertion'
    assert len(passkey_ids()) == 1


def test_an_assertion_flood_is_capped_per_client(app, authn):
    """Audit finding 2: assert/options answers any loopback caller before it
    proves anything. One client address may hold only a few pending ceremonies."""
    c = browser(app)
    enroll(c, authn())
    codes = [call(c, 'post', ASSERT, {'purpose': 'add'}).status_code for _ in range(40)]
    assert codes.count(200) == challenges.MAX_PENDING_PER_CLIENT['assertion']
    assert set(codes) == {200, 429}


def test_a_full_assertion_bucket_does_not_block_enrollment(app, authn):
    """... and the assertion and registration ceremonies no longer share slots:
    with every assertion slot taken, the owner can still start an enrollment."""
    c = browser(app)
    for i in range(challenges.MAX_PENDING_BY_KIND['assertion']):
        challenges.STORE.issue(kind='assertion', rp_id='localhost', origin=ORIGIN, owner_handle=HANDLE,
                               epoch=0, session_nonce='n', label='', purpose='add', client=f'c{i}')
    r = start(c)                                                  # passcode path: nothing enrolled yet
    assert r.status_code == 200, r.get_data(as_text=True)


def test_a_full_registration_bucket_does_not_block_assertions(app, authn):
    c = browser(app)
    a = authn()
    enroll(c, a)
    for i in range(challenges.MAX_PENDING_BY_KIND['registration']):
        challenges.STORE.issue(kind='registration', rp_id='localhost', origin=ORIGIN, owner_handle=HANDLE,
                               epoch=0, session_nonce='n', label='', client=f'c{i}')
    assert call(c, 'post', ASSERT, {'purpose': 'add'}).status_code == 200


def test_an_assertion_is_single_use_and_bound_to_one_operation(app, authn):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    proof = prove(c, a, 'add')
    assert call(c, 'post', OPTIONS, {'proof': proof}).status_code == 200
    replay = call(c, 'post', OPTIONS, {'proof': proof})
    assert replay.status_code == 400 and replay.get_json()['error'] == 'unknown_or_used_ceremony'
    # an 'add' approval cannot revoke, and a revoke approval cannot revoke another credential
    path = f"/api/passkeys/{cred['id']}"
    add_proof = prove(c, a, 'add')
    r = call(c, 'delete', path, {'proof': add_proof})
    assert r.status_code == 403 and r.get_json()['error'] == 'operation_mismatch'
    assert passkey_ids() == [cred['id']]
    revoke_proof = prove(c, a, 'revoke', cred['id'])
    other = call(c, 'post', ASSERT, {'purpose': 'revoke', 'credential_id': 'nope'})
    assert other.status_code == 404                           # cannot even ask for a stranger
    assert call(c, 'delete', path, {'proof': revoke_proof}).status_code == 200


def test_a_revoke_approval_for_one_credential_does_not_revoke_another(app, authn):
    c = browser(app)
    a, b = authn(), authn()
    _j, ca, _f = enroll(c, a)
    _j, cb, _f = enroll(c, b, by=a)
    proof_for_b = prove(c, a, 'revoke', cb['id'])
    r = call(c, 'delete', f"/api/passkeys/{ca['id']}", {'proof': proof_for_b})
    assert r.status_code == 403 and r.get_json()['error'] == 'operation_mismatch'
    assert sorted(passkey_ids()) == sorted([ca['id'], cb['id']])


def test_an_assertion_from_another_browser_session_is_refused(app, authn):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    proof = prove(c, a, 'revoke', cred['id'])
    other = browser(app)                            # no assertion-ceremony cookie
    r = call(other, 'delete', f"/api/passkeys/{cred['id']}", {'proof': proof})
    assert r.status_code == 403 and r.get_json()['error'] == 'session_mismatch'
    assert passkey_ids() == [cred['id']]            # and the ceremony is spent for the real browser too
    retry = call(c, 'delete', f"/api/passkeys/{cred['id']}", {'proof': proof})
    assert retry.status_code == 400 and passkey_ids() == [cred['id']]


def test_an_expired_assertion_ceremony_is_refused(app, authn, monkeypatch):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    proof = prove(c, a, 'revoke', cred['id'])
    now = time.time()
    monkeypatch.setattr(challenges, '_clock', lambda: now + challenges.CHALLENGE_TTL_S + 5)
    r = call(c, 'delete', f"/api/passkeys/{cred['id']}", {'proof': proof})
    assert r.status_code == 410 and passkey_ids() == [cred['id']]


@pytest.mark.parametrize('name,kw', [
    ('no user verification', {'uv': False}),
    ('no user presence', {'up': False}),
    ('wrong rp id', {'rp_id': 'evil.example'}),
    ('wrong ceremony type', {'ceremony_type': 'webauthn.create'}),
    ('wrong challenge', {'challenge': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'}),
    ('bad signature', {'bad_signature': True}),
    ('cross origin frame', {'cross_origin': True, 'top_origin': 'http://evil.example'}),
    ('other origin', {'origin': 'http://localhost:6000'}),
])
def test_invalid_assertions_are_rejected_and_never_fall_back_to_the_passcode(app, authn, name, kw):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    r = call(c, 'delete', f"/api/passkeys/{cred['id']}",
             {'proof': prove(c, a, 'revoke', cred['id'], **kw), 'passcode': PASSCODE})
    assert r.status_code == 403 and r.get_json()['error'] == 'invalid_assertion', name
    assert passkey_ids() == [cred['id']], name
    assert local_auth._LOCAL_AUTH_FAILS == {}


def test_a_stranger_key_under_a_registered_credential_id_is_rejected(app, authn):
    from cryptography.hazmat.primitives.asymmetric import ec
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    r = call(c, 'delete', f"/api/passkeys/{cred['id']}",
             {'proof': prove(c, a, 'revoke', cred['id'], key=ec.generate_private_key(ec.SECP256R1()))})
    assert r.status_code == 403 and passkey_ids() == [cred['id']]


def test_an_unregistered_authenticator_cannot_approve(app, authn):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    r = call(c, 'delete', f"/api/passkeys/{cred['id']}",
             {'proof': prove(c, authn(), 'revoke', cred['id'])})
    assert r.status_code == 403 and r.get_json()['error'] == 'invalid_assertion'
    assert passkey_ids() == [cred['id']]


def test_the_assertion_counter_must_advance_once_it_has_started(app, authn):
    c = browser(app)
    a = authn()
    _j, ca, _f = enroll(c, a)
    _j, cb, _f = enroll(c, authn(), by=a)
    assert call(c, 'post', OPTIONS, {'proof': prove(c, a, 'add', sign_count=5)}).status_code == 200
    stale = call(c, 'post', OPTIONS, {'proof': prove(c, a, 'add', sign_count=5)})
    assert stale.status_code == 403 and stale.get_json()['error'] == 'invalid_assertion'
    assert call(c, 'post', OPTIONS, {'proof': prove(c, a, 'add', sign_count=6)}).status_code == 200
    row = next(r for r in call(c, 'get', '/api/passkeys').get_json()['credentials'] if r['id'] == ca['id'])
    assert row['last_used_at']


def test_assert_options_validates_its_request_and_the_host(app, authn):
    c = browser(app)
    assert call(c, 'post', ASSERT, {'purpose': 'add'}).status_code == 409   # nothing enrolled yet
    a = authn()
    _j, cred, _f = enroll(c, a)
    assert call(c, 'post', ASSERT, {'purpose': 'nope'}).status_code == 400
    assert call(c, 'post', ASSERT, {'purpose': 'revoke'}).status_code == 400
    assert call(c, 'post', ASSERT, {'purpose': 'revoke', 'credential_id': '../x'}).status_code == 400
    lan = browser(app, addr='192.168.1.50')
    assert call(lan, 'post', ASSERT, {'purpose': 'add'}).status_code == 403
    r = call(c, 'post', ASSERT, {'purpose': 'add'})
    opts = r.get_json()['options']
    assert opts['userVerification'] == 'required' and opts['rpId'] == 'localhost'
    assert [x['id'] for x in opts['allowCredentials']] == [cred['id']]


def test_a_revoke_while_the_vault_is_locked_reports_unavailable_not_proof_required(app, authn, monkeypatch):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    proof = prove(c, a, 'revoke', cred['id'])
    FakeVault(monkeypatch, state='locked')
    r = call(c, 'delete', f"/api/passkeys/{cred['id']}", {'proof': proof})
    assert r.status_code == 503 and r.get_json()['error'] == 'passkey_vault_locked'
    listing = call(c, 'get', '/api/passkeys')
    assert listing.status_code == 503 and listing.get_json()['reason'] == 'vault_locked'


# ── lost-all recovery (decision 3) ───────────────────────────────────────────

RECOVER = '/api/passkeys/recover'


def test_recover_revokes_every_passkey_with_the_passcode_and_confirmation(app, authn):
    c = browser(app)
    a, b = authn(), authn()
    enroll(c, a)
    enroll(c, b, by=a)
    pending = call(c, 'post', ASSERT, {'purpose': 'add'}).get_json()
    assert call(c, 'post', RECOVER, {'passcode': PASSCODE}).get_json()['error'] == 'bad_confirm'
    assert len(passkey_ids()) == 2
    r = call(c, 'post', RECOVER, {'passcode': PASSCODE, 'confirm': 'revoke-all-passkeys'})
    assert r.status_code == 200 and r.get_json()['revoked'] == 2
    assert passkey_ids() == []
    assert challenges.STORE.clear() == 0
    # pending ceremonies are gone, and the passcode enrolls again
    assert call(c, 'post', OPTIONS, {'passcode': PASSCODE, 'label': 'New'}).status_code == 200
    ops = [(e['operation'], e['outcome']) for e in
           (json.loads(x) for x in audit.audit_path().read_text(encoding='utf-8').splitlines())]
    assert ('recover', 'ok') in ops
    assert pending['ceremony_id']                             # issued before, dropped by the recovery


def test_recover_needs_the_right_passcode_and_the_host(app, authn):
    c = browser(app)
    enroll(c, authn())
    body = {'passcode': 'wrong-wrong', 'confirm': 'revoke-all-passkeys'}
    assert call(c, 'post', RECOVER, body).status_code in (401, 403)
    assert len(passkey_ids()) == 1
    lan = browser(app, addr='192.168.1.50')
    r = call(lan, 'post', RECOVER, {'passcode': PASSCODE, 'confirm': 'revoke-all-passkeys'})
    assert r.status_code == 403 and len(passkey_ids()) == 1
    tunnel = call(c, 'post', RECOVER, {'passcode': PASSCODE, 'confirm': 'revoke-all-passkeys'},
                  headers={'Origin': 'https://x.example', 'X-Forwarded-For': '1.2.3.4'})
    assert tunnel.status_code == 403 and len(passkey_ids()) == 1


def test_recover_with_nothing_enrolled_is_a_409_that_spends_no_guess(app):
    c = browser(app)
    r = call(c, 'post', RECOVER, {'passcode': 'wrong-wrong', 'confirm': 'revoke-all-passkeys'})
    assert r.status_code == 409 and local_auth._LOCAL_AUTH_FAILS == {}


# ── 3. host reset of an unusable registry ────────────────────────────────────

RESET = '/api/passkeys/reset'
RESET_BODY = {'passcode': PASSCODE, 'confirm': 'reset-passkey-registry'}


def _crash_state():
    add('AAAA')
    store.marker_path().unlink()                              # registry written, marker not


def test_reset_quarantines_the_crash_state_and_clears_ceremonies(app, authn):
    c = browser(app)
    _crash_state()
    challenges.STORE.issue(kind='registration', rp_id='localhost', origin=ORIGIN,
                           owner_handle=HANDLE, epoch=0, session_nonce='n', label='x')
    assert call(c, 'get', '/api/passkeys').get_json()['resettable'] is True
    assert start(c).status_code == 503                        # nothing can enroll either
    r = call(c, 'post', RESET, RESET_BODY)
    j = r.get_json()
    assert r.status_code == 200 and j['moved'] == ['registry.json']
    qdir = store.passkeys_dir() / j['quarantine']
    assert (qdir / 'registry.json').is_file()                 # moved, not deleted
    assert not store.registry_path().exists() and challenges.STORE.clear() == 0
    assert call(c, 'get', '/api/passkeys').get_json()['credentials'] == []
    assert enroll(c, authn())[2].status_code == 200           # passcode bootstrap works again


def test_reset_clears_a_tampered_registry_and_its_tombstones(app, authn):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    call(c, 'delete', f"/api/passkeys/{cred['id']}", {'proof': prove(c, a, 'revoke', cred['id'])})
    reg = read(store.registry_path())
    reg['policy_epoch'] = 0
    write(store.registry_path(), reg)
    assert call(c, 'get', '/api/passkeys').get_json()['error'] == 'passkey_store_tampered'
    r = call(c, 'post', RESET, RESET_BODY)
    assert r.status_code == 200 and sorted(r.get_json()['moved']) == sorted(
        [store.registry_path().name, store.marker_path().name])
    assert call(c, 'get', '/api/passkeys').status_code == 200


def test_reset_refuses_a_healthy_registry(app, authn):
    c = browser(app)
    a = authn()
    _j, cred, _f = enroll(c, a)
    r = call(c, 'post', RESET, RESET_BODY)
    assert r.status_code == 409 and r.get_json()['error'] == 'reset_not_needed'
    assert local_auth._LOCAL_AUTH_FAILS == {} and passkey_ids() == [cred['id']]
    with pytest.raises(recovery.NotResettable):
        recovery.reset_unusable()
    assert passkey_ids() == [cred['id']]


def test_reset_needs_host_passcode_and_confirmation(app):
    c = browser(app)
    _crash_state()
    assert call(c, 'post', RESET, {'passcode': PASSCODE}).get_json()['error'] == 'bad_confirm'
    assert call(c, 'post', RESET, dict(RESET_BODY, passcode='wrong-wrong')).status_code in (401, 403)
    lan = browser(app, addr='192.168.1.50')
    assert call(lan, 'post', RESET, RESET_BODY).status_code == 403
    assert call(c, 'post', RESET, RESET_BODY, headers={'Origin': 'https://x.example'}).status_code == 403
    assert store.registry_path().exists()                     # still untouched


def test_reset_with_a_locked_vault_reports_locked_and_moves_nothing(app, monkeypatch):
    c = browser(app)
    add('AAAA')                                               # a healthy, signed registry
    FakeVault(monkeypatch, state='locked')
    r = call(c, 'post', RESET, RESET_BODY)
    assert r.status_code == 503 and r.get_json()['error'] == 'passkey_vault_locked'
    assert store.registry_path().exists() and store.marker_path().exists()


def test_two_resets_in_a_row_do_not_collide_on_the_quarantine_dir(_home):
    _crash_state()
    first = recovery.reset_unusable()
    _crash_state()
    second = recovery.reset_unusable()
    assert first['quarantine'] != second['quarantine']
    assert (store.passkeys_dir() / first['quarantine'] / 'registry.json').is_file()
    assert (store.passkeys_dir() / second['quarantine'] / 'registry.json').is_file()
