"""The reviewed Aider surface. Any source/version drift requires another audit."""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

VERSION = '0.86.2'
SOURCE_SHA256 = 'c20db8e0c2fd5bd0bc97ca30ac4fd638de61272c1a7b19fd24f15cbfe8c69eda'
RESOURCES_SHA256 = '3839547cfc5eb0d0cd86a264f3d86cbb367fa69225838a7167804a09eeadf2b0'
# These dependencies sit on the process/model/native-feature boundaries.
DEPENDENCIES = {'GitPython': '3.1.46', 'litellm': '1.81.10',
                'diskcache': '5.6.3', 'soundfile': '0.13.1',
                'pexpect': '4.9.0', 'pillow': '12.1.1'}


def verify_tree(root: Path) -> None:
    # Extension modules and sourceless bytecode can shadow a hashed .py file.
    # __pycache__ files are ignored by the runner's fresh bytecode prefix.
    for path in root.rglob('*'):
        if path.is_file() and (path.name.endswith(tuple(importlib.machinery.EXTENSION_SUFFIXES))
                               or (path.suffix == '.pyc' and '__pycache__' not in path.parts)):
            raise RuntimeError('Aider executable-shape drift; native/sourceless module refused')
    digest = hashlib.sha256()
    files = sorted(root.rglob('*.py'))
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
    if len(files) != 81 or digest.hexdigest() != SOURCE_SHA256:
        raise RuntimeError('Aider audited internal-shape/source drift; re-audit required')
    resources = sorted(p for p in (root / 'resources').rglob('*')
                       if p.is_file() and p.suffix != '.pyc')
    digest = hashlib.sha256()
    for path in resources:
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
    if len(resources) != 3 or digest.hexdigest() != RESOURCES_SHA256:
        raise RuntimeError('Aider model-resource drift; re-audit required')


def verify_installed() -> Path:
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('restricted Aider requires audited CPython 3.12')
    if importlib.metadata.version('aider-chat') != VERSION:
        raise RuntimeError(f'restricted Aider requires aider-chat=={VERSION}; version drift')
    for name, version in DEPENDENCIES.items():
        if importlib.metadata.version(name) != version:
            raise RuntimeError(f'Aider dependency drift: requires {name}=={version}')
    root = Path(str(importlib.metadata.distribution('aider-chat').locate_file('aider'))).resolve()
    verify_tree(root)
    spec = importlib.util.find_spec('aider')
    if (spec is None or not spec.origin or Path(spec.origin).resolve() != root / '__init__.py'
            or [Path(p).resolve() for p in spec.submodule_search_locations or ()] != [root]):
        raise RuntimeError('Aider import-path drift; selected package is shadowed')
    return root
