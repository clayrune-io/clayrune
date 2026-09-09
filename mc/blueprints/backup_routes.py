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

Restore/import/rollback are attended-only in the full spec (§6) — Phase 1
ships only same-machine restore, and this route still refuses a request
whose trigger type is unattended, the same gate style as
`with-secret --unattended` detection, so a steward cycle cannot roll back
its own history.
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
