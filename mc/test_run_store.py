"""MC-1148 phase 1: project-scoped telemetry, with no execution or integration.

A finished run is one atomic record holding enrollment, canonical events and the
snapshot. While a run is open (MC-1179 SF30) its events are appended to a tail
file (mc/test_run_log.py) and the reducer state is kept in memory; the tail is
folded back into the record when the run ends. The reducer is replayable; a
reporter finish never establishes process success.
Callers are trusted server code (producer authorization belongs to phase 3).
One server owns a data root; the per-run locks cover multiple store instances in
that process, not concurrent servers. Expired runs are removed only by
mc/test_run_retention.py.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import re
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from mc import test_run_log as tail_log
from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.test_run_ledger import ValidatedLedger
from mc.test_run_listing import ListEntry, ListIndex
from mc.test_run_open import OpenRegistry, OpenRun, RunLocks
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
# One lock per run file, never one for every project: a writer holds only its own
# run's lock, and only for its in-memory step plus one small append.
_LOCKS = RunLocks()
_OPEN = OpenRegistry()
_LEDGER = ValidatedLedger()
_SNAPSHOTS = SnapshotCache()
_TERMINAL = TerminalIndex()
_LIST = ListIndex()
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


def _digest(encoded: str) -> bytes:
    """Identity of one canonical event: duplicates are compared, never stored."""
    return hashlib.blake2b(encoded.encode('utf-8'), digest_size=16).digest()


class Reducer:
    """Canonical event reduction, kept incrementally.

    ``replay`` below is this class run over a whole history; an open run keeps
    one alive so a batch applies only its own events. Producer sequence
    identifies, not orders. Explicit collection/report revisions handle delayed
    worker events. A retry replaces the previous attempt of one logical unit,
    not its identity. A final report closes the unit; earlier phase failures
    still determine its outcome.

    ``apply`` mutates as it goes and may raise part-way through a batch. A
    reducer that raised is not reusable: the store drops it and rebuilds from
    disk, where the failed batch was never written.
    """

    def __init__(self, enrollment: dict[str, Any]):
        self.run_id = enrollment['run_id']
        self.state = _initial(enrollment)
        # key -> (digest of the canonical event, its observed_at)
        self.seen: dict[tuple[str, int], tuple[bytes, Any]] = {}
        self.units: dict[str, dict[str, Any]] = {}
        self.failures: dict[tuple[str, str], dict[str, Any]] = {}
        self.process_payload: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.state['state'] in TERMINAL_STATES

    def apply(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Reduce one event. Returns the validated event when it was accepted,
        None when it was a repeat or arrived after the run ended."""
        state = self.state
        event = _event(raw, self.run_id)
        key = (event['producer_id'], event['producer_seq'])
        digest = _digest(_json(event))
        known = self.seen.get(key)
        if known is not None:
            if known[0] != digest:
                raise ValueError('Conflicting duplicate event')
            return None
        if state['state'] in TERMINAL_STATES:
            return None
        self.seen[key] = (digest, event['observed_at'])
        state['seq'] += 1
        at = _time(event['observed_at'])
        state['updated_at'] = max(state['updated_at'], at)
        payload = event['payload']
        kind = event['type']
        if event['producer_id'] == _PROCESS_PRODUCER and self.process_payload is None:
            self.process_payload = copy.deepcopy(payload)
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
            self._unit(kind, payload, at)
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
        return event

    def _unit(self, kind: str, payload: dict[str, Any], at: float) -> None:
        state, units, failures = self.state, self.units, self.failures
        identity, attempt = payload['unit_id'], payload['attempt']
        unit = units.get(identity)
        if unit is not None and attempt < unit['attempt']:
            return
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

    def snapshot(self) -> dict[str, Any]:
        """A private copy with the bounded display details materialized.

        Only the last MAX_DETAILS failures are copied, so this costs the same
        for a run of ten units and a run of seventeen thousand.
        """
        state = copy.deepcopy(self.state)
        state['completed'] = sum(state['counts'].values())
        newest = list(itertools.islice(reversed(self.failures.values()), MAX_DETAILS))
        state['latest_failures'] = copy.deepcopy(newest[::-1])
        state['failure_details_omitted'] = max(0, len(self.failures) - MAX_DETAILS)
        total = state['selected_total']
        state['percent'] = (100 * state['completed'] / total
                            if total and state['completed'] <= total else None)
        return state


