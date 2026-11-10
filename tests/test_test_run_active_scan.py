"""History beyond snapshot capacity must not turn every Floor poll cold."""
import json
from pathlib import Path

import pytest

from mc import test_run_store as runs
from mc.test_run_snapshots import SnapshotCache
from mc.test_run_terminal import TerminalIndex


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache(max_entries=2))
    monkeypatch.setattr(runs, '_TERMINAL', TerminalIndex())
    return runs.TestRunStore(tmp_path)


def watch_reads(store, monkeypatch):
    reads = []
    original = store._read

    def read(pid, rid):
        reads.append(rid)
        return original(pid, rid)

    monkeypatch.setattr(store, '_read', read)
    return reads


@pytest.mark.parametrize('budget', ['entries', 'bytes'])
def test_bulk_scan_preserves_warm_residents_beyond_capacity(store, monkeypatch, budget):
    records = [store.create('a', observed_at=i) for i in range(7)]
    if budget == 'bytes':
        store.get('a', records[0]['run_id'])
        size = runs._SNAPSHOTS.bytes
        monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache(max_entries=100, max_bytes=size * 2))
    reads = watch_reads(store, monkeypatch)
    assert store.all_snapshots('a') == records[::-1]
    assert len(reads) == 7
    residents = set(runs._SNAPSHOTS.entries)
    assert 0 < len(residents) < len(records)
    for _ in range(3):
        reads.clear()
        assert store.all_snapshots('a') == records[::-1]
        assert len(reads) == len(records) - len(residents)
        assert set(runs._SNAPSHOTS.entries) == residents


def test_floor_warm_scan_skips_terminal_history_beyond_lru_cap(store, monkeypatch):
    records = [store.create('a', observed_at=i) for i in range(9)]
    for record in records:
        store.finish('a', record['run_id'], state='passed', exit_code=0, observed_at=20)
    active = store.create('a', observed_at=30)
    reads = watch_reads(store, monkeypatch)
    assert store.active_snapshots('a') == [active]
    assert len(reads) == 10
    assert len(runs._TERMINAL.entries) == 9 > runs._SNAPSHOTS.max_entries
    assert set(runs._SNAPSHOTS.entries) == {store._path('a', active['run_id'])}
    # Evict every snapshot, including active, without losing terminal knowledge.
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache(max_entries=2))
    reads.clear()
    for _ in range(3):
        assert store.active_snapshots('a') == [active]
    assert reads == [active['run_id']]
    # The public APIs still include/read finished records and return copies.
    assert len(store.list_runs('a')) == 10
    assert store.get('a', records[0]['run_id'])['state'] == 'passed'


def test_terminal_markers_work_when_snapshot_cache_is_disabled(store, monkeypatch):
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache(max_entries=0, max_bytes=0))
    for i in range(5):
        record = store.create('a', observed_at=i)
        store.finish('a', record['run_id'], state='passed', exit_code=0, observed_at=10)
    reads = watch_reads(store, monkeypatch)
    assert store.active_snapshots('a') == []
    assert len(reads) == 5
    reads.clear()
    assert store.active_snapshots('a') == []
    assert reads == []
    assert len(runs._TERMINAL.entries) == 5
    assert not runs._SNAPSHOTS.entries


@pytest.mark.parametrize('state', sorted(runs.TERMINAL_STATES))
def test_terminal_append_never_persists_late_events(store, state):
    record = store.create('a', observed_at=1)
    rid = record['run_id']
    terminal = store.finish('a', rid, state=state, exit_code=0, observed_at=2)
    path = store._path('a', rid)
    before, stat = path.read_bytes(), path.stat()
    assert store.apply('a', rid, dict(schema=1, run_id=rid, producer_id='late',
        producer_seq=1, observed_at=3, type='heartbeat', payload={'output': True})) == terminal
    assert store.finish('a', rid, state='error', observed_at=4) == terminal
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stat.st_mtime_ns
    assert len(store.events('a', rid)) == 1


@pytest.mark.parametrize('change', ['corrupt', 'active', 'new_terminal'])
def test_terminal_marker_revalidates_external_replacement(store, change):
    record = store.create('a', observed_at=1)
    rid = record['run_id']
    path = store._path('a', rid)
    original = path.read_text()
    store.finish('a', rid, state='passed', exit_code=0, observed_at=2)
    assert store.active_snapshots('a') == []
    if change == 'corrupt':
        path.write_text('{', encoding='utf-8')
        with pytest.raises(runs.StoreError):
            store.active_snapshots('a')
        assert path not in runs._TERMINAL.entries
    elif change == 'active':
        path.write_text(original, encoding='utf-8')
        assert store.active_snapshots('a') == [record]
        assert path not in runs._TERMINAL.entries
    else:
        replacement = json.loads(path.read_text())
        replacement['enrollment']['display_label'] = 'replacement terminal'
        replacement['snapshot']['display_label'] = 'replacement terminal'
        path.write_text(json.dumps(replacement), encoding='utf-8')
        assert store.active_snapshots('a') == []
        assert runs._TERMINAL.entries[path] == store._fingerprint(path)


def test_create_invalidates_terminal_marker_with_reused_fingerprint(store, monkeypatch):
    record = store.create('a', observed_at=1)
    store.finish('a', record['run_id'], state='passed', exit_code=0, observed_at=2)
    monkeypatch.setattr(store, '_fingerprint', lambda _: (1, 1))
    assert store.active_snapshots('a') == []

    class SameID:
        hex = record['run_id']

    monkeypatch.setattr(runs.uuid, 'uuid4', SameID)
    recreated = store.create('a', observed_at=3)
    assert store.active_snapshots('a') == [recreated]


@pytest.mark.parametrize('active_only', [False, True])
@pytest.mark.parametrize('stage', ['stat', 'read'])
def test_listing_skips_disappearance_after_glob(store, monkeypatch, active_only, stage):
    vanished = store.create('a', observed_at=1)
    retained = store.create('a', observed_at=2)
    path = store._path('a', vanished['run_id'])
    original = store._fingerprint if stage == 'stat' else store._read

    def disappear(*args):
        if (args[0] == path if stage == 'stat' else args[1] == vanished['run_id']):
            if path.exists():
                path.unlink()
        return original(*args)

    monkeypatch.setattr(store, '_fingerprint' if stage == 'stat' else '_read', disappear)
    assert store.all_snapshots('a', active_only=active_only) == [retained]


@pytest.mark.parametrize('active_only', [False, True])
def test_unreadable_file_still_fails_listing(store, monkeypatch, active_only):
    record = store.create('a', observed_at=1)
    path = store._path('a', record['run_id'])
    original = Path.stat

    def refused(target, *args, **kwargs):
        if target == path:
            raise PermissionError('stat refused')
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', refused)
    with pytest.raises(runs.StoreError, match='Unreadable'):
        store.all_snapshots('a', active_only=active_only)
