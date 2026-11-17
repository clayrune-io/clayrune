"""Snapshot cache correctness and lock boundaries, using only temporary records."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import threading

import pytest

from mc import test_run_store as runs
from mc.test_run_snapshots import SnapshotCache
from mc.test_run_terminal import TerminalIndex
from tests.run_store_cold import cold


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache())
    monkeypatch.setattr(runs, '_TERMINAL', TerminalIndex())
    cold(monkeypatch)
    return runs.TestRunStore(tmp_path)


def test_unchanged_files_reuse_validation_across_store_instances(store, monkeypatch):
    snapshots = [store.create('a', observed_at=i) for i in range(3)]
    reads = []
    original = runs.TestRunStore._read

    def read(self, pid, rid):
        reads.append(rid)
        return original(self, pid, rid)

    monkeypatch.setattr(runs.TestRunStore, '_read', read)
    assert store.all_snapshots('a') == snapshots[::-1]
    assert len(reads) == 3
    another = runs.TestRunStore(store.root.parent)
    assert another.list_runs('a', limit=1) == snapshots[-1:]
    assert another.get('a', snapshots[0]['run_id']) == snapshots[0]
    assert len(reads) == 3
    # Canonical history and write validation still read/replay full records.
    assert another.events('a', snapshots[0]['run_id']) == []
    assert len(reads) == 4


@pytest.mark.parametrize('changed', ['mtime', 'size'])
def test_changed_file_revalidates_even_if_other_fingerprint_field_is_unchanged(store, monkeypatch, changed):
    run = store.create('a', observed_at=1, display_label='before')
    path = store._path('a', run['run_id'])
    assert store.get('a', run['run_id']) == run
    stat = path.stat()
    label = 'after!' if changed == 'mtime' else 'a longer label'
    # Preserve original JSON formatting/length when only the mtime changes.
    content = path.read_text().replace('before', label)
    path.write_text(content, encoding='utf-8')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000
                       if changed == 'mtime' else stat.st_mtime_ns))
    reads = []
    original = store._read

    def read(pid, rid):
        reads.append(rid)
        return original(pid, rid)

    monkeypatch.setattr(store, '_read', read)
    assert store.get('a', run['run_id'])['display_label'] == label
    assert reads == [run['run_id']]
    assert store.all_snapshots('a')[0]['display_label'] == label
    assert len(reads) == 1


@pytest.mark.parametrize('content', ['{', '[]', 'tampered_snapshot'])
@pytest.mark.parametrize('method', ['get', 'list_runs', 'all_snapshots'])
def test_corruption_after_cache_hit_raises_store_error(store, content, method):
    run = store.create('a', observed_at=1)
    store.get('a', run['run_id'])
    path = store._path('a', run['run_id'])
    if content == 'tampered_snapshot':
        record = json.loads(path.read_text())
        record['snapshot']['completed'] = 999
        content = json.dumps(record)
    path.write_text(content, encoding='utf-8')
    with pytest.raises(runs.StoreError):
        getattr(store, method)('a', *([run['run_id']] if method == 'get' else []))
    assert path.read_text() == content


def test_append_and_finish_invalidate_even_when_stat_fingerprint_is_reused(store, monkeypatch):
    run = store.create('a', observed_at=1)
    monkeypatch.setattr(store, '_fingerprint', lambda _: (1, 1))
    store.get('a', run['run_id'])
    other = runs.TestRunStore(store.root.parent)
    updated = other.apply('a', run['run_id'], dict(
        schema=1, run_id=run['run_id'], producer_id='reporter', producer_seq=1,
        observed_at=2, type='collection', payload=dict(selected_total=3, revision=1)))
    assert store.get('a', run['run_id']) == updated
    terminal = other.finish('a', run['run_id'], state='passed', exit_code=0, observed_at=3)
    assert store.get('a', run['run_id']) == terminal


def test_create_invalidates_an_existing_path_even_with_reused_fingerprint(store, monkeypatch):
    run = store.create('a', observed_at=1)
    monkeypatch.setattr(store, '_fingerprint', lambda _: (1, 1))
    store.get('a', run['run_id'])

    class SameID:
        hex = run['run_id']

    monkeypatch.setattr(runs.uuid, 'uuid4', SameID)
    recreated = store.create('a', observed_at=2)
    assert store.get('a', run['run_id']) == recreated


def test_new_files_deletion_and_returned_mutation_are_observed(store):
    first = store.create('a', observed_at=1)
    listed = store.all_snapshots('a')
    listed[0]['counts']['pass'] = 123
    second = store.create('a', observed_at=2)
    assert store.all_snapshots('a') == [second, first]
    store._path('a', first['run_id']).unlink()
    assert store.all_snapshots('a') == [second]
    with pytest.raises(KeyError):
        store.get('a', first['run_id'])


def test_failed_append_does_not_publish_an_uncommitted_snapshot(store, monkeypatch):
    run = store.create('a', observed_at=1)
    assert store.get('a', run['run_id']) == run

    def fail(*args, **kwargs):
        raise OSError('write refused')

    with monkeypatch.context() as patch:
        patch.setattr(runs.tail_log, 'append', fail)
        with pytest.raises(OSError, match='write refused'):
            store.apply('a', run['run_id'], dict(
                schema=1, run_id=run['run_id'], producer_id='reporter', producer_seq=1,
                observed_at=2, type='collection', payload=dict(selected_total=3, revision=1)))
    assert store.get('a', run['run_id']) == run


def test_stat_error_never_serves_a_cached_snapshot(store, monkeypatch):
    run = store.create('a', observed_at=1)
    store.get('a', run['run_id'])
    path = store._path('a', run['run_id'])
    original = Path.stat

    def stat(target, *args, **kwargs):
        if target == path:
            raise PermissionError('stat refused')
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat)
    with pytest.raises(runs.StoreError, match='Unreadable'):
        store.get('a', run['run_id'])
    assert path not in runs._SNAPSHOTS.entries


def test_cache_isolates_data_roots_and_projects(store, tmp_path):
    first = store.create('a', observed_at=1)
    store.all_snapshots('a')
    other = runs.TestRunStore(tmp_path / 'other')
    assert other.list_runs('a') == []
    with pytest.raises(KeyError):
        other.get('a', first['run_id'])
    assert store.list_runs('b') == []


def test_lru_eviction_and_byte_budget_keep_only_snapshots(store, monkeypatch):
    cache = SnapshotCache(max_entries=2)
    monkeypatch.setattr(runs, '_SNAPSHOTS', cache)
    records = [store.create('a', observed_at=i) for i in range(3)]
    paths = [store._path('a', r['run_id']) for r in records]
    for record in records[:2]:
        store.get('a', record['run_id'])
    store.get('a', records[0]['run_id'])
    store.get('a', records[2]['run_id'])
    assert list(cache.entries) == [paths[0], paths[2]]
    assert all('events' not in entry[1] for entry in cache.entries.values())
    size = cache.entries[paths[0]][2]
    bounded = SnapshotCache(max_bytes=size, max_entries=100)
    bounded.put(paths[0], (1, 1), records[0])
    bounded.put(paths[1], (1, 1), records[1])
    assert bounded.bytes <= size and len(bounded.entries) <= 1
    bounded.put(paths[2], (1, 1), {**records[2], 'display_label': 'x' * (size + 1)})
    assert paths[2] not in bounded.entries
    cache.discard(paths[0])
    assert cache.bytes == sum(entry[2] for entry in cache.entries.values())


def test_external_replacement_during_validation_is_not_cached_under_new_stat(store, monkeypatch):
    run = store.create('a', observed_at=1)
    path = store._path('a', run['run_id'])
    original = store._read
    changed = False

    def read(pid, rid):
        nonlocal changed
        record = original(pid, rid)
        if not changed:
            changed = True
            replacement = json.loads(path.read_text())
            replacement['enrollment']['display_label'] = 'replacement'
            replacement['snapshot']['display_label'] = 'replacement'
            path.write_text(json.dumps(replacement), encoding='utf-8')
        return record

    monkeypatch.setattr(store, '_read', read)
    assert store.get('a', run['run_id']) == run
    assert path not in runs._SNAPSHOTS.entries
    assert store.get('a', run['run_id'])['display_label'] == 'replacement'


def test_directory_scan_does_not_block_a_submit(store, monkeypatch):
    run = store.create('a', observed_at=1)
    scanning, release = threading.Event(), threading.Event()
    original = Path.glob

    def glob(path, pattern):
        if path == store._path('a', run['run_id']).parent:
            scanning.set()
            assert release.wait(5), 'test did not release directory scan'
        yield from original(path, pattern)

    monkeypatch.setattr(Path, 'glob', glob)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(store.all_snapshots, 'a')
        try:
            assert scanning.wait(5)
            submit = pool.submit(store.create, 'a', observed_at=2)
            assert submit.result(timeout=2)['state'] == 'running'
        finally:
            release.set()
        assert len(scan.result(timeout=5)) == 2


def test_one_runs_lock_does_not_block_any_other_run(store):
    # MC-1179 SF30: the lock is per run file. While one run is held (as it is
    # during its own validation or append), writes and reads of every other run
    # - same project or another - go through.
    held, other = (store.create('a', observed_at=i) for i in range(2))
    foreign = store.create('b', observed_at=9)
    entered, release = threading.Event(), threading.Event()

    def hold():
        with store.locked('a', held['run_id']):
            entered.set()
            assert release.wait(5), 'test did not release the run lock'

    event = dict(schema=1, producer_id='r', producer_seq=1, observed_at=5,
                 type='heartbeat', payload={'output': True})
    with ThreadPoolExecutor(max_workers=4) as pool:
        holder = pool.submit(hold)
        try:
            assert entered.wait(5)
            applied = pool.submit(store.apply, 'a', other['run_id'], {**event, 'run_id': other['run_id']})
            created = pool.submit(store.create, 'b', observed_at=10)
            read = pool.submit(store.get, 'b', foreign['run_id'])
            assert applied.result(timeout=2)['seq'] == 1
            assert created.result(timeout=2)['state'] == 'running'
            assert read.result(timeout=2) == foreign
            blocked = pool.submit(store.get, 'a', held['run_id'])
            with pytest.raises(FutureTimeout):
                blocked.result(timeout=0.3)
        finally:
            release.set()
        holder.result(timeout=5)
        assert blocked.result(timeout=5) == held
