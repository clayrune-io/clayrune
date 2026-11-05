"""MC-1148 phase 3: dark telemetry-only enrollment and producer boundary.

The caller resolver is trusted server code, never a request-body decoder.
This module neither authenticates HTTP nor launches processes. Spools are newly
created here and read through pinned descriptors; arbitrary paths are not input.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mc.atomic_json import write_json_atomic
from mc import test_run_store as runs

MAX_BATCH_EVENTS = 64
MAX_BATCH_BYTES = 256 * 1024
MAX_RECEIPTS = 1024
RECEIPT_SECONDS = 3600
SPOOL_READ_BYTES = 64 * 1024


class IngestRefused(PermissionError):
    """Unavailable attribution, wrong scope, expired receipt or restricted mode."""


@dataclass(frozen=True)
class OwnerBinding:
    """Authoritative project/session state resolved outside the producer body.

    Null session is an explicitly authorized project service, not failed
    attribution. Both restriction flags must be supplied from live policy.
    """

    project_id: str
    session_id: str | None
    incognito: bool
    sandboxed: bool
    cwd: str | None = None
    turn_id: str | None = None
    job_id: str | None = None
    process_identity: str | None = None
    source: str = 'wrapper'


@dataclass(frozen=True)
class ProducerReceipt:
    run_id: str
    producer_id: str
    token: str
    expires_at: float


@dataclass
class _Grant:
    receipt: ProducerReceipt
    owner: OwnerBinding
    deadline: float
    spool_path: Path | None = None
    spool_fd: int | None = None
    offset: int = 0


def _encoded(value: Any, limit: int) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('Invalid telemetry JSON') from None
    if len(data) > limit:
        raise ValueError('Telemetry size limit exceeded')
    return data


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate telemetry field')
        result[key] = value
    return result


class TestRunIngest:
    """One server instance; receipts/descriptors do not survive its restart.

    resolve_owner(project_id, caller_evidence) must verify managed ancestry or
    an existing authorized job/service identity. Never infer ownerless authority
    from absent/unavailable attribution. Re-resolved on every operation.
    """

    __test__ = False

    def __init__(self, store: runs.TestRunStore, *,
                 resolve_owner: Callable[[str, Any], OwnerBinding | None],
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self.store = store
        self._resolve = resolve_owner
        self._clock = clock
        self._monotonic = monotonic
        self._grants: dict[str, _Grant] = {}
        self._lock = threading.RLock()

    def _owner(self, project_id: str, caller: Any) -> OwnerBinding:
        if not isinstance(project_id, str) or not 0 < len(project_id) <= 128:
            raise IngestRefused('Owner unavailable')
        owner = self._resolve(project_id, caller)
        if (not isinstance(owner, OwnerBinding) or owner.project_id != project_id
                or type(owner.incognito) is not bool or type(owner.sandboxed) is not bool
                or owner.incognito or owner.sandboxed or project_id == '_incognito'
                or (owner.session_id is not None and (
                    not isinstance(owner.session_id, str) or not 0 < len(owner.session_id) <= 512))
                or (owner.session_id is None and owner.turn_id is not None)):
            raise IngestRefused('Owner unavailable or restricted')
        return owner

    def _expire(self) -> None:
        for key, grant in list(self._grants.items()):
            if self._monotonic() >= grant.deadline:
                if grant.spool_fd is not None:
                    os.close(grant.spool_fd)
                del self._grants[key]

    def _issue(self, owner: OwnerBinding, run_id: str) -> ProducerReceipt:
        token = secrets.token_urlsafe(32)
        receipt = ProducerReceipt(run_id, 'reporter_' + uuid.uuid4().hex, token,
                                  self._clock() + RECEIPT_SECONDS)
        self._grants[hashlib.sha256(token.encode()).hexdigest()] = _Grant(
            receipt, owner, self._monotonic() + RECEIPT_SECONDS)
        return receipt

    def _grant(self, project_id: str, caller: Any, run_id: str, token: str) -> _Grant:
        owner = self._owner(project_id, caller)
        self._expire()
        if not isinstance(token, str) or len(token) > 128:
            raise IngestRefused('Producer unavailable')
        grant = self._grants.get(hashlib.sha256(token.encode()).hexdigest())
        if (grant is None or grant.receipt.run_id != run_id
                or grant.owner.project_id != owner.project_id
                or grant.owner.session_id != owner.session_id):
            raise IngestRefused('Producer unavailable')
        return grant

    def enroll(self, project_id: str, caller: Any, metadata: dict[str, Any], *,
               parent_run_id: str | None = None) -> tuple[
            dict[str, Any], ProducerReceipt]:
        """Only display/runner metadata is producer-controlled; labels are pre-redacted."""
        with self._lock:
            owner = self._owner(project_id, caller)
            self._expire()
            if len(self._grants) >= MAX_RECEIPTS:
                raise IngestRefused('Producer capacity reached')
            if (not isinstance(metadata, dict) or set(metadata) - {
                    'runner', 'unit', 'display_label', 'target_id'}):
                raise ValueError('Invalid enrollment metadata')
            _encoded(metadata, runs.MAX_EVENT_BYTES)
            # Parent validation and child creation share the finalizer/store lock:
            # a concurrent terminal transition cannot slip between them.
            with runs._LOCK:
                if parent_run_id is not None:
                    if not isinstance(parent_run_id, str):
                        raise ValueError('Invalid parent run')
                    parent = self.store.get(project_id, parent_run_id)
                    if (owner.session_id is None
                            or parent['owner_session_id'] != owner.session_id
                            or parent['project_id'] != project_id
                            or parent['state'] in runs.TERMINAL_STATES):
                        raise IngestRefused('Parent unavailable or terminal')
                snapshot = self.store.create(
                    project_id, observed_at=self._clock(), owner_session_id=owner.session_id,
                    owner_turn_id=owner.turn_id, cwd=owner.cwd, job_id=owner.job_id,
                    parent_run_id=parent_run_id,
                    process_identity=owner.process_identity, source=owner.source, **metadata)
            return snapshot, self._issue(owner, snapshot['run_id'])

    def producer(self, project_id: str, caller: Any, run_id: str) -> ProducerReceipt:
        """Trusted owner may enroll another reporter generation for an existing run."""
        with self._lock:
            owner = self._owner(project_id, caller)
            self._expire()
            if len(self._grants) >= MAX_RECEIPTS:
                raise IngestRefused('Producer capacity reached')
            snapshot = self.store.get(project_id, run_id)
            if snapshot['owner_session_id'] != owner.session_id:
                raise IngestRefused('Owner mismatch')
            if snapshot['state'] in runs.TERMINAL_STATES:
                raise IngestRefused('Run is terminal')
            return self._issue(owner, run_id)

    def submit(self, project_id: str, caller: Any, run_id: str, token: str,
               events: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate the entire batch, then atomically commit once, including dedupe.

        Producer wire events contain schema/producer_seq/type/payload only.
        Identity and observer timestamps are supplied here, never by reporters.
        """
        with self._lock:
            grant = self._grant(project_id, caller, run_id, token)
            if not isinstance(events, list) or not 0 < len(events) <= MAX_BATCH_EVENTS:
                raise ValueError('Invalid telemetry batch')
            # Detach nested payloads from the caller before validation/persistence.
            events = json.loads(_encoded(events, MAX_BATCH_BYTES))
            # Shared lock and frozen envelope are deliberate private store seams.
            # No store implementation or on-disk format changes for batching.
            with runs._LOCK:
                record = self.store._read(project_id, run_id)
                if record['enrollment']['owner_session_id'] != grant.owner.session_id:
                    raise IngestRefused('Owner mismatch')
                history = record['events']
                known = {(e['producer_id'], e['producer_seq']): e for e in history}
                additions: list[dict[str, Any]] = []
                at = self._clock()
                for raw in events:
                    if (not isinstance(raw, dict) or set(raw) != {
                            'schema', 'producer_seq', 'type', 'payload'}
                            or type(raw['producer_seq']) is not int
                            or not 1 <= raw['producer_seq'] <= 2**53 - 1):
                        raise ValueError('Invalid producer event')
                    event = dict(raw, run_id=run_id, producer_id=grant.receipt.producer_id,
                                 observed_at=at)
                    key = (grant.receipt.producer_id, raw['producer_seq'])
                    prior = known.get(key)
                    if prior is not None:
                        event['observed_at'] = prior['observed_at']
                    try:
                        # Validate even events ignored by a frozen terminal record.
                        runs.replay(record['enrollment'], [event])
                    except (ValueError, TypeError, KeyError, OverflowError):
                        raise ValueError('Invalid producer event') from None
                    if prior is not None:
                        if prior != event:
                            raise ValueError('Conflicting producer sequence')
                    else:
                        known[key] = event
                        additions.append(event)
                # Full replay also catches conflicting phase/collection revisions.
                snapshot = runs.replay(record['enrollment'], [*history, *additions])
                if additions and record['snapshot']['state'] not in runs.TERMINAL_STATES:
                    record['events'] = [*history, *additions]
                    record['snapshot'] = snapshot
                    write_json_atomic(self.store._path(project_id, run_id), record,
                                      allow_nan=False)
                return copy.deepcopy(record['snapshot'])

    def create_spool(self, project_id: str, caller: Any, run_id: str, token: str) -> Path:
        """Create the only permissible attachment; never open a producer path.

        This storage directory is server-owned, outside the target workspace.
        O_EXCL ensures the descriptor belongs to a new file, even during a race.
        """
        with self._lock:
            grant = self._grant(project_id, caller, run_id, token)
            if grant.spool_fd is not None:
                raise ValueError('Spool already created')
            folder = self.store.root.parent / 'test_run_spools'
            folder.mkdir(parents=True, exist_ok=True)
            if folder.resolve() != folder.absolute():
                raise IngestRefused('Spool directory redirected')
            path = folder / (uuid.uuid4().hex + '.jsonl')
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            flags |= getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(path, flags, 0o600)
            grant.spool_fd, grant.spool_path = fd, path
            return path

    def attach_spool(self, project_id: str, caller: Any, run_id: str, token: str,
                     path: str) -> None:
        """Confirm a minted attachment by exact name, with no path resolution/read."""
        with self._lock:
            grant = self._grant(project_id, caller, run_id, token)
            if grant.spool_path is None or path != str(grant.spool_path):
                raise IngestRefused('Spool was not created for this producer')

    def poll_spool(self, project_id: str, caller: Any, run_id: str, token: str) -> int:
        """Read a bounded JSONL batch from the pinned file; advance only on commit.

        Partial trailing records remain on disk. Errors propagate without
        advancing; there is no silent detail drop or fallback to another file.
        """
        with self._lock:
            grant = self._grant(project_id, caller, run_id, token)
            fd = grant.spool_fd
            if fd is None:
                raise IngestRefused('No producer spool')
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size < grant.offset:
                raise IngestRefused('Spool identity or size changed')
            os.lseek(fd, grant.offset, os.SEEK_SET)
            chunk = os.read(fd, SPOOL_READ_BYTES)
            lines = chunk.split(b'\n')
            complete = lines[:-1][:MAX_BATCH_EVENTS]
            if any(len(line) > runs.MAX_EVENT_BYTES for line in complete) or (
                    not complete and len(chunk) > runs.MAX_EVENT_BYTES):
                raise ValueError('Spool event exceeds size limit')
            if not complete:
                return 0
            try:
                events = [json.loads(line, object_pairs_hook=_unique_object) for line in complete]
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError('Invalid spool JSON') from None
            self.submit(project_id, caller, run_id, token, events)
            grant.offset += sum(len(line) + 1 for line in complete)
            return len(complete)

    def revoke(self, project_id: str, caller: Any, run_id: str, token: str) -> None:
        with self._lock:
            grant = self._grant(project_id, caller, run_id, token)
            if grant.spool_fd is not None:
                os.close(grant.spool_fd)
            del self._grants[hashlib.sha256(token.encode()).hexdigest()]

    def close(self) -> None:
        """Server teardown: release only descriptors owned by this service."""
        with self._lock:
            for grant in self._grants.values():
                if grant.spool_fd is not None:
                    os.close(grant.spool_fd)
            self._grants.clear()