def replay(enrollment: dict[str, Any], events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reduce a whole canonical history in order."""
    reducer = Reducer(enrollment)
    for raw in events:
        reducer.apply(raw)
    return reducer.snapshot()


class RunHandle:
    """One run, with its lock held, for the callers that must read and write
    it as a unit (ingest, finalize, the chat anchor). Obtain it only through
    ``TestRunStore.locked``; it is valid inside that ``with`` and nowhere else.
    Copies are returned throughout: the open state is never handed out.
    """

    def __init__(self, store: TestRunStore, project_id: str, run_id: str, path: Path):
        self._store, self._project_id, self._run_id, self._path = store, project_id, run_id, path

    def _open(self) -> OpenRun:
        return self._store._open_run(self._project_id, self._run_id, self._path)

    @property
    def enrollment(self) -> dict[str, Any]:
        return copy.deepcopy(self._open().enrollment)

    @property
    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._open().snapshot)

    @property
    def terminal(self) -> bool:
        return self._open().snapshot['state'] in TERMINAL_STATES

    @property
    def process_payload(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._open().reducer.process_payload)

    def stored_observed_at(self, producer_id: str, producer_seq: int) -> Any:
        """observed_at of an event already in the run, or None."""
        known = self._open().reducer.seen.get((producer_id, producer_seq))
        return None if known is None else known[1]

    def matches_stored(self, event: dict[str, Any]) -> bool:
        """True when the run already holds exactly this (already validated) event."""
        known = self._open().reducer.seen.get((event['producer_id'], event['producer_seq']))
        return known is not None and known[0] == _digest(_json(event))

    def commit(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply a batch: nothing is written unless every event is accepted."""
        return self._store._commit(self._project_id, self._run_id, self._path, self._open(), events)

    def claimed(self, name: str) -> bool:
        """Internal record metadata outside replay (the chat-anchor claim)."""
        return self._store._record_flag(self._path, name)

    def claim(self, name: str) -> None:
        self._store._set_record_flag(self._path, name)


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

    @contextmanager
    def locked(self, project_id: str, run_id: str) -> Generator[RunHandle]:
        """Hold exactly this run's lock. No other run, in any project, waits.

        Re-entrant: ``get``/``apply``/``finish`` may be called inside it.
        """
        path = self._path(project_id, run_id)
        with _LOCKS.hold(path):
            yield RunHandle(self, project_id, run_id, path)

    def _parse(self, project_id: str, run_id: str, path: Path) -> dict[str, Any]:
        """The record file as stored, identity-checked but not replayed."""
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
            # The list index relies on these canonical enrollment fields. A
            # malformed record must not look like a disappearing file (KeyError).
            _time(enrollment['created_at'])
            if enrollment['owner_session_id'] is not None:
                _text(enrollment['owner_session_id'], 'owner_session_id')
            _ = record['events'], record['snapshot']
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f'Invalid test run {run_id}') from exc
        return record

    @staticmethod
    def _reduce(record: dict[str, Any], path: Path) -> tuple[Reducer, list[dict[str, Any]], int | None]:
        """Replay the record, check its stored snapshot, then its open tail.

        The tail is read only while the record is not terminal: a terminal
        record already holds every event, and a tail beside it is the leftover
        of a compaction that was interrupted before the tail was removed.
        """
        reducer = Reducer(record['enrollment'])
        for raw in record['events']:
            reducer.apply(raw)
        if reducer.snapshot() != record['snapshot']:
            raise ValueError('Snapshot differs from canonical replay')
        tail: list[dict[str, Any]] = []
        truncate_to = None
        if not reducer.terminal:
            tail, covered, torn = tail_log.read_tail(tail_log.log_path(path))
            for raw in tail:
                reducer.apply(raw)
            truncate_to = covered if torn else None
        return reducer, tail, truncate_to

    def _read(self, project_id: str, run_id: str) -> dict[str, Any]:
        """The whole run: record plus open tail, validated by canonical replay.

        A terminal record whose file is exactly as it was when last validated
        (the ledger) is parsed but not replayed again.
        """
        path = self._path(project_id, run_id)
        before = self._peek(path)
        record = self._parse(project_id, run_id, path)
        trusted = before is not None and _LEDGER.get(path, before) is not None
        if trusted and record['snapshot']['state'] in TERMINAL_STATES:
            return record
        try:
            reducer, tail, _ = self._reduce(record, path)
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f'Invalid test run {run_id}') from exc
        except OSError as exc:
            raise StoreError(f'Unreadable test run {run_id}') from exc
        if tail:
            record = {**record, 'events': [*record['events'], *tail],
                      'snapshot': reducer.snapshot()}
        elif record['snapshot']['state'] in TERMINAL_STATES and before is not None \
                and self._peek(path) == before:
            _LEDGER.add(path, before, record['snapshot'])
        return record

    @staticmethod
    def _version(path: Path) -> Fingerprint:
        """Version of a run as stored: its record, and its tail if it has one.

        The tail grows while the record is untouched, so it must be part of the
        version or an open run would look unchanged. A run with no tail has
        exactly the record's own (mtime, size), as before.
        """
        stat = path.stat()
        try:
            tail = tail_log.log_path(path).stat()
        except FileNotFoundError:
            return stat.st_mtime_ns, stat.st_size
        return max(stat.st_mtime_ns, tail.st_mtime_ns), stat.st_size + tail.st_size

    def _peek(self, path: Path) -> Fingerprint | None:
        """``_fingerprint`` that reports a missing or unreadable run as None."""
        try:
            return self._version(path)
        except OSError:
            return None

    def _fingerprint(self, path: Path) -> Fingerprint:
        try:
            return self._version(path)
        except FileNotFoundError:
            self._forget(path)
            raise KeyError(path.stem) from None
        except OSError as exc:
            self._forget(path)
            raise StoreError(f'Unreadable test run {path.stem}') from exc

    @staticmethod
    def _forget(path: Path) -> None:
        _SNAPSHOTS.discard(path)
        _TERMINAL.discard(path)
        _LIST.discard(path)

    def _open_run(self, project_id: str, run_id: str, path: Path) -> OpenRun:
        """The run's reducer state; the caller holds the run's lock.

        Served from memory when the files are exactly as this process left
        them; otherwise rebuilt by one replay from disk (first use after a
        restart, an eviction, or an outside edit).
        """
        version = self._fingerprint(path)
        live = _OPEN.get(path)
        if live is not None and live.fingerprint == version:
            return live
        _OPEN.discard(path)
        record = self._parse(project_id, run_id, path)
        try:
            reducer, _, truncate_to = self._reduce(record, path)
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f'Invalid test run {run_id}') from exc
        except OSError as exc:
            raise StoreError(f'Unreadable test run {run_id}') from exc
        run = OpenRun(record['enrollment'], reducer, reducer.snapshot(), version, truncate_to)
        # An outside replacement while we replayed must not be tagged with the
        # new version's fingerprint; the next call simply rebuilds.
        if self._peek(path) == version:
            _OPEN.put(path, run)
        return run

    def _commit(self, project_id: str, run_id: str, path: Path, run: OpenRun,
                events: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply a batch to an open run and append what was accepted.

        The lock held is this run's, for this body only: reducing the batch in
        memory, one append (a single write and fsync of the batch's own bytes)
        and, when the batch ends the run, one compaction pass. If anything
        raises, the in-memory state may be ahead of the disk, so it is dropped
        and rebuilt from the files, which hold exactly the acknowledged events.
        """
        try:
            accepted = [done for raw in events if (done := run.reducer.apply(raw)) is not None]
            if not accepted:
                return copy.deepcopy(run.snapshot)
            self._forget(path)
            lines = [(_json(event) + '\n').encode('utf-8') for event in accepted]
            tail_log.append(tail_log.log_path(path), lines, truncate_to=run.truncate_to)
            run.truncate_to = None
            run.snapshot = run.reducer.snapshot()
            if run.reducer.terminal:
                # The run ended: fold the tail into the record, so a finished
                # run is one self-contained file, as it always was.
                tail_log.compact(path, run.snapshot)
                _OPEN.discard(path)
                version = self._peek(path)
                if version is not None:
                    _LEDGER.add(path, version, run.snapshot)
                    _LEDGER.flush()
            else:
                run.fingerprint = self._peek(path) or (0, -1)
            return copy.deepcopy(run.snapshot)
        except BaseException:
            _OPEN.discard(path)
            raise

    def _record_flag(self, path: Path, name: str) -> bool:
        try:
            return json.loads(read_text_with_retry(path)).get(name) is True
        except FileNotFoundError:
            raise KeyError(path.stem) from None

    def _set_record_flag(self, path: Path, name: str) -> None:
        """Set one metadata flag on the record file itself.

        Events, snapshot and the open tail are untouched: the record is
        rewritten as it stands on disk, then the open state's version follows.
        """
        record = json.loads(read_text_with_retry(path))
        record[name] = True
        self._forget(path)
        write_json_atomic(path, record, allow_nan=False)
        live = _OPEN.get(path)
        if live is not None:
            live.fingerprint = self._peek(path) or (0, -1)

    def _snapshot(self, project_id: str, run_id: str, *, evict: bool = True,
                  cache_terminal: bool = True) -> dict[str, Any]:
        """Caller holds the run's lock, including the cache lookup."""
        path = self._path(project_id, run_id)
        fingerprint = self._fingerprint(path)
        _TERMINAL.matches(path, fingerprint)
        cached = _SNAPSHOTS.get(path, fingerprint)
        if cached is not None:
            _LIST.remember(path, fingerprint, cached)
            if cached['state'] in TERMINAL_STATES:
                _TERMINAL.remember(path, fingerprint)
            return cached
        live = _OPEN.get(path)
        if live is not None and live.fingerprint == fingerprint:
            # An open run this process is writing: its state is in memory, so a
            # read is a copy, not a replay of everything it has recorded.
            snapshot = copy.deepcopy(live.snapshot)
            _LIST.remember(path, fingerprint, snapshot)
            if snapshot['state'] in TERMINAL_STATES:
                _TERMINAL.remember(path, fingerprint)
            return snapshot
        snapshot = self._read(project_id, run_id)['snapshot']
        # An external atomic replacement during validation must not tag the old
        # snapshot with the new version's fingerprint. Recheck on the next read.
        if self._fingerprint(path) == fingerprint:
            _LIST.remember(path, fingerprint, snapshot)
            if cache_terminal or snapshot['state'] not in TERMINAL_STATES:
                _SNAPSHOTS.put(path, fingerprint, snapshot, evict=evict)
            if snapshot['state'] in TERMINAL_STATES:
                _TERMINAL.remember(path, fingerprint)
        return copy.deepcopy(snapshot)

    def _trusted_terminal(self, path: Path, fingerprint: Fingerprint) -> ListEntry | None:
        """A terminal run validated before, by this process or an earlier one,
        and unchanged since: its list metadata without opening the file."""
        trusted = _LEDGER.get(path, fingerprint)
        if trusted is None:
            return None
        _TERMINAL.remember(path, fingerprint)
        _LIST.remember(path, fingerprint, {'created_at': trusted.created_at,
                                           'run_id': trusted.run_id,
                                           'owner_session_id': trusted.owner_session_id})
        return ListEntry(fingerprint, trusted.created_at, trusted.run_id, trusted.owner_session_id)

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
        path = self._path(project_id, enrollment['run_id'])
        with _LOCKS.hold(path):
            if parent_run_id is not None:
                self._read(project_id, parent_run_id)
            reducer = Reducer(enrollment)
            snapshot = reducer.snapshot()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._forget(path)
            _OPEN.discard(path)
            write_json_atomic(path, {'schema': SCHEMA, 'enrollment': enrollment,
                                    'events': [], 'snapshot': snapshot}, allow_nan=False)
            # The first batch finds its reducer waiting; nothing is replayed.
            _OPEN.put(path, OpenRun(enrollment, reducer, snapshot,
                                    self._peek(path) or (0, -1)))
            return copy.deepcopy(snapshot)

    def get(self, project_id: str, run_id: str) -> dict[str, Any]:
        path = self._path(project_id, run_id)
        try:
            with _LOCKS.hold(path):
                return self._snapshot(project_id, run_id)
        finally:
            _LEDGER.flush()

    def events(self, project_id: str, run_id: str) -> list[dict[str, Any]]:
        path = self._path(project_id, run_id)
        try:
            with _LOCKS.hold(path):
                return copy.deepcopy(self._read(project_id, run_id)['events'])
        finally:
            _LEDGER.flush()

    def apply(self, project_id: str, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        if event.get('producer_id') == _PROCESS_PRODUCER:
            raise ValueError('Process evidence must use finish()')
        return self._append(project_id, run_id, event)

    def _append(self, project_id: str, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        path = self._path(project_id, run_id)
        with _LOCKS.hold(path):
            run = self._open_run(project_id, run_id, path)
            return self._commit(project_id, run_id, path, run, [event])

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
        _event(event, run_id)
        path = self._path(project_id, run_id)
        with _LOCKS.hold(path):
            run = self._open_run(project_id, run_id, path)
            if run.snapshot['state'] in TERMINAL_STATES:
                return copy.deepcopy(run.snapshot)
            return self._commit(project_id, run_id, path, run, [event])

    def expire(self, path: Path, cutoff: float) -> int | None:
        """Delete one run file (and its tail) if it ended before ``cutoff``.

        Returns the bytes removed, or None when the run is kept: still active,
        ended at or after the cutoff, or already gone. Only the retention sweep
        calls this. Holds only this run's lock; the decision and the delete
        see the same version of the file or nothing is removed.
        """
        with _LOCKS.hold(path):
            try:
                version = self._fingerprint(path)
            except KeyError:
                return None
            trusted = _LEDGER.get(path, version)
            if trusted is not None:
                finished = trusted.finished_at
            else:
                project_id = self._parse_enrollment_project(path)
                snapshot = self._snapshot(project_id, path.stem, cache_terminal=False)
                if snapshot['state'] not in TERMINAL_STATES:
                    return None
                finished = (snapshot['finished_at'] if snapshot['finished_at'] is not None
                            else snapshot['updated_at'])
            if finished >= cutoff or self._peek(path) != version:
                return None
            tail = tail_log.log_path(path)
            freed = 0
            for target in (tail, path):  # tail first: a half-done delete never leaves a tail with no record
                try:
                    size = target.stat().st_size
                    target.unlink()
                    freed += size
                except FileNotFoundError:
                    pass
            self._forget(path)
            _OPEN.discard(path)
            _LEDGER.forget(path)
            return freed

    def trim_ledger(self, folder: Path) -> None:
        """Drop validated-ledger lines of runs that no longer exist."""
        _LEDGER.rewrite(folder, {p.stem for p in folder.glob('*.json')})

    @staticmethod
    def _parse_enrollment_project(path: Path) -> str:
        try:
            project_id = json.loads(read_text_with_retry(path))['enrollment']['project_id']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StoreError(f'Unreadable test run {path.stem}') from exc
        return _text(project_id, 'project_id', 128)

    def run_files(self) -> Generator[Path]:
        """Every run record under the root, any project. Enumeration only."""
        try:
            folders = [p for p in self.root.iterdir() if p.is_dir()]
        except FileNotFoundError:
            return
        for folder in folders:
            yield from folder.glob('*.json')

    def list_runs(self, project_id: str, *, owner_session_id: str | None = None,
                  limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _integer(limit, 'limit', 1)
        _integer(offset, 'offset')
        if limit > 100:
            raise ValueError('List limit exceeds 100')
        # Only metadata scales with unchanged history. Missing/stale metadata
        # must first pass canonical validation, including off-page corruption.
        # As with all_snapshots this is a per-file view, not a transaction.
        folder = self._path(project_id, '0' * 32).parent
        entries: list[ListEntry] = []
        try:
            for path in list(folder.glob('*.json')):
                with _LOCKS.hold(path):
                    try:
                        fingerprint = self._fingerprint(path)
                        entry = _LIST.get(path, fingerprint) or self._trusted_terminal(path, fingerprint)
                        if entry is None:
                            snapshot = self._read(project_id, path.stem)['snapshot']
                            # No defensive snapshot copy or LRU admission for
                            # off-page metadata. Only page reads need snapshots.
                            if self._fingerprint(path) == fingerprint:
                                _LIST.remember(path, fingerprint, snapshot)
                                if snapshot['state'] in TERMINAL_STATES:
                                    _TERMINAL.remember(path, fingerprint)
                            # Use this observed version even if an external replace
                            # prevented caching its fingerprint during validation.
                            entry = ListEntry(fingerprint, snapshot['created_at'],
                                              snapshot['run_id'], snapshot['owner_session_id'])
                    except KeyError:
                        self._forget(path)
                        continue
                if owner_session_id is None or entry.owner_session_id == owner_session_id:
                    entries.append(entry)
            entries.sort(key=lambda e: (e.created_at, e.run_id), reverse=True)
            snapshots = []
            for entry in entries[offset:]:
                path = folder / (entry.run_id + '.json')
                with _LOCKS.hold(path):
                    try:
                        snapshot = self._snapshot(project_id, entry.run_id)
                    except KeyError:
                        self._forget(path)
                        continue
                # An external replacement between indexing and page materialization
                # must not return another owner's snapshot through a scoped list.
                if owner_session_id is not None and snapshot['owner_session_id'] != owner_session_id:
                    continue
                snapshots.append(snapshot)
                if len(snapshots) == limit:
                    break
            return snapshots
        finally:
            _LEDGER.flush()

    def active_snapshots(self, project_id: str) -> list[dict[str, Any]]:
        """Floor-only view: unchanged terminal files require only a stat.

        Fingerprint markers have no LRU cap and retain no snapshot copies.
        Validation, copying and projection scale with active/changed files;
        enumeration/stat still scale with history to detect external changes.
        """
        return self.all_snapshots(project_id, active_only=True)

    def all_snapshots(self, project_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Read every run once, newest first, without a whole-directory lock.

        Enumeration and sorting hold no lock. Each file is observed under its
        own run's lock, so writes to any run can interleave even during cold
        validation. This is a per-file view, not a project-wide transaction.
        """
        folder = self._path(project_id, '0' * 32).parent
        paths = list(folder.glob('*.json'))
        snapshots = []
        try:
            for path in paths:
                with _LOCKS.hold(path):
                    try:
                        if active_only:
                            version = self._fingerprint(path)
                            if (_TERMINAL.matches(path, version)
                                    or self._trusted_terminal(path, version) is not None):
                                continue
                        snapshot = self._snapshot(project_id, path.stem, evict=active_only,
                                                  cache_terminal=not active_only)
                    except KeyError:
                        # The file may disappear after enumeration or stat. Other
                        # unreadable/corrupt records must still raise StoreError.
                        self._forget(path)
                        continue
                    if not active_only or snapshot['state'] not in TERMINAL_STATES:
                        snapshots.append(snapshot)
        finally:
            _LEDGER.flush()
        snapshots.sort(key=lambda s: (s['created_at'], s['run_id']), reverse=True)
        return snapshots
