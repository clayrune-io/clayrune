"""Standalone pytest launcher adapter; target interpreter and argv stay selected.

Only direct pytest/py.test and interpreter -m pytest argv are instrumented.
Opaque shells/scripts stay script units. No target imports mc or HTTP tokens.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

from test_run_transport import Telemetry

ASSET = Path(__file__).resolve().parents[1] / 'data' / 'test_reporters' / 'clayrune_pytest_progress.py'
ENV_PATH = 'CLAYRUNE_PYTEST_PROGRESS_PATH'
MAX_RECORD_BYTES = 16384
MAX_SPOOL_BYTES = 64 * 1024 * 1024
# Config, parent enrollment and child enrollment each have a two-second HTTP
# budget. Allow those serial requests plus scheduling margin, then run exact
# argv without a reporter rather than persist data before policy is known.
ENROLLMENT_WAIT_SECONDS = 8.0


def pytest_position(argv: list[str] | str) -> int | None:
    if isinstance(argv, str):
        return None
    if Path(argv[0]).name.lower() in ('pytest', 'pytest.exe', 'py.test', 'py.test.exe'):
        return 1
    if len(argv) >= 3 and argv[1:3] == ['-m', 'pytest']:
        return 3
    return None


class PytestProgress:
    def __init__(self, spec: Any, telemetry: Telemetry, key: str, warn: Callable[[str], None]):
        # A progress path inherited from an outer monitored run belongs to that
        # run, never to this unit: scrub it so off/refused runs stay clean and a
        # nested pytest cannot write into the outer run's sidecar.
        env = {k: v for k, v in spec.env.items() if k.upper() != ENV_PATH}
        spec = replace(spec, env=env) if len(env) != len(spec.env) else spec
        self.spec, self.telemetry, self.key, self.warn = spec, telemetry, key, warn
        self.position = pytest_position(spec.argv)
        self.seq = self.offset = 0
        self.partial = b''
        self.finished = False
        self.broken = False
        self.codes: set[str] = set()

    def metadata(self, label: str) -> dict:
        return dict(runner='pytest' if self.position is not None else 'command',
                    unit='test' if self.position is not None else 'script', display_label=label)

    def send(self, kind: str, **payload: Any) -> None:
        self.telemetry.send('events', self.key, {'events': [dict(type=kind, payload=payload)]})

    def diagnostic(self, code: str) -> None:
        if code not in self.codes:
            self.codes.add(code)
            self.warn(code)
            self.send('diagnostic', code=code, capture='partial')

    def read(self, stream: Any) -> None:
        try:
            raw = stream.read(65536)
            if not raw:
                return
            self.offset += len(raw)
            if self.offset > MAX_SPOOL_BYTES:
                raise ValueError('spool limit')
            lines = (self.partial + raw).split(b'\n')
            self.partial = lines.pop()
            if len(self.partial) > MAX_RECORD_BYTES:
                raise ValueError('record limit')
            for line in lines:
                if len(line) + 1 > MAX_RECORD_BYTES:
                    raise ValueError('record limit')
                message = json.loads(line)
                if (not isinstance(message, dict) or set(message) != {'schema', 'seq', 'type', 'payload'}
                        or type(message['schema']) is not int or message['schema'] != 1
                        or type(message['seq']) is not int or message['seq'] != self.seq + 1
                        or message['type'] not in ('run_started', 'collection', 'unit_started',
                                                   'unit_report', 'diagnostic', 'run_finished')
                        or not isinstance(message['payload'], dict)):
                    raise ValueError('invalid sidecar record')
                self.seq = message['seq']
                self.finished |= message['type'] == 'run_finished'
                if message['type'] == 'diagnostic' and isinstance(message['payload'].get('code'), str):
                    self.codes.add(message['payload']['code'])
                self.send(message['type'], **message['payload'])
        except (OSError, ValueError, TypeError, RecursionError):
            self.broken = True
            self.diagnostic('pytest_sidecar_invalid_progress_incomplete')

    def observe(self, stream: Any, stop: threading.Event) -> None:
        while not stop.wait(0.02):
            if not self.broken:
                self.read(stream)

    def execute(self, execute: Callable[[Any], dict]) -> dict:
        if self.position is None:
            self.send('run_started', reporter_version='launcher-1')
            self.send('collection', revision=1, selected_total=1)
            self.send('diagnostic', code='framework_reporter_unavailable', capture='partial')
            self.send('unit_started', unit_id=self.key, attempt=1)
            observed = execute(self.spec)
            self.send('unit_report', unit_id=self.key, attempt=1, phase='process',
                      outcome='pass' if observed['exit_code'] == 0 else 'fail', final=True, revision=1)
            return observed
        # Confirm scoped enrollment before creating persistent test-name data.
        # This is bounded and opt-in; a refused/off/stalled API runs exact argv.
        if not self.telemetry.enrolled(self.key, timeout=ENROLLMENT_WAIT_SECONDS):
            self.diagnostic('pytest_enrollment_unavailable_progress_incomplete')
            return execute(self.spec)
        if not ASSET.is_file():
            self.diagnostic('pytest_reporter_asset_unavailable')
            return execute(self.spec)
        try:
            directory = tempfile.TemporaryDirectory(prefix='clayrune-pytest-')
        except OSError:
            self.diagnostic('pytest_sidecar_unavailable')
            return execute(self.spec)
        started = False
        observed: dict | None = None
        try:
            path = Path(directory.name) / 'events.jsonl'
            with path.open('x+b') as stream:
                env = dict(self.spec.env)
                env[ENV_PATH] = str(path)
                env['PYTHONPATH'] = str(ASSET.parent) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
                argv = list(self.spec.argv)
                argv[self.position:self.position] = ['-p', 'clayrune_pytest_progress']
                prepared = replace(self.spec, argv=argv, env=env)
                stop = threading.Event()
                thread = threading.Thread(target=self.observe, args=(stream, stop), daemon=True,
                                          name='pytest-sidecar')
                thread.start()
                try:
                    started = True
                    observed = execute(prepared)
                finally:
                    stop.set()
                    thread.join(1)
                if thread.is_alive():
                    self.diagnostic('pytest_sidecar_drain_timeout_progress_incomplete')
                elif not self.broken:
                    # Child wait has completed. Drain every bounded block before
                    # queuing finalize, including events from late cleanup.
                    while stream.tell() < os.fstat(stream.fileno()).st_size and not self.broken:
                        self.read(stream)
                    if self.partial or not self.finished:
                        self.diagnostic('pytest_reporter_finish_missing_progress_incomplete')
                    if not self.finished and observed['exit_code'] in (2, 3, 4, 5):
                        self.diagnostic({2: 'pytest_exit_interrupted', 3: 'pytest_exit_internal_error',
                                         4: 'pytest_exit_usage_error', 5: 'pytest_exit_no_tests'}[observed['exit_code']])
                return observed
        except OSError:
            self.diagnostic('pytest_sidecar_io_failed_progress_incomplete')
            if observed is not None:
                return observed
            if not started:
                return execute(self.spec)
            # Never rerun a callback that has already started the command.
            raise
        finally:
            try:
                directory.cleanup()
            except OSError:
                self.diagnostic('pytest_sidecar_cleanup_failed')
