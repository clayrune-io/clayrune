"""The launch gate of an approved npm package that carries a dependency closure (docs/
DESK_SERVICE_PROFILES_SPEC.md, section 6.2, slice U2b).

A package is started by `tools/with-secret.py ... node <entry>`. Node finds a `require`d package it
cannot find in the package's own tree by walking UP the directory tree, so a dependency that was deleted
from the approved tree, or an optional one that was never installed, would be answered by whatever sits
further up: an unpinned launch, with the service's secrets already in its environment. Every approved npm
package (a self-contained one as well as one with a closure) is therefore started through this gate,
BEFORE the credential wrapper:

    python tools/custom-mcp-gate.py --scope S --project P --name N -- python tools/with-secret.py ... -- node <entry>

The gate loads the approval Desk recorded for that server (never anything from the command line), and
starts the rest only when what is on disk is what that approval covers:

    * the record is intact (its operation still has the fingerprint stored with it);
    * no folder from the package's own up to the drive root has a `node_modules` in it (`ancestor_problem`):
      Node would search it for anything the approved tree does not hold, and run what it finds;
    * for a package with a closure: the directory carries our marker for exactly this closure, the entry
      file is there, every approved dependency is on disk at its place with the approved name and
      version, and every file of the whole tree is the one recorded at Save, each file read and hashed
      again (`custom_package_manifest.verify(rehash=True)`, not the size and time shortcut). A package
      whose files were never recorded is refused.

Otherwise it prints why on stderr, starts NOTHING (no wrapper, so no secret is read, and no Node), and
exits non-zero: the server is "returned to Review" and the card says what to do (save it again). A
self-contained package's own files stay detect-only (`custom_package_manifest.check`); only the
`node_modules` search path is refused for it.

It also asks Node for its own global search list (`custom_npm_node_paths`: `~/.node_modules`,
`~/.node_libraries`, `<node folder>/lib/node`, any `NODE_PATH`), with the environment the server will get, and
refuses when a listed folder exists or the question cannot be answered in 2 seconds. This runs at start, not
in the connection's status (`problem`), which stays a file-system check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

from mc.core import _log
from mc.desk_connect import custom_connection_operation as _op
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import custom_npm_artifact as _artifact
from mc.desk_connect import custom_npm_install as _install
from mc.desk_connect import custom_npm_node_paths as _node_paths
from mc.desk_connect import custom_package_manifest as _manifest
from mc.desk_connect.mcp_errors import ActivationError

EXIT_REFUSED = 78
_STOP_AFTER: Path | None = None                 # test seam: the folder after which the `node_modules` walk up stops
_MAX_MANIFEST = 1024 * 1024
_SHOWN = 4


def gate_path() -> Path:
    """`tools/custom-mcp-gate.py`. Not in a frozen build (which has no `python` to run it)."""
    if getattr(sys, 'frozen', False):
        raise ActivationError('this build of Clayrune does not include the launch check a package with dependencies '
                              'needs, so it cannot activate one', 'gate_missing', 409)
    p = Path(__file__).resolve().parents[2] / 'tools' / 'custom-mcp-gate.py'
    if not p.is_file():
        raise ActivationError('the launch check (tools/custom-mcp-gate.py) is missing from this install', 'gate_missing', 409)
    return p


def gate_flags(op: dict) -> list[str]:
    """The gate's own arguments, up to and including `--`: which approval it checks."""
    return ['--scope', op['scope']['kind'], '--project', op['scope']['project_id'] or '', '--name', op['server_name'], '--']


def _read_manifest(path: Path):
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_MANIFEST:
            return None
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _shown(paths: list[str]) -> str:
    text = ', '.join(''.join(c if c.isprintable() else '?' for c in p)[:80] for p in paths[:_SHOWN])
    return text + (f' and {len(paths) - _SHOWN} more' if len(paths) > _SHOWN else '')


def _ancestors(base: Path) -> list[Path]:
    """`base` and every folder above it, up to the root (stopping after `_STOP_AFTER` when a test sets one)."""
    start = Path(os.path.abspath(base))
    out = []
    for d in (start, *start.parents):
        out.append(d)
        if _STOP_AFTER is not None and d == _STOP_AFTER:
            break
    return out


def ancestor_problem(op: dict) -> dict | None:
    """None when no folder from the package's directory up to the root holds a `node_modules`, else
    `{code, message, paths}`. Node resolves a `require` by walking up from the file asking for each
    `<folder>/node_modules`, so one anywhere above (the package area, `~/.clayrune`, the home folder, the
    drive root) can answer for a package the approved tree does not hold, and that code runs with the
    server's secrets in its environment. Never raises."""
    try:
        found = [str(d / 'node_modules') for d in _ancestors(_artifact.package_dir(op))
                 if (d / 'node_modules').exists() or (d / 'node_modules').is_symlink()]
    except Exception as e:                                  # noqa: BLE001 - a check that cannot tell must refuse
        _log(f'[desk_connect] custom MCP node_modules check could not read the folders: {type(e).__name__}', flush=True)
        return {'code': 'dependency_changed', 'message': 'The folders above the package could not be read.', 'paths': []}
    if not found:
        return None
    return {'code': 'dependency_changed', 'paths': found,
            'message': f'A node_modules folder sits above the package ({_shown(found)}). Node searches it for anything '
                       f'the approved package does not hold and runs what it finds with this server\'s secrets, so the '
                       f'server will not start until it is removed.'}


