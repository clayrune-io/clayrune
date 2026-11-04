"""Durable post-merge review evidence (BUILTINS-4 HR-3).

This leaf never runs git, dispatches a process, or advances integration. The
integrator supplies freshly observed candidate pins under its landing lock;
HR-4 supplies the real request, never a reviewer id from its body. See
docs/HIVEMIND_REVIEW_RECORDS.md for the handoff/consumption contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from mc import hivemind_lineage, review_lineage
from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log, now_iso
from mc.hivemind_roster import RosterError, parse_character_ref
from mc.merge_review_gate import CHANGES_REQUESTED, PASS, VERDICTS, ReviewRefused

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
_SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
_lock = threading.RLock()


@dataclass(frozen=True)
class Candidate:
    """Server-owned observations, including ALL contributing worker owners.

    workstream_attempt is HR-2's assignment_attempt. integration_attempt is
    the integration owner's attempt, not a reviewer-controlled retry counter.
    """

    project_id: str
    project_generation: int
    hivemind_id: str
    workstream_id: str
    integration_attempt: int
    workstream_attempt: int
    pre_merge_sha: str
    incoming_branch: str
    incoming_tip: str
    merged_sha: str
    merged_tree: str
    contributing_sessions: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ('project_id', 'hivemind_id', 'workstream_id'):
            _identifier(getattr(self, name), name)
        for name in ('project_generation', 'integration_attempt', 'workstream_attempt'):
            value = getattr(self, name)
            minimum = 0 if name == 'project_generation' else 1
            if type(value) is not int or value < minimum:
                raise ReviewRefused(400, f'invalid {name}')
        for name in ('pre_merge_sha', 'incoming_tip', 'merged_sha', 'merged_tree'):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA.fullmatch(value):
                raise ReviewRefused(400, f'{name} must be a full lowercase SHA')
        if len({len(self.pre_merge_sha), len(self.incoming_tip), len(self.merged_sha),
                len(self.merged_tree)}) != 1:
            raise ReviewRefused(400, 'candidate mixes Git object formats')
        branch = self.incoming_branch
        if (not isinstance(branch, str) or not branch or len(branch) > 250
                or branch.startswith('-') or any(ord(c) < 32 for c in branch)):
            raise ReviewRefused(400, 'invalid incoming_branch')
        if not isinstance(self.contributing_sessions, tuple) or not self.contributing_sessions:
            raise ReviewRefused(400, 'candidate must pin contributing worker sessions')
        for sid in self.contributing_sessions:
            _identifier(sid, 'contributing session')
        if len(set(self.contributing_sessions)) != len(self.contributing_sessions):
            raise ReviewRefused(400, 'duplicate contributing worker session')

    def pins(self) -> dict[str, Any]:
        pins = asdict(self)
        pins['contributing_sessions'] = sorted(self.contributing_sessions)
        return pins

    @property
    def key(self) -> str:
        return _digest(self.pins())


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ReviewRefused(400, f'invalid {name}')


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate review record key')
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError(f'invalid JSON constant {value}')


def _independent(reviewer: str, candidate: Candidate,
                 spawner_of: review_lineage.SpawnerOf | None = None) -> None:
    if reviewer in candidate.contributing_sessions:
        raise ReviewRefused(403, 'self-review refused: reviewer is a contributing worker')
    for owner in candidate.contributing_sessions:
        review_lineage.check(reviewer, owner, spawner_of)


class ReviewStore:
    """One run's records under data/hiveminds/<id>/reviews, outside DATA_DIR.

    run_dir comes from the server's existing hivemind directory resolver.
    No body-supplied path is accepted by the future route adapter.
    """

    def __init__(self, run_dir: Path):
        self.directory = Path(run_dir) / 'reviews'

    def path(self, candidate: Candidate) -> Path:
        return self.directory / f'{candidate.key}.json'

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # Short state transactions only; never hold this across reviewer wait
        # or git. The byte lock also serializes overlapping server processes.
        with _lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with (self.directory / '.lock').open('a+b') as stream:
                    if stream.tell() == 0:
                        stream.write(b'0')
                        stream.flush()
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    # Closing releases the OS lock on every exit path.
                    yield
            except OSError as exc:
                _log(f'[hivemind-review] store transaction failed: {exc}', flush=True)
                raise ReviewRefused(409, 'review store unavailable or busy; integration held') from exc

    def _write(self, candidate: Candidate, record: dict) -> None:
        payload = {**record, 'content_digest': _digest(record)}
        write_json_atomic(self.path(candidate), payload, ensure_ascii=False, indent=2,
                          allow_nan=False)

    def _read(self, candidate: Candidate) -> dict:
        try:
            rec = json.loads(read_text_with_retry(self.path(candidate)),
                             object_pairs_hook=_json_object, parse_constant=_invalid_constant)
        except (OSError, ValueError, UnicodeError) as exc:
            _log(f'[hivemind-review] record unavailable for {candidate.key[:12]}: {exc}', flush=True)
            raise ReviewRefused(409, 'missing or unreadable review record; integration held') from exc
        keys = {'schema_version', 'candidate', 'reviewer_session', 'reviewer_character',
                'designated_at', 'result', 'consumed_at', 'content_digest'}
        if (not isinstance(rec, dict) or set(rec) != keys
                or type(rec['schema_version']) is not int or rec['schema_version'] != 1
                or rec['candidate'] != candidate.pins()
                or rec['content_digest'] != _digest({k: v for k, v in rec.items() if k != 'content_digest'})):
            raise ReviewRefused(409, 'corrupt or mismatched review record; integration held')
        try:
            stored_pins = dict(rec['candidate'])
            stored_pins['contributing_sessions'] = tuple(stored_pins['contributing_sessions'])
            if Candidate(**stored_pins).pins() != candidate.pins():
                raise ValueError('invalid candidate pins')
            _identifier(rec['reviewer_session'], 'reviewer session')
            if parse_character_ref(rec['reviewer_character']) != rec['reviewer_character']:
                raise ValueError('noncanonical reviewer character')
            if rec['reviewer_session'] in candidate.contributing_sessions:
                raise ValueError('reviewer is an author')
            if not isinstance(rec['designated_at'], str) or not rec['designated_at']:
                raise ValueError('missing designation timestamp')
            result = rec['result']
            if result is not None:
                if (not isinstance(result, dict)
                        or set(result) != {'verdict', 'report_path', 'recorded_at', 'attribution'}
                        or result['verdict'] not in VERDICTS
                        or result['attribution'] != 'attributed'
                        or not isinstance(result['recorded_at'], str) or not result['recorded_at']
                        or not isinstance(result['report_path'], str)
                        or len(result['report_path']) > 2000 or '\x00' in result['report_path']):
                    raise ValueError('malformed result')
            if (not isinstance(rec['consumed_at'], str)
                    or (rec['consumed_at'] and (result is None or result['verdict'] != PASS))):
                raise ValueError('invalid consumption state')
        except (ReviewRefused, RosterError, ValueError, TypeError, KeyError) as exc:
            raise ReviewRefused(409, 'corrupt review ownership/result; integration held') from exc
        return rec

    def designate(self, candidate: Candidate, *, reviewer_session: str,
                  reviewer_character: str) -> dict:
        """Persist exact ownership BEFORE HR-4 launches anything.

        An exact repeat reuses ownership, never authorizes another launch.
        Corrupt existing records are retained and refused, never reset.
        """
        _identifier(reviewer_session, 'reviewer session')
        if reviewer_session in candidate.contributing_sessions:
            raise ReviewRefused(403, 'self-review refused: reviewer is a contributing worker')
        try:
            character = parse_character_ref(reviewer_character)
        except RosterError as exc:
            raise ReviewRefused(400, str(exc)) from exc
        with self._transaction():
            path = self.path(candidate)
            if path.exists():
                old = self._read(candidate)
                if (old['reviewer_session'], old['reviewer_character']) != (reviewer_session, character):
                    raise ReviewRefused(409, 'candidate already has a designated reviewer')
                return old
            rec = {'schema_version': 1, 'candidate': candidate.pins(),
                   'reviewer_session': reviewer_session, 'reviewer_character': character,
                   'designated_at': now_iso(), 'result': None, 'consumed_at': ''}
            self._write(candidate, rec)
            return self._read(candidate)

    def record(self, candidate: Candidate, request: Any, *, verdict: str,
               report_path: str = '',
               spawner_of: review_lineage.SpawnerOf | None = None) -> dict:
        """Only the OS-attributed designated reviewer can submit a verdict.

        spawner_of is an internal test/adapter seam, never a request field.
        Native subprocesses attribute to their managed owner; separately
        managed native children must carry the same durable dispatch ancestry.
        Unknown children/ancestors fail closed through the existing checker.
        """
        reviewer = hivemind_lineage.creator_of_request(request)
        if not reviewer:
            raise ReviewRefused(403, 'caller is not attributed to a managed reviewer session')
        if verdict not in VERDICTS:
            raise ReviewRefused(400, f'verdict must be one of {list(VERDICTS)}')
        if not isinstance(report_path, str) or len(report_path) > 2000 or '\x00' in report_path:
            raise ReviewRefused(400, 'invalid report_path')
        with self._transaction():
            rec = self._read(candidate)
            _independent(reviewer, candidate, spawner_of)
            if reviewer != rec['reviewer_session']:
                raise ReviewRefused(403, 'caller is not the designated reviewer session')
            if rec['result'] is not None:
                if (rec['result']['verdict'], rec['result']['report_path']) != (verdict, report_path):
                    raise ReviewRefused(409, 'conflicting review replay; original result retained')
                return rec
            rec.pop('content_digest')
            rec['result'] = {'verdict': verdict, 'report_path': report_path,
                             'recorded_at': now_iso(), 'attribution': 'attributed'}
            self._write(candidate, rec)
            return self._read(candidate)

    def find(self, candidate: Candidate) -> dict:
        """Read an exact record; absence/corruption is a hold, never a pass."""
        with self._transaction():
            return self._read(candidate)

    def hold_reason(self, candidate: Candidate) -> str:
        try:
            rec = self.find(candidate)
        except ReviewRefused as exc:
            return exc.message
        if rec['result'] is None:
            return 'awaiting designated reviewer verdict'
        if rec['result']['verdict'] == CHANGES_REQUESTED:
            return 'review requested changes; provisional candidate held'
        return ''

    def consume_pass(self, candidate: Candidate) -> bool:
        """Durably claim an exact pass once; duplicate consumers return False.

        Called only after the integrator revalidates HEAD/tree, workstream tip,
        generation and attempt under its existing landing ownership. This
        marker is evidence, not a command to merge; recovery must reconcile
        integration's durable cursor if it crashed after marking consumption.
        """
        with self._transaction():
            rec = self._read(candidate)
            if rec['result'] is None or rec['result']['verdict'] != PASS:
                raise ReviewRefused(409, 'no exact passing review; integration held')
            if rec['consumed_at']:
                return False
            rec.pop('content_digest')
            rec['consumed_at'] = now_iso()
            self._write(candidate, rec)
            return True
