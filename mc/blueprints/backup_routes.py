"""Backup / restore endpoints — Phase 1 of docs/BACKUP_EXPORT_SPEC.md.

New namespace (spec §6 — `/api/project/<id>/import` is already taken by the
CHANGELOG importer and does something unrelated). All logic lives in
``mc/backup.py``, importable and fully functional without this blueprint or
a running server — ``tools/clayrune-backup.py`` drives the same module so
recovery works when the server won't start.

Routes:
    GET  /api/backup/size-preview   live per-category (+ per-directory §4.8)
                                     byte counts, before anything is written
    POST /api/backup/create         no `categories` body key => full default
                                     (everything ON, vault not_available);
                                     `dest_dir` overrides the configured
                                     destination for this call only (MC-945
                                     follow-up, refused if inside the repo or
                                     data/projects/ — see
                                     mc.backup.validate_backup_dest_dir);
                                     `async: true` returns `{job_id}`
                                     immediately (202) and runs the write on a
                                     worker thread instead — see the job
                                     endpoints below. Omitted/false keeps the
                                     original synchronous contract byte-for-
                                     byte (the CLI and the route tests in
                                     tests/test_backup_dest_dir.py depend on
                                     this staying the default).
    POST /api/backup/create/cancel/<job_id>   ask a running async job to
                                     stop. Cooperative: it sets a flag the
                                     write loop checks between entries (the
                                     thread is never killed — that would
                                     strand the multi-GB .partial temp), and
                                     the worker deletes its own temp on the
                                     way out via the same cleanup path as a
                                     failure. Terminal state is `cancelled`,
                                     deliberately NOT `error` — the user
                                     asked for it.
    GET  /api/backup/jobs           active + recently-finished create jobs,
                                     so a REOPENED panel (or a reloaded page)
                                     can reattach to a write it never started
                                     — the job_id itself only ever lived in
                                     the tab that launched it
    GET  /api/backup/create/status/<job_id>   poll an async job: status
                                     (running/cancelling/done/error/
                                     cancelled), files_written/
                                     total_files, bytes_written/total_bytes,
                                     current_file, warnings_count, and (once
                                     done) the same body the synchronous path
                                     returns. Same in-memory job dict +
                                     background-thread shape as
                                     terminal_routes.py's terminal_sessions —
                                     this codebase's existing pattern for a
                                     long-running job with live progress.
    GET  /api/backup/list           archives under the configured backup
                                     destination (default ~/.clayrune/backups/);
                                     `dest_dir` query arg overrides it, same
                                     refusal rule as create
    GET  /api/backup/dest-dir       `{configured, effective}` — the persisted
                                     `backup_dest_dir` config value and the
                                     actual path writes land in when unset;
                                     backs the Backup panel's override field
    POST /api/backup/restore        per-category additive restore (§4.7/§4.8);
                                     announces absent categories before it runs

    Phase 2 (docs/BACKUP_EXPORT_SPEC.md §7):
    POST /api/backup/export-project/<id>   one project's own slice; body may
                                            set `categories`, `vault` (tri-state:
                                            true/false/omitted-=-unanswered),
                                            `vault_passphrase`, `label`
    POST /api/backup/import                dry-run by default (returns the
                                            §4.4 collision report); `apply:true`
                                            plus `project_resolution` /
                                            `schedule_resolution` /
                                            `new_project_path` / `vault_passphrase`
                                            commits it

Restore/import/rollback are attended-only in the full spec (§6) — Phase 1
ships only same-machine restore, and this route still refuses a request
whose trigger type is unattended, the same gate style as
`with-secret --unattended` detection, so a steward cycle cannot roll back
its own history. `import` is attended-only outright (no trigger-type carve-out
at all, unlike restore/export-project); `export-project` itself may run
unattended, but its `vault: true` option cannot (mc.backup._resolve_vault_choice
enforces this — see spec §4.2, secret-bearing export is attended-only).

    Phase 3a — restore points (docs/BACKUP_EXPORT_SPEC.md §4.3, backend only):
    GET  /api/backup/restore-point/<project_id>            list, newest first
    POST /api/backup/restore-point/<project_id>             create; body may
                                                              set `label`, `pin`
    PATCH  /api/backup/restore-point/<project_id>/<snap_id>  body: `label`
                                                              and/or `pinned`
    DELETE /api/backup/restore-point/<project_id>/<snap_id>  delete one
    POST /api/backup/rollback/<project_id>/<snap_id>         body: `dry_run`
                                                              (read-only preview
                                                              — cannot_reverse
                                                              + memory-index
                                                              diff, no writes),
                                                              `restore_memory_index`

Restore-point creation/list/label/pin/delete are reversible, additive
operations on the project's OWN backup artifacts (not the project itself) —
same "backup creation may run unattended" class as export-project, no gate.
Rollback is the only Phase 3a write into project state, so it follows
restore/import's attended-only rule — except `dry_run:true`, which never
writes and is safe for a steward cycle to request as a preview.
"""
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from mc import backup as _backup
from mc.core import _log
from mc.state import agent_sessions

