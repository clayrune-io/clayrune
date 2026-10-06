"""What one npm package's `package.json` asks for (docs/DESK_SERVICE_PROFILES_SPEC.md, section 6.2,
slice U2b). Pure functions over a manifest that was read from an archive in memory
(`custom_npm_artifact.inspect`): no I/O, no registry, no npm.

`edges_of` splits a package's wishes into what Clayrune installs and what it does not:

    required   `dependencies` that the archive does not carry itself, plus `peerDependencies` that
               are not marked optional (npm 7+ installs those too). name -> range text.
    optional   `optionalDependencies`: NOT installed (they are platform extras; a package that
               cannot start without one will say so when it starts).
    bundled    names the archive carries under its own `node_modules/` (they come with its bytes).
    scripts    `preinstall`, `install`, `postinstall`: the bodies npm would run at install, exact.
    other_scripts  the other lifecycle scripts the package declares (`prepare`, ...): never run.
    native_build   a `binding.gyp` with no `install` script: npm would run `node-gyp rebuild`.

A malformed field is a refusal (`EdgeError`), never a guess: the card must show what will be
installed, so a manifest that cannot be read exactly is not approved.
"""
from __future__ import annotations

import re

from mc.desk_connect import parameter_schema as _ps

NAME_RE = re.compile(r'^(?:@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$')
MAX_NAME = 214
MAX_RANGE = 200
MAX_EDGES = 500
RUN_SCRIPTS = ('preinstall', 'install', 'postinstall')
OTHER_SCRIPTS = ('prepublish', 'preprepare', 'prepare', 'postprepare', 'prepublishOnly')
MAX_SCRIPT = 1000
BLANK_RUN = re.compile(r'\s{20,}')              # a body is shown in a short scrolling box: a long blank run could push text out of sight


def cannot_be_shown(body: str) -> str | None:
    """Why a script body cannot be approved (it is still listed, off), or None. One rule for Review and for
    the install that re-checks the record."""
    if len(body) > MAX_SCRIPT:
        return 'longer than Clayrune shows in full'
    if _ps.has_hidden_chars(body):
        return 'contains control or hidden characters, so it cannot be shown exactly'
    if BLANK_RUN.search(body):
        return 'contains a long run of blank space that could hide text from the card, so it cannot be shown exactly'
    return None


class EdgeError(ValueError):
    """The manifest's dependency fields are not in a shape Clayrune will install from."""


def valid_name(name) -> bool:
    return isinstance(name, str) and len(name) <= MAX_NAME and bool(NAME_RE.match(name))


def _shown(value) -> str:
    return ''.join(c if c.isprintable() else '?' for c in str(value)[:60])


def _dep_map(raw, field: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_EDGES:
        raise EdgeError(f'its "{field}" is not a list of packages Clayrune can read')
    out = {}
    for name, rng in raw.items():
        if not valid_name(name):
            raise EdgeError(f'it names a dependency Clayrune cannot read: {_shown(name)}')
        if not isinstance(rng, str) or len(rng) > MAX_RANGE or _ps.has_hidden_chars(rng):
            raise EdgeError(f'the version of its dependency {name} is not plain text')
        out[name] = rng.strip()
    return out


def bundled_present(files: dict) -> set[str]:
    """Names of the packages the archive carries at `package/node_modules/<name>/package.json`."""
    out = set()
    for path in files:
        parts = path.split('/')
        if parts[:2] != ['package', 'node_modules'] or parts[-1] != 'package.json':
            continue
        if len(parts) == 4 and not parts[2].startswith('@'):
            out.add(parts[2])
        elif len(parts) == 5 and parts[2].startswith('@'):
            out.add(f'{parts[2]}/{parts[3]}')
    return out


def occupied_names(files: dict) -> set[str]:
    """Every name with ANY file under `package/node_modules/` (a folder Clayrune must not place into);
    `.bin` stands for the folder of command links."""
    out = set()
    for path in files:
        parts = path.split('/')
        if parts[:2] != ['package', 'node_modules'] or len(parts) < 3:
            continue
        if parts[2].startswith('@'):
            if len(parts) >= 4:
                out.add(f'{parts[2]}/{parts[3]}')
        else:
            out.add(parts[2])
    return out


def edges_of(manifest: dict, files: dict) -> dict:
    """`{required, optional, bundled, scripts, other_scripts, native_build}` for one package; raises EdgeError."""
    deps = _dep_map(manifest.get('dependencies'), 'dependencies')
    optional = _dep_map(manifest.get('optionalDependencies'), 'optionalDependencies')
    peers = _dep_map(manifest.get('peerDependencies'), 'peerDependencies')
    meta = manifest.get('peerDependenciesMeta')
    meta = meta if isinstance(meta, dict) else {}
    present = bundled_present(files)
    declared = manifest.get('bundledDependencies', manifest.get('bundleDependencies'))
    bundled = set(deps) if declared is True else {b for b in declared if isinstance(b, str)} if isinstance(declared, list) else set()
    carried = sorted(n for n in bundled if n in present)
    required = {n: r for n, r in deps.items() if n not in optional and n not in carried}
    for n, r in peers.items():
        optional_peer = isinstance(meta.get(n), dict) and meta[n].get('optional') is True
        if n not in deps and n not in optional and not optional_peer:
            required[n] = r
    declared_scripts = manifest.get('scripts')
    raw = declared_scripts if isinstance(declared_scripts, dict) else {}
    scripts = {s: raw[s] for s in RUN_SCRIPTS if isinstance(raw.get(s), str) and raw[s].strip()}
    return {'required': dict(sorted(required.items())), 'optional': sorted(optional), 'bundled': carried,
            'scripts': scripts,
            'other_scripts': [s for s in OTHER_SCRIPTS if s in raw],
            'native_build': 'package/binding.gyp' in files and 'install' not in raw}
