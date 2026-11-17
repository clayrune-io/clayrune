"""MC-1179 SF30 part A: an open run is appended to, not rewritten.

Pins the cost of a write, the equivalence of the incremental reducer with the
whole-history replay, the torn-tail and legacy-file behaviours, and that a
restart does not replay every finished file.
"""
import hashlib
import json
import os
import sys

import pytest

from mc import test_run_log as tail_log
from mc import test_run_store as runs
from mc.test_run_ledger import ValidatedLedger
from mc.test_run_listing import ListIndex
from mc.test_run_open import OpenRegistry
from mc.test_run_snapshots import SnapshotCache
from mc.test_run_terminal import TerminalIndex


@pytest.fixture
def store(tmp_path):
    return runs.TestRunStore(tmp_path / 'data')


def restart(monkeypatch):
    """A new server process over the same files: nothing in memory survives."""
    monkeypatch.setattr(runs, '_OPEN', OpenRegistry())
    monkeypatch.setattr(runs, '_LEDGER', ValidatedLedger())
    monkeypatch.setattr(runs, '_SNAPSHOTS', SnapshotCache())
    monkeypatch.setattr(runs, '_TERMINAL', TerminalIndex())
    monkeypatch.setattr(runs, '_LIST', ListIndex())


def ev(run_id, seq, kind, at=None, producer='reporter', **payload):
    return dict(schema=1, run_id=run_id, producer_id=producer, producer_seq=seq,
                observed_at=at if at is not None else 100 + seq / 1000, type=kind, payload=payload)


def history(run_id, units=1000):
    """A deterministic, awkward history: revised totals, retries, split phases,
    failures in every phase, out-of-order reports, more diagnostics than the
    snapshot keeps, and a duplicate of an earlier event."""
    events, seq = [], 0

    def add(kind, **payload):
        nonlocal seq
        seq += 1
        events.append(ev(run_id, seq, kind, **payload))

    add('run_started', reporter_version='1.0')
    add('collection', selected_total=units, revision=1, deselected=2, collection_errors=0)
    for i in range(units):
        name = f'tests/test_mod_{i % 37}.py::test_{i}'
        add('unit_started', unit_id=name, attempt=1)
        bad = i % 11 == 0
        add('unit_report', unit_id=name, attempt=1, phase='setup', outcome='pass', final=False, revision=1)
        add('unit_report', unit_id=name, attempt=1, phase='call',
            outcome='fail' if bad else 'pass', final=False, revision=1)
        add('unit_report', unit_id=name, attempt=1, phase='teardown',
            outcome='error' if i % 53 == 0 else 'pass', final=True, revision=1)
        if i % 17 == 0:
            add('unit_started', unit_id=name, attempt=2)
            add('unit_report', unit_id=name, attempt=2, phase='call', outcome='pass', final=True, revision=1)
        if i % 9 == 0:
            add('heartbeat', output=True)
        if i % 5 == 0:
            add('diagnostic', code=f'note_{i % 7}', capture='partial')
        if i == units // 2:
            add('collection', selected_total=units + 5, revision=2, deselected=2, collection_errors=0)
    add('unit_report', unit_id='late', attempt=1, phase='call', outcome='skip', final=True, revision=1)
    events.append(dict(events[10]))  # an exact repeat is a no-op
    return events


GOLDEN_OPEN = '43477cd9cadd95a6b5264acefd2fdf34dd7175b4137877955f644701bc528f15'
GOLDEN_DONE = '86ff2cd147e80397e6ca450f507a881f00b8ff18c845ce2d2cbc00c99552b2c6'


def digest(snapshot):
    # run_id is random per run; everything else is a function of the history
    body = {k: v for k, v in snapshot.items() if k != 'run_id'}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def enrollment(store, run):
    return json.loads(store._path(run['project_id'], run['run_id']).read_text())['enrollment']


def feed(store, run, events):
    for event in events:
        store.apply(run['project_id'], run['run_id'], event)


def report(rid, n):
    return ev(rid, n, 'unit_report', unit_id=f'u{n}', attempt=1, phase='call',
              outcome='pass', final=True, revision=1)


