"""Enrolled capture must never acquire job execution/completion ownership."""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from mc import agent_jobs as jobs
from mc import test_run_capture as capture_module
from mc.test_run_capture import RunCapture
from mc.test_run_store import TestRunStore


def enrolled(tmp_path, store_type=TestRunStore, **kwargs):
    store = store_type(tmp_path / 'data')
    run = store.create('p', observed_at=time.time(), owner_session_id='s')
    return store, run['run_id'], RunCapture(store, 'p', run['run_id'], **kwargs)


def wait_until(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


class Process:
    pid = 123456  # Fake process: these tests never launch an OS child.

    def __init__(self, code=0):
        self.code = code
        self.exited = threading.Event()
        self.killed = 0

    def wait(self, timeout=None):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired('test job', timeout)
        return self.code

    def poll(self):
        return self.code if self.exited.is_set() else None

    def kill(self):
        self.killed += 1
        self.code = -9
        self.exited.set()


def job(tmp_path, capture, *, output=b'last output\n', code=0, direct=False,
        on_complete=None, timeout_minutes=1):
    proc = Process(code)
    calls = []
    done = threading.Event()
    spawn = {}

    def popen(argv, **kwargs):
        spawn.update(kwargs)
        if not direct:
            kwargs['stdout'].write(output)
            kwargs['stdout'].flush()
        return proc

    def complete(result):
        calls.append(result)
        if on_complete:
            on_complete(result)
        done.set()

    record = jobs.start_job(
        project_id='p', session_id='s', command='unchanged command', cwd=str(tmp_path),
        shell='cmd', timeout_minutes=timeout_minutes, log_dir=tmp_path / 'logs',
        capture=capture, popen=popen, on_complete=complete,
        direct_argv=['private-executable', 'private-argument'] if direct else None)
    return proc, record, calls, done, spawn


def test_normal_final_drain_is_persisted_before_exactly_once_callback(tmp_path):
    batches = []
    store, run, capture = enrolled(tmp_path, on_batch=batches.append)
    snapshots = []
    proc, record, calls, done, spawn = job(
        tmp_path, capture, output=b'first\nlast without newline', code=7,
        on_complete=lambda _: snapshots.append(store.get('p', run)))
    proc.exited.set()
    assert done.wait(4)
    assert snapshots[0]['state'] == 'failed'
    assert snapshots[0]['exit_code'] == 7
    assert [line for batch in batches for line in batch] == ['first', 'last without newline']
    assert snapshots[0]['last_output_at'] is not None
    assert len(calls) == 1
    assert calls[0]['tail'] == 'first\nlast without newline'
    assert calls[0]['command'] == 'unchanged command'
    assert spawn['shell'] is False
    assert spawn['stderr'] == subprocess.STDOUT
    assert spawn['stdout'].name == record['log_path']
    assert 'capture' not in jobs.public_job(record['job_id'])


def test_stalled_store_callback_at_two_seconds_and_late_finalization(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class StalledStore(TestRunStore):
        def finish(self, *args, **kwargs):
            entered.set()
            release.wait(8)
            return super().finish(*args, **kwargs)

    store, run, capture = enrolled(tmp_path, StalledStore)
    logs = []
    monkeypatch.setattr(capture_module, '_log', lambda message, **_: logs.append(message))
    proc, _, calls, done, _ = job(tmp_path, capture, code=23)
    started = time.monotonic()
    proc.exited.set()
    try:
        assert entered.wait(2)
        assert done.wait(4)
        elapsed = time.monotonic() - started
        assert 1.9 <= elapsed < 3.5
        assert store.get('p', run)['state'] == 'running'
        assert calls[0]['exit_code'] == 23
        assert any(run in message and 'elapsed=' in message for message in logs)
    finally:
        release.set()
    assert capture._done.wait(3)
    assert store.get('p', run)['state'] == 'failed'
    assert store.get('p', run)['exit_code'] == 23
    assert len(calls) == 1


def test_hung_consumer_callback_at_two_seconds_without_terminal_record(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    batches = []

    def blocked(batch):
        batches.append(batch)
        entered.set()
        release.wait(8)

    store, run, capture = enrolled(tmp_path, on_batch=blocked)
    proc, _, calls, done, _ = job(tmp_path, capture, output=b'first\nfinal without newline')
    try:
        assert entered.wait(2)
        started = time.monotonic()
        proc.exited.set()
        assert done.wait(4)
        assert 1.9 <= time.monotonic() - started < 3.5
        assert len(calls) == 1 and calls[0]['exit_code'] == 0
        assert store.get('p', run)['state'] == 'running'
        assert not capture._done.is_set()
    finally:
        release.set()  # Clean up our own thread after proving the hung state.
    assert capture._done.wait(3)
    assert len(calls) == 1
    assert [line for batch in batches for line in batch] == ['first', 'final without newline']
    snapshot = store.get('p', run)
    assert snapshot['state'] == 'passed' and snapshot['capture'] == 'complete'
    assert snapshot['diagnostics'] == []  # Wait expiry never marks partial capture.


def test_slow_consumer_uses_disk_backlog_without_losing_lines(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    batches = []

    def slow(batch):
        batches.append(batch)
        entered.set()
        release.wait(6)

    store, run, capture = enrolled(tmp_path, on_batch=slow)
    path = tmp_path / 'output.log'
    path.write_bytes(b'first\n')
    capture.start(path, suppressed=False)
    try:
        assert entered.wait(2)
        with path.open('ab') as stream:
            stream.write(b'x\n' * 200000)
        assert capture._exit.maxsize == 1
        assert capture._offset <= capture_module.READ_BYTES
        assert len(capture._partial) <= capture_module.MAX_LINE_CHARS
    finally:
        release.set()
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert capture._done.wait(4)
    assert sum(len(batch) for batch in batches) == 200001
    assert store.get('p', run)['state'] == 'passed'


def test_partial_utf8_across_chunks_and_unterminated_final_line(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module, 'READ_BYTES', 2)
    batches = []
    store, run, capture = enrolled(tmp_path, on_batch=batches.append)
    path = tmp_path / 'output.log'
    path.write_bytes('A\u20ac\U0001f9ea\r\nend\u00e9'.encode())
    capture.start(path, suppressed=False)
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert capture._done.is_set()
    assert [line for batch in batches for line in batch] == ['A\u20ac\U0001f9ea', 'end\u00e9']
    assert store.get('p', run)['state'] == 'passed'


def test_invalid_final_utf8_is_replaced(tmp_path):
    batches = []
    _, _, capture = enrolled(tmp_path, on_batch=batches.append)
    path = tmp_path / 'output.log'
    path.write_bytes(b'\xe2\x82')
    capture.start(path, suppressed=False)
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert batches == [('\ufffd',)]


@pytest.mark.parametrize('replacement', [b'new\n', b'new longer than previous content\n'])
def test_truncate_and_regrow_reset_decoder_and_report_loss(tmp_path, replacement):
    batches = []
    store, run, capture = enrolled(tmp_path, on_batch=batches.append)
    path = tmp_path / 'output.log'
    path.write_bytes(b'old\n\xe2')
    capture.start(path, suppressed=False)
    wait_until(lambda: capture._offset == 5)
    path.write_bytes(replacement)
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert capture._done.wait(3)
    assert [line for batch in batches for line in batch] == ['old', replacement.decode().strip()]
    snapshot = store.get('p', run)
    assert snapshot['capture'] == 'partial'
    assert [d['code'] for d in snapshot['diagnostics']] == ['source_reset']


def test_rotation_resets_generation(tmp_path):
    batches = []
    store, run, capture = enrolled(tmp_path, on_batch=batches.append)
    path = tmp_path / 'output.log'
    path.write_bytes(b'old\n')
    capture.start(path, suppressed=False)
    wait_until(lambda: capture._offset == 4)
    path.rename(tmp_path / 'old.log')
    path.write_bytes(b'new\n')
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert [line for batch in batches for line in batch] == ['old', 'new']
    assert store.get('p', run)['capture'] == 'partial'


def test_oversized_line_memory_is_bounded_and_loss_is_explicit(tmp_path):
    batches = []
    store, run, capture = enrolled(tmp_path, on_batch=batches.append)
    path = tmp_path / 'output.log'
    path.write_bytes(b'x' * 1000000 + b'\nlast')
    capture.start(path, suppressed=False)
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert capture._done.wait(3)
    assert [line for batch in batches for line in batch] == [
        'x' * capture_module.MAX_LINE_CHARS, 'last']
    snapshot = store.get('p', run)
    assert snapshot['capture'] == 'partial'
    assert [d['code'] for d in snapshot['diagnostics']] == ['line_truncated']


def test_three_thousand_lines_coalesce_instead_of_per_line_store_writes(tmp_path):
    store, run, capture = enrolled(tmp_path)
    path = tmp_path / 'output.log'
    path.write_bytes(b'case passed\n' * 3000)
    capture.start(path, suppressed=False)
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    events = store.events('p', run)
    assert [e['type'] for e in events] == ['run_started', 'heartbeat', 'run_finished']
    snapshot = store.get('p', run)
    assert snapshot['completed'] == 0 and snapshot['selected_total'] is None
    assert not any('case passed' in str(event) for event in events)


def test_interval_flush_while_process_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module, 'FLUSH_INTERVAL_SECONDS', 0.02)
    store, run, capture = enrolled(tmp_path)
    path = tmp_path / 'output.log'
    path.write_bytes(b'live output')
    capture.start(path, suppressed=False)
    wait_until(lambda: store.get('p', run)['last_output_at'] is not None)
    assert store.get('p', run)['state'] == 'running'
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())


def test_suppressed_direct_argv_preserves_devnull_and_never_calls_consumer(tmp_path):
    batches = []
    store, run, capture = enrolled(tmp_path, on_batch=batches.append)
    proc, record, calls, done, spawn = job(tmp_path, capture, direct=True)
    proc.exited.set()
    assert done.wait(3)
    assert spawn['stdout'] == subprocess.DEVNULL
    assert spawn['stderr'] == subprocess.DEVNULL
    assert Path(record['log_path']).read_bytes() == b''
    assert not batches and calls[0]['tail'] == ''
    snapshot = store.get('p', run)
    assert snapshot['state'] == 'passed' and snapshot['last_output_at'] is None
    assert snapshot['diagnostics'][0]['code'] == 'output_suppressed'


def test_capture_exception_does_not_change_success_or_callback_payload(tmp_path, monkeypatch):
    def broken(_):
        raise RuntimeError('private child output')

    store, run, capture = enrolled(tmp_path, on_batch=broken)
    logs = []
    monkeypatch.setattr(capture_module, '_log', lambda message, **_: logs.append(message))
    proc, _, calls, done, _ = job(tmp_path, capture)
    proc.exited.set()
    assert done.wait(3)
    assert len(calls) == 1 and calls[0]['exit_code'] == 0
    assert calls[0]['timed_out'] is False
    snapshot = store.get('p', run)
    assert snapshot['state'] == 'running' and snapshot['capture'] == 'partial'
    assert snapshot['diagnostics'][0]['code'] == 'capture_failed'
    assert logs and all('private child output' not in line for line in logs)


def test_capture_start_exception_does_not_change_job(tmp_path, monkeypatch):
    _, _, capture = enrolled(tmp_path)
    monkeypatch.setattr(capture, 'start', lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    proc, _, calls, done, _ = job(tmp_path, capture)
    proc.exited.set()
    assert done.wait(2)
    assert len(calls) == 1 and calls[0]['exit_code'] == 0


def test_completion_hook_exception_does_not_change_callback(tmp_path, monkeypatch):
    store, run, capture = enrolled(tmp_path)
    monkeypatch.setattr(capture, 'complete', lambda **kw: (_ for _ in ()).throw(OSError()))
    proc, _, calls, done, _ = job(tmp_path, capture)
    proc.exited.set()
    try:
        assert done.wait(2)
        assert len(calls) == 1 and calls[0]['exit_code'] == 0
        assert store.get('p', run)['state'] == 'running'
    finally:
        # Restore the hook to stop our own otherwise-running capture worker.
        RunCapture.complete(capture, exit_code=0, timed_out=False, observed_at=time.time())


@pytest.mark.parametrize('operation', ['apply', 'finish'])
def test_store_exception_is_observable_and_never_changes_job(tmp_path, monkeypatch, operation):
    store, run, capture = enrolled(tmp_path)
    logs = []

    def broken(*args, **kwargs):
        raise OSError('sensitive failure detail')

    monkeypatch.setattr(store, operation, broken)
    monkeypatch.setattr(capture_module, '_log', lambda message, **_: logs.append(message))
    proc, _, calls, done, _ = job(tmp_path, capture)
    proc.exited.set()
    assert done.wait(3)
    assert capture._done.wait(1)
    assert len(calls) == 1 and calls[0]['exit_code'] == 0
    assert store.get('p', run)['state'] == 'running'
    assert any('capture failed' in line and run in line for line in logs)
    assert all('sensitive failure detail' not in line for line in logs)


def test_stalled_initial_store_write_does_not_hold_launch_or_callback(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    store, run, capture = enrolled(tmp_path)
    apply = store.apply

    def stalled(*args):
        entered.set()
        release.wait(8)
        return apply(*args)

    monkeypatch.setattr(store, 'apply', stalled)
    started = time.monotonic()
    proc, _, calls, done, _ = job(tmp_path, capture)
    assert time.monotonic() - started < 1
    try:
        assert entered.wait(2)
        started = time.monotonic()
        proc.exited.set()
        assert done.wait(4)
        assert 1.9 <= time.monotonic() - started < 3.5
        assert store.get('p', run)['state'] == 'running'
    finally:
        release.set()
    assert capture._done.wait(3)
    assert store.get('p', run)['state'] == 'passed'
    assert len(calls) == 1


def test_byte_count_flush_bounds_high_volume_writes(tmp_path):
    store, run, capture = enrolled(tmp_path)
    path = tmp_path / 'output.log'
    path.write_bytes(b'x\n' * (capture_module.FLUSH_BYTES * 2))
    capture.start(path, suppressed=False)
    capture.complete(exit_code=0, timed_out=False, observed_at=time.time())
    assert capture._done.wait(4)
    heartbeats = [e for e in store.events('p', run) if e['type'] == 'heartbeat']
    assert 4 <= len(heartbeats) <= 6


def test_missing_exit_evidence_never_produces_success(tmp_path):
    store, run, capture = enrolled(tmp_path)
    path = tmp_path / 'output.log'
    path.write_bytes(b'passed\n')
    capture.start(path, suppressed=False)
    capture.complete(exit_code=None, timed_out=False, observed_at=time.time())
    assert store.get('p', run)['state'] == 'lost'


def test_missing_enrollment_does_not_create_a_record_or_change_job(tmp_path):
    store = TestRunStore(tmp_path / 'data')
    capture = RunCapture(store, 'p', '0' * 32)
    proc, _, calls, done, _ = job(tmp_path, capture)
    proc.exited.set()
    assert done.wait(3)
    assert len(calls) == 1 and calls[0]['exit_code'] == 0
    assert store.list_runs('p') == []


def test_disabled_capture_has_no_worker_or_store_activity(tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail('unenrolled job touched capture')

    monkeypatch.setattr(RunCapture, 'start', forbidden)
    proc, _, calls, done, _ = job(tmp_path, None)
    proc.exited.set()
    assert done.wait(2)
    assert len(calls) == 1


def test_timeout_preserves_kill_and_real_exit_evidence(tmp_path):
    store, run, capture = enrolled(tmp_path)
    proc, _, calls, done, _ = job(tmp_path, capture, timeout_minutes=0.001)
    assert done.wait(3)
    assert proc.killed == 1 and len(calls) == 1
    assert calls[0]['timed_out'] is True and calls[0]['exit_code'] == -9
    snapshot = store.get('p', run)
    assert snapshot['state'] == 'timeout' and snapshot['exit_code'] == -9
