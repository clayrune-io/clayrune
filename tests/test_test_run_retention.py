"""MC-1179 SF30 part B: finished test runs are deleted after the retention window."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

from mc import test_run_log as tail_log
from mc import test_run_retention as retention
from mc import test_run_store as runs

DAY = 24 * 60 * 60
NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return runs.TestRunStore(tmp_path / 'data')


@pytest.fixture
def logs(monkeypatch):
    lines = []
    monkeypatch.setattr(retention, '_log', lambda message, **kw: lines.append(message))
    return lines


def age(store, project, run_id, days):
    """Backdate a run file as if it last changed ``days`` ago."""
    path = store._path(project, run_id)
    then = NOW - days * DAY
    os.utime(path, (then, then))
    return path


def finished(store, days_ago, project='p', state='passed'):
    run = store.create(project, observed_at=NOW - days_ago * DAY - 60)
    store.apply(project, run['run_id'], dict(
        schema=1, run_id=run['run_id'], producer_id='r', producer_seq=1,
        observed_at=NOW - days_ago * DAY - 30, type='heartbeat', payload={'output': True}))
    store.finish(project, run['run_id'], state=state, exit_code=0 if state == 'passed' else 1,
                 observed_at=NOW - days_ago * DAY)
    age(store, project, run['run_id'], days_ago)
    return run['run_id']


def active(store, days_ago, project='p'):
    run = store.create(project, observed_at=NOW - days_ago * DAY)
    age(store, project, run['run_id'], days_ago)
    return run['run_id']


def left(store, project='p'):
    return {p.stem for p in store._path(project, '0' * 32).parent.glob('*.json')}


def test_an_old_finished_run_goes_and_a_younger_one_stays(store, logs):
    old, edge, young = finished(store, 15), finished(store, 13.9), finished(store, 1)
    result = retention.sweep(store, 14, now=NOW)
    assert (result.runs, result.failed) == (1, 0) and result.bytes > 0
    assert left(store) == {edge, young} and old not in left(store)


@pytest.mark.parametrize('days', [15, 400])
def test_an_active_run_is_never_deleted_by_age(store, days):
    stuck = active(store, days)
    finished(store, days)
    retention.sweep(store, 14, now=NOW)
    assert left(store) == {stuck}


@pytest.mark.parametrize('state', sorted(runs.TERMINAL_STATES))
def test_every_terminal_state_ages_out(store, state):
    finished(store, 20, state=state if state != 'passed' else 'failed')
    retention.sweep(store, 14, now=NOW)
    assert left(store) == set()


def test_zero_keeps_everything(store):
    ids = {finished(store, 900), finished(store, 15), active(store, 900)}
    result = retention.sweep(store, 0, now=NOW)
    assert (result.runs, result.bytes) == (0, 0)
    assert left(store) == ids


def test_a_delete_failure_is_logged_and_the_sweep_goes_on(store, logs, monkeypatch):
    monkeypatch.setattr(retention, '_now', lambda: NOW)
    _first, second, _third = (finished(store, 30) for _ in range(3))
    refused = store._path('p', second)
    real = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == refused:
            raise PermissionError('held open by another program')
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    result = retention.run_sweep(store, {'test_run_retention_days': 14})
    assert result.failed == 1 and result.runs == 2
    assert left(store) == {second}
    assert any('could not remove' in line and second in line for line in logs)
    # one summary line, and it says how many runs and bytes went
    summary = [line for line in logs if 'removed' in line and 'could not' not in line]
    assert len(summary) == 1 and '2 run(s)' in summary[0] and f'{result.bytes} bytes' in summary[0]
    # the next sweep, with the file free again, removes it
    monkeypatch.undo()
    monkeypatch.setattr(retention, '_now', lambda: NOW)
    assert retention.sweep(store, 14).runs == 1
    assert left(store) == set()


def test_a_recent_file_is_not_even_opened(store, monkeypatch):
    young = finished(store, 2)
    opened = []
    real = runs.TestRunStore.expire
    monkeypatch.setattr(runs.TestRunStore, 'expire',
                        lambda self, path, cutoff: (opened.append(path.stem), real(self, path, cutoff))[1])
    retention.sweep(store, 14, now=NOW)
    assert opened == [] and left(store) == {young}


def test_an_old_file_that_is_still_open_is_kept_whatever_its_age(store):
    stuck = active(store, 60)
    store.apply('p', stuck, dict(schema=1, run_id=stuck, producer_id='r', producer_seq=1,
                                 observed_at=NOW - 60 * DAY + 1, type='heartbeat', payload={'output': True}))
    os.utime(tail_log.log_path(store._path('p', stuck)), (NOW - 60 * DAY, NOW - 60 * DAY))
    age(store, 'p', stuck, 60)
    retention.sweep(store, 14, now=NOW)
    assert left(store) == {stuck}
    assert tail_log.log_path(store._path('p', stuck)).exists()


def test_old_format_and_tail_files_are_deleted_together_and_nothing_else_is_touched(store, monkeypatch):
    # a finished run left a tail behind (a crash after compaction), and an
    # unrelated file sits in the folder
    rid = finished(store, 30)
    path = store._path('p', rid)
    leftover = tail_log.log_path(path)
    leftover.write_bytes(b'{"x":1}\n')
    other = path.parent / 'notes.txt'
    other.write_text('keep me')
    survivor = finished(store, 1)
    before = (store._path('p', survivor).read_bytes(),)
    result = retention.sweep(store, 14, now=NOW)
    assert result.runs == 1
    assert not path.exists() and not leftover.exists()
    assert other.read_text() == 'keep me'
    assert (store._path('p', survivor).read_bytes(),) == before


def test_sweep_spans_every_project_and_trims_the_ledger(store):
    a, b = finished(store, 30, project='p'), finished(store, 30, project='q')
    keep = finished(store, 1, project='p')
    ledger = store._path('p', '0' * 32).parent / '_validated.jsonl'
    assert ledger.exists()
    retention.sweep(store, 14, now=NOW)
    assert left(store, 'p') == {keep} and left(store, 'q') == set()
    assert [json.loads(line)['i'] for line in ledger.read_text().splitlines()] == [keep]
    assert a not in ledger.read_text() and b not in ledger.read_text()


def test_a_missing_root_is_nothing_to_do(tmp_path):
    result = retention.sweep(runs.TestRunStore(tmp_path / 'nothing'), 14, now=NOW)
    assert (result.runs, result.bytes, result.failed) == (0, 0, 0)


def test_a_corrupt_old_file_is_logged_and_kept(store, logs):
    good = finished(store, 30)
    bad = finished(store, 30)
    store._path('p', bad).write_text('{broken', encoding='utf-8')
    age(store, 'p', bad, 30)
    result = retention.sweep(store, 14, now=NOW)
    assert (result.runs, result.failed) == (1, 1)
    assert left(store) == {bad}


@pytest.mark.parametrize('config,days', [
    ({}, 14), ({'test_run_retention_days': 3}, 3), ({'test_run_retention_days': 0}, 0),
    ({'test_run_retention_days': -1}, 14), ({'test_run_retention_days': '7'}, 14),
    ({'test_run_retention_days': True}, 14), ({'test_run_retention_days': 2.5}, 14),
    ({'test_run_retention_days': None}, 14)])
def test_setting_defaults_to_fourteen_and_a_bad_value_never_means_delete_everything(config, days):
    assert retention.retention_days(config) == days


def test_the_server_default_is_fourteen():
    source = Path(runs.__file__).resolve().parents[1].joinpath('server.py').read_text(encoding='utf-8')
    assert "'test_run_retention_days': 14," in source


def test_run_sweep_logs_one_line_and_disabled_logs_none(store, logs, monkeypatch):
    monkeypatch.setattr(retention, '_now', lambda: NOW)
    finished(store, 30)
    finished(store, 1)
    retention.run_sweep(store, {'test_run_retention_days': 14})
    assert len(logs) == 1 and logs[0].startswith('[test-run-retention] removed 1 run(s),')
    logs.clear()
    retention.run_sweep(store, {'test_run_retention_days': 0})
    assert logs == []


def test_start_sweeps_now_off_the_calling_thread_and_then_waits(store, logs, monkeypatch):
    monkeypatch.setattr(retention, '_now', lambda: NOW)
    old = finished(store, 30)
    caller = threading.get_ident()
    seen = []
    real = retention.run_sweep

    def run_sweep(target, config=None):
        seen.append(threading.get_ident())
        return real(target, {'test_run_retention_days': 14})
    monkeypatch.setattr(retention, 'run_sweep', run_sweep)
    stop = threading.Event()
    thread = retention.start(lambda: store, interval=3600, stop=stop)
    deadline = time.time() + 5
    while old in left(store) and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(5)
    assert old not in left(store)
    assert seen and seen[0] != caller and len(seen) == 1  # the daily wait, not a spin
    assert not thread.is_alive()


def test_an_unreadable_store_does_not_kill_the_sweeper(logs, monkeypatch):
    stop = threading.Event()
    calls = []

    def storage():
        calls.append(1)
        stop.set() if len(calls) == 2 else None
        raise RuntimeError('not wired')
    thread = retention.start(storage, interval=0.01, stop=stop)
    thread.join(5)
    assert len(calls) == 2
    assert any('sweep failed' in line for line in logs)
