"""The launch gate of an approved npm package that carries a dependency closure (docs/
DESK_SERVICE_PROFILES_SPEC.md, section 6.2, slice U2b).

A self-contained package is started by `tools/with-secret.py ... node <entry>`. Node finds a missing
`require`d package by walking UP the directory tree, so a dependency that was deleted from the approved
tree would be answered by whatever sits further up: an unpinned launch, with the service's secrets already
in its environment. A package with a closure is therefore started through this gate, BEFORE the credential
wrapper:

    python tools/custom-mcp-gate.py --scope S --project P --name N -- python tools/with-secret.py ... -- node <entry>

The gate loads the approval Desk recorded for that server (never anything from the command line), and
starts the rest only when what is on disk is what that approval covers:

    * the record is intact (its operation still has the fingerprint stored with it) and names a closure;
    * the directory carries our marker for exactly this closure, and the entry file is there;
    * every approved dependency is on disk at its place with the approved name and version;
    * no `node_modules` folder sits in Clayrune's own package area above the package (a place Node
      would look when an approved dependency is missing);
    * the files of the whole tree are the ones recorded at Save (`custom_package_manifest.verify`): for a
      package with a closure, a changed file of a dependency is refused here, where a self-contained
      package's drift is only reported.

Otherwise it prints why on stderr, starts NOTHING (no wrapper, so no secret is read, and no Node), and
exits non-zero: the server is "returned to Review" and the card says what to do (save it again). This is
the structural check, cheap enough for every start. What the files CONTAIN is the drift manifest's job
(`custom_package_manifest`), which covers the whole dependency tree too; the gate refuses on its
`changed` verdict, and lets an approval with no manifest recorded start (the card says `not_recorded`).

Limit, stated on the card: Node also looks in `node_modules` folders above Clayrune's folder (the home
folder, the drive root). A package that requires something it never declared could be answered there;
every package the approval lists is found first, in its own place.
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
from mc.desk_connect import custom_package_manifest as _manifest
from mc.desk_connect.mcp_errors import ActivationError

EXIT_REFUSED = 78
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


def problem(op: dict) -> dict | None:
    """None when the closure on disk is the approved one, else `{code, message, paths}`:
    `dependency_missing` (the folder, a package or the entry is not there) or `dependency_changed`
    (a package is there but is not the approved name and version, or an unapproved `node_modules`
    would be searched). Never raises."""
    try:
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
        for ambient in (base / 'node_modules', _artifact.packages_root() / 'node_modules'):
            if ambient.exists() or ambient.is_symlink():
                changed.append(str(ambient.name) + ' (above the package)')
        if missing:
            return {'code': 'dependency_missing', 'paths': missing,
                    'message': f'An approved package is missing from disk: {_shown(missing)}.'}
        if changed:
            return {'code': 'dependency_changed', 'paths': changed,
                    'message': f'A package on disk is not the one that was approved: {_shown(changed)}.'}
        return None
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
    if rec is None or not isinstance(op, dict) or op.get('ecosystem') != 'npm' or not _install.needs_closure(op):
        return _refuse('there is no approval on record for a package with dependencies under this name.')
    try:
        intact = _op.fingerprint(op) == rec['fingerprint']
    except (TypeError, ValueError, KeyError):
        intact = False
    if not intact:
        return _refuse('the approval record was changed after it was saved.')
    found = problem(op)
    if found is not None:
        _log(f'[desk_connect] custom MCP {name} not started: {found["code"]}', flush=True)
        return _refuse(found['message'])
    files = _manifest.verify(rec)
    if files['status'] == 'changed':
        listed = files['changed'] + files['added'] + files['removed']
        _log(f'[desk_connect] custom MCP {name} not started: package files changed ({files["reason"]})', flush=True)
        return _refuse('a file of the package or of its dependencies is not the one recorded when you approved it'
                       + (f' ({_shown(listed)})' if listed else '') + '.')
    if run is not None:
        return run(rest)
    if os.name != 'nt':
        os.execv(rest[0], rest)                             # replaces this process: no extra layer between the agent and the server
    return subprocess.call(rest)
