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
                                     (everything ON, vault not_available)
    GET  /api/backup/list           archives under ~/.clayrune/backups/
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
"""
from pathlib import Path

from flask import Blueprint, jsonify, request

from mc import backup as _backup
from mc.core import _log

bp = Blueprint('backup_routes', __name__)


def _err(e: Exception, code: int = 400):
    return jsonify({'error': str(e)}), code


def _is_unattended() -> bool:
    """Same signal class as with-secret's server-side unattended detection
    (CLAUDE.md secrets section) — a header the dispatcher sets for
    steward/scheduled trigger types, absent for an interactive session."""
    return (request.headers.get('X-Clayrune-Trigger-Type') or '').lower() in (
        'steward', 'scheduled', 'unattended')


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
    try:
        result = _backup.create_backup(categories=categories, label=label)
    except _backup.BackupError as e:
        return _err(e)
    except Exception as e:
        _log(f"[backup] create failed: {e}")
        return _err(e, 500)
    _log(f"[backup] created {result['path']} "
        f"({result['files_written']} files, {len(result['warnings'])} warnings)")
    return jsonify({'path': result['path'], 'manifest': result['manifest'],
                    'files_written': result['files_written'], 'warnings': result['warnings']})


@bp.route('/api/backup/list')
def api_backup_list():
    try:
        return jsonify({'backups': _backup.list_backups()})
    except Exception as e:
        _log(f"[backup] list failed: {e}")
        return _err(e, 500)


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
            unattended=_is_unattended())
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
