"""MC-1148 phases 4/4b: dark telemetry and launcher exit evidence, no execution."""
from __future__ import annotations

import atexit
from dataclasses import asdict
import json
from pathlib import Path
import threading
from typing import Any, Callable

from flask import Blueprint, abort, jsonify, request

from mc import caller_attribution, state
from mc.core import _log
from mc.test_run_ingest import IngestRefused, OwnerBinding, TestRunIngest, MAX_BATCH_BYTES
from mc.test_run_finalize import TestRunFinalize, FinalizeConflict, peer_process_identity
from mc.test_run_store import StoreError, TestRunStore

bp = Blueprint('test_run_routes', __name__)
FLAG = 'test_run_progress_enabled'
MAX_REQUEST_BYTES = MAX_BATCH_BYTES + 8192
_data_root: Path | None = None
_load_project: Callable[[str], Any] = lambda _: None
_load_agent_log: Callable[[str], list[dict[str, Any]]] = lambda _: []
_session_cwd: Callable[[dict[str, Any], str], str] = lambda s, p: s.get('_agent_cwd') or p
_proc_identity: Callable[[int], Any] = lambda _: (None, None)
_store: TestRunStore | None = None
_ingest: TestRunIngest | None = None
_finalize: TestRunFinalize | None = None
_lock = threading.RLock()


def wire(*, data_root: Path, load_project_fn: Callable[[str], Any],
         load_agent_log_fn: Callable[[str], list[dict[str, Any]]],
         session_cwd_fn: Callable[[dict[str, Any], str], str],
         proc_identity_fn: Callable[[int], Any]) -> None:
    """Bind server seams without constructing a store or ingest service."""
    global _data_root, _load_project, _load_agent_log, _session_cwd, _proc_identity, _store
    close()
    _data_root, _load_project = Path(data_root), load_project_fn
    _load_agent_log, _session_cwd = load_agent_log_fn, session_cwd_fn
    _proc_identity, _store = proc_identity_fn, None


def close() -> None:
    global _ingest, _finalize
    with _lock:
        if _finalize is not None:
            _finalize.close()
            _finalize = None
        if _ingest is not None:
            _ingest.close()
            _ingest = None


atexit.register(close)


@bp.before_request
def _enabled():
    if state.CONFIG.get(FLAG) is not True:
        close()
        abort(404)


@bp.after_request
def _private(response):
    # Receipts, paths and run details must not survive in browser/proxy caches.
    response.headers['Cache-Control'] = 'no-store'
    return response


def _attribution():
    return caller_attribution.attribute_caller(
        request.remote_addr or '', request.environ.get('REMOTE_PORT'),
        request.environ.get('SERVER_PORT'),
        caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))


def resolve_owner(project_id: str, caller: Any) -> OwnerBinding | None:
    """Only the HTTP peer's OS ancestry plus live server records bind an owner.

    Neither body fields nor browser Origin are evidence. Tracked job roots may
    resolve through their owning session, including between that session's turns.
    There is no HTTP project-service/project-only enrollment fallback.
    """
    if not isinstance(caller, caller_attribution.Attribution):
        return None
    if caller.status != caller_attribution.ATTRIBUTED or caller.pid is None:
        return None
    session = state.agent_sessions.get(caller.session_id)
    project = _load_project(project_id)
    if (not session or not project or session.get('project_id') != project_id
            or session.get('session_id', caller.session_id) != caller.session_id):
        return None
    # Recheck managed membership: a request cannot retain a stale root after
    # its process exits or the tracked/session registry changes.
    roots = caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes)
    if roots.get(caller.pid) != caller.session_id:
        return None
    tracked = state.tracked_processes.get(caller.pid)
    if tracked and (tracked.get('session_id') != caller.session_id
                    or tracked.get('project_id') != project_id):
        return None
    identity = peer_process_identity(
        caller, request.remote_addr or '', request.environ.get('REMOTE_PORT'),
        request.environ.get('SERVER_PORT'), roots, _proc_identity)
    if identity is None:
        return None
    return OwnerBinding(
        project_id, caller.session_id,
        bool(session.get('incognito') or project.get('_is_incognito_project')),
        bool(session.get('_codex_unattended_sandbox')),
        cwd=_session_cwd(session, project.get('project_path', '')),
        process_identity=identity, source='wrapper')


