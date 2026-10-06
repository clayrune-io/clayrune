"""Installing an approved package with its dependency closure (docs/DESK_SERVICE_PROFILES_SPEC.md,
section 6.2, slice U2b). Runs only after the human Save, from `custom_connection_activation.provision`.

A package that needs nothing else is `custom_npm_artifact.install`, unchanged. A package with an approved
closure is built in a STAGING folder next to its final place and moved there in one step:

    1. the root archive and every dependency archive are downloaded from the registry's fixed address
       (`custom_npm_artifact.tarball_url`) and each is held to the sha512 the person approved: a
       different byte is an error, never a new pin;
    2. each is unpacked by the same safe extractor into `package/<path>` (`path` is where the closure
       placed it); a folder that already exists is a refusal, never a merge;
    3. approved install scripts (and only those) run in the staging folder, in dependency order
       (`custom_npm_scripts`);
    4. our marker is written and the staging folder is renamed to `~/.clayrune/mcp_custom_packages/<dir_id>/`,
       which is named by everything the approval covers and is never overwritten.

No npm, no `.npmrc`, no lockfile, no global or ambient state takes part: the only inputs are the
operation (which holds exact versions and digests) and the registry's bytes. Nothing is placed unless
every archive verified and every approved script succeeded.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from mc.core import _log
from mc.desk_connect import custom_npm_artifact as _artifact
from mc.desk_connect import custom_npm_edges as _edges
from mc.desk_connect import custom_npm_scripts as _scripts
from mc.desk_connect import mcp_activation as _base
from mc.desk_connect import mcp_package_store as _store
from mc.desk_connect import parameter_parsers as _pp
from mc.desk_connect import parameter_schema as _ps
from mc.desk_connect.mcp_errors import ActivationError

WORKERS = 6
_PATH_RE = re.compile(r'^node_modules/(?:@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*'
                      r'(?:/node_modules/(?:@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*)*$')


def _bad(message: str = 'the approved package details are not valid, so nothing was saved') -> ActivationError:
    return ActivationError(message, 'package_invalid', 400)


def needs_closure(op: dict) -> bool:
    return bool(op.get('dependencies') or op.get('install_steps'))


def validate(op: dict) -> None:
    """The shape of an approved closure and its steps, checked again at install (the record is data on disk).
    Raises ActivationError."""
    deps, steps = op.get('dependencies') or [], op.get('install_steps') or []
    if not isinstance(deps, list) or not isinstance(steps, list) or len(deps) > 400 or len(steps) > 800:
        raise _bad()
    places: dict[str, dict] = {}
    for d in deps:
        if not isinstance(d, dict) or set(d) != {'name', 'version', 'integrity', 'path'} \
                or not all(isinstance(d[k], str) for k in d):
            raise _bad()
        if not _edges.valid_name(d['name']) or not _pp._EXACT_NPM.match(d['version']) \
                or not _artifact._INTEGRITY_RE.match(d['integrity']) or not _PATH_RE.match(d['path']) \
                or not d['path'].endswith(f'node_modules/{d["name"]}') or d['path'] in places:
            raise _bad()
        places[d['path']] = d
    for p in places:                                       # a nested package sits inside another approved one
        parent = p.rsplit('/node_modules/', 1)[0] if '/node_modules/' in p else ''
        if parent and parent not in places:
            raise _bad()
    for s in steps:
        if not isinstance(s, dict) or set(s) != {'path', 'package', 'version', 'script', 'body'} \
                or not all(isinstance(s[k], str) for k in s) or s['script'] not in _edges.RUN_SCRIPTS \
                or not s['body'].strip() or len(s['body']) > _edges.MAX_SCRIPT or _ps.has_hidden_chars(s['body']):
            raise _bad()
        owner = places.get(s['path'])
        if (owner is None and not (s['path'] == '' and s['package'] == op['package'] and s['version'] == op['version'])) \
                or (owner is not None and (owner['name'], owner['version']) != (s['package'], s['version'])):
            raise _bad()


def is_installed(op: dict) -> bool:
    """`custom_npm_artifact.is_installed`, and for a closure also: the marker names this closure and every
    approved package is on disk."""
    if not needs_closure(op):
        return _artifact.is_installed(op)
    try:
        marker = json.loads((_artifact.package_dir(op) / _artifact.VERIFIED_MARKER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if not isinstance(marker, dict) or marker.get('integrity') != op['integrity'] \
            or marker.get('closure') != _artifact.dir_id(op) or not _artifact.entry_path(op).is_file():
        return False
    base = _artifact.package_dir(op) / 'package'
    return all((base / PurePosixPath(d['path']) / 'package.json').is_file() for d in op.get('dependencies') or [])


def _fetch_verified(label: str, url: str, integrity: str) -> bytes:
    data = _artifact._download(url, label)
    if not hmac.compare_digest(_artifact.integrity_of(data), integrity):
        raise ActivationError(f'the file the registry served for {label} does not match the checksum you approved, so '
                              f'it was NOT saved. Nothing from it was run.', 'pin_mismatch', 409)
    _artifact._check_decompressed(data)
    return data


def _unpack_into(data: bytes, stage: Path, dest: Path, n: int) -> None:
    """Unpack one archive and move its `package/` folder to `dest`; an existing `dest` is a refusal."""
    scratch = stage / f'.unpack-{n}'
    scratch.mkdir()
    try:
        _store._extract(data, scratch, _artifact.MAX_UNPACKED, _artifact.MAX_MEMBERS)
        if dest.exists() or dest.is_symlink():
            raise _bad('a package folder is already in the place a dependency belongs, so nothing was saved')
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(scratch / 'package', dest)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def install(op: dict) -> str:
    """Install `op` and return its entry file's path. Raises ActivationError; nothing is left in the
    final place on failure."""
    if not needs_closure(op):
        return _artifact.install(op)
    validate(op)
    if op['tarball'] != _artifact.tarball_url(op['package'], op['version']) or not _artifact._INTEGRITY_RE.match(op['integrity']):
        raise _bad()
    final = _artifact.package_dir(op)
    with _artifact._lock_for(final.name):
        if is_installed(op):
            return str(_artifact.entry_path(op))
        if final.exists():
            raise ActivationError('a folder for this package digest exists but is not the verified package. Clayrune '
                                  'does not overwrite it: remove it from Clayrune\'s own folder, then save again.',
                                  'package_store_conflict', 409)
        deps = op.get('dependencies') or []
        jobs = [(f'{op["package"]}@{op["version"]}', op['tarball'], op['integrity'])] + \
               [(f'{d["name"]}@{d["version"]}', _artifact.tarball_url(d['name'], d['version']), d['integrity']) for d in deps]
        stage = final.parent / f'.{final.name}.{uuid.uuid4().hex[:8]}.tmp'
        try:
            stage.mkdir(parents=True)
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                blobs = list(pool.map(lambda j: _fetch_verified(*j), jobs))
            _unpack_into(blobs[0], stage, stage / 'package', 0)
            if not (stage / 'package' / PurePosixPath(op['entry'])).is_file():
                raise _bad('the downloaded package does not contain the file you approved to run, so it was NOT saved')
            # a package that sits inside another one is unpacked after it (the list is in dependency order)
            for i, d in sorted(enumerate(deps, start=1), key=lambda x: (x[1]['path'].count('/node_modules/'), x[1]['path'])):
                _unpack_into(blobs[i], stage, stage / 'package' / PurePosixPath(d['path']), i)
            del blobs
            _run_steps(op, stage)
            (stage / _artifact.VERIFIED_MARKER).write_text(
                json.dumps({'integrity': op['integrity'], 'package': op['package'], 'version': op['version'],
                            'closure': _artifact.dir_id(op)}), encoding='utf-8')
            os.replace(stage, final)
        except ActivationError:
            raise
        except OSError as e:
            _log(f'[desk_connect] custom npm closure could not be placed: {type(e).__name__}', flush=True)
            raise ActivationError('the package could not be written to Clayrune\'s own folder; see the server log',
                                  'package_write_failed', 500) from e
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return str(_artifact.entry_path(op))


def _run_steps(op: dict, stage: Path) -> None:
    """Run the approved install steps, in the order of the operation, in the staging folder."""
    steps = op.get('install_steps') or []
    if not steps:
        return
    node_dir = str(Path(_base._node()).parent)
    home = stage / '.script-home'
    try:
        for s in steps:
            cwd = stage / 'package' / PurePosixPath(s['path']) if s['path'] else stage / 'package'
            if not cwd.is_dir():
                raise _bad('an approved install script names a folder that is not in the package, so nothing was saved')
            _scripts.run(s, cwd, home, node_dir=node_dir)
    finally:
        shutil.rmtree(home, ignore_errors=True)
