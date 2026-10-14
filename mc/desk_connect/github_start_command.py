"""Deterministic Node entry inference; reads metadata, never executes package scripts."""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath


def _local_file(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'(?:\./)?[\w@./-]+\.(?:js|cjs|mjs)', value):
        return ''
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        return ''
    return path.as_posix()


def infer(package: object, files: dict[str, str]) -> dict:
    """Prefer one declared bin, then a plain node scripts.start. Missing build output
    is usable only with an explicit build script; it is never run/approved here.
    Registry launchers, shell expressions and ambiguous bins are not inferred.
    """
    if not isinstance(package, dict):
        return {}
    scripts = package.get('scripts')
    scripts = scripts if isinstance(scripts, dict) else {}
    bins = package.get('bin')
    paths = list(bins.values()) if isinstance(bins, dict) else [bins]
    paths = list(dict.fromkeys(_local_file(p) for p in paths))
    candidates: list[tuple[str, list[str]]] = []
    if len(paths) == 1 and paths[0]:
        candidates.append(('package.json bin', paths))
    start = scripts.get('start')
    if isinstance(start, str) and len(start) <= 2000:
        try:
            argv = shlex.split(start)
        except ValueError:
            argv = []
        if len(argv) == 2 and argv[0] == 'node' and _local_file(argv[1]):
            candidates.append(('package.json scripts.start', [_local_file(argv[1])]))
    for source, args in candidates:
        entry = args[0]
        build = entry not in files
        if build and not (entry.startswith(('dist/', 'build/', 'lib/')) and isinstance(scripts.get('build'), str) and scripts['build'].strip()):
            continue
        return {'command': 'node', 'args': args, 'command_source': source,
                'build_required': build, 'required_steps': ['script:build'] if build else []}
    return {}
