"""Bounded, best-effort loopback telemetry for the standalone test launcher.

No mc imports, credentials on argv, reporter finalization, or process execution.
The one worker is a THREAD in the enrolling launcher, preserving peer identity.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

HTTP_TIMEOUT = 2.0
FINAL_DRAIN_SECONDS = 30.0
QUEUE_SIZE = 256


class TransportError(RuntimeError):
    """Only sanitized diagnostic codes cross this boundary."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalClient:
    def __init__(self, port: int, project: str):
        self.base = f'http://127.0.0.1:{port}'
        self.prefix = '/api/project/' + urllib.parse.quote(project, safe='') + '/test-runs'
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, method: str, path: str, body: dict | None = None,
                token: str | None = None) -> dict[str, Any]:
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None if body is None else json.dumps(body, allow_nan=False).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=HTTP_TIMEOUT) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise TransportError('response_too_large')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TransportError('invalid_response')
            return result
        except urllib.error.HTTPError as exc:
            exc.close()
            raise TransportError(f'http_{exc.code}') from None
        except (OSError, ValueError, urllib.error.URLError):
            raise TransportError('transport_unavailable') from None

    def register(self, pid: int, project: str) -> None:
        # Never send the command or environment, which may contain credentials.
        self.request('POST', '/api/processes/register', {
            'pid': pid, 'project_id': project, 'name': 'Reported test command',
            'command': 'test-run child (arguments withheld)'})


class Telemetry:
    def __init__(self, client: LocalClient, warn: Callable[[str], None]):
        self.client, self.warn = client, warn
        self.pending: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.closing = threading.Event()
        self.dropped = threading.Event()
        self.inactive = threading.Event()
        self.runs: dict[str, dict[str, Any]] = {}
        self.failed = False
        self.thread = threading.Thread(target=self._work, name='test-run-telemetry', daemon=True)
        self.thread.start()

    def send(self, action: str, key: str, payload: dict) -> None:
        if self.inactive.is_set():
            return
        try:
            self.pending.put_nowait((action, key, payload))
        except queue.Full:
            if not self.dropped.is_set():
                self.warn('telemetry_queue_full_progress_incomplete')
            self.dropped.set()

    def close(self) -> None:
        self.closing.set()
        self.thread.join(FINAL_DRAIN_SECONDS)
        if self.thread.is_alive():
            self.warn('telemetry_final_drain_timeout_progress_incomplete')

    def _submit(self, record: dict, events: list[dict]) -> None:
        if record['receipt'].get('expires_at', 0) <= time.time() + 30:
            response = self.client.request('POST', record['path'] + '/producer', {})
            record['receipt'], record['seq'] = response['receipt'], 0
        wire = []
        for event in events:
            record['seq'] += 1
            wire.append(dict(schema=1, producer_seq=record['seq'], **event))
        self.client.request('POST', record['path'] + '/submit', {'events': wire},
                            record['receipt']['token'])

    def _flush(self, record: dict) -> None:
        if record['buffer']:
            batch, record['buffer'] = record['buffer'], []
            self._submit(record, batch)

    def _handle(self, action: str, key: str, payload: dict) -> None:
        if action == 'enroll':
            parent = payload.pop('parent', None)
            if parent is not None:
                if parent not in self.runs:
                    raise TransportError('parent_unavailable')
                payload['parent_run_id'] = self.runs[parent]['run_id']
            response = self.client.request('POST', self.client.prefix + '/enroll', payload)
            run_id = response['run']['run_id']
            if (not isinstance(run_id, str) or len(run_id) != 32
                    or any(c not in '0123456789abcdef' for c in run_id)):
                raise TransportError('invalid_run_id')
            self.runs[key] = dict(run_id=run_id, path=self.client.prefix + '/' + run_id,
                                  receipt=response['receipt'],
                                  capability=response['finalize_capability']['token'], seq=0, buffer=[])
        elif key in self.runs:
            record = self.runs[key]
            if action == 'events':
                for event in payload['events']:
                    record['buffer'].append(event)
                    if len(record['buffer']) == 64:
                        self._flush(record)
            elif action == 'finalize':
                # Called only by execution code AFTER wait(), never by a reporter.
                try:
                    self._flush(record)
                except TransportError:
                    self.failed = True
                    self.warn('event_flush_failed_progress_incomplete')
                if self.failed or self.dropped.is_set():
                    try:
                        self._submit(record, [dict(type='diagnostic', payload={
                            'code': 'launcher_telemetry_incomplete', 'capture': 'partial'})])
                    except (TransportError, KeyError, TypeError):
                        self.warn('capture_diagnostic_unavailable')
                self.client.request('POST', record['path'] + '/finalize', payload,
                                    record['capability'])
                self.client.request('POST', record['path'] + '/revoke', {},
                                    record['receipt']['token'])
                del self.runs[key]

    def _work(self) -> None:
        try:
            if self.client.request('GET', '/api/config').get('test_run_progress_enabled') is not True:
                self.warn('capture_disabled')
                self.inactive.set()
                return
        except TransportError as exc:
            self.warn(str(exc) + '_progress_unavailable')
            self.inactive.set()
            return
        while not self.closing.is_set() or not self.pending.empty():
            try:
                action, key, payload = self.pending.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._handle(action, key, payload)
                if self.pending.empty():
                    for record in list(self.runs.values()):
                        self._flush(record)
            except (TransportError, KeyError, TypeError, ValueError) as exc:
                self.failed = True
                code = str(exc) if isinstance(exc, TransportError) else 'invalid_response'
                self.warn(code + '_progress_incomplete')
        if self.runs:
            self.warn('unfinalized_runs_progress_incomplete')