bp = Blueprint('backup_routes', __name__)


def _err(e: Exception, code: int = 400):
    return jsonify({'error': str(e)}), code


# ── Async create job (POST /api/backup/create with `async: true`) ──────────
#
# Same shape as terminal_routes.py's terminal_sessions: an in-memory dict of
# job state, mutated by a background thread, read by a polling GET. Lives
# here (not in mc/backup.py) so create_backup() stays importable/synchronous
# for tools/clayrune-backup.py and the test suite — threading is an HTTP-layer
# concern, not something the standalone module needs to know about.
#
# The registry is also the ONLY way a reopened panel finds a running job:
# the job_id lives in the closing tab's JS state and dies with it, so
# GET /api/backup/jobs below re-discovers it from here. That makes retention
# a UX property, not just a memory one — a job pruned too eagerly shows the
# user a blank form where their finished 48GB backup's result should be.
_backup_jobs: dict[str, dict] = {}
_backup_jobs_lock = threading.Lock()
_TERMINAL_STATES = ('done', 'error', 'cancelled')
_MAX_FINISHED_JOBS = 20          # bounded memory: an uptime-long dict, never otherwise cleared
_FINISHED_JOB_TTL = 6 * 60 * 60  # seconds a finished job stays queryable (see above)


def _prune_backup_jobs_locked():
    """Bound the registry two ways: drop finished jobs older than the TTL,
    then cap what remains at _MAX_FINISHED_JOBS (oldest first). A running
    job is never pruned at any age — a 48GB write legitimately takes hours."""
    now = time.time()
    for j in [j for j in _backup_jobs.values() if j['status'] in _TERMINAL_STATES]:
        if now - (j.get('finished_at_mono') or now) > _FINISHED_JOB_TTL:
            _backup_jobs.pop(j['job_id'], None)
    finished = [j for j in _backup_jobs.values() if j['status'] in _TERMINAL_STATES]
    if len(finished) <= _MAX_FINISHED_JOBS:
        return
    finished.sort(key=lambda j: j['started_at'])
    for j in finished[:len(finished) - _MAX_FINISHED_JOBS]:
        _backup_jobs.pop(j['job_id'], None)


