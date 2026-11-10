"""MC-1148 phase 1: project-scoped telemetry, with no execution or integration.

One atomic record commits enrollment, canonical events and the snapshot together.
The reducer is replayable; a reporter finish never establishes process success.
Callers are trusted server code (producer authorization belongs to phase 3).
One server owns a data root; the shared lock covers multiple store instances in
that process, not concurrent servers. No automatic retention/deletion policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.test_run_snapshots import Fingerprint, SnapshotCache
from mc.test_run_terminal import TerminalIndex

SCHEMA = 1
TERMINAL_STATES = frozenset({'passed', 'failed', 'error', 'cancelled', 'timeout',
                             'no_tests', 'lost'})
OUTCOMES = ('pass', 'fail', 'skip', 'error', 'xfail', 'xpass')
EVENT_TYPES = frozenset({'run_started', 'collection', 'unit_started',
                         'unit_report', 'diagnostic', 'heartbeat', 'run_finished'})
MAX_EVENT_BYTES = 16384
MAX_DETAILS = 100
_LOCK = threading.RLock()
_SNAPSHOTS = SnapshotCache()
_TERMINAL = TerminalIndex()
_PROCESS_PRODUCER = '_store_process'


class StoreError(RuntimeError):
    """Corrupt or unreadable telemetry must never look like a successful run."""


def _text(value: Any, field: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f'Invalid {field}')
    return value


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f'Invalid {field}')
    return value


def _time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Invalid observed_at')
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid observed_at')
    return float(value)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _event(event: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Allowlist the envelope and payload; no stdout, env or owner overrides."""
    try:
        return _validate_event(event, run_id)
    except (KeyError, TypeError) as exc:
        raise ValueError('Invalid event payload') from exc


def _validate_event(event: dict[str, Any], run_id: str) -> dict[str, Any]:
    fields = {'schema', 'run_id', 'producer_id', 'producer_seq', 'observed_at',
              'type', 'payload'}
    if (set(event) != fields or type(event['schema']) is not int
            or event['schema'] != SCHEMA or event['run_id'] != run_id):
        raise ValueError('Invalid event envelope')
    _text(event['producer_id'], 'producer_id', 128)
    _integer(event['producer_seq'], 'producer_seq', 1)
    _time(event['observed_at'])
    kind = event['type']
    if not isinstance(kind, str) or kind not in EVENT_TYPES:
        raise ValueError('Unknown event type')
    payload = event['payload']
    if not isinstance(payload, dict):
        raise ValueError('Invalid payload')
    allowed = {
        'run_started': {'reporter_version'},
        'collection': {'selected_total', 'revision', 'deselected', 'collection_errors'},
        'unit_started': {'unit_id', 'attempt'},
        'unit_report': {'unit_id', 'attempt', 'phase', 'outcome', 'final', 'revision'},
        'diagnostic': {'code', 'capture'},
        'heartbeat': {'output'},
        'run_finished': {'state', 'exit_code', 'not_run', 'evidence'},
    }[kind]
    if set(payload) - allowed:
        raise ValueError('Unknown payload field')
    if kind == 'run_started' and 'reporter_version' in payload:
        _text(payload['reporter_version'], 'reporter_version', 128)
    if kind == 'collection':
        _integer(payload['revision'], 'revision', 1)
        if payload['selected_total'] is not None:
            _integer(payload['selected_total'], 'selected_total')
        for key in ('deselected', 'collection_errors'):
            if key in payload:
                _integer(payload[key], key)
    if kind in {'unit_started', 'unit_report'}:
        _text(payload['unit_id'], 'unit_id', 1024)
        _integer(payload['attempt'], 'attempt', 1)
    if kind == 'unit_report':
        _text(payload['phase'], 'phase', 64)
        if payload['outcome'] not in OUTCOMES or type(payload['final']) is not bool:
            raise ValueError('Invalid unit report')
        _integer(payload['revision'], 'revision', 1)
    if kind == 'diagnostic':
        # Codes, rather than arbitrary runner output, keep secrets out of storage.
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', payload['code']):
            raise ValueError('Invalid diagnostic code')
        if payload.get('capture', 'partial') not in {'complete', 'partial', 'lost'}:
            raise ValueError('Invalid capture state')
    if kind == 'heartbeat' and 'output' in payload and type(payload['output']) is not bool:
        raise ValueError('Invalid output flag')
    if kind == 'run_finished':
        if 'evidence' in payload:
            evidence = payload['evidence']
            if (event['producer_id'] != _PROCESS_PRODUCER
                    or not isinstance(evidence, dict)
                    or set(evidence) != {'source', 'process_identity', 'signal', 'termination'}
                    or evidence['source'] != 'launcher_observed'
                    or evidence['termination'] not in {'exited', 'signalled', 'cancelled', 'timeout'}):
                raise ValueError('Invalid launcher evidence')
            _text(evidence['process_identity'], 'process_identity')
            signal = evidence['signal']
            if signal is not None and (type(signal) is not int or not 1 <= signal <= 255):
                raise ValueError('Invalid termination signal')
        if payload['state'] not in TERMINAL_STATES:
            raise ValueError('Invalid finish state')
        code = payload.get('exit_code')
        if code is not None and type(code) is not int:
            raise ValueError('Invalid exit code')
        if 'not_run' in payload:
            _integer(payload['not_run'], 'not_run')
    if len(_json(event).encode('utf-8')) > MAX_EVENT_BYTES:
        raise ValueError('Event exceeds size limit')
    return copy.deepcopy(event)