def _storage() -> TestRunStore:
    global _store
    with _lock:
        if _store is None:
            if _data_root is None:
                raise StoreError('Test-run routes not wired')
            _store = TestRunStore(_data_root)
        return _store


def _service() -> TestRunIngest:
    global _ingest
    with _lock:
        if _ingest is None:
            _ingest = TestRunIngest(_storage(), resolve_owner=resolve_owner)
        return _ingest


def _finalizer() -> TestRunFinalize:
    global _finalize
    with _lock:
        if _finalize is None:
            _finalize = TestRunFinalize(_service())
        return _finalize


def _session(project_id: str, session_id: str) -> None:
    session = state.agent_sessions.get(session_id)
    if session is not None:
        if session.get('project_id') != project_id:
            raise KeyError('Session mismatch')
        if session.get('incognito') or session.get('_codex_unattended_sandbox'):
            raise IngestRefused('Restricted session')
        return
    # Historical reads still work after live session teardown.
    if not any(row.get('session_id') == session_id and not row.get('incognito')
               for row in _load_agent_log(project_id)):
        raise KeyError('Session not found')


def _scope(project_id: str, *, writing: bool = False) -> tuple[Any, str | None]:
    project = _load_project(project_id)
    if not project:
        raise KeyError('Project not found')
    if project_id == '_incognito' or project.get('_is_incognito_project'):
        raise IngestRefused('Restricted project')
    caller = _attribution()
    session_id = request.args.get('session')
    if session_id is not None:
        if not session_id or len(session_id) > 512:
            raise ValueError('Invalid session')
        _session(project_id, session_id)
    if writing or caller.status == caller_attribution.ATTRIBUTED:
        owner = resolve_owner(project_id, caller)
        if owner is None or owner.incognito or owner.sandboxed:
            raise IngestRefused('Owner unavailable or restricted')
        if session_id is not None and session_id != owner.session_id:
            raise IngestRefused('Owner mismatch')
        session_id = owner.session_id
    elif caller.status != caller_attribution.UNATTRIBUTED:
        raise IngestRefused('Caller attribution unavailable')
    return caller, session_id


def _snapshot(project_id: str, run_id: str, session_id: str | None):
    snapshot = _storage().get(project_id, run_id)
    if session_id is not None and snapshot['owner_session_id'] != session_id:
        raise KeyError('Run not found')
    return snapshot


def _number(name: str, default: int, maximum: int) -> int:
    raw = request.args.get(name, str(default))
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 16:
        raise ValueError('Invalid paging value')
    value = int(raw)
    if value < (0 if name == 'offset' else 1) or value > maximum:
        raise ValueError('Invalid paging value')
    return value


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError('Duplicate JSON field')
        data[key] = value
    return data


def _invalid_constant(value):
    raise ValueError('Non-finite JSON constant')


def _body() -> dict[str, Any]:
    if not request.is_json:
        abort(415)
    if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
        abort(413)
    raw = request.stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        abort(413)
    try:
        data = json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('Invalid JSON object') from None
    if not isinstance(data, dict):
        raise ValueError('Expected JSON object')
    return data


def _token() -> str:
    value = request.headers.get('Authorization', '')
    if not value.startswith('Bearer ') or not 0 < len(value[7:]) <= 128:
        raise IngestRefused('Producer receipt required')
    return value[7:]


@bp.errorhandler(IngestRefused)
def _refused(exc):
    return jsonify({'error': 'Test-run owner or producer unavailable or restricted'}), 403


@bp.errorhandler(KeyError)
def _missing(exc):
    return jsonify({'error': 'Test run, project or session not found'}), 404


@bp.errorhandler(ValueError)
def _invalid(exc):
    return jsonify({'error': 'Invalid test-run request or telemetry'}), 400


@bp.errorhandler(FinalizeConflict)
def _conflict(exc):
    return jsonify({'error': 'Conflicting test-run finalization'}), 409


