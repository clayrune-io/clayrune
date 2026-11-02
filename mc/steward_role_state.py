"""Per-assignment runtime state for steward roles (BUILTINS-3 SR-4).

One JSON file per (project, schedule): ``<data_root>/steward/roles/<project>/
<schedule>.json``. It lives beside ``steward/fence-settings.json`` and OUTSIDE
``data/projects/`` (CLAUDE.md "DATA_DIR pollution"), so ``load_projects()``
never sees it. The journal is the narrative record; this file is only the
machine state a later cycle needs: sessions, timestamps, the refresh
watermark, the in-flight claim and the decision ledger.

Fails closed. A file that is present but unreadable (NUL bytes from a crash,
bad JSON, a future version, ids that do not match its path) raises
``StateError``. It is never replaced by an empty default, because that would
silently forget a pending decision or a claim and let a second cycle run.
Writes are serialised per assignment and go through
``mc.atomic_json.write_json_atomic`` (fsync before replace).

Nothing here stores a credential or a mail body: decision records carry a
fixed key set of bounded scalars, and ``reply_decision`` is an enum.
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log
from mc.steward_role_model import RoleError, identifier

STATE_VERSION = 1
MAX_STR = 400
MAX_DECISIONS = 200

DECISION_STATUS = frozenset({
    'held',              # not sent; retryable (reason says why)
    'sending',           # write-ahead marker; seen at rest => a crashed send
    'sent',              # mailer accepted and verified; awaiting a reply
    'delivery_unknown',  # outcome uncertain; never resent blindly
    'answered',          # a reply was read; reply_decision holds the enum
    'reported',          # routed to the spawner or the attended chat
    'resolved',          # closed by explicit review
})
OPEN_STATUS = frozenset({'sent', 'delivery_unknown'})
REPLY_DECISIONS = frozenset({'approved', 'declined'})

_DECISION_FIELDS: dict[str, tuple[type, ...]] = {
    'id': (str,), 'key': (str,), 'route': (str,), 'status': (str,),
    'reason': (str,), 'subject': (str,), 'created_at': (str,),
    'email_attempted_at': (str, type(None)), 'cap_counted_at': (str, type(None)),
    'sent_at': (str, type(None)), 'attempts': (int,),
    'reply_decision': (str, type(None)), 'reply_checked_at': (str, type(None)),
    'last_reported_status': (str,),
}
_STATE_FIELDS: dict[str, tuple[type, ...]] = {
    'version': (int,), 'project_id': (str,), 'schedule_id': (str,),
    'prior_session_id': (str,), 'current_session_id': (str,),
    'snapshot_hashes': (dict,), 'last_attempt_at': (str, type(None)),
    'last_completed_at': (str, type(None)), 'refresh_watermark': (str, type(None)),
    'claim': (dict, type(None)), 'last_result': (dict, type(None)),
    'decisions': (list,),
}
_CLAIM_FIELDS = frozenset({'id', 'trigger', 'claimed_at', 'state'})
# preparing: claimed, task not yet handed over. delivery_unknown: handed over but
# not acknowledged (or the process died mid-handoff); never auto-cleared, never
# re-dispatched. accepted: the delivery path acknowledged the cycle task.
CLAIM_STATES = frozenset({'preparing', 'delivery_unknown', 'accepted'})
_RESULT_FIELDS = frozenset({'event', 'at', 'detail'})


class StateError(RoleError):
    """Role state is unreadable, invalid or unwritable; callers must stop."""


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now().astimezone()).astimezone().isoformat(timespec='seconds')


def _parse(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StateError(f'{label} is not an ISO timestamp') from exc
    if parsed.tzinfo is None:
        raise StateError(f'{label} must carry a UTC offset')
    return parsed


def _check_scalars(record: dict, fields: dict[str, tuple[type, ...]], label: str) -> None:
    unknown = set(record) - set(fields)
    if unknown:
        raise StateError(f'{label} has unknown keys: {sorted(unknown)}')
    for name, types in fields.items():
        if name in record and (type(record[name]) not in types):
            raise StateError(f'{label}.{name} has the wrong type')
        if isinstance(record.get(name), str) and (
                len(record[name]) > MAX_STR or '\x00' in record[name]):
            raise StateError(f'{label}.{name} is oversized or contains NUL')


def _fresh(project_id: str, schedule_id: str) -> dict[str, Any]:
    return {
        'version': STATE_VERSION, 'project_id': project_id, 'schedule_id': schedule_id,
        'prior_session_id': '', 'current_session_id': '', 'snapshot_hashes': {},
        'last_attempt_at': None, 'last_completed_at': None, 'refresh_watermark': None,
        'claim': None, 'last_result': None, 'decisions': [],
    }


def validate(state: Any, project_id: str, schedule_id: str) -> dict[str, Any]:
    """Return ``state`` if it is a well-formed record for exactly this assignment."""
    if not isinstance(state, dict):
        raise StateError('role state must be a JSON object')
    if set(state) != set(_STATE_FIELDS):
        raise StateError('role state keys do not match the v1 schema')
    _check_scalars(state, _STATE_FIELDS, 'state')
    if state['version'] != STATE_VERSION:
        raise StateError(f'unsupported role state version {state["version"]!r}')
    if state['project_id'] != project_id or state['schedule_id'] != schedule_id:
        raise StateError('role state ids do not match its path')
    for name, value in state['snapshot_hashes'].items():
        if (not isinstance(name, str) or not isinstance(value, str)
                or len(name) > 64 or len(value) > 128):
            raise StateError('snapshot_hashes must map short strings to short strings')
    for name in ('last_attempt_at', 'last_completed_at', 'refresh_watermark'):
        if state[name] is not None:
            _parse(state[name], name)
    claim = state['claim']
    if claim is not None:
        if set(claim) != _CLAIM_FIELDS:
            raise StateError('claim must have exactly id, trigger, claimed_at, state')
        _check_scalars(claim, {k: (str,) for k in _CLAIM_FIELDS}, 'claim')
        if claim['state'] not in CLAIM_STATES:
            raise StateError(f'unknown claim state {claim["state"]!r}')
        _parse(claim['claimed_at'], 'claim.claimed_at')
    result = state['last_result']
    if result is not None:
        if set(result) != _RESULT_FIELDS:
            raise StateError('last_result must have exactly event, at, detail')
        _check_scalars(result, {k: (str,) for k in _RESULT_FIELDS}, 'last_result')
    seen: set[str] = set()
    for dec in state['decisions']:
        if not isinstance(dec, dict) or set(dec) != set(_DECISION_FIELDS):
            raise StateError('decision records must carry exactly the v1 keys')
        _check_scalars(dec, _DECISION_FIELDS, 'decision')
        if dec['status'] not in DECISION_STATUS:
            raise StateError(f'unknown decision status {dec["status"]!r}')
        if dec['reply_decision'] is not None and dec['reply_decision'] not in REPLY_DECISIONS:
            raise StateError('reply_decision must be approved or declined')
        if dec['id'] in seen:
            raise StateError('duplicate decision id')
        seen.add(dec['id'])
    return state


_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = Lock()


def lock_for(path: Path) -> RLock:
    """One re-entrant lock per state file (shared by state writes and comms)."""
    key = str(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, RLock())


class RoleStateStore:
    """Reads and writes role state under an application data root.

    ``data_root`` is the server's configured data directory, never a cwd or an
    agent-supplied path. The caller wires it (see ``steward._config.CFG``).
    """

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)

    def path(self, project_id: str, schedule_id: str) -> Path:
        pid = identifier(project_id, 'project_id')
        sid = identifier(schedule_id, 'schedule_id')
        base = (self.data_root / 'steward' / 'roles').resolve()
        target = (base / pid / f'{sid}.json').resolve()
        if not target.is_relative_to(base):
            raise StateError('role state path escapes the steward roles directory')
        return target

    def lock(self, project_id: str, schedule_id: str) -> RLock:
        return lock_for(self.path(project_id, schedule_id))

    def read(self, project_id: str, schedule_id: str) -> dict[str, Any]:
        """Current state; a never-written assignment yields a fresh default."""
        path = self.path(project_id, schedule_id)
        with lock_for(path):
            return self._read_locked(path, project_id, schedule_id)

    def _read_locked(self, path: Path, project_id: str, schedule_id: str) -> dict[str, Any]:
        if not path.exists():
            return _fresh(project_id, schedule_id)
        try:
            text = read_text_with_retry(path)
        except (OSError, UnicodeError) as exc:
            _log(f'[steward-role-state] read {path.name} failed: {exc}')
            raise StateError(f'role state unreadable: {exc}') from exc
        if '\x00' in text:
            _log(f'[steward-role-state] {path.name} contains NUL bytes; refusing to load')
            raise StateError('role state contains NUL bytes (crash-damaged); refusing to load')
        try:
            data = json.loads(text)
        except ValueError as exc:
            _log(f'[steward-role-state] {path.name} is not valid JSON: {exc}')
            raise StateError(f'role state is not valid JSON: {exc}') from exc
        return validate(data, project_id, schedule_id)

    def update(self, project_id: str, schedule_id: str,
               mutate: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        """Read, apply ``mutate`` to a copy, validate, write. Returns the new state.

        ``mutate`` raising leaves the stored file untouched.
        """
        path = self.path(project_id, schedule_id)
        with lock_for(path):
            state = copy.deepcopy(self._read_locked(path, project_id, schedule_id))
            mutate(state)
            state['decisions'] = state['decisions'][-MAX_DECISIONS:]
            validate(state, project_id, schedule_id)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(path, state, indent=2, ensure_ascii=False, sort_keys=True)
            except (OSError, TypeError, ValueError) as exc:
                _log(f'[steward-role-state] write {path.name} failed: {exc}')
                raise StateError(f'role state write failed: {exc}') from exc
            return state

    # ── narrow operations the fire path and notifier use ──────────────────

    def acquire_claim(self, project_id: str, schedule_id: str, claim_id: str,
                      trigger: str, now: datetime | None = None) -> tuple[bool, dict | None]:
        """Take the single-cycle claim (state ``preparing``). An existing claim is
        never stolen, however old: only ``release_claim`` with its id clears it
        (spec section 4.3)."""
        identifier(claim_id, 'claim_id')
        won: list[bool] = [False]

        def mutate(state: dict[str, Any]) -> None:
            if state['claim'] is None:
                state['claim'] = {'id': claim_id, 'trigger': str(trigger)[:MAX_STR],
                                  'claimed_at': _stamp(now), 'state': 'preparing'}
                won[0] = True
        state = self.update(project_id, schedule_id, mutate)
        return won[0], (None if won[0] else state['claim'])

    def set_claim_state(self, project_id: str, schedule_id: str, claim_id: str,
                        new_state: str) -> bool:
        """Move the held claim to ``delivery_unknown`` or ``accepted``. A claim
        that is not held under ``claim_id`` is left alone and returns False."""
        if new_state not in CLAIM_STATES:
            raise StateError(f'unknown claim state {new_state!r}')
        moved: list[bool] = [False]

        def mutate(state: dict[str, Any]) -> None:
            claim = state['claim']
            if claim is not None and claim['id'] == claim_id:
                claim['state'] = new_state
                moved[0] = True
        self.update(project_id, schedule_id, mutate)
        return moved[0]

    def accept_delivery(self, project_id: str, schedule_id: str, claim_id: str,
                        delivered_at: datetime) -> bool:
        """The delivery path acknowledged the cycle task: mark the claim accepted
        AND advance the refresh watermark in one write. The watermark never moves
        any other way, so an unacknowledged launch cannot consume unseen deltas."""
        done: list[bool] = [False]
        stamp = _stamp(delivered_at)

        def mutate(state: dict[str, Any]) -> None:
            claim = state['claim']
            if claim is None or claim['id'] != claim_id:
                return
            claim['state'] = 'accepted'
            old = state['refresh_watermark']
            if old is None or _parse(stamp, 'watermark') > _parse(old, 'refresh_watermark'):
                state['refresh_watermark'] = stamp
            done[0] = True
        self.update(project_id, schedule_id, mutate)
        return done[0]

    def release_claim(self, project_id: str, schedule_id: str, claim_id: str) -> bool:
        released: list[bool] = [False]

        def mutate(state: dict[str, Any]) -> None:
            if state['claim'] is not None and state['claim']['id'] == claim_id:
                state['claim'] = None
                released[0] = True
        self.update(project_id, schedule_id, mutate)
        return released[0]

    def record_attempt(self, project_id: str, schedule_id: str, *,
                       snapshot_hashes: dict[str, str] | None = None,
                       now: datetime | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state['last_attempt_at'] = _stamp(now)
            if snapshot_hashes is not None:
                state['snapshot_hashes'] = dict(snapshot_hashes)
        return self.update(project_id, schedule_id, mutate)

    def record_completion(self, project_id: str, schedule_id: str,
                          now: datetime | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state['last_completed_at'] = _stamp(now)
        return self.update(project_id, schedule_id, mutate)

    def record_session(self, project_id: str, schedule_id: str, session_id: str) -> dict[str, Any]:
        sid = identifier(session_id, 'session_id')

        def mutate(state: dict[str, Any]) -> None:
            if state['current_session_id'] != sid:
                state['prior_session_id'] = state['current_session_id']
                state['current_session_id'] = sid
        return self.update(project_id, schedule_id, mutate)

    def set_result(self, project_id: str, schedule_id: str, event: str, detail: str = '',
                   now: datetime | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state['last_result'] = {'event': event[:MAX_STR], 'at': _stamp(now),
                                    'detail': detail[:MAX_STR]}
        return self.update(project_id, schedule_id, mutate)