def problem(op: dict) -> dict | None:
    """None when what is on disk is what the approval covers, else `{code, message, paths}`:
    `dependency_missing` (the folder, a package or the entry is not there) or `dependency_changed`
    (a package is there but is not the approved name and version, or a `node_modules` above the package
    would be searched). A package with no closure is checked for the `node_modules` search path only.
    Never raises."""
    try:
        if not _install.needs_closure(op):
            return ancestor_problem(op)
        _install.validate(op)
        base = _artifact.package_dir(op)
        marker = _read_manifest(base / _artifact.VERIFIED_MARKER)
        if marker is None or marker.get('integrity') != op['integrity'] or marker.get('closure') != _artifact.dir_id(op):
            return {'code': 'dependency_missing', 'message': 'The approved package and its dependencies are not all on disk.',
                    'paths': []}
        if not _artifact.entry_path(op).is_file():
            return {'code': 'dependency_missing', 'message': 'The approved start file is not on disk.', 'paths': []}
        missing, changed = [], []
        wanted = [('', op['package'], op['version'])] + [(d['path'], d['name'], d['version']) for d in op.get('dependencies') or []]
        for rel, name, version in wanted:
            pj = base / 'package' / PurePosixPath(rel) / 'package.json' if rel else base / 'package' / 'package.json'
            doc = _read_manifest(pj)
            if doc is None:
                missing.append(rel or name)
            elif doc.get('name') != name or doc.get('version') != version:
                changed.append(rel or name)
        if missing:
            return {'code': 'dependency_missing', 'paths': missing,
                    'message': f'An approved package is missing from disk: {_shown(missing)}.'}
        if changed:
            return {'code': 'dependency_changed', 'paths': changed,
                    'message': f'A package on disk is not the one that was approved: {_shown(changed)}.'}
        return ancestor_problem(op)
    except ActivationError:
        return {'code': 'dependency_changed', 'message': 'The approval record for its dependencies is not valid.', 'paths': []}
    except Exception as e:                                  # noqa: BLE001 - a check that cannot tell must refuse
        _log(f'[desk_connect] custom MCP closure gate could not read the package: {type(e).__name__}', flush=True)
        return {'code': 'dependency_changed', 'message': 'The package folder could not be read.', 'paths': []}


def _parse(argv: list[str]):
    """`(scope, project_id, name, rest)` from `--scope S --project P --name N -- rest...`, or None."""
    if len(argv) < 8 or argv[0] != '--scope' or argv[2] != '--project' or argv[4] != '--name' or argv[6] != '--':
        return None
    scope, project, name, rest = argv[1], argv[3], argv[5], argv[7:]
    if scope not in ('project', 'global') or not rest or (scope == 'project') != bool(project):
        return None
    return scope, project or None, name, rest


def _refuse(text: str) -> int:
    sys.stderr.write(f'Clayrune did not start this MCP server: {text}\n'
                     f'Nothing was started and no secret was read. Open Desk > Connect and save the server again to '
                     f'review and restore it.\n')
    sys.stderr.flush()
    return EXIT_REFUSED


def main(argv: list[str], *, run=None) -> int:
    """Check the approval, then run the rest of the line. `run(cmd)` is the test seam for the process start."""
    parsed = _parse(argv)
    if parsed is None:
        return _refuse('the launch line is not one Clayrune wrote.')
    scope, project, name, rest = parsed
    try:
        rec = _store.get(scope, project, name)
    except _store.StoreUnreadable:
        return _refuse('the record of what was approved cannot be read.')
    op = rec.get('operation') if rec else None
    if rec is None or not isinstance(op, dict) or op.get('ecosystem') != 'npm':
        return _refuse('there is no approval on record for a package under this name.')
    try:
        intact = _op.fingerprint(op) == rec['fingerprint']
    except (TypeError, ValueError, KeyError):
        intact = False
    if not intact:
        return _refuse('the approval record was changed after it was saved.')
    found = problem(op) or _node_paths.problem(op, rest)
    if found is not None:
        _log(f'[desk_connect] custom MCP {name} not started: {found["code"]}', flush=True)
        return _refuse(found['message'])
    if _install.needs_closure(op):                          # a closure is held to its recorded files, every file read again
        files = _manifest.verify(rec, rehash=True)
        if files['status'] != 'unchanged':
            listed = files['changed'] + files['added'] + files['removed']
            _log(f'[desk_connect] custom MCP {name} not started: package files {files["status"]} ({files["reason"]})',
                 flush=True)
            if files['status'] == 'not_recorded':
                return _refuse('the files of this package were never recorded, so they cannot be checked.')
            return _refuse('a file of the package or of its dependencies is not the one recorded when you approved it'
                           + (f' ({_shown(listed)})' if listed else '') + '.')
    if run is not None:
        return run(rest)
    if os.name != 'nt':
        os.execv(rest[0], rest)                             # replaces this process: no extra layer between the agent and the server
    return subprocess.call(rest)