def _finish_job_locked(job: dict, status: str, **fields) -> None:
    """Move a job to a terminal state. Stamps both a wall-clock time (for the
    UI) and a monotonic-ish one (for the TTL) so every terminal path ages out
    the same way — an un-stamped job would sit in the registry forever."""
    job['status'] = status
    job['finished_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    job['finished_at_mono'] = time.time()
    job.update(fields)


def _run_backup_job(job_id, categories, label, dest_dir):
    def _progress(p):
        with _backup_jobs_lock:
            job = _backup_jobs.get(job_id)
            if job is not None:
                job.update(p)

    def _cancel_requested():
        # Read under the lock — the cancel route writes this flag from the
        # request thread while the worker is mid-write.
        with _backup_jobs_lock:
            job = _backup_jobs.get(job_id)
            return bool(job and job.get('cancel_requested'))

    try:
        result = _backup.create_backup(categories=categories, label=label, dest_dir=dest_dir,
                                       progress_cb=_progress, cancel_cb=_cancel_requested)
    except _backup.BackupCancelled as e:
        # Terminal, but NOT an error: create_backup() already unwound through
        # its own cleanup, so the .partial is gone. Must be caught BEFORE
        # BackupError below — BackupCancelled subclasses it.
        _log(f"[backup] async create {job_id} cancelled: {e}")
        with _backup_jobs_lock:
            job = _backup_jobs.get(job_id)
            if job is not None:
                _finish_job_locked(job, 'cancelled', cancelled_reason=str(e))
        return
    except _backup.BackupError as e:
        with _backup_jobs_lock:
            job = _backup_jobs.get(job_id)
            if job is not None:
                _finish_job_locked(job, 'error', error=str(e))
        return
    except Exception as e:
        _log(f"[backup] async create {job_id} failed: {e}")
        with _backup_jobs_lock:
            job = _backup_jobs.get(job_id)
            if job is not None:
                _finish_job_locked(job, 'error', error=str(e))
        return
    _log(f"[backup] created (async {job_id}) {result['path']} "
        f"({result['files_written']} files, {len(result['warnings'])} warnings)")
    # Deliberately NOT the full manifest: it carries one entry per archived
    # file, so on a real install (96k files) the job result — and every status
    # poll that echoes it — would be hundreds of MB of JSON nobody reads. Keep
    # the fields a panel renders, plus the size/categories a REOPENED panel
    # needs to describe a job it never saw start. The synchronous route still
    # returns the manifest, unchanged.
    try:
        archive_bytes = Path(result['path']).stat().st_size
    except OSError:
        archive_bytes = None
    manifest = result['manifest'] or {}
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        if job is not None:
            _finish_job_locked(job, 'done', result={
                'path': result['path'], 'files_written': result['files_written'],
                'warnings': result['warnings'], 'bytes': archive_bytes,
                'categories': manifest.get('categories'),
                'created_at': manifest.get('created_at'),
            })


def _is_unattended(project_id: str | None = None) -> bool:
    """Server-side unattended detection — same source of truth as MC-923's
    with-secret.py / GET /api/session/trigger-type (agent_routes.py:3802):
    the `trigger_type` MC itself recorded on a session at dispatch time, which
    the calling agent process cannot rewrite. The previous version trusted an
    `X-Clayrune-Trigger-Type` header nobody ever sent (self-reported, and
    absent by default resolves the PERMISSIVE branch — that was the bug: every
    unattended gate on this surface was silently off).

    This never reads anything the caller sends. Instead it asks: is there a
    LIVE session, currently mid-turn (`status == 'running'`), that could be
    the one making this very HTTP call right now? A Bash-tool `curl` to this
    route can only exist because some Claude CLI session is executing a tool
    call at this instant, so if such a session is running and its recorded
    trigger_type isn't `'manual'`, this request is presumed to be that
    session's own tool call.

    `project_id` scopes the check to sessions dispatched against that project
    (export-project, rollback both operate on one project already named in
    the URL). Routes with no project scope (restore, import) pass None and
    every running session anywhere counts — conservative, same "one witness
    taints the candidate" OR the learning-safety rails use elsewhere.

    Fails CLOSED: a running session whose trigger_type is missing or blank
    (e.g. a revived session — some revive paths don't carry it, see
    `_note_claude_sid`) is treated as unattended, not as the lenient 'manual'
    default the rest of this file uses for display. No running session found
    at all (the common case for a human clicking Export in the SPA, which
    isn't a Claude CLI session and has nothing to find) resolves attended.
    """
    for s in agent_sessions.values():
        if s.get('status') != 'running':
            continue
        if project_id is not None and s.get('project_id') != project_id:
            continue
        if s.get('trigger_type') != 'manual':
            return True
    return False


@bp.route('/api/backup/size-preview')
def api_backup_size_preview():
    categories = None
    raw = request.args.get('categories')
    if raw:
        import json
        try:
            categories = json.loads(raw)
        except Exception as e:
            return _err(e)
    try:
        return jsonify(_backup.size_preview(categories))
    except Exception as e:
        _log(f"[backup] size-preview failed: {e}")
        return _err(e, 500)


@bp.route('/api/backup/create', methods=['POST'])
def api_backup_create():
    data = request.get_json(silent=True) or {}
    categories = data.get('categories')  # absent => full default (spec §6)
    label = data.get('label')
    raw_dest = data.get('dest_dir') or None  # absent/blank => configured default (spec §5 + MC-945 follow-up)
    dest_dir = Path(raw_dest) if raw_dest else None

    if data.get('async'):
        # Validate up front so a bad destination 400s immediately instead of
        # only surfacing through the first poll (dest_dir's own validation
        # inside create_backup() still applies too — this is just fail-fast).
        if dest_dir is not None:
            try:
                _backup.validate_backup_dest_dir(dest_dir)
            except _backup.BackupError as e:
                return _err(e)
        job_id = uuid.uuid4().hex[:12]
        job = {
            'job_id': job_id, 'status': 'running',
            'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'files_written': 0, 'total_files': 0, 'bytes_written': 0, 'total_bytes': 0,
            'current_file': None, 'warnings_count': 0, 'result': None, 'error': None,
            'cancel_requested': False, 'cancelled_reason': None,
            'finished_at': None, 'finished_at_mono': None,
            'label': label, 'dest_dir': str(dest_dir) if dest_dir else None,
        }
        with _backup_jobs_lock:
            _prune_backup_jobs_locked()
            _backup_jobs[job_id] = job
        threading.Thread(target=_run_backup_job, args=(job_id, categories, label, dest_dir),
                         daemon=True).start()
        return jsonify({'job_id': job_id, 'status': 'running'}), 202

    try:
        result = _backup.create_backup(categories=categories, label=label, dest_dir=dest_dir)
    except _backup.BackupError as e:
        return _err(e)
    except Exception as e:
        _log(f"[backup] create failed: {e}")
        return _err(e, 500)
    _log(f"[backup] created {result['path']} "
        f"({result['files_written']} files, {len(result['warnings'])} warnings)")
    return jsonify({'path': result['path'], 'manifest': result['manifest'],
                    'files_written': result['files_written'], 'warnings': result['warnings']})


@bp.route('/api/backup/create/status/<job_id>')
def api_backup_create_status(job_id):
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        if not job:
            return jsonify({'error': 'unknown job_id'}), 404
        out = dict(job)
    out.pop('finished_at_mono', None)  # internal TTL bookkeeping, not API
    return jsonify(out)


@bp.route('/api/backup/jobs')
def api_backup_jobs():
    """Discover create jobs WITHOUT knowing an id — the reattach path.

    A job_id only ever lived in the JS state of the tab that started it, so
    closing the Backup panel (or reloading the page) used to orphan a running
    48GB write: the user got an idle form, could not watch it, and could not
    cancel it. The server outlives both, so the panel asks here on open.

    `active` is what is still writing (running/cancelling), newest first;
    `recent` is the finished tail the registry still holds, so a panel
    reopened after the write completed shows the result instead of a blank.
    """
    with _backup_jobs_lock:
        _prune_backup_jobs_locked()
        jobs = [dict(j) for j in _backup_jobs.values()]
    for j in jobs:
        j.pop('finished_at_mono', None)  # internal TTL bookkeeping, not API
    active = sorted([j for j in jobs if j['status'] not in _TERMINAL_STATES],
                    key=lambda j: j['started_at'], reverse=True)
    recent = sorted([j for j in jobs if j['status'] in _TERMINAL_STATES],
                    key=lambda j: (j.get('finished_at') or '', j['started_at']), reverse=True)
    return jsonify({'active': active, 'recent': recent})


@bp.route('/api/backup/create/cancel/<job_id>', methods=['POST'])
def api_backup_create_cancel(job_id):
    """Cooperative abort for an async create. Sets a flag; the worker
    notices between entries, raises BackupCancelled, and deletes its own
    .partial on the way out. We never kill the thread: a killed writer
    leaves a half-flushed zip and a multi-GB orphan nobody knows to clean
    up (one 15.6GB one was already found in ~/.clayrune/backups)."""
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        if not job:
            return jsonify({'error': 'unknown job_id'}), 404
        if job['status'] in ('done', 'error', 'cancelled'):
            # Already terminal — report it rather than pretend we stopped
            # something. Not an error: a double-click on Cancel is normal.
            return jsonify({'job_id': job_id, 'status': job['status'],
                            'cancelled': False,
                            'detail': f"job already finished ({job['status']})"})
        job['cancel_requested'] = True
        job['status'] = 'cancelling'
    _log(f"[backup] cancel requested for async create {job_id}")
    return jsonify({'job_id': job_id, 'status': 'cancelling', 'cancelled': True})


@bp.route('/api/backup/list')
def api_backup_list():
    raw_dest = request.args.get('dest_dir') or None
    dest_dir = Path(raw_dest) if raw_dest else None
    try:
        return jsonify({'backups': _backup.list_backups(dest_dir=dest_dir)})
    except _backup.BackupError as e:
        return _err(e)
    except Exception as e:
        _log(f"[backup] list failed: {e}")
        return _err(e, 500)


@bp.route('/api/backup/dest-dir')
def api_backup_dest_dir():
    """Effective backup destination for the UI's override field (MC-945
    follow-up): the persisted `backup_dest_dir` config value plus the actual
    path writes land in when unset (~/.clayrune/backups)."""
    try:
        configured = _backup.effective_backup_dir_config()
        effective = _backup.effective_backup_dir()
    except Exception as e:
        _log(f"[backup] dest-dir failed: {e}")
        return _err(e, 500)
    return jsonify({'configured': configured, 'effective': effective})


@bp.route('/api/backup/restore', methods=['POST'])
def api_backup_restore():
    if _is_unattended():
        return jsonify({'error': 'restore is attended-only — refused for this trigger type'}), 403
    data = request.get_json(silent=True) or {}
    path = data.get('path')
    if not path:
        return jsonify({'error': 'path is required'}), 400
    categories = data.get('categories')  # None => restore every category present in the archive
    try:
        report = _backup.restore_backup(Path(path), categories=categories)
    except _backup.BackupFormatError as e:
        return _err(e, 409)
    except _backup.BackupIntegrityError as e:
        return _err(e, 422)
    except FileNotFoundError as e:
        return _err(e, 404)
    except Exception as e:
        _log(f"[backup] restore failed: {e}")
        return _err(e, 500)
    _log(f"[backup] restored {path}: {report['restored_categories']}")
    return jsonify(report)


# ── Phase 2 — per-project export/import (spec §4.1/§4.2/§4.4/§8) ───────────
#
# export-project is safe unattended (reversible, additive) EXCEPT the vault
# category, which stays attended-only same as full-backup secrets; import
# is attended-only outright, same gate style as restore above — an
# unattended cycle must never remap or overwrite a project's own identity.

@bp.route('/api/backup/export-project/<project_id>', methods=['POST'])
def api_backup_export_project(project_id):
    data = request.get_json(silent=True) or {}
    categories = data.get('categories')
    vault = data.get('vault')  # tri-state: True | False | omitted (None) => unanswered
    vault_passphrase = data.get('vault_passphrase')
    label = data.get('label')
    try:
        result = _backup.export_project(
            project_id, categories=categories, vault=vault,
            vault_passphrase=vault_passphrase, label=label,
            unattended=_is_unattended(project_id))
    except _backup.BackupError as e:
        return _err(e)
    except Exception as e:
        _log(f"[backup] export-project {project_id} failed: {e}")
        return _err(e, 500)
    _log(f"[backup] exported project {project_id} -> {result['path']} "
        f"({result['files_written']} files, {len(result['warnings'])} warnings)")
    return jsonify({'path': result['path'], 'manifest': result['manifest'],
                    'files_written': result['files_written'], 'warnings': result['warnings'],
                    'repo_checklist': result['repo_checklist']})


@bp.route('/api/backup/import', methods=['POST'])
def api_backup_import():
    if _is_unattended():
        return jsonify({'error': 'import is attended-only — refused for this trigger type'}), 403
    data = request.get_json(silent=True) or {}
    path = data.get('path')
    if not path:
        return jsonify({'error': 'path is required'}), 400
    try:
        if not data.get('apply'):
            report = _backup.import_dry_run(Path(path))
            return jsonify(report)
        report = _backup.import_project(
            Path(path),
            project_resolution=data.get('project_resolution', 'skip'),
            schedule_resolution=data.get('schedule_resolution', 'skip'),
            new_project_path=data.get('new_project_path'),
            vault_passphrase=data.get('vault_passphrase'),
            unattended=False)
    except _backup.BackupFormatError as e:
        return _err(e, 409)
    except _backup.BackupIntegrityError as e:
        return _err(e, 422)
    except _backup.BackupError as e:
        return _err(e)
    except FileNotFoundError as e:
        return _err(e, 404)
    except Exception as e:
        _log(f"[backup] import {path} failed: {e}")
        return _err(e, 500)
    _log(f"[backup] import {path}: {report.get('status')}")
    return jsonify(report)


# ── Phase 3a — restore points (spec §4.3) ───────────────────────────────────

@bp.route('/api/backup/restore-point/<project_id>', methods=['GET', 'POST'])
def api_backup_restore_point(project_id):
    if request.method == 'GET':
        try:
            return jsonify({'restore_points': _backup.list_restore_points(project_id)})
        except Exception as e:
            _log(f"[backup] restore-point list failed for {project_id}: {e}")
            return _err(e, 500)
    data = request.get_json(silent=True) or {}
    try:
        result = _backup.create_restore_point(
            project_id, label=data.get('label'), pin=bool(data.get('pin')))
    except _backup.BackupError as e:
        return _err(e)
    except Exception as e:
        _log(f"[backup] restore-point create failed for {project_id}: {e}")
        return _err(e, 500)
    _log(f"[backup] restore point created {project_id}/{result['snap_id']} "
        f"({result['files_written']} files)")
    return jsonify(result)


@bp.route('/api/backup/restore-point/<project_id>/<snap_id>', methods=['PATCH', 'DELETE'])
def api_backup_restore_point_item(project_id, snap_id):
    try:
        if request.method == 'DELETE':
            return jsonify(_backup.delete_restore_point(project_id, snap_id))
        data = request.get_json(silent=True) or {}
        manifest = None
        if 'label' in data:
            manifest = _backup.label_restore_point(project_id, snap_id, data['label'])
        if 'pinned' in data:
            manifest = _backup.pin_restore_point(project_id, snap_id, bool(data['pinned']))
        if manifest is None:
            return jsonify({'error': "PATCH body needs 'label' and/or 'pinned'"}), 400
        return jsonify(manifest)
    except _backup.BackupError as e:
        return _err(e, 404)
    except Exception as e:
        _log(f"[backup] restore-point {project_id}/{snap_id} update failed: {e}")
        return _err(e, 500)


@bp.route('/api/backup/rollback/<project_id>/<snap_id>', methods=['POST'])
def api_backup_rollback(project_id, snap_id):
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get('dry_run'))
    if _is_unattended(project_id) and not dry_run:
        return jsonify({'error': 'rollback is attended-only — refused for this trigger type'}), 403
    try:
        report = _backup.rollback(
            project_id, snap_id,
            restore_memory_index=bool(data.get('restore_memory_index')),
            unattended=_is_unattended(project_id), dry_run=dry_run)
    except _backup.BackupIntegrityError as e:
        return _err(e, 422)
    except _backup.BackupError as e:
        return _err(e, 404 if 'no restore point' in str(e) else 400)
    except Exception as e:
        _log(f"[backup] rollback {project_id}/{snap_id} failed: {e}")
        return _err(e, 500)
    _log(f"[backup] rollback {project_id}/{snap_id}: dry_run={dry_run} restored={report.get('restored')}")
    return jsonify(report)
