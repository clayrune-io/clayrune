"""Paged snapshots preserve full-read semantics without copying all history."""
import copy
import json
from pathlib import Path

import pytest

from mc import test_run_store as runs
from mc.test_run_listing import ListIndex
from mc.test_run_snapshots import SnapshotCache
from mc.test_run_terminal import TerminalIndex


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, '_LIST', ListIndex())
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache())
    monkeypatch.setattr(runs, '_TERMINAL', TerminalIndex())
    return runs.TestRunStore(tmp_path)


def full_read(store, pid, owner, limit, offset):
    # The old implementation, deliberately independent of both new indexes.
    snapshots = [copy.deepcopy(store._read(pid, p.stem)['snapshot'])
                 for p in store._path(pid, '0' * 32).parent.glob('*.json')]
    snapshots.sort(key=lambda s: (s['created_at'], s['run_id']), reverse=True)
    if owner is not None:
        snapshots = [s for s in snapshots if s['owner_session_id'] == owner]
    return snapshots[offset:offset + limit]


@pytest.mark.parametrize('owner', [None, 'alice', 'bob', 'missing'])
def test_mixed_fixture_matches_old_order_filter_and_offset(store, owner):
    for i, at in enumerate([5, 2, 5, 1, 9, 9, 2, 4, 6, 6, 0, 8]):
        run = store.create('a', observed_at=at,
                           owner_session_id=[None, 'alice', 'bob'][i % 3],
                           display_label=f'mixed {i}')
        if i % 2:
            store.finish('a', run['run_id'], state='passed', exit_code=0, observed_at=20)
        else:
            store.apply('a', run['run_id'], dict(schema=1, run_id=run['run_id'],
                producer_id='reporter', producer_seq=1, observed_at=20,
                type='collection', payload={'selected_total': i, 'revision': 1}))
    store.create('b', observed_at=100, owner_session_id='alice')
    for offset in [0, 1, 3, 11, 12, 100]:
        for limit in [1, 3, 100]:
            expected = full_read(store, 'a', owner, limit, offset)
            assert store.list_runs('a', owner_session_id=owner, limit=limit, offset=offset) == expected
            assert store.list_runs('a', owner_session_id=owner, limit=limit, offset=offset) == expected


@pytest.mark.parametrize('capacity', [0, 2])
def test_warm_list_only_snapshots_page_beyond_cache_capacity(store, monkeypatch, capacity):
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache(max_entries=capacity))
    records = [store.create('a', observed_at=i, owner_session_id=str(i % 2)) for i in range(9)]
    expected = [records[6], records[4]]
    assert store.list_runs('a', owner_session_id='0', limit=2, offset=1) == expected
    assert len(runs._LIST.entries) == 9 > runs._SNAPSHOTS.max_entries
    calls = []
    original = store._snapshot

    def snapshot(pid, rid, **kwargs):
        calls.append(rid)
        return original(pid, rid, **kwargs)

    monkeypatch.setattr(store, '_snapshot', snapshot)
    for _ in range(3):
        calls.clear()
        assert store.list_runs('a', owner_session_id='0', limit=2, offset=1) == expected
        assert calls == [r['run_id'] for r in expected]
    assert len(runs._SNAPSHOTS.entries) <= capacity


def test_missing_and_changed_off_page_metadata_require_validation(store, monkeypatch):
    records = [store.create('a', observed_at=i) for i in range(4)]
    store.list_runs('a', limit=1)
    path = store._path('a', records[0]['run_id'])
    runs._LIST.discard(path)
    # Even with a validated snapshot resident, missing metadata rereads its
    # canonical file instead of silently relying on another cache's admission.
    store.get('a', records[0]['run_id'])
    runs._LIST.discard(path)
    reads = []
    original = store._read

    def read(pid, rid):
        reads.append(rid)
        return original(pid, rid)

    monkeypatch.setattr(store, '_read', read)
    assert store.list_runs('a', limit=1) == [records[-1]]
    assert reads == [records[0]['run_id']]
    replacement = json.loads(path.read_text())
    replacement['enrollment'].update(created_at=99, owner_session_id='changed')
    replacement['snapshot'] = runs.replay(replacement['enrollment'], replacement['events'])
    path.write_text(json.dumps(replacement), encoding='utf-8')
    reads.clear()
    assert store.list_runs('a', owner_session_id='changed', limit=1) == [replacement['snapshot']]
    # Metadata validation and page materialization can independently read the
    # changed file; unchanged files must not be revalidated.
    assert set(reads) == {records[0]['run_id']}


def test_cold_list_admits_and_copies_only_returned_snapshots(store, monkeypatch):
    records = [store.create('a', observed_at=i) for i in range(7)]
    calls = []
    original = store._snapshot

    def snapshot(pid, rid, **kwargs):
        calls.append(rid)
        return original(pid, rid, **kwargs)

    monkeypatch.setattr(store, '_snapshot', snapshot)
    assert store.list_runs('a', limit=2, offset=1) == [records[5], records[4]]
    assert calls == [records[5]['run_id'], records[4]['run_id']]
    assert set(runs._SNAPSHOTS.entries) == {store._path('a', rid) for rid in calls}
    assert len(runs._LIST.entries) == 7


def test_owner_replacement_between_index_and_page_cannot_escape_filter(store, monkeypatch):
    record = store.create('a', observed_at=1, owner_session_id='alice')
    path = store._path('a', record['run_id'])
    store.list_runs('a')
    original = store._snapshot

    def snapshot(pid, rid, **kwargs):
        value = json.loads(path.read_text())
        value['enrollment']['owner_session_id'] = 'bob'
        value['snapshot'] = runs.replay(value['enrollment'], value['events'])
        path.write_text(json.dumps(value), encoding='utf-8')
        return original(pid, rid, **kwargs)

    monkeypatch.setattr(store, '_snapshot', snapshot)
    assert store.list_runs('a', owner_session_id='alice') == []