def _initial(enrollment: dict[str, Any]) -> dict[str, Any]:
    return {**copy.deepcopy(enrollment), 'state': 'running', 'seq': 0,
            'selected_total': None, 'total_revision': 0, 'total_recalculated': False,
            'completed': 0, 'counts': dict.fromkeys(OUTCOMES, 0),
            'collection_errors': 0, 'deselected': 0, 'not_run': None,
            'percent': None, 'eta_seconds': None, 'capture': 'complete',
            'latest_failures': [], 'failure_total': 0, 'failure_details_omitted': 0,
            'diagnostics': [], 'diagnostics_omitted': 0, 'exit_code': None,
            'reporter_state': None, 'reporter_version': None,
            'updated_at': enrollment['created_at'], 'finished_at': None,
            'last_output_at': None, 'last_completion_at': None}


def _unit_outcome(unit: dict[str, Any] | None) -> str | None:
    if unit is None or not unit['final'] or not unit['phases']:
        return None
    outcomes = {report['outcome'] for report in unit['phases'].values()}
    # Setup/teardown errors outrank a passing call; one count per logical unit.
    return next(o for o in ('error', 'fail', 'xpass', 'xfail', 'skip', 'pass') if o in outcomes)


def replay(enrollment: dict[str, Any], events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reduce canonical event order; producer sequence identifies, not orders.

    Explicit collection/report revisions handle delayed worker events. A retry
    replaces the previous attempt of one logical unit, not its identity. A final
    report closes the unit; earlier phase failures still determine its outcome.
    """
    state = _initial(enrollment)
    seen: dict[tuple[str, int], str] = {}
    units: dict[str, dict[str, Any]] = {}
    failures: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in events:
        event = _event(raw, enrollment['run_id'])
        key = (event['producer_id'], event['producer_seq'])
        encoded = _json(event)
        if key in seen:
            if seen[key] != encoded:
                raise ValueError('Conflicting duplicate event')
            continue
        if state['state'] in TERMINAL_STATES:
            continue
        seen[key] = encoded
        state['seq'] += 1
        at = _time(event['observed_at'])
        state['updated_at'] = max(state['updated_at'], at)
        payload = event['payload']
        kind = event['type']
        if kind == 'run_started':
            state['reporter_version'] = payload.get('reporter_version')
        elif kind == 'collection':
            if payload['revision'] == state['total_revision'] and (
                payload['selected_total'] != state['selected_total']
                or payload.get('deselected', state['deselected']) != state['deselected']
                or payload.get('collection_errors', state['collection_errors']) != state['collection_errors']
            ):
                raise ValueError('Conflicting collection revision')
            if payload['revision'] > state['total_revision']:
                changed = state['selected_total'] != payload['selected_total']
                state['total_recalculated'] = state['total_revision'] > 0 and changed
                state['selected_total'] = payload['selected_total']
                state['total_revision'] = payload['revision']
                state['deselected'] = payload.get('deselected', state['deselected'])
                state['collection_errors'] = payload.get('collection_errors', state['collection_errors'])
        elif kind in {'unit_started', 'unit_report'}:
            identity, attempt = payload['unit_id'], payload['attempt']
            unit = units.get(identity)
            if unit is not None and attempt < unit['attempt']:
                continue
            previous_outcome = _unit_outcome(unit)
            if previous_outcome is not None:
                state['counts'][previous_outcome] -= 1
            if unit is not None:
                for phase in unit['phases']:
                    failures.pop((identity, phase), None)
            if unit is None or attempt > unit['attempt']:
                unit = {'attempt': attempt, 'phases': {}, 'final': False}
                units[identity] = unit
            if attempt == unit['attempt'] and kind == 'unit_report':
                phase = payload['phase']
                old = unit['phases'].get(phase)
                if old is not None and payload['revision'] == old['revision'] and payload != old:
                    raise ValueError('Conflicting unit report revision')
                if old is None or payload['revision'] > old['revision']:
                    unit['phases'][phase] = payload
                    if payload['final'] and not unit['final']:
                        unit['final'] = True
                        state['last_completion_at'] = max(state['last_completion_at'] or 0, at)
            outcome = _unit_outcome(unit)
            if outcome is not None:
                state['counts'][outcome] += 1
            for phase, report in unit['phases'].items():
                if report['outcome'] in {'fail', 'error'}:
                    failures[(identity, phase)] = {
                        'name': identity, 'phase': phase, 'attempt': unit['attempt']}
            state['failure_total'] = len(failures)
        elif kind == 'diagnostic':
            state['capture'] = payload.get('capture', 'partial')
            state['diagnostics'].append({'code': payload['code'], 'observed_at': at})
            if len(state['diagnostics']) > MAX_DETAILS:
                state['diagnostics'].pop(0)
                state['diagnostics_omitted'] += 1
        elif kind == 'heartbeat' and payload.get('output'):
            state['last_output_at'] = max(state['last_output_at'] or 0, at)
        elif kind == 'run_finished':
            if event['producer_id'] == _PROCESS_PRODUCER:
                verdict, code = payload['state'], payload.get('exit_code')
                if verdict in {'passed', 'failed', 'no_tests'} and code is None:
                    raise ValueError('Process exit evidence required')
                if verdict == 'passed' and code != 0:
                    raise ValueError('Success requires exit zero')
                # Fail closed if an adapter reports success despite known failures.
                if verdict == 'passed' and (state['counts']['fail'] or state['counts']['error']
                                            or state['collection_errors'] or state['failure_total']):
                    verdict = 'failed'
                state.update(state=verdict, exit_code=code, finished_at=at,
                             not_run=payload.get('not_run'))
                if 'evidence' in payload:
                    state['process_evidence'] = copy.deepcopy(payload['evidence'])
                if verdict == 'lost':
                    state['capture'] = 'lost'
            else:
                state['reporter_state'] = payload['state']

    # Materialize bounded display details once. Replaying N units is linear,
    # rather than recounting every previous unit after every event.
    state['completed'] = sum(state['counts'].values())
    state['latest_failures'] = list(failures.values())[-MAX_DETAILS:]
    state['failure_details_omitted'] = max(0, len(failures) - MAX_DETAILS)
    total = state['selected_total']
    state['percent'] = (100 * state['completed'] / total
                        if total and state['completed'] <= total else None)
    return state


class TestRunStore:
    """Explicit data root is DATA_DIR.parent; construction performs no disk I/O."""

    __test__ = False

    def __init__(self, data_root: Path):
        base = Path(data_root).resolve()
        parts = tuple(part.casefold() for part in base.parts)
        if parts[-1] == 'projects' or any(
            parts[i:i + 2] == ('data', 'projects') for i in range(len(parts) - 1)
        ):
            raise ValueError('Telemetry must live outside data/projects/')
        self.root = base / 'test_runs'

    def _path(self, project_id: str, run_id: str) -> Path:
        _text(project_id, 'project_id', 128)
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError('Invalid run ID')
        # Hash the project identity so arbitrary project IDs never become paths.
        path = self.root / hashlib.sha256(project_id.encode()).hexdigest() / (run_id + '.json')
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise StoreError('Run path escapes telemetry root')
        return path

    def _read(self, project_id: str, run_id: str) -> dict[str, Any]:
        path = self._path(project_id, run_id)
        try:
            record = json.loads(read_text_with_retry(path))
        except FileNotFoundError:
            raise KeyError(run_id) from None
        except (OSError, ValueError, UnicodeError) as exc:
            raise StoreError(f'Unreadable test run {run_id}') from exc
        try:
            enrollment = record['enrollment']
            if (record['schema'] != SCHEMA or enrollment['project_id'] != project_id
                    or enrollment['run_id'] != run_id):
                raise ValueError('Run identity mismatch')
            if replay(enrollment, record['events']) != record['snapshot']:
                raise ValueError('Snapshot differs from canonical replay')
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f'Invalid test run {run_id}') from exc
        return record

    def _fingerprint(self, path: Path) -> Fingerprint:
        try:
            stat = path.stat()
        except FileNotFoundError:
            _SNAPSHOTS.discard(path)
            _TERMINAL.discard(path)
            raise KeyError(path.stem) from None
        except OSError as exc:
            _SNAPSHOTS.discard(path)
            _TERMINAL.discard(path)
            raise StoreError(f'Unreadable test run {path.stem}') from exc
        return stat.st_mtime_ns, stat.st_size

    def _snapshot(self, project_id: str, run_id: str, *, evict: bool = True,
                  cache_terminal: bool = True) -> dict[str, Any]:
        """Caller holds _LOCK for one file only, including the cache lookup."""
        path = self._path(project_id, run_id)
        fingerprint = self._fingerprint(path)
        _TERMINAL.matches(path, fingerprint)
        cached = _SNAPSHOTS.get(path, fingerprint)
        if cached is not None:
            if cached['state'] in TERMINAL_STATES:
                _TERMINAL.remember(path, fingerprint)
            return cached
        snapshot = self._read(project_id, run_id)['snapshot']
        # An external atomic replacement during validation must not tag the old
        # snapshot with the new version's fingerprint. Recheck on the next read.
        if self._fingerprint(path) == fingerprint:
            if cache_terminal or snapshot['state'] not in TERMINAL_STATES:
                _SNAPSHOTS.put(path, fingerprint, snapshot, evict=evict)
            if snapshot['state'] in TERMINAL_STATES:
                _TERMINAL.remember(path, fingerprint)
        return copy.deepcopy(snapshot)

    def create(self, project_id: str, *, observed_at: float,
               owner_session_id: str | None = None, owner_turn_id: str | None = None,
               job_id: str | None = None, parent_run_id: str | None = None,
               source: str = 'job', runner: str = 'unknown', unit: str = 'test',
               cwd: str | None = None, target_id: str | None = None,
               display_label: str = 'Test run', process_identity: str | None = None) -> dict[str, Any]:
        """Enroll trusted metadata; display_label must already be redacted.

        No arbitrary command/environment fields are accepted. Enrollment is
        immutable; telemetry cannot reassign ownership. Restricted-mode support
        is deliberately unspecified and this durable store is not incognito-safe.
        """
        enrollment: dict[str, Any] = dict(
            schema=SCHEMA, run_id=uuid.uuid4().hex, project_id=project_id,
            owner_session_id=owner_session_id, owner_turn_id=owner_turn_id,
            job_id=job_id, parent_run_id=parent_run_id, source=source,
            runner=runner, unit=unit, cwd=cwd, target_id=target_id,
            display_label=display_label, process_identity=process_identity,
            created_at=_time(observed_at))
        for key, value in enrollment.items():
            if key not in {'schema', 'created_at'} and value is not None:
                _text(value, key, 2048 if key == 'cwd' else 512)
        with _LOCK:
            if parent_run_id is not None:
                self._read(project_id, parent_run_id)
            path = self._path(project_id, enrollment['run_id'])
            snapshot = _initial(enrollment)
            path.parent.mkdir(parents=True, exist_ok=True)
            _SNAPSHOTS.discard(path)
            _TERMINAL.discard(path)
            write_json_atomic(path, {'schema': SCHEMA, 'enrollment': enrollment,
                                    'events': [], 'snapshot': snapshot}, allow_nan=False)
            return copy.deepcopy(snapshot)

    def get(self, project_id: str, run_id: str) -> dict[str, Any]:
        with _LOCK:
            return self._snapshot(project_id, run_id)

    def events(self, project_id: str, run_id: str) -> list[dict[str, Any]]:
        with _LOCK:
            return copy.deepcopy(self._read(project_id, run_id)['events'])

    def apply(self, project_id: str, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        if event.get('producer_id') == _PROCESS_PRODUCER:
            raise ValueError('Process evidence must use finish()')
        return self._append(project_id, run_id, event)

    def _append(self, project_id: str, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            record = self._read(project_id, run_id)
            validated = _event(event, run_id)
            for prior in record['events']:
                if (prior['producer_id'], prior['producer_seq']) == (validated['producer_id'], validated['producer_seq']):
                    if prior != validated:
                        raise ValueError('Conflicting duplicate event')
                    return copy.deepcopy(record['snapshot'])
            if record['snapshot']['state'] in TERMINAL_STATES:
                return copy.deepcopy(record['snapshot'])
            record['events'].append(validated)
            record['snapshot'] = replay(record['enrollment'], record['events'])
            path = self._path(project_id, run_id)
            _SNAPSHOTS.discard(path)
            _TERMINAL.discard(path)
            write_json_atomic(path, record, allow_nan=False)
            return copy.deepcopy(record['snapshot'])

    def finish(self, project_id: str, run_id: str, *, state: str,
               observed_at: float, exit_code: int | None = None,
               not_run: int | None = None,
               evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """Trusted capture layer supplies observed exit/loss/timeout, never a reporter."""
        payload: dict[str, Any] = {'state': state, 'exit_code': exit_code}
        if evidence is not None:
            payload['evidence'] = evidence
        if not_run is not None:
            payload['not_run'] = not_run
        event = {
            'schema': SCHEMA, 'run_id': run_id, 'producer_id': _PROCESS_PRODUCER,
            'producer_seq': 1, 'observed_at': observed_at, 'type': 'run_finished',
            'payload': payload}
        with _LOCK:
            _event(event, run_id)
            snapshot = self._read(project_id, run_id)['snapshot']
            if snapshot['state'] in TERMINAL_STATES:
                return copy.deepcopy(snapshot)
            return self._append(project_id, run_id, event)

    def list_runs(self, project_id: str, *, owner_session_id: str | None = None,
                  limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _integer(limit, 'limit', 1)
        _integer(offset, 'offset')
        if limit > 100:
            raise ValueError('List limit exceeds 100')
        snapshots = self.all_snapshots(project_id)
        if owner_session_id is not None:
            snapshots = [s for s in snapshots if s['owner_session_id'] == owner_session_id]
        return snapshots[offset:offset + limit]

    def active_snapshots(self, project_id: str) -> list[dict[str, Any]]:
        """Floor-only view: unchanged terminal files require only a stat.

        Fingerprint markers have no LRU cap and retain no snapshot copies.
        Validation, copying and projection scale with active/changed files;
        enumeration/stat still scale with history to detect external changes.
        """
        return self.all_snapshots(project_id, active_only=True)

    def all_snapshots(self, project_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Read every run once, newest first, without a whole-directory lock.

        Enumeration and sorting never hold _LOCK. Each file is observed under
        its own short critical section, so submits can interleave even during
        cold validation. This is a per-file view, not a project-wide transaction.
        """
        folder = self._path(project_id, '0' * 32).parent
        paths = list(folder.glob('*.json'))
        snapshots = []
        for path in paths:
            with _LOCK:
                try:
                    if active_only and _TERMINAL.matches(path, self._fingerprint(path)):
                        continue
                    snapshot = self._snapshot(project_id, path.stem, evict=active_only,
                                              cache_terminal=not active_only)
                except KeyError:
                    # The file may disappear after enumeration or stat. Other
                    # unreadable/corrupt records must still raise StoreError.
                    _SNAPSHOTS.discard(path)
                    _TERMINAL.discard(path)
                    continue
                if not active_only or snapshot['state'] not in TERMINAL_STATES:
                    snapshots.append(snapshot)
        snapshots.sort(key=lambda s: (s['created_at'], s['run_id']), reverse=True)
        return snapshots
