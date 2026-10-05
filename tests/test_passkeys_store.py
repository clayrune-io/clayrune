"""Credential registry (mc/passkeys/store.py): fail-closed reads, tombstones,
concurrent writes, restart. Every test runs against its own CLAYRUNE_HOME."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mc.passkeys import challenges, store

HANDLE = 'aGFuZGxlLWhhbmRsZS1oYW5kbGUtaGFuZGxlLWhhbmRsZQ'


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / 'home'))
    return tmp_path / 'home'


def add(cid='AAAA', **kw):
    args = dict(credential_id=cid, public_key='cHVi', rp_id='localhost',
                origin='http://localhost:5199', transports=['internal'], sign_count=0,
                backup_eligible=False, backup_state=False, aaguid='0' * 8,
                label='Laptop', owner_handle=HANDLE, epoch=0)
    args.update(kw)
    return store.add_credential(**args)


def test_store_lives_under_clayrune_home_never_the_repo(_home):
    add()
    assert store.registry_path() == _home / 'passkeys' / 'registry.json'
    assert store.registry_path().is_file() and store.marker_path().is_file()
    repo = Path(__file__).resolve().parent.parent
    assert repo not in store.registry_path().parents


def test_fresh_install_reads_as_empty_and_writes_nothing(_home):
    assert store.list_credentials() == []
    assert store.status() == {'enrolled': False, 'active_count': 0, 'policy_epoch': 0}
    assert not (_home / 'passkeys').exists()


def test_list_is_metadata_only(_home):
    add()
    row, = store.list_credentials()
    assert set(row) == {'id', 'label', 'rp_id', 'created_at', 'last_used_at', 'revoked_at',
                        'transports', 'backup_eligible', 'backup_state'}
    assert 'cHVi' not in json.dumps(row)


def test_duplicate_credential_id_is_refused_and_leaves_one_row():
    add('AAAA')
    with pytest.raises(store.DuplicateCredential):
        add('AAAA', label='again')
    assert len(store.list_credentials()) == 1


def test_a_second_owner_handle_is_refused():
    add('AAAA')
    with pytest.raises(store.OwnerHandleMismatch):
        add('BBBB', owner_handle='b3RoZXItaGFuZGxl')
    assert [c['id'] for c in store.list_credentials()] == ['AAAA']


def test_cap_on_active_credentials():
    for i in range(store.MAX_ACTIVE_CREDENTIALS):
        add(f'id{i}')
    with pytest.raises(store.TooManyCredentials):
        add('one-too-many')
    store.revoke('id0')
    add('fits-now', epoch=1)   # a revoked credential frees a slot; its tombstone stays


def test_revocation_leaves_a_tombstone_and_bumps_the_epoch():
    add('AAAA')
    add('BBBB')
    out = store.revoke('AAAA')
    assert out['revoked_at']
    state = store.load()
    assert state['policy_epoch'] == 1
    tomb = next(c for c in state['credentials'] if c['id'] == 'AAAA')
    assert tomb['public_key'] is None and tomb['transports'] == []
    assert store.active_credential_ids() == ['BBBB']


def test_revoked_id_cannot_be_registered_again():
    add('AAAA')
    store.revoke('AAAA')
    with pytest.raises(store.DuplicateCredential):
        add('AAAA', epoch=1)


def test_revoking_twice_or_an_unknown_id_is_an_error_not_a_success():
    add('AAAA')
    store.revoke('AAAA')
    with pytest.raises(store.UnknownCredential):
        store.revoke('AAAA')
    with pytest.raises(store.UnknownCredential):
        store.revoke('nope')


def test_enrollment_marker_survives_revoking_everything():
    add('AAAA')
    store.revoke('AAAA')
    assert store.status() == {'enrolled': True, 'active_count': 0, 'policy_epoch': 1}


def test_a_revocation_between_options_and_finish_invalidates_the_ceremony():
    add('AAAA')
    started_at_epoch = store.status()['policy_epoch']
    store.revoke('AAAA')
    with pytest.raises(store.PolicyChanged):
        add('BBBB', epoch=started_at_epoch)
    assert store.active_credential_ids() == []


# ── fail closed ──────────────────────────────────────────────────────────────

def corrupt(text):
    store.registry_path().write_text(text, encoding='utf-8')


@pytest.mark.parametrize('text', [
    '', '{', 'null', '[]', '"str"', '{"schema": 1}',
    '{"schema": 2, "owner_handle": null, "enrolled_at": null, "policy_epoch": 0, "credentials": []}',
    '{"schema": 1, "policy_epoch": 0, "credentials": {}}',
    '{"schema": 1, "policy_epoch": -1, "credentials": []}',
    '{"schema": 1, "policy_epoch": 0, "credentials": [1]}',
])
def test_a_damaged_registry_is_unavailable_never_empty(text):
    add('AAAA')
    corrupt(text)
    for call in (store.load, store.list_credentials, store.status, store.active_credential_ids):
        with pytest.raises(store.StoreCorrupt):
            call()
    with pytest.raises(store.StoreCorrupt):
        add('BBBB')
    with pytest.raises(store.StoreCorrupt):
        store.revoke('AAAA')


def test_a_deleted_registry_with_a_marker_is_unavailable_not_empty():
    add('AAAA')
    store.registry_path().unlink()
    with pytest.raises(store.StoreCorrupt):
        store.list_credentials()


def test_a_registry_rolled_back_below_the_marker_epoch_is_unavailable():
    add('AAAA')
    old = store.registry_path().read_text(encoding='utf-8')
    store.revoke('AAAA')                      # epoch 1, marker follows
    store.registry_path().write_text(old, encoding='utf-8')   # restore the pre-revocation file
    with pytest.raises(store.StoreCorrupt):
        store.list_credentials()


def test_a_registry_swapped_for_another_owner_is_unavailable():
    add('AAAA')
    state = json.loads(store.registry_path().read_text(encoding='utf-8'))
    state['owner_handle'] = 'b3RoZXItb3duZXI'
    corrupt(json.dumps(state))
    with pytest.raises(store.StoreCorrupt):
        store.load()


def test_credentials_without_a_marker_are_unavailable():
    add('AAAA')
    store.marker_path().unlink()
    with pytest.raises(store.StoreCorrupt):
        store.load()


def test_a_corrupt_marker_is_unavailable():
    add('AAAA')
    store.marker_path().write_text('{not json', encoding='utf-8')
    with pytest.raises(store.StoreCorrupt):
        store.load()


def test_an_active_credential_without_a_public_key_is_unavailable():
    add('AAAA')
    state = json.loads(store.registry_path().read_text(encoding='utf-8'))
    state['credentials'][0]['public_key'] = None
    corrupt(json.dumps(state))
    with pytest.raises(store.StoreCorrupt):
        store.load()


def test_a_failed_write_never_damages_the_existing_registry(monkeypatch):
    add('AAAA')
    before = store.registry_path().read_text(encoding='utf-8')

    def boom(*_a, **_k):
        raise OSError('disk full')
    with monkeypatch.context() as m:
        m.setattr(store, 'write_json_atomic', boom)
        with pytest.raises(OSError):
            add('BBBB')
    assert store.registry_path().read_text(encoding='utf-8') == before
    assert store.active_credential_ids() == ['AAAA']


# ── concurrency and restart ──────────────────────────────────────────────────

def test_concurrent_enrollments_all_land_exactly_once():
    n = 8
    errors, start = [], threading.Barrier(n)

    def worker(i):
        start.wait()
        try:
            add(f'cred{i}', label=f'dev{i}')
        except Exception as e:      # pragma: no cover - failure path is the assertion
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(store.active_credential_ids()) == sorted(f'cred{i}' for i in range(n))
    assert not store._lock_path().exists()          # no leaked lock file


def test_concurrent_add_of_the_same_id_has_one_winner():
    n, outcomes, start = 6, [], threading.Barrier(6)

    def worker():
        start.wait()
        try:
            add('same')
            outcomes.append('ok')
        except store.DuplicateCredential:
            outcomes.append('dup')

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count('ok') == 1 and outcomes.count('dup') == n - 1
    assert store.active_credential_ids() == ['same']


def test_concurrent_revoke_and_add_keep_the_registry_valid():
    for i in range(4):
        add(f'seed{i}')
    start = threading.Barrier(8)

    def revoker(i):
        start.wait()
        store.revoke(f'seed{i}')

    def adder(i):
        start.wait()
        try:
            add(f'new{i}', epoch=store.status()['policy_epoch'])
        except store.PolicyChanged:
            pass

    threads = [threading.Thread(target=revoker, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=adder, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state = store.load()                      # valid, not corrupt
    assert state['policy_epoch'] == 4
    assert not any(c['id'].startswith('seed') for c in state['credentials'] if not c.get('revoked_at'))


def test_a_stale_cross_process_lock_is_broken(monkeypatch):
    add('AAAA')
    store.passkeys_dir().mkdir(parents=True, exist_ok=True)
    store._lock_path().write_text('held', encoding='utf-8')
    monkeypatch.setattr(store, '_LOCK_TIMEOUT_S', 0.05)
    import os
    import time
    old = time.time() - 60
    os.utime(store._lock_path(), (old, old))
    add('BBBB')                               # would hang or raise if the lock were honoured
    assert sorted(store.active_credential_ids()) == ['AAAA', 'BBBB']


def test_a_live_cross_process_lock_blocks_then_reports_busy(monkeypatch):
    add('AAAA')
    store._lock_path().write_text('held', encoding='utf-8')
    monkeypatch.setattr(store, '_LOCK_TIMEOUT_S', 0.2)
    # fresh mtime: not stale, so the writer waits out the timeout and refuses
    monkeypatch.setattr(store.time, 'time', lambda: store._lock_path().stat().st_mtime)
    with pytest.raises(store.StoreError, match='busy'):
        add('BBBB')
    assert store.active_credential_ids() == ['AAAA']


def test_restart_keeps_credentials_and_drops_pending_ceremonies():
    add('AAAA')
    pending = challenges.STORE
    c = pending.issue(kind='registration', rp_id='localhost', origin='http://localhost:5199',
                      owner_handle=HANDLE, epoch=0, session_nonce='n', label='x')
    # A restart is a new process: the registry is re-read from disk, and the
    # in-memory ceremony table starts empty.
    fresh = challenges.ChallengeStore()
    with pytest.raises(challenges.UnknownCeremony):
        fresh.consume(c.id, kind='registration', session_nonce='n')
    assert store.active_credential_ids() == ['AAAA']
    pending.clear()
