"""MC-1148 phase 2: opt-in observation of an existing job's binary log.

The log is the backlog, not a pipe or an unbounded in-memory output queue.
Only this worker touches the store/consumer. No reporter parsing or enrollment
policy lives here; raw output never enters the store. One instance per launch.
"""
from __future__ import annotations

import codecs
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from mc.core import _log
from mc.test_run_store import SCHEMA, TestRunStore

FINAL_DRAIN_WAIT_SECONDS = 2.0
POLL_SECONDS = 0.05
FLUSH_INTERVAL_SECONDS = 0.5
FLUSH_BYTES = 256 * 1024
READ_BYTES = 64 * 1024
MAX_LINE_CHARS = 16 * 1024


class RunCapture:
    """Capture for an already enrolled run; no thread/I/O until start_job.

    on_batch receives bounded tuples of decoded lines on the capture thread.
    It must not retain unlimited output or treat it as trusted instructions.
    Slow consumers leave unread bytes in the original log, without losing them.
    An exception disables capture, logs the failure and leaves the run active
    (never invents a terminal success). Restart/adoption is a later phase.
    """

    def __init__(self, store: TestRunStore, project_id: str, run_id: str, *,
                 on_batch: Callable[[tuple[str, ...]], None] | None = None):
        self.store = store
        self.project_id = project_id
        self.run_id = run_id
        self.on_batch = on_batch
        self._exit: queue.Queue[tuple[int | None, bool, float]] = queue.Queue(maxsize=1)
        self._wake = threading.Event()
        self._done = threading.Event()
        self._started = False
        self._guard = threading.Lock()
        self._producer = 'capture_' + uuid.uuid4().hex
        self._seq = 0
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._anchor = b''
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._partial = ''
        self._discarding = False
        self._diagnostics: set[str] = set()
        self._reported: set[str] = set()
        self._pending_bytes = 0
        self._output_at: float | None = None
        self._last_flush = time.monotonic()

    def start(self, path: Path, *, suppressed: bool) -> None:
        with self._guard:
            if self._started:
                raise ValueError('Capture already attached to a job')
            self._started = True
        threading.Thread(target=self._run, args=(path, suppressed), daemon=True,
                         name=f'test-capture-{self.run_id[:8]}').start()

    def complete(self, *, exit_code: int | None, timed_out: bool,
                 observed_at: float) -> None:
        """Submit real process evidence; never perform store/file/consumer I/O."""
        self._exit.put_nowait((exit_code, timed_out, observed_at))
        self._wake.set()
        started = time.monotonic()
        if not self._done.wait(FINAL_DRAIN_WAIT_SECONDS):
            _log(f'[test-run-capture] final drain wait expired for run {self.run_id}; '
                 f'elapsed={time.monotonic() - started:.3f}s', flush=True)

    def _event(self, kind: str, payload: dict[str, Any], at: float) -> None:
        self._seq += 1
        self.store.apply(self.project_id, self.run_id, {
            'schema': SCHEMA, 'run_id': self.run_id, 'producer_id': self._producer,
            'producer_seq': self._seq, 'observed_at': at, 'type': kind,
            'payload': payload})

    def _lines(self, text: str, *, final: bool = False) -> None:
        lines: list[str] = []
        parts = text.split('\n')
        for index, part in enumerate(parts):
            if not self._discarding:
                room = MAX_LINE_CHARS - len(self._partial)
                self._partial += part[:room]
                if len(part) > room:
                    self._discarding = True
                    self._diagnostics.add('line_truncated')
            if index < len(parts) - 1:
                lines.append(self._partial.removesuffix('\r'))
                self._partial = ''
                self._discarding = False
        if final and (self._partial or self._discarding):
            lines.append(self._partial)
            self._partial = ''
            self._discarding = False
        if lines and self.on_batch is not None:
            self.on_batch(tuple(lines))

    def _read(self, path: Path) -> bool:
        # Reopen each bounded read so rotation is seen on Windows too. A short
        # anchor detects truncate-and-regrow even when size already exceeds offset.
        with path.open('rb') as source:
            stat = os.fstat(source.fileno())
            identity = (stat.st_dev, stat.st_ino)
            changed = self._identity is not None and identity != self._identity
            if self._offset and not changed and stat.st_size >= self._offset:
                source.seek(self._offset - len(self._anchor))
                changed = source.read(len(self._anchor)) != self._anchor
            if changed or stat.st_size < self._offset:
                self._diagnostics.add('source_reset')
                self._offset = 0
                self._anchor = b''
                self._decoder.reset()
                self._partial = ''
                self._discarding = False
            self._identity = identity
            source.seek(self._offset)
            data = source.read(READ_BYTES)
        if not data:
            return False
        self._offset += len(data)
        self._anchor = (self._anchor + data)[-32:]
        self._pending_bytes += len(data)
        self._output_at = time.time()
        self._lines(self._decoder.decode(data))
        return True

    def _flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._pending_bytes < FLUSH_BYTES and (
            now - self._last_flush < FLUSH_INTERVAL_SECONDS
        ):
            return
        for code in sorted(self._diagnostics - self._reported):
            self._event('diagnostic', {'code': code, 'capture': 'partial'}, time.time())
            self._reported.add(code)
        if self._output_at is not None:
            self._event('heartbeat', {'output': True}, self._output_at)
            self._output_at = None
        self._pending_bytes = 0
        self._last_flush = now

    def _run(self, path: Path, suppressed: bool) -> None:
        try:
            self.store.get(self.project_id, self.run_id)  # Requires enrollment.
            self._event('run_started', {}, time.time())
            if suppressed:
                self._diagnostics.add('output_suppressed')
            evidence = None
            while True:
                if evidence is None:
                    try:
                        evidence = self._exit.get_nowait()
                    except queue.Empty:
                        pass  # Expected empty control queue; no failed operation.
                if not suppressed and self._read(path):
                    self._flush()
                    continue
                if evidence is not None:
                    if not suppressed:
                        self._lines(self._decoder.decode(b'', final=True), final=True)
                    self._flush(force=True)
                    code, timed_out, at = evidence
                    state = ('timeout' if timed_out else 'lost' if code is None
                             else 'passed' if code == 0 else 'failed')
                    self.store.finish(self.project_id, self.run_id, state=state,
                                      observed_at=at, exit_code=code)
                    break
                self._flush()
                self._wake.wait(POLL_SECONDS)
                self._wake.clear()
        except Exception as exc:
            # Only a code enters telemetry; exception text can contain log data.
            _log(f'[test-run-capture] capture failed for run {self.run_id}: '
                 f'{type(exc).__name__}', flush=True)
            try:
                self._event('diagnostic', {'code': 'capture_failed', 'capture': 'partial'},
                            time.time())
            except Exception as diagnostic_exc:
                _log(f'[test-run-capture] diagnostic failed for run {self.run_id}: '
                     f'{type(diagnostic_exc).__name__}', flush=True)
        finally:
            self._done.set()


def start_capture(capture: RunCapture | None, path: Path, *, suppressed: bool) -> RunCapture | None:
    """Guard the optional hook; an initialization failure cannot change a job."""
    if capture is not None:
        try:
            capture.start(path, suppressed=suppressed)
            return capture
        except Exception as exc:
            _log(f'[test-run-capture] start failed for run {capture.run_id}: '
                 f'{type(exc).__name__}', flush=True)
    return None


def finish_capture(capture: RunCapture | None, *, exit_code: int | None,
                   timed_out: bool, observed_at: float) -> None:
    if capture is not None:
        try:
            capture.complete(exit_code=exit_code, timed_out=timed_out, observed_at=observed_at)
        except Exception as exc:
            _log(f'[test-run-capture] completion hook failed for run {capture.run_id}: '
                 f'{type(exc).__name__}', flush=True)