def test_several_thousand_events_round_trip_to_todays_reducer_snapshot(store, monkeypatch):
    run = store.create('p', observed_at=100, owner_session_id='s')
    events = history(run['run_id'])
    assert len(events) > 4000
    feed(store, run, events)
    live = store.get('p', run['run_id'])
    # The same events replayed whole, and the digest of what the reducer on
    # master produced for this history before SF30 (frozen, not recomputed).
    assert live == runs.replay(enrollment(store, run), store.events('p', run['run_id']))
    assert digest(live) == GOLDEN_OPEN
    assert live['failure_total'] > 50 and live['diagnostics_omitted'] > 0
    # A restarted server rebuilds the identical state from the files alone.
    restart(monkeypatch)
    assert store.get('p', run['run_id']) == live
    assert len(store.events('p', run['run_id'])) == len(events) - 1  # the repeat was a no-op
    done = store.finish('p', run['run_id'], state='passed', exit_code=0, observed_at=500)
    assert done['state'] == 'failed'  # fail closed: a "pass" over known failures
    assert digest(done) == GOLDEN_DONE
    # Ending the run folds the tail into the one record, as it always was.
    path = store._path('p', run['run_id'])
    assert not tail_log.log_path(path).exists()
    restart(monkeypatch)
    assert store.get('p', run['run_id']) == done
    assert len(store.events('p', run['run_id'])) == len(events)


class IOCount:
    """File opens under the run folder and bytes handed to the OS for writing."""

    def __init__(self, monkeypatch, folder):
        self.opens, self.written, self.fsyncs = [], 0, 0
        self.folder = str(folder)
        real_write, real_fsync = os.write, os.fsync

        def write(fd, data):
            self.written += len(data)
            return real_write(fd, data)

        def fsync(fd):
            self.fsyncs += 1
            return real_fsync(fd)

        monkeypatch.setattr(os, 'write', write)
        monkeypatch.setattr(os, 'fsync', fsync)
        self.live = True
        sys.addaudithook(self.audit)

    def audit(self, event, args):
        if self.live and event == 'open' and str(args[0]).startswith(self.folder):
            self.opens.append((str(args[0]), args[1]))

    def stop(self):
        self.live = False  # audit hooks cannot be removed; this one goes quiet


def test_the_thousandth_batch_touches_only_its_own_bytes(store, monkeypatch):
    run = store.create('p', observed_at=100)
    rid = run['run_id']
    path = store._path('p', rid)
    record_before = path.read_bytes()
    events = [report(rid, n) for n in range(1, 1001)]
    for event in events[:999]:
        store.apply('p', rid, event)
    own = len((runs._json(events[999]) + '\n').encode())
    seen = IOCount(monkeypatch, path.parent)
    try:
        store.apply('p', rid, events[999])
    finally:
        seen.stop()
    # One open (the tail), exactly the batch's own bytes, one fsync; the record
    # is neither re-read nor rewritten, however long the run already is.
    assert [name for name, _ in seen.opens] == [str(tail_log.log_path(path))]
    assert seen.written == own
    assert seen.fsyncs == 1
    assert path.read_bytes() == record_before
    assert tail_log.log_path(path).stat().st_size > 999 * 100  # the earlier lines are all there
    assert store.get('p', rid)['completed'] == 1000


def test_cost_of_a_batch_does_not_grow_with_the_run(store, monkeypatch):
    run = store.create('p', observed_at=100)
    rid = run['run_id']
    path = store._path('p', rid)

    def one(n):
        seen = IOCount(monkeypatch, path.parent)
        try:
            store.apply('p', rid, report(rid, n))
        finally:
            seen.stop()
        return len(seen.opens), seen.written
    first = one(1)
    for n in range(2, 1500):
        store.apply('p', rid, report(rid, n))
    last = one(1500)
    assert first[0] == last[0] == 1
    assert abs(first[1] - last[1]) < 16  # only the digits of the sequence number differ


def tail_events(store, run, count):
    feed(store, run, [report(run['run_id'], n) for n in range(1, count + 1)])
    return tail_log.log_path(store._path(run['project_id'], run['run_id']))


def test_a_truncated_last_line_loads_and_keeps_every_complete_event(store, monkeypatch):
    run = store.create('p', observed_at=100)
    rid = run['run_id']
    log = tail_events(store, run, 5)
    whole = log.read_bytes()
    log.write_bytes(whole + b'{"schema":1,"run_id":"' + rid.encode() + b'","prod')  # torn, no newline
    restart(monkeypatch)
    snapshot = store.get('p', rid)
    assert snapshot['completed'] == 5 and snapshot['seq'] == 5
    assert len(store.events('p', rid)) == 5
    # The next write cuts the torn bytes away first, so nothing is glued to them.
    store.apply('p', rid, ev(rid, 6, 'unit_report', unit_id='u6', attempt=1,
                             phase='call', outcome='fail', final=True, revision=1))
    assert log.read_bytes().startswith(whole) and log.read_bytes().endswith(b'\n')
    assert log.read_bytes().count(b'\n') == 6
    restart(monkeypatch)
    assert store.get('p', rid)['seq'] == 6
    done = store.finish('p', rid, state='failed', exit_code=1, observed_at=200)
    assert len(store.events('p', rid)) == 7 and done['counts']['fail'] == 1


