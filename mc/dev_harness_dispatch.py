"""Server-built requests through existing session jobs; fixed worker only (M5)."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import freeze_candidate, preview, safe_path
from mc.dev_harness_integration import _save, evaluate_locked, identities, snapshot_pins
from mc.dev_harness_store import HarnessStore, RevisionConflict, StoreError
from mc.landing_serialization import landing


def _request_path(store: HarnessStore, project_id: str, run_id: str) -> Path:
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('invalid_run_id')
    return safe_path(store.project_dir(project_id), 'run_requests/' + run_id + '.json')


def _write_request(path: Path, value: dict[str, Any]) -> None:
    value['content_digest'] = digest({k: v for k, v in value.items() if k != 'content_digest'})
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value, allow_nan=False)


def dispatch(store: HarnessStore, project: dict[str, Any], data: dict[str, Any], *, kind: str):
    """Called from the leaf route; exact schema already checked, no shell input."""
    from flask import jsonify, make_response
    from mc.blueprints import agent_routes as jobs
    from mc import state
    from mc import project_sync
    repo = Path(project['project_path'])
    refusal = _session_refusal(jobs, project['id'], data['session_id'])
    if refusal is not None:
        return refusal
    with landing(repo) if project_sync.is_git_repo(str(repo)) else nullcontext():
        snapshot = store._read_snapshot(project['id'])
        pins = snapshot_pins(snapshot)
        if data['expected_revision'] != pins['revision'] or data['native_plan_digest'] != pins['native_plan_digest']:
            raise RevisionConflict('Active revision or native plan changed')
        if snapshot and snapshot['mode'] != 'off' and snapshot.get('native_plan'):
            from mc.dev_harness_native_plan import validate_plan
            validate_plan(store, project, snapshot['policy'], snapshot['native_plan'])
        measurement = data.get('purpose') == 'activation_measurement'
        if measurement and (not snapshot or snapshot['mode'] != 'preview' or not snapshot.get('native_plan')):
            raise ValueError('approved_preview_required')
        frozen = freeze_candidate(repo, data['target'], data['incoming']) if kind == 'run' else preview(repo)
        manifest_only = kind == 'preview' and not frozen.get('head')
        if measurement and manifest_only:
            raise ValueError('checkoutable_baseline_required')
        if not snapshot or snapshot['mode'] == 'off' or not snapshot.get('native_plan') or manifest_only:
            # Session ownership and refusal policy still apply to manifest-only observations.
            refusal = _session_refusal(jobs, project['id'], data['session_id'])
            if refusal is not None:
                return refusal
            return jsonify({'project_id': project['id'], **pins, 'status': 'unsupported',
                            'code': 'mode_off' if snapshot and snapshot['mode'] == 'off' else 'manifest_only',
                            'source': identities(frozen), 'authorizes_merge': False})
        if measurement and frozen != snapshot['native_plan']['payload']['baseline_identity']['source']:
            raise RevisionConflict('Measured baseline changed')
        run_id = uuid.uuid4().hex
        record = {'schema_version': 1, 'run_id': run_id, 'project': {'id': project['id'], 'project_path': str(repo.resolve())},
                  'session_id': data['session_id'], 'state': 'dispatching', 'kind': kind,
                  'measurement': measurement, 'expected': {**pins, 'source': frozen},
                  'target': data.get('target'), 'incoming': data.get('incoming'),
                  'registration_port': state.CONFIG.get('port', 5199)}
        path = _request_path(store, project['id'], run_id)
        _write_request(path, record)
        value = {'schema_version': 1, 'kind': 'native_integration_run', 'run_id': run_id,
                 'project_id': project['id'], **pins, 'status': 'pending', 'source': identities(frozen),
                 '_refs': [data.get('target'), data.get('incoming')],
                 'authorizes_merge': False, 'standards_hold': False, 'required': {}, 'warnings': {},
                 'receipt_ids': [], 'receipt_digests': []}
        _save(store, project['id'], value)
    # No landing/lifecycle/snapshot lock across job start or waiting.
    app_root = Path(__file__).resolve().parents[1]
    # An explicit MC_DATA_DIR remains inherited. A nonstandard test store uses
    # direct run_request(); production paths are derived by the existing server.
    command = ' '.join(shlex.quote(v) for v in [sys.executable, '-m', 'mc.dev_harness_dispatch', run_id])
    completion_lock = threading.RLock()
    completed = False

    def complete(result):
        nonlocal completed
        try:
            with completion_lock:
                completed = True
                terminal = store._read_json(store.project_dir(project['id'])/'integration_runs'/(run_id+'.json'))
                if terminal['status'] == 'pending':
                    terminal.update(status='error', code='job_terminated', standards_hold=False)
                    _save(store, project['id'], terminal)
                path.unlink(missing_ok=True)
        finally:
            jobs._job_on_complete(result)

    try:
        response = make_response(jobs.agent_job_start(project['id'], data['session_id'], job_data={
            'command': command, 'shell': 'bash', 'cwd': str(app_root),
            'timeout_minutes': min(120, sum(v['timeout_seconds'] * 3 for v in snapshot['native_plan']['payload']['steps']) / 60 + 1)},
            completion_hook=complete))
        body = response.get_json()
        if response.status_code != 200 or not isinstance(body, dict) or not body.get('job_id'):
            path.unlink(missing_ok=True)
            value.update(status='error', code='dispatch_refused')
            _save(store, project['id'], value)
            return response
        with completion_lock:
            # A short timeout/early worker exit can complete before start_job
            # returns. Never recreate a runnable request after that completion.
            if not completed:
                record.update(state='queued', job_id=body['job_id'])
                _write_request(path, record)
        return jsonify({'project_id': project['id'], 'run_id': run_id, 'job_id': body['job_id'],
                        'status': 'pending', 'revision': pins['revision'],
                        'native_plan_digest': pins['native_plan_digest'], 'authorizes_merge': False}), 202
    except Exception:
        path.unlink(missing_ok=True)
        value.update(status='error', code='dispatch_failed')
        _save(store, project['id'], value)
        raise StoreError('Harness job dispatch failed') from None


def _session_refusal(jobs, project_id: str, session_id: str):
    """Mirror existing job guards for paths which deliberately create no job."""
    from flask import jsonify
    with jobs.get_manager(project_id).lock:
        session = jobs.agent_sessions.get(session_id)
        if not session or session.get('project_id') != project_id:
            return jsonify({'error': 'session not found'}), 404
        if session.get('incognito'):
            return jsonify({'error': 'incognito sessions cannot receive job completion delivery'}), 400
        if session.get('_codex_unattended_sandbox'):
            return jsonify({'error': 'sandboxed session cannot start harness jobs'}), 403
    return None


def run_request(store: HarnessStore, project_id: str, run_id: str, *, register, unregister,
                load_project=None) -> dict[str, Any]:
    path = _request_path(store, project_id, run_id)
    run_path = store.project_dir(project_id)/'integration_runs'/(run_id+'.json')
    value = store._read_json(run_path)
    try:
        if value.get('status') != 'pending':
            raise StoreError('Request already consumed')
        # Parent registers the outer job before changing dispatching to queued.
        deadline = time.monotonic() + 30
        while True:
            request = store._read_json(path)
            if request.get('content_digest') != digest({k: v for k, v in request.items() if k != 'content_digest'}):
                raise StoreError('Invalid request')
            if request.get('state') != 'dispatching' or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        project = request['project']
        if (request['state'] not in ('queued', 'running') or request['run_id'] != run_id or project['id'] != project_id
                or (load_project is not None and load_project(project_id).get('project_path') != project['project_path'])):
            raise StoreError('Invalid request')
        with landing(Path(project['project_path'])) as owner:
            # Re-read after acquiring the OS lock. Another worker may have
            # consumed this request while this one waited; an old in-memory
            # queued object is not permission to replay it.
            if (store._read_json(run_path).get('status') != 'pending'
                    or store._read_json(path) != request or request['state'] != 'queued'):
                raise StoreError('Request already consumed')
            request['state'] = 'running'
            _write_request(path, request)  # A second worker cannot reuse a queued request under the lock.
            evaluation = evaluate_locked(store=store, project=project, owner=owner, run_id=run_id,
                target=request['target'], incoming=request['incoming'], measurement=request['measurement'],
                expected=request['expected'], register=register, unregister=unregister)
            return evaluation.record
    except Exception:
        value.update(status='error', code='worker_inputs_unavailable', standards_hold=False)
        if store._read_json(run_path).get('status') == 'pending':
            _save(store, project_id, value)
        return value
    finally:
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Private worker entry: accepts a server-generated request ID only."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not re.fullmatch(r'[0-9a-f]{32}', args[0]):
        return 2
    app_root = Path(__file__).resolve().parents[1]
    data_root = Path(os.environ.get('MC_DATA_DIR', str(app_root))) / 'data'
    store = HarnessStore(data_root)
    candidates = list(store.root.glob('*/run_requests/' + args[0] + '.json'))
    if len(candidates) != 1:
        return 2
    project_id = candidates[0].parent.parent.name
    request = store._read_json(candidates[0])

    def register(proc, check_id):
        register_external_child(proc, check_id, project_id, data_root.parent, port=request['registration_port'])

    def load_project(identity):
        return store._read_json(data_root/'projects'/(identity+'.json'))

    result = run_request(store, project_id, args[0], register=register,
                         unregister=lambda pid: None, load_project=load_project)
    # External process registry retains completed rows; its existing live probe
    # reports them dead. Never call the cleanup endpoint (it kills other jobs).
    print(json.dumps({'run_id': args[0], 'status': result['status'], 'authorizes_merge': False}))
    return 0 if result['status'] == 'pass' else 1 if result['status'] in ('fail', 'review_required') else 2


def register_external_child(proc, check_id: str, project_id: str, data_root: Path, *, port: int | None = None) -> None:
    """Reuse the server's external PID registration, including manual preflight."""
    import urllib.request
    if port is None:
        config = data_root/'config.json'
        port = json.loads(config.read_text(encoding='utf-8')).get('port', 5199) if config.exists() else 5199
    if type(port) is not int or not 0 < port <= 65535:
        raise ValueError('invalid_registration_port')
    body = json.dumps({'pid': proc.pid, 'name': 'native check ' + check_id,
                       'project_id': project_id, 'command': 'dev-harness:' + check_id}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{int(port)}/api/processes/register',
                                 data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        if json.load(response).get('ok') is not True:
            raise StoreError('Process registration failed')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        # A corrupt request can fail before run_request owns a record. The
        # existing outer completion hook finalizes Pending as Error; no raw
        # exception/traceback enters its delivered output tail.
        print(json.dumps({'status': 'error', 'code': 'worker_inputs_unavailable', 'authorizes_merge': False}))
        raise SystemExit(2)