@bp.errorhandler(StoreError)
@bp.errorhandler(OSError)
def _unavailable(exc):
    _log(f'[test-runs] storage operation failed: {type(exc).__name__}', flush=True)
    return jsonify({'error': 'Test-run storage unavailable or corrupt'}), 500


@bp.get('/api/project/<project_id>/test-runs')
def list_runs(project_id: str):
    _, session_id = _scope(project_id)
    result = _storage().list_runs(project_id, owner_session_id=session_id,
                                 limit=_number('limit', 100, 100),
                                 offset=_number('offset', 0, 2**53 - 1))
    return jsonify({'runs': result})


@bp.get('/api/project/<project_id>/test-runs/<run_id>')
def snapshot(project_id: str, run_id: str):
    _, session_id = _scope(project_id)
    return jsonify(_snapshot(project_id, run_id, session_id))


@bp.get('/api/project/<project_id>/test-runs/<run_id>/events')
def events(project_id: str, run_id: str):
    _, session_id = _scope(project_id)
    _snapshot(project_id, run_id, session_id)
    limit = _number('limit', 100, 100)
    cursor = request.args.get('cursor', f'{run_id}:0')
    prefix, separator, raw_offset = cursor.partition(':')
    if (prefix != run_id or not separator or not raw_offset.isascii()
            or not raw_offset.isdecimal() or len(raw_offset) > 16):
        raise ValueError('Invalid cursor')
    offset = int(raw_offset)
    history = _storage().events(project_id, run_id)
    if offset > len(history):
        raise ValueError('Cursor beyond history')
    end = min(offset + limit, len(history))
    return jsonify({'events': [dict(history[i], seq=i + 1) for i in range(offset, end)],
                    'next_cursor': f'{run_id}:{end}', 'has_more': end < len(history),
                    'seq': len(history)})


@bp.post('/api/project/<project_id>/test-runs/enroll')
def enroll(project_id: str):
    caller, _ = _scope(project_id, writing=True)
    data = _body()
    # Body owner/cwd/process/source fields cannot assign ownership. The parent
    # reference is validated separately against the resolved owner and state.
    run, receipt, capability = _finalizer().enroll(
        project_id, caller, data.get('metadata', {}), parent_run_id=data.get('parent_run_id'))
    return jsonify({'run': run, 'receipt': asdict(receipt),
                    'finalize_capability': asdict(capability)}), 201


@bp.post('/api/project/<project_id>/test-runs/<run_id>/finalize')
def finalize(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    return jsonify(_finalizer().finalize(project_id, caller, run_id, _token(), _body()))


@bp.post('/api/project/<project_id>/test-runs/<run_id>/producer')
def producer(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    _body()
    return jsonify({'receipt': asdict(_service().producer(project_id, caller, run_id))}), 201


@bp.post('/api/project/<project_id>/test-runs/<run_id>/submit')
def submit(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    data, token = _body(), _token()
    batch = data.get('events')
    if not isinstance(batch, list):
        raise ValueError('Expected event batch')
    return jsonify(_service().submit(project_id, caller, run_id, token, batch))


@bp.post('/api/project/<project_id>/test-runs/<run_id>/spool')
def create_spool(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    _body()
    path = _service().create_spool(project_id, caller, run_id, _token())
    return jsonify({'path': str(path)}), 201


@bp.post('/api/project/<project_id>/test-runs/<run_id>/spool/attach')
def attach_spool(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    data = _body()
    path = data.get('path')
    if not isinstance(path, str):
        raise ValueError('Expected spool path')
    _service().attach_spool(project_id, caller, run_id, _token(), path)
    return jsonify({'ok': True})


@bp.post('/api/project/<project_id>/test-runs/<run_id>/spool/poll')
def poll_spool(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    _body()
    count = _service().poll_spool(project_id, caller, run_id, _token())
    return jsonify({'accepted': count})


@bp.post('/api/project/<project_id>/test-runs/<run_id>/revoke')
def revoke(project_id: str, run_id: str):
    caller, _ = _scope(project_id, writing=True)
    _body()
    _service().revoke(project_id, caller, run_id, _token())
    return jsonify({'ok': True})
