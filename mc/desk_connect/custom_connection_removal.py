"""Remove an approved MCP connection, including an unregistered failed setup.

Unregister first, retain the approval until cleanup succeeds so errors are retryable.
Only managed direct-child install directories are eligible; shared packages survive.
No executable is started and no credential or browser profile is touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
from pathlib import Path

from mc import mcp, mcp_installer
from mc.desk_connect import custom_connection_service as service
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect import custom_npm_artifact as npm
from mc.desk_connect import custom_package_manifest as manifests
from mc.desk_connect import remote_mcp_observed as observed
from mc.desk_connect.mcp_errors import ActivationError

_PROJECT = re.compile(r'^[A-Za-z0-9_.-]{1,120}$')
_cleanup_lock = threading.Lock()


def target(body) -> tuple[str, str | None, str]:
    if not isinstance(body, dict) or set(body) - {'scope', 'project_id', 'server_name', 'passcode'}:
        raise ActivationError('Name the connection to remove.', 'invalid', 400)
    scope, pid, name = body.get('scope'), body.get('project_id'), body.get('server_name')
    if scope not in ('global', 'project') or not isinstance(name, str) or mcp.validate_name(name):
        raise ActivationError('Name the connection and who can use it.', 'invalid', 400)
    if scope == 'project' and (not isinstance(pid, str) or not _PROJECT.fullmatch(pid)):
        raise ActivationError('Name the connection\'s project.', 'invalid', 400)
    if scope == 'global' and pid is not None:
        raise ActivationError('An all-project connection cannot name a project.', 'invalid', 400)
    return scope, pid, name


def _operations(rec: dict) -> list[dict]:
    return [op for op in (rec.get('operation'), rec.get('replaces')) if isinstance(op, dict)]


def _directory(op: dict) -> tuple[Path, Path] | None:
    if op.get('ecosystem') == 'npm':
        return npm.packages_root(), npm.package_dir(op)
    if op.get('ecosystem') == 'github':
        return mcp_installer.INSTALLS_ROOT, Path(op['directory'])
    return None


def _checked(root: Path, path: Path) -> Path:
    resolved_root, resolved = root.resolve(), path.resolve()
    # Never delete a root, a parent, or a link/junction into another location.
    if resolved.parent != resolved_root or path.absolute().parent != root.absolute():
        raise ActivationError('The install folder is outside its managed location.', 'unsafe_directory', 409)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ActivationError('The install folder is a link; it was not removed.', 'unsafe_directory', 409)
    return resolved


def _remove_readonly(function, path, exc_info):
    # Windows Git objects are read-only. Retry once; a failure must reach the route.
    if not isinstance(exc_info[1], PermissionError):
        raise exc_info[1]
    info = Path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise exc_info[1]
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    function(path)


def remove(scope: str, project_id: str | None, name: str) -> dict:
    key = store.key(scope, project_id, name)
    with service._name_lock(key), _cleanup_lock:
        rec = store.get(scope, project_id, name)
        if rec is None:
            raise ActivationError('Connection not found.', 'not_found', 404)
        operations = _operations(rec)
        candidates = [_directory(op) for op in operations]
        paths = {_checked(*item) for item in candidates if item is not None}

        def unregister(servers):
            servers.pop(name, None)

        # The ordinary delete_server also releases the approval immediately. Use its
        # same locked config writers but release only after the filesystem work.
        config = mcp.GLOBAL_CLAUDE_JSON
        if scope == 'project':
            path = rec.get('project_path')
            if not path:
                raise ActivationError('The connection has no saved project folder.', 'project_no_path', 409)
            config = mcp.project_mcp_path(path)
        try:
            document = json.loads(config.read_text(encoding='utf-8'))
        except FileNotFoundError:
            document = {}
        if not isinstance(document, dict) or not isinstance(document.get('mcpServers', {}), dict):
            raise ActivationError('The saved server configuration could not be read.', 'config_unreadable', 409)
        if name in document.get('mcpServers', {}):
            if scope == 'global':
                mcp._write_global_servers(unregister)
            else:
                mcp._write_project_servers(rec['project_path'], unregister)
        for path in sorted(paths):
            with npm._lock_for(path.name):
                # A Save may have approved this same digest while we waited for
                # its install lock. Re-read ownership under that lock.
                shared = any(
                    item and item[1].resolve() == path
                    for other in store.all_records()
                    if store.key(other['scope'], other.get('project_id'), other['server_name']) != key
                    for op in _operations(other)
                    for item in [_directory(op)]
                )
                if shared:
                    continue
                if path.exists():
                    shutil.rmtree(path, onerror=_remove_readonly)
        manifests.manifest_path(key).unlink(missing_ok=True)
        for op in operations:
            if op.get('ecosystem') == 'github':
                from mc.desk_connect.github_activation import _manifest_path
                _manifest_path(op).unlink(missing_ok=True)
        observed.forget(scope, project_id, name)
        store.remove(scope, project_id, name)
        return {'ok': True, 'server_name': name, 'scope': scope, 'project_id': project_id}