def test_a_complete_line_that_is_not_an_event_is_corruption_not_a_torn_write(store, monkeypatch):
    run = store.create('p', observed_at=100)
    log = tail_events(store, run, 2)
    log.write_bytes(log.read_bytes() + b'[1]\n')
    restart(monkeypatch)
    with pytest.raises(runs.StoreError):
        store.get('p', run['run_id'])


def test_a_crash_after_compaction_before_the_tail_is_removed_is_harmless(store, monkeypatch):
    run = store.create('p', observed_at=100)
    log = tail_events(store, run, 3)
    leftover = log.read_bytes()
    done = store.finish('p', run['run_id'], state='passed', exit_code=0, observed_at=200)
    assert not log.exists()
    log.write_bytes(leftover)  # the crash window: record is terminal, tail still there
    restart(monkeypatch)
    assert store.get('p', run['run_id']) == done
    assert len(store.events('p', run['run_id'])) == 4


def legacy_file(store, project, observed_at, count=0, terminal=None):
    """A record exactly as the pre-SF30 store wrote it: events inline, no tail."""
    run = store.create(project, observed_at=observed_at)
    rid = run['run_id']
    path = store._path(project, rid)
    enr = json.loads(path.read_text())['enrollment']
    evs = [report(rid, n) for n in range(1, count + 1)]
    if terminal:
        evs.append(ev(rid, 1, 'run_finished', producer='_store_process', state=terminal, exit_code=0))
    path.write_text(json.dumps({'schema': 1, 'enrollment': enr, 'events': evs,
                                'snapshot': runs.replay(enr, evs)}), encoding='utf-8')
    return rid, path


def test_an_old_format_file_loads_unchanged_and_is_not_rewritten(store, monkeypatch):
    rid, path = legacy_file(store, 'p', 100, count=1)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    restart(monkeypatch)
    assert store.get('p', rid)['completed'] == 1
    assert len(store.events('p', rid)) == 1
    assert [r['run_id'] for r in store.list_runs('p')] == [rid]
    assert [r['run_id'] for r in store.active_snapshots('p')] == [rid]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not tail_log.log_path(path).exists()
    # Writing to it appends; the old record is still not rewritten until it ends.
    store.apply('p', rid, report(rid, 2))
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert store.get('p', rid)['completed'] == 2
    done = store.finish('p', rid, state='passed', exit_code=0, observed_at=300)
    assert done['completed'] == 2 and len(store.events('p', rid)) == 3
    restart(monkeypatch)
    assert store.get('p', rid) == done


def test_a_finished_old_format_file_is_never_touched_by_reads(store, monkeypatch):
    rid, path = legacy_file(store, 'p', 100, terminal='passed')
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    restart(monkeypatch)
    assert store.get('p', rid)['state'] == 'passed'
    assert store.active_snapshots('p') == []
    assert store.list_runs('p')[0]['run_id'] == rid
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def finished_runs(store, count):
    ids = []
    for n in range(count):
        run = store.create('p', observed_at=100 + n)
        tail_events(store, run, 3)
        store.finish('p', run['run_id'], state='passed', exit_code=0, observed_at=200 + n)
        ids.append(run['run_id'])
    return ids


def count_replays(monkeypatch):
    calls = []
    original = runs.TestRunStore._reduce

    def reduce(record, path):
        calls.append(path.stem)
        return original(record, path)
    monkeypatch.setattr(runs.TestRunStore, '_reduce', staticmethod(reduce))
    return calls


def test_a_restart_does_not_replay_every_finished_file(store, monkeypatch):
    finished_runs(store, 12)
    active = store.create('p', observed_at=999)
    restart(monkeypatch)
    calls = count_replays(monkeypatch)
    seen = store.active_snapshots('p')
    assert [r['run_id'] for r in seen] == [active['run_id']]
    # Only the file that is not finished is replayed; the 12 finished ones are
    # trusted from the ledger by their fingerprint.
    assert calls == [active['run_id']]
    assert len(store.list_runs('p')) == 13
    assert calls == [active['run_id']]


def test_without_a_ledger_each_finished_file_is_replayed_once_and_then_trusted(store, monkeypatch):
    ids = finished_runs(store, 6)
    folder = store._path('p', '0' * 32).parent
    (folder / '_validated.jsonl').unlink()
    restart(monkeypatch)
    calls = count_replays(monkeypatch)
    assert store.active_snapshots('p') == []
    assert sorted(calls) == sorted(ids)
    restart(monkeypatch)
    calls.clear()
    assert store.active_snapshots('p') == []
    assert calls == []


def test_a_changed_finished_file_is_validated_again(store, monkeypatch):
    ids = finished_runs(store, 3)
    path = store._path('p', ids[0])
    record = json.loads(path.read_text())
    record['snapshot']['completed'] = 99
    path.write_text(json.dumps(record), encoding='utf-8')
    restart(monkeypatch)
    with pytest.raises(runs.StoreError):
        store.active_snapshots('p')
