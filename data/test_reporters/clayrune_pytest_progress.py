"""Standalone, opt-in pytest telemetry asset; stdlib only, no Clayrune imports.

The launcher supplies a private sidecar path, never an HTTP capability. Workers
do not write the controller file. Serial pytest is measured; distributed/retry
runs retain failure observations with unknown totals and incomplete counts.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

ENV_PATH = 'CLAYRUNE_PYTEST_PROGRESS_PATH'
MAX_RECORD_BYTES = 16384
MAX_SPOOL_BYTES = 64 * 1024 * 1024


def pytest_configure(config: Any) -> None:
    path = os.environ.get(ENV_PATH)
    # xdist workers inherit the env, but only the controller observes reports.
    if path and not hasattr(config, 'workerinput') and not os.environ.get('PYTEST_XDIST_WORKER'):
        config.pluginmanager.register(Progress(config, path), 'clayrune-pytest-progress-state')


class Progress:
    def __init__(self, config: Any, path: str):
        self.config, self.path = config, path
        self.stream: Any = None
        self.seq = self.size = self.revision = self.deselected = self.errors = 0
        self.selected: int | None = None
        self.collection_seen = False
        self.session: Any = None
        self.partial = False
        self.disabled = False
        self.codes: set[str] = set()
        self.attempts: dict[str, int] = {}
        self.phases: dict[tuple[str, int, str], int] = {}
        self.completed: set[str] = set()

    def emit(self, kind: str, **payload: Any) -> None:
        if self.disabled:
            return
        try:
            record = json.dumps(dict(schema=1, seq=self.seq + 1, type=kind, payload=payload),
                                ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
            if len(record) > MAX_RECORD_BYTES or self.size + len(record) > MAX_SPOOL_BYTES:
                raise ValueError('reporter size limit')
            if self.stream is None:
                # The launcher creates the file exclusively before passing its
                # name. Do not create an arbitrary missing destination here.
                self.stream = open(self.path, 'r+b', buffering=0)
                self.stream.seek(0, os.SEEK_END)
            if self.stream.write(record) != len(record):
                raise OSError('short reporter write')
            self.stream.flush()
            self.seq += 1
            self.size += len(record)
        except (OSError, ValueError, TypeError):
            self.disabled = True
            self.warning('pytest_reporter_write_failed_progress_incomplete')

    @staticmethod
    def warning(code: str) -> None:
        try:
            print('[test-run] ' + code, file=sys.__stderr__ or sys.stderr, flush=True)
        except (OSError, ValueError):
            # No usable telemetry or stderr remains; never alter pytest exit.
            return

    def diagnostic(self, code: str) -> None:
        if code not in self.codes:
            self.codes.add(code)
            self.emit('diagnostic', code=code, capture='partial')

    def unmeasured(self, code: str) -> None:
        changed = not self.partial
        self.partial = True
        self.diagnostic(code)
        if changed:
            self.collection(None)

    def collection(self, selected: int | None) -> None:
        if self.partial:
            selected = None
        # Collection errors/deselection also revise the accounting snapshot.
        self.revision += 1
        self.selected = selected
        self.emit('collection', revision=self.revision, selected_total=selected,
                  deselected=self.deselected, collection_errors=self.errors)

    def name(self, nodeid: str) -> str:
        if len(nodeid) <= 1024:
            return nodeid
        self.diagnostic('pytest_name_oversized_detail_unavailable')
        return 'oversized-nodeid:' + hashlib.sha256(nodeid.encode('utf-8', errors='replace')).hexdigest()

    def pytest_sessionstart(self, session: Any) -> None:
        self.session = session
        self.emit('run_started', reporter_version='pytest-1')
        option = self.config.option
        if getattr(option, 'numprocesses', None) or getattr(option, 'dist', 'no') != 'no':
            self.unmeasured('pytest_xdist_not_measured')
        if getattr(option, 'reruns', 0) or getattr(option, 'force_reruns', 0):
            self.unmeasured('pytest_retries_not_measured')
        if getattr(option, 'collectonly', False):
            self.diagnostic('pytest_collection_only')

    def pytest_deselected(self, items: Any) -> None:
        self.deselected += len(items)

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.errors += 1
            self.collection(self.selected)
        elif report.skipped:
            self.diagnostic('pytest_collection_skipped')

    def pytest_collection_finish(self, session: Any) -> None:
        self.collection_seen = True
        self.collection(len(session.items))

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        name = self.name(nodeid)
        attempt = self.attempts.get(name, 0) + 1
        self.attempts[name] = attempt
        if attempt > 1:
            self.unmeasured('pytest_retries_not_measured')
            self.completed.discard(name)
        if self.session is not None and self.collection_seen and not self.partial:
            selected = len(self.session.items)
            if selected != self.selected:
                self.collection(selected)
        self.emit('unit_started', unit_id=name, attempt=attempt)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if getattr(report, 'worker_id', None) and not self.partial:
            self.unmeasured('pytest_xdist_not_measured')
        if report.outcome == 'rerun' or getattr(report, 'rerun', 0):
            self.unmeasured('pytest_retries_not_measured')
        name = self.name(report.nodeid)
        attempt = self.attempts.setdefault(name, 1)
        phase = report.when
        if phase not in ('setup', 'call', 'teardown'):
            self.diagnostic('pytest_unknown_phase')
            return
        if report.failed or report.outcome == 'rerun':
            outcome = 'fail' if phase == 'call' else 'error'
        elif report.skipped:
            outcome = 'xfail' if hasattr(report, 'wasxfail') else 'skip'
        elif report.passed:
            outcome = 'xpass' if phase == 'call' and hasattr(report, 'wasxfail') else 'pass'
        else:
            self.diagnostic('pytest_unknown_outcome')
            return
        # Distributed/retry counts are withheld. Failure phases remain visible.
        if self.partial and outcome not in ('fail', 'error'):
            return
        identity = (name, attempt, phase)
        revision = self.phases.get(identity, 0) + 1
        self.phases[identity] = revision
        final = phase == 'teardown' and not self.partial
        self.emit('unit_report', unit_id=name, attempt=attempt, phase=phase,
                  outcome=outcome, final=final, revision=revision)
        if final:
            self.completed.add(name)

    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> None:
        if self.collection_seen and not self.partial and len(session.items) != self.selected:
            self.collection(len(session.items))
        code = int(exitstatus)
        states = {0: 'passed', 1: 'failed', 2: 'cancelled', 3: 'error', 4: 'error', 5: 'no_tests'}
        # Informational exit reasons live in this finish record. A diagnostic
        # with capture=complete could erase an earlier launcher I/O warning.
        payload: dict[str, Any] = dict(state=states.get(code, 'error'), exit_code=code)
        if self.selected is not None:
            payload['not_run'] = max(0, self.selected - len(self.completed))
        self.emit('run_finished', **payload)

    def pytest_unconfigure(self) -> None:
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                self.warning('pytest_reporter_close_failed_progress_incomplete')
