"""Standalone direct-Node script adapter; one selected command is one script.

The existing launcher manifest owns ordering/stop policy. The preload observes
lifecycle; actual wait owns the script outcome and process finalization.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

from test_run_transport import Telemetry

ASSET = Path(__file__).resolve().parents[1] / 'data' / 'test_reporters' / 'smoke-progress.mjs'
ENV_PATH = 'CLAYRUNE_SMOKE_PROGRESS_PATH'
ENV_MANIFEST = 'CLAYRUNE_SMOKE_PROGRESS_MANIFEST'
MAX_RECORD_BYTES = 16384
MAX_SPOOL_BYTES = 65536
ENROLLMENT_WAIT_SECONDS = 8.0


def node_script(argv: list[str] | str) -> str | None:
    # No option parsing, npm expansion, shell parsing or native test runner
    # guessing. Such invocations retain the existing opaque-command fallback.
    if (isinstance(argv, list) and len(argv) >= 2
            and Path(argv[0]).name.lower() in ('node', 'node.exe')
            and not argv[1].startswith('-') and Path(argv[1]).suffix.lower() in ('.mjs', '.cjs', '.js')):
        return argv[1]
    return None


class NodeProgress:
    def __init__(self, spec: Any, telemetry: Telemetry, key: str, warn: Callable[[str], None]):
        self.spec, self.telemetry, self.key, self.warn = spec, telemetry, key, warn
        script = node_script(spec.argv)
        if script is None:
            raise ValueError('expected direct Node script')
        self.script_path = Path(spec.cwd) / script
        name = Path(script).name
        self.name = name if len(name) <= 1024 else 'script-sha256:' + hashlib.sha256(name.encode()).hexdigest()
        self.seq = self.offset = 0
        self.partial = b''
        self.finished = self.broken = False
        self.codes: set[str] = set()

    def metadata(self, label: str) -> dict:
        return dict(runner='node-script', unit='script', display_label='Smoke script ' + label)

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
                record = json.loads(line)
                if (not isinstance(record, dict) or set(record) != {'schema', 'seq', 'type', 'payload'}
                        or type(record['schema']) is not int or record['schema'] != 1
                        or type(record['seq']) is not int or record['seq'] != self.seq + 1):
                    raise ValueError('invalid envelope')
                # Fixed lifecycle protocol; no arbitrary stdout or diagnostics.
                expected = {
                    1: ('run_started', dict(reporter_version='node-script-1')),
                    2: ('collection', dict(revision=1, selected_total=1)),
                    3: ('unit_started', dict(unit_id=self.name, attempt=1)),
                }
                kind, payload = record['type'], record['payload']
                if self.seq < 3:
                    if (kind, payload) != expected[self.seq + 1]:
                        raise ValueError('invalid lifecycle')
                elif kind == 'heartbeat' and not self.finished:
                    if payload != {'output': False}:
                        raise ValueError('invalid checkpoint')
                elif kind == 'run_finished' and not self.finished:
                    if (not isinstance(payload, dict) or set(payload) != {'state', 'exit_code', 'not_run'}
                            or type(payload['exit_code']) is not int or payload['not_run'] != 0
                            or payload['state'] != ('passed' if payload['exit_code'] == 0 else 'failed')):
                        raise ValueError('invalid finish')
                    self.finished = True
                else:
                    raise ValueError('invalid lifecycle')
                self.seq = record['seq']
                self.send(kind, **payload)
        except (OSError, ValueError, TypeError, RecursionError):
            self.broken = True
            self.diagnostic('node_sidecar_invalid_progress_incomplete')

    def observe(self, stream: Any, stop: threading.Event) -> None:
        while not stop.wait(0.02):
            if not self.broken:
                self.read(stream)

    def execute(self, execute: Callable[[Any], dict]) -> dict:
        observed = self.capture(execute)
        # The selected unit is a script, so wait proves its outcome even when
        # a preload is missing/broken, exits early, or cleanup changes the code.
        self.send('collection', revision=2, selected_total=1)
        self.send('unit_report', unit_id=self.name, attempt=1, phase='process',
                  outcome='pass' if observed['exit_code'] == 0 else 'fail', final=True, revision=1)
        return observed

    def capture(self, execute: Callable[[Any], dict]) -> dict:
        if not self.telemetry.enrolled(self.key, timeout=ENROLLMENT_WAIT_SECONDS):
            self.diagnostic('node_enrollment_unavailable_progress_incomplete')
            return execute(self.spec)
        if not ASSET.is_file():
            self.diagnostic('node_reporter_asset_unavailable')
            return execute(self.spec)
        try:
            # Observation-only path failures must not block original execution,
            # and disabled/refused capture needs no filesystem resolution.
            script = str(self.script_path.resolve())
            directory = tempfile.TemporaryDirectory(prefix='clayrune-node-')
        except (OSError, RuntimeError):
            self.diagnostic('node_sidecar_unavailable')
            return execute(self.spec)
        started = False
        observed: dict | None = None
        try:
            path = Path(directory.name) / 'events.jsonl'
            with path.open('x+b') as stream:
                env = dict(self.spec.env)
                env[ENV_PATH] = str(path)
                env[ENV_MANIFEST] = json.dumps(dict(schema=1, script=script, unit_id=self.name))
                env.pop('CLAYRUNE_SMOKE_PROGRESS_OWNER_PID', None)
                prepared = replace(self.spec, argv=[self.spec.argv[0], '--import', ASSET.as_uri(), *self.spec.argv[1:]], env=env)
                stop = threading.Event()
                thread = threading.Thread(target=self.observe, args=(stream, stop), daemon=True, name='node-sidecar')
                thread.start()
                try:
                    started = True
                    observed = execute(prepared)
                finally:
                    stop.set()
                    thread.join(1)
                if thread.is_alive():
                    self.diagnostic('node_sidecar_drain_timeout_progress_incomplete')
                elif not self.broken:
                    while stream.tell() < os.fstat(stream.fileno()).st_size and not self.broken:
                        self.read(stream)
                    if self.partial or not self.finished:
                        self.diagnostic('node_reporter_finish_missing_progress_incomplete')
                return observed
        except OSError:
            self.diagnostic('node_sidecar_io_failed_progress_incomplete')
            if observed is not None:
                return observed
            if not started:
                return execute(self.spec)
            raise  # Never rerun a command that already started.
        finally:
            try:
                directory.cleanup()
            except OSError:
                self.diagnostic('node_sidecar_cleanup_failed')
