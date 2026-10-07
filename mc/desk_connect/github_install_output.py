"""One Save-time record of generated files, separate from the reviewed source pin.

Generated files are not rechecked at launch. Their directories are pruned so a
large node_modules/build tree never consumes the source check's limits or I/O.
The operation pins this policy and the approval card states the limitation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from mc.desk_connect import github_manifest as manifest
from mc.desk_connect.mcp_errors import ActivationError

SCHEMA = 'desk-github-files/2'
MAX_ENTRIES = 200_000
MAX_BYTES = 2_000_000_000
POLICY = {'source': 'sha256-before-every-start', 'install_output': 'record-once-at-save'}
NOTICE = ('Reviewed source files are checked before every start. Dependencies and build output '
          'are recorded once at Save, but are not checked again at start; later changes to them can run code.')


def _parents(path: str) -> set[str]:
    return {p.as_posix() for p in PurePosixPath(path).parents if p.as_posix() != '.'}


def _source_dirs(source: dict[str, str]) -> set[str]:
    return {parent for path in source for parent in _parents(path)}


def _changed() -> ActivationError:
    return ActivationError('The reviewed source files changed during setup. Review the connection again.',
                           'repository_changed', 409)


def capture(directory: str, source: dict[str, str]) -> dict:
    """Hash outputs once with independent bounds; never adopt edits to reviewed source."""
    root = Path(directory)
    protected = _source_dirs(source)
    outputs: dict[str, dict] = {}
    generated_dirs: set[str] = set()
    found: dict[str, str] = {}
    count = total = 0
    for p in manifest.entries(directory):
        count += 1
        if count > MAX_ENTRIES:
            raise ActivationError('Install output is too large to record safely.', 'install_output_too_large', 400)
        rel = p.relative_to(root).as_posix()
        if getattr(p, 'is_junction', lambda: False)():
            raise ActivationError('Junctions in install output cannot be recorded safely.', 'unsafe_repository', 400)
        if p.is_symlink():
            if rel in source or rel in protected:
                raise _changed()
            # npm's .bin links are legitimate output, but cannot point outside this clone.
            try:
                p.resolve(strict=True).relative_to(root.resolve())
            except (OSError, ValueError, RuntimeError) as e:
                raise ActivationError('Install output links must stay inside the saved repository.',
                                      'unsafe_repository', 400) from e
            outputs[rel] = {'link': p.readlink().as_posix()}
            continue
        if p.is_dir():
            if rel not in protected:
                generated_dirs.add(rel)
            continue
        if not p.is_file():
            raise ActivationError('Install output contains an unsupported file.', 'unsafe_repository', 400)
        digest = hashlib.sha256()
        size = 0
        with p.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ActivationError('Install output is too large to record safely.', 'install_output_too_large', 400)
                digest.update(chunk)
        if rel in source:
            found[rel] = digest.hexdigest()
        else:
            outputs[rel] = {'sha256': digest.hexdigest(), 'bytes': size}
    if found != source:
        raise _changed()
    # Keep only the outermost generated paths; no source file/ancestor may be skipped.
    skip = {p for p in generated_dirs if not (_parents(p) & generated_dirs)}
    skip.update(p for p in outputs if not (_parents(p) & skip))
    return {'schema': SCHEMA, 'source_files': source, 'output_files': outputs,
            'skip': sorted(skip), 'entry_count': count, 'bytes': total}


def source_matches(op: dict, saved: object) -> bool:
    """Fail closed on an incompatible record or an exclusion that could hide source."""
    if not isinstance(saved, dict) or saved.get('schema') != SCHEMA or saved.get('source_files') != op['source_files']:
        return False
    skip = saved.get('skip')
    if not isinstance(skip, list) or len(skip) > MAX_ENTRIES:
        return False
    protected = _source_dirs(op['source_files']) | set(op['source_files'])
    for path in skip:
        if not isinstance(path, str) or not path or '\\' in path or ':' in path:
            return False
        rel = PurePosixPath(path)
        if rel.is_absolute() or '..' in rel.parts or rel.as_posix() != path or path == '.' or path in protected:
            return False
    if not isinstance(saved.get('output_files'), dict):
        return False
    return manifest.inventory(op['directory'], skip=set(skip)) == op['source_files']
