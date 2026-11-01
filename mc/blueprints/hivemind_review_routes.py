"""Reviewer verdict HTTP adapter (BUILTINS-4 HR-4b).

GET/POST /api/hivemind/<hivemind_id>/review/<workstream_id>/<integration_attempt>

HTTP only. Every rule about WHO may pass a candidate lives in
`mc.hivemind_review.ReviewStore.record` (OS-attributed caller, designated
reviewer, independence from every contributing worker, immutable first result).
This module resolves the candidate from the server's own records, checks the
project generation, translates `ReviewRefused` into the status it carries, and
never takes a reviewer identity, run directory or authoritative pin from the
request. Local auth is the app-wide `local_auth_gate` before_request, exactly
as for the other /api/hivemind routes. See docs/HIVEMIND_REVIEW_ROUTES.md.
"""
from __future__ import annotations

import importlib
import json
import re
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from flask import Blueprint, jsonify, request

from mc import hivemind_review as hr
from mc.core import _log
from mc.merge_review_gate import ReviewRefused

bp = Blueprint('hivemind_review_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
HIVEMIND_DIR: Path = None  # type: ignore[assignment]
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
_assert_project_generation: Callable[..., Any] = None  # type: ignore[assignment]
# Lifecycle seam (HR-4a), REQUIRED: a missing guard would admit verdicts from
# cancelled or timed-out reviewers. _verdict_guard(candidate) returns a context
# manager that raises ReviewRefused to hold a verdict; _reviewer_status(candidate)
# returns HR-4a's status dict (state, hold_reason, result, can_record, ...).
_verdict_guard: Callable[[hr.Candidate], AbstractContextManager] = None  # type: ignore[assignment]
_reviewer_status: Callable[[hr.Candidate], dict] = None  # type: ignore[assignment]

# HR-4a keeps <key>.runtime.json beside each review record. It is not a review
# record: skipped by exact name, while any other damaged *.json still counts.
_RUNTIME_SIDECAR = re.compile(r'[0-9a-f]{64}\.runtime\.json')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
_POST_KEYS = {'verdict', 'merged_sha', 'report_path', 'incoming_tip', 'project_generation'}
# Identity-shaped keys get a specific refusal: the answer is never "ignored".
_IDENTITY_KEYS = {'reviewer', 'reviewer_session', 'reviewer_id', 'reviewer_character',
                  'session_id', 'session', 'character', 'run_dir', 'candidate'}


def wire(*, hivemind_dir, load_project_fn, assert_project_generation_fn,
         verdict_guard_fn, reviewer_status_fn):
    """Late-bind server dependencies. Called once from server.py before the
    blueprint is registered. Both lifecycle callables are required keywords;
    production passes lifecycle_for(hivemind_routes)."""
    global HIVEMIND_DIR, load_project, _assert_project_generation
    global _verdict_guard, _reviewer_status
    HIVEMIND_DIR = Path(hivemind_dir)
    load_project = load_project_fn
    _assert_project_generation = assert_project_generation_fn
    _verdict_guard = verdict_guard_fn
    _reviewer_status = reviewer_status_fn


def _reviewer_module():
    """HR-4a's runtime, imported lazily. Fails closed: if it is absent every
    verdict is held and every state read shows a hold, never a pass."""
    try:
        module = importlib.import_module('mc.hivemind_reviewer')
        if not all(callable(getattr(module, n, None)) for n in ('verdict_guard', 'reviewer_status')):
            raise ImportError('mc.hivemind_reviewer lacks verdict_guard/reviewer_status')
    except ImportError as exc:
        _log(f'[hivemind-review] reviewer lifecycle unavailable: {exc}', flush=True)
        raise ReviewRefused(409, 'reviewer lifecycle unavailable; integration held') from exc
    return module


def lifecycle_for(api):
    """(verdict_guard_fn, reviewer_status_fn) bound to the hivemind_routes
    module `api`, resolving mc.hivemind_reviewer at call time."""
    @contextmanager
    def guard(candidate: hr.Candidate) -> Iterator[None]:
        with _reviewer_module().verdict_guard(api, candidate):
            yield

    def status(candidate: hr.Candidate) -> dict:
        return _reviewer_module().reviewer_status(api, candidate)

    return guard, status


class _Reject(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def verdict_path(candidate: hr.Candidate) -> str:
    """The server-relative path a reviewer POSTs its verdict to. The launcher
    prefixes the scheme/host/port it already knows; it must not build this by
    hand."""
    return (f'/api/hivemind/{candidate.hivemind_id}/review/'
            f'{candidate.workstream_id}/{candidate.integration_attempt}')


def _err(status: int, message: str, **extra):
    return jsonify({'error': message, **extra}), status


def _manifest(hivemind_id: str) -> dict | None:
    if not _ID.fullmatch(hivemind_id or ''):
        return None
    try:
        data = json.loads((HIVEMIND_DIR / hivemind_id / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _scan(store: hr.ReviewStore, workstream_id: str, attempt: int):
    """(matches, unreadable): validated (candidate, record) pairs for this
    workstream attempt, plus a count of files that could not be trusted.

    The store keeps no index, so the candidate is rebuilt from the pins inside
    each record file and then re-validated by ReviewStore.find, which checks the
    digest and that the pins hash to the file name. A file that fails either
    check is counted, never skipped silently."""
    matches: list[tuple[hr.Candidate, dict]] = []
    unreadable = 0
    try:
        paths = sorted(store.directory.glob('*.json'))
    except OSError as exc:
        _log(f'[hivemind-review] cannot list review records: {exc}', flush=True)
        return matches, 1
    for path in paths:
        if _RUNTIME_SIDECAR.fullmatch(path.name):
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            pins = dict(raw['candidate'])
            pins['contributing_sessions'] = tuple(pins['contributing_sessions'])
            candidate = hr.Candidate(**pins)
            if candidate.key != path.stem:
                raise ValueError('record name does not match its pins')
            if (candidate.workstream_id, candidate.integration_attempt) != (workstream_id, attempt):
                continue
            matches.append((candidate, store.find(candidate)))
        except (ReviewRefused, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            unreadable += 1
            _log(f'[hivemind-review] untrusted record {path.name}: {exc}', flush=True)
    return matches, unreadable


def _resolve(hivemind_id: str, workstream_id: str, attempt: int, claimed: dict):
    """(store, candidate, record), or raises _Reject. `claimed` pins narrow the
    server's own records; they never create or alter a candidate."""
    manifest = _manifest(hivemind_id)
    if manifest is None:
        raise _Reject(404, 'hivemind not found')
    project_id = manifest.get('project_id')
    if not isinstance(project_id, str) or not load_project(project_id):
        raise _Reject(404, 'project not found')
    if not _ID.fullmatch(workstream_id or ''):
        raise _Reject(404, 'review not found')
    store = hr.ReviewStore(HIVEMIND_DIR / hivemind_id)
    matches, unreadable = _scan(store, workstream_id, attempt)
    chosen = [(c, r) for c, r in matches
              if all(c.pins().get(k) == v for k, v in claimed.items())]
    if len(chosen) > 1:
        raise _Reject(409, 'ambiguous review candidate; merged_sha and incoming_tip required')
    if not chosen:
        if unreadable:
            raise _Reject(409, 'review store has unreadable records; integration held')
        if matches:
            raise _Reject(409, 'claimed pins do not match the designated candidate')
        raise _Reject(404, 'no designated review for this workstream attempt')
    candidate, record = chosen[0]
    # The manifest's persisted generation is the run's identity; the lifecycle
    # service says whether that generation is still the project's current one.
    generation = manifest.get('project_generation')
    if candidate.project_generation != generation:
        raise _Reject(409, 'candidate project generation does not match the hivemind run')
    try:
        _assert_project_generation(project_id, generation)
    except Exception as exc:
        raise _Reject(409, f'project generation unavailable: {exc}') from exc
    return store, candidate, record


def _public(candidate: hr.Candidate, record: dict, store: hr.ReviewStore) -> dict:
    """Projection of one review. The lifecycle (HR-4a) is consulted on every
    read: a cancelled, timed-out or held reviewer shows no result and a hold
    reason, and an unavailable lifecycle is a hold, never green."""
    evidence = store.hold_reason(candidate)
    try:
        life = _reviewer_status(candidate)
        result = life.get('result')
        hold = life.get('hold_reason') or ''
        if life.get('state') != 'pass' and not hold and not life.get('can_record'):
            hold = f"reviewer {life.get('state') or 'held'}"
        lifecycle = {k: life.get(k) for k in ('state', 'can_record', 'deadline_at', 'stop_error')}
    except ReviewRefused as exc:
        result, hold, lifecycle = None, exc.message, None
    except Exception as exc:
        _log(f'[hivemind-review] lifecycle status failed: {exc}', flush=True)
        result, hold, lifecycle = None, 'reviewer lifecycle unavailable; integration held', None
    return {'candidate_key': candidate.key, 'candidate': candidate.pins(),
            'reviewer_session': record['reviewer_session'],
            'reviewer_character': record['reviewer_character'],
            'designated_at': record['designated_at'], 'result': result,
            'consumed_at': record['consumed_at'], 'lifecycle': lifecycle,
            'hold_reason': hold or evidence}


def _claimed_from_query() -> dict:
    claimed: dict[str, Any] = {}
    for key in ('merged_sha', 'incoming_tip'):
        value = request.args.get(key)
        if value:
            claimed[key] = value
    return claimed


@bp.route('/api/hivemind/<hivemind_id>/review/<workstream_id>/<int:integration_attempt>')
def review_state(hivemind_id, workstream_id, integration_attempt):
    """Review state for the designated candidate of one integration attempt."""
    try:
        store, candidate, record = _resolve(
            hivemind_id, workstream_id, integration_attempt, _claimed_from_query())
    except _Reject as exc:
        return _err(exc.status, exc.message)
    return jsonify({'review': _public(candidate, record, store)})


@bp.route('/api/hivemind/<hivemind_id>/review/<workstream_id>/<int:integration_attempt>',
          methods=['POST'])
def review_submit(hivemind_id, workstream_id, integration_attempt):
    """Submit the structured verdict. {verdict, merged_sha, report_path?,
    incoming_tip?, project_generation?}: the pins are claims checked against the
    server's record, never inputs to it."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, 'JSON body required: {verdict, merged_sha, report_path?}')
    forged = sorted(_IDENTITY_KEYS & set(body))
    if forged:
        return _err(400, 'reviewer identity comes from the caller, not the body: '
                         + ', '.join(forged))
    unknown = sorted(set(body) - _POST_KEYS)
    if unknown:
        return _err(400, 'unknown field(s): ' + ', '.join(unknown))
    if not isinstance(body.get('merged_sha'), str) or not body['merged_sha']:
        return _err(400, 'merged_sha is required: the SHA the reviewer examined')
    if not isinstance(body.get('verdict'), str):
        return _err(400, 'verdict is required')
    claimed = {k: body[k] for k in ('merged_sha', 'incoming_tip', 'project_generation')
               if body.get(k) is not None}
    for key in ('merged_sha', 'incoming_tip'):
        if key in claimed and not isinstance(claimed[key], str):
            return _err(400, f'{key} must be a string')
    if 'project_generation' in claimed and type(claimed['project_generation']) is not int:
        return _err(400, 'project_generation must be an integer')
    try:
        store, candidate, _record = _resolve(
            hivemind_id, workstream_id, integration_attempt, claimed)
    except _Reject as exc:
        return _err(exc.status, exc.message)
    try:
        with _verdict_guard(candidate):
            rec = store.record(candidate, request, verdict=body['verdict'],
                               report_path=body.get('report_path') or '')
    except ReviewRefused as exc:
        _log(f'[hivemind-review] refused for {hivemind_id}/{workstream_id}: {exc.message}',
             flush=True)
        return _err(exc.status, exc.message, **exc.extra)
    except Exception as exc:
        _log(f'[hivemind-review] record failed for {hivemind_id}/{workstream_id}: {exc}',
             flush=True)
        return _err(500, 'could not record the review')
    return jsonify({'ok': True, 'review': _public(candidate, rec, store)})