@pytest.mark.parametrize('fault', ['json', 'replay', 'identity', 'missing_owner',
                                  'invalid_owner', 'invalid_created_at', 'stat', 'read'])
def test_off_page_fault_never_becomes_empty_or_successful_list(store, monkeypatch, fault):
    old = store.create('a', observed_at=1)
    store.create('a', observed_at=2)
    store.list_runs('a', limit=1)
    path = store._path('a', old['run_id'])
    if fault in {'json', 'replay', 'identity', 'missing_owner', 'invalid_owner', 'invalid_created_at'}:
        record = json.loads(path.read_text())
        if fault == 'replay':
            record['snapshot']['completed'] = 99
        elif fault == 'identity':
            record['enrollment']['project_id'] = 'b'
        elif fault == 'missing_owner':
            record['enrollment'].pop('owner_session_id')
            record['snapshot'].pop('owner_session_id')
        elif fault == 'invalid_owner':
            record['enrollment']['owner_session_id'] = []
            record['snapshot']['owner_session_id'] = []
        elif fault == 'invalid_created_at':
            record['enrollment']['created_at'] = 'bad'
            record['snapshot']['created_at'] = 'bad'
        path.write_text('{' if fault == 'json' else json.dumps(record), encoding='utf-8')
    elif fault == 'stat':
        original = Path.stat

        def stat(target, *args, **kwargs):
            if target == path:
                raise PermissionError('stat refused')
            return original(target, *args, **kwargs)

        monkeypatch.setattr(Path, 'stat', stat)
    else:
        runs._LIST.discard(path)
        runs._SNAPSHOTS.discard(path)
        original = store._read

        def read(pid, rid):
            if rid == old['run_id']:
                raise runs.StoreError('Unreadable test run')
            return original(pid, rid)

        monkeypatch.setattr(store, '_read', read)
    with pytest.raises(runs.StoreError):
        store.list_runs('a', limit=1)


@pytest.mark.parametrize('stage', ['stat', 'metadata_read', 'page'])
def test_vanished_files_are_skipped_and_page_is_filled(store, monkeypatch, stage):
    older = store.create('a', observed_at=1)
    vanished = store.create('a', observed_at=2)
    store.list_runs('a')
    path = store._path('a', vanished['run_id'])
    if stage == 'stat':
        original = store._fingerprint

        def fingerprint(target):
            if target == path and target.exists():
                target.unlink()
            return original(target)

        monkeypatch.setattr(store, '_fingerprint', fingerprint)
    elif stage == 'metadata_read':
        runs._LIST.discard(path)
        original = store._read

        def read(pid, rid):
            if rid == vanished['run_id'] and path.exists():
                path.unlink()
            return original(pid, rid)

        monkeypatch.setattr(store, '_read', read)
    else:
        original = store._snapshot

        def snapshot(pid, rid, **kwargs):
            if rid == vanished['run_id'] and path.exists():
                path.unlink()
            return original(pid, rid, **kwargs)

        monkeypatch.setattr(store, '_snapshot', snapshot)
    assert store.list_runs('a', limit=1) == [older]
    assert path not in runs._LIST.entries


def test_returned_mutation_and_data_root_isolation(store, tmp_path):
    record = store.create('a', observed_at=1)
    value = store.list_runs('a')
    value[0]['counts']['pass'] = 999
    assert store.list_runs('a') == [record]
    assert runs.TestRunStore(tmp_path / 'other').list_runs('a') == []


@pytest.mark.parametrize('operation', ['create', 'append', 'failed_append'])
def test_writes_invalidate_index_even_with_reused_fingerprint(store, monkeypatch, operation):
    record = store.create('a', observed_at=1)
    path = store._path('a', record['run_id'])
    monkeypatch.setattr(store, '_fingerprint', lambda _: (1, 1))
    store.list_runs('a')
    assert path in runs._LIST.entries
    if operation == 'create':
        class SameID:
            hex = record['run_id']
        monkeypatch.setattr(runs.uuid, 'uuid4', SameID)
        expected = store.create('a', observed_at=3, owner_session_id='new')
    else:
        event = dict(schema=1, run_id=record['run_id'], producer_id='r', producer_seq=1,
                     observed_at=2, type='collection', payload={'selected_total': 3, 'revision': 1})
        if operation == 'failed_append':
            def fail(*args, **kwargs):
                raise OSError('write refused')
            with monkeypatch.context() as patch:
                patch.setattr(runs, 'write_json_atomic', fail)
                with pytest.raises(OSError):
                    store.apply('a', record['run_id'], event)
            expected = record
        else:
            expected = store.apply('a', record['run_id'], event)
    assert path not in runs._LIST.entries
    assert store.list_runs('a') == [expected]


def test_replacement_during_validation_is_not_indexed_with_new_fingerprint(store, monkeypatch):
    record = store.create('a', observed_at=1)
    path = store._path('a', record['run_id'])
    original = store._read

    def read(pid, rid):
        value = original(pid, rid)
        replacement = copy.deepcopy(value)
        replacement['enrollment']['owner_session_id'] = 'replacement'
        replacement['snapshot'] = runs.replay(replacement['enrollment'], replacement['events'])
        path.write_text(json.dumps(replacement), encoding='utf-8')
        return value

    with monkeypatch.context() as patch:
        patch.setattr(store, '_read', read)
        assert store.list_runs('a', owner_session_id='absent') == []
    assert path not in runs._LIST.entries
    assert store.list_runs('a', owner_session_id='replacement')[0]['run_id'] == record['run_id']
