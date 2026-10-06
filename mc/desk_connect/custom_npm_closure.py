"""The exact dependency closure of a user-chosen npm package (docs/DESK_SERVICE_PROFILES_SPEC.md,
section 6.2, slice U2b). Clayrune does this itself, at Review, on the server; no `npm`, `npx`,
`.npmrc`, lockfile or lifecycle script takes part, so none of them can choose what is installed.

    1. FACTS      For every package the root asks for (and every package those ask for, to the
                  end) the registry's version list (`custom_npm_registry`) selects ONE exact
                  version for each range, and that version's archive is downloaded, hashed and
                  read WITHOUT unpacking it (`custom_npm_artifact.inspect`). The next level of
                  dependencies comes from that archive's own `package.json`, not from the
                  registry's copy of it. A range is resolved here, once, to a version; only exact
                  versions leave this module.
    2. PLACEMENT  A pure, deterministic layout of the facts into folders, the way Node finds them:
                  a package goes to the top `node_modules` when nothing is there, and next to its
                  dependent when a different version is in the way; an already placed version
                  that satisfies a range is reused. Packages an archive carries inside itself
                  (`bundledDependencies`) stay as the archive has them.
    3. RESULT     The ordered list `[{name, version, integrity, path}]` (dependencies before the
                  packages that need them), the install scripts each package declares (exact
                  bodies, none run), and totals. That list IS what the approval card shows and the
                  operation fingerprints; installation (`custom_npm_install`) downloads exactly
                  those archives again and holds each to its digest.

What is refused rather than guessed: a dependency that is not a registry range (a git, file, link,
alias or workspace address), a name or version the registry does not have, an archive that names
another package than the one asked for, a registry that points a tarball at another address, a
stated digest that does not match the bytes, and a closure past the limits below. Optional
dependencies are not installed and are listed on the card; required peer dependencies are installed.

Every refusal is an `ActivationError` with a plain message; none carries package text beyond
names and versions cut to a safe length.
"""
from __future__ import annotations

import hmac
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from mc.desk_connect import custom_npm_artifact as _artifact
from mc.desk_connect import custom_npm_edges as _edges
from mc.desk_connect import custom_npm_registry as _registry
from mc.desk_connect import custom_npm_semver as _semver
from mc.desk_connect import parameter_schema as _ps
from mc.desk_connect.mcp_errors import ActivationError

MAX_PACKAGES = 200
MAX_LEVELS = 40
MAX_DOWNLOAD_TOTAL = 96 * 1024 * 1024
TOTAL_S = 120
WORKERS = 6
CACHE_TTL_S = 10 * 60

_cache_lock = threading.Lock()
_docs: dict[str, tuple[float, dict]] = {}
_facts: dict[tuple[str, str], tuple[float, dict]] = {}
_BUNDLED = object()                                      # a folder the archive carries: its version is not read


def _forget_all_for_tests() -> None:
    with _cache_lock:
        _docs.clear()
        _facts.clear()


def _refuse(message: str, code: str, status: int = 422) -> ActivationError:
    return ActivationError(message, code, status)


def _shown(text) -> str:
    return ''.join(c if c.isprintable() else '?' for c in str(text)[:80])


# ── 1. facts ─────────────────────────────────────────────────────────────────

def _doc(name: str) -> dict:
    with _cache_lock:
        hit = _docs.get(name)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
    doc = _registry.fetch(name)
    with _cache_lock:
        _docs[name] = (time.monotonic(), doc)
    return doc


def _fact(name: str, version: str, meta: dict) -> dict:
    """The facts of one exact version: downloaded, hashed, read in memory, checked against what the
    registry states for it. Raises ActivationError."""
    with _cache_lock:
        hit = _facts.get((name, version))
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
    label = f'{name}@{version}'
    url = _artifact.tarball_url(name, version)
    if meta['tarball'] not in (None, url):
        raise _refuse(f'the registry points {_shown(label)} at an archive address Clayrune does not use, so nothing was '
                      f'saved', 'registry_address_unexpected', 502)
    data = _artifact._download(url, label)
    integrity = _artifact.integrity_of(data)
    stated = meta['integrity']
    if stated is not None and stated.startswith('sha512-') and not hmac.compare_digest(stated, integrity):
        raise _refuse(f'the archive the registry served for {_shown(label)} does not match the checksum the registry '
                      f'states for it, so nothing was saved', 'pin_mismatch', 409)
    seen = _artifact.inspect(data)
    manifest = seen['manifest']
    if manifest.get('name') != name or manifest.get('version') != version:
        raise _artifact._bad(f'the package.json inside {_shown(label)} names a different package or version than the '
                             f'registry, so nothing was saved')
    try:
        edges = _edges.edges_of(manifest, seen['files'])
    except _edges.EdgeError as e:
        raise _refuse(f'{_shown(label)}: {e}, so nothing was saved', 'dependencies_unreadable') from e
    fact = {'name': name, 'version': version, 'integrity': integrity, 'size_bytes': len(data),
            'unpacked_bytes': seen['unpacked_bytes'], 'members': seen['members'], 'edges': edges,
            'occupied': sorted(_edges.occupied_names(seen['files'])), 'licence': _artifact._licence(manifest),
            'stated': stated is not None and stated.startswith('sha512-'), 'deprecated': meta['deprecated']}
    with _cache_lock:
        _facts[(name, version)] = (time.monotonic(), fact)
    return fact


def _check_range(owner: str, name: str, range_text: str) -> None:
    if _semver.is_tag(range_text.strip()):
        return
    try:
        _semver.parse_range(range_text)
    except _semver.RangeError as e:
        raise _refuse(f'{_shown(owner)} depends on {_shown(name)} as "{_shown(range_text)}", which is not a version of '
                      f'a registry package Clayrune can pin (a git, file, link, alias or workspace address is not '
                      f'installed), so nothing was saved', 'dependency_unsupported') from e


def _explore(root: dict, *, now=time.monotonic) -> tuple[dict, dict]:
    """`(picks, facts)`: `picks[(name, range)]` is the exact version a range selects, `facts[(name, version)]`
    what its archive holds. Level by level, each level fetched in parallel; the result does not depend on
    the order downloads finish."""
    started = now()
    picks: dict[tuple[str, str], str] = {}
    facts: dict[tuple[str, str], dict] = {}
    docs: dict[str, dict] = {}
    owners: dict[tuple[str, str], str] = {}
    frontier = sorted(root['edges']['required'].items())
    for n, r in frontier:
        owners[(n, r)] = f'{root["package"]}@{root["version"]}'
    levels = 0
    downloaded = 0
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='mcp-closure') as pool:
        while frontier:
            levels += 1
            if levels > MAX_LEVELS or now() - started > TOTAL_S:
                raise _refuse('the dependency tree is too deep or took too long to read, so nothing was saved',
                              'closure_too_large' if levels > MAX_LEVELS else 'closure_timeout', 413)
            for (n, r) in frontier:
                _check_range(owners[(n, r)], n, r)
            need = sorted({n for n, _ in frontier if n not in docs})
            for name, doc in zip(need, pool.map(_doc, need)):
                docs[name] = doc
            fresh: list[tuple[str, str]] = []
            for (n, r) in frontier:
                if (n, r) in picks:
                    continue
                v = _registry.pick(docs[n], r)
                if v is None:
                    raise _refuse(f'no published version of {_shown(n)} fits "{_shown(r)}", which '
                                  f'{_shown(owners[(n, r)])} asks for, so nothing was saved', 'dependency_unsatisfiable')
                picks[(n, r)] = v
                if (n, v) not in facts and (n, v) not in fresh:
                    fresh.append((n, v))
            if len(facts) + len(fresh) > MAX_PACKAGES:
                raise _refuse(f'the dependency tree has more than {MAX_PACKAGES} packages, which is more than Clayrune '
                              f'installs, so nothing was saved', 'closure_too_large', 413)
            for (n, v), fact in zip(fresh, pool.map(lambda nv: _fact(nv[0], nv[1], docs[nv[0]]['versions'][nv[1]]), fresh)):
                facts[(n, v)] = fact
                downloaded += fact['size_bytes']
            if downloaded > MAX_DOWNLOAD_TOTAL:
                raise _refuse('the dependencies add up to more data than Clayrune downloads, so nothing was saved',
                              'closure_too_large', 413)
            nxt: dict[tuple[str, str], None] = {}
            for (n, v) in fresh:
                for dn, dr in facts[(n, v)]['edges']['required'].items():
                    if (dn, dr) not in picks and (dn, dr) not in nxt:
                        nxt[(dn, dr)] = None
                        owners[(dn, dr)] = f'{n}@{v}'
            frontier = sorted(nxt)
    return picks, facts


# ── 2. placement ─────────────────────────────────────────────────────────────

class _Node:
    __slots__ = ('name', 'version', 'path', 'parent', 'children', 'requires', 'fact')

    def __init__(self, name, version, path, parent, fact):
        self.name, self.version, self.path, self.parent, self.fact = name, version, path, parent, fact
        self.children: dict = {n: _BUNDLED for n in fact['occupied']}
        self.requires: list = []


def _visible(node: _Node, name: str):
    """The folder Node would find for `name` from inside `node`: the nearest `node_modules` up the chain."""
    n = node
    while n is not None:
        c = n.children.get(name)
        if c is not None:
            return c
        n = n.parent
    return None


def _place(root_fact: dict, picks: dict, facts: dict) -> _Node:
    root = _Node(root_fact['name'], root_fact['version'], '', None, root_fact)
    queue = deque([root])
    placed = 0
    while queue:
        parent = queue.popleft()
        for dname, drange in sorted(parent.fact['edges']['required'].items()):
            seen = _visible(parent, dname)
            if seen is not None and seen is not _BUNDLED and _semver.satisfies(seen.version, drange):
                if seen not in parent.requires:
                    parent.requires.append(seen)
                continue
            version = picks[(dname, drange)]
            if seen is None:
                host = root
            elif parent.children.get(dname) is None:
                host = parent                               # a different version is in the way up the chain: nest
            else:
                raise _refuse(f'{_shown(parent.name)} and the package it carries inside itself disagree about '
                              f'{_shown(dname)}, so nothing was saved', 'dependency_conflict')
            path = f'{host.path}/node_modules/{dname}' if host.path else f'node_modules/{dname}'
            node = _Node(dname, version, path, host, facts[(dname, version)])
            host.children[dname] = node
            parent.requires.append(node)
            queue.append(node)
            placed += 1
            if placed > MAX_PACKAGES:
                raise _refuse(f'the dependency tree has more than {MAX_PACKAGES} packages, which is more than Clayrune '
                              f'installs, so nothing was saved', 'closure_too_large', 413)
    return root


def _ordered(root: _Node) -> list[_Node]:
    """Every placed package, each after the packages it requires (a cycle is cut where it closes)."""
    out: list[_Node] = []
    seen: set[int] = {id(root)}

    def visit(n: _Node) -> None:
        for r in sorted(n.requires, key=lambda x: (x.name, x.path)):
            if id(r) not in seen:
                seen.add(id(r))
                visit(r)
                out.append(r)
    visit(root)
    return out


# ── 3. result ────────────────────────────────────────────────────────────────

def _scripts_of(path: str, name: str, version: str, bodies: dict) -> list[dict]:
    out = []
    for script in _edges.RUN_SCRIPTS:
        body = bodies.get(script)
        if body is None:
            continue
        reason = None
        if len(body) > _edges.MAX_SCRIPT:
            reason = 'longer than Clayrune shows in full'
        elif _ps.has_hidden_chars(body):
            reason = 'contains control or hidden characters, so it cannot be shown exactly'
        out.append({'id': f'{path or "."}#{script}', 'path': path, 'package': name, 'version': version,
                    'script': script, 'body': body if reason is None else None,
                    'body_escaped': None if reason is None else body.encode('unicode_escape').decode('ascii')[:300],
                    'approvable': reason is None, 'reason': reason})
    return out


def resolve(root: dict, *, now=time.monotonic) -> dict:
    """The closure of the root package `root` (a `custom_npm_artifact.resolve` result):

        {dependencies: [{name, version, integrity, path}], details: {path: {...}}, scripts: [...],
         totals: {count, download_bytes, unpacked_bytes, members}, skipped: [{package, optional: [...]}],
         native_build: [path, ...]}

    A root that needs nothing gives the empty closure with no network use. Raises ActivationError."""
    edges = root['edges']
    if not edges['required']:
        return {'dependencies': [], 'details': {}, 'scripts': _scripts_of('', root['package'], root['version'], edges['scripts']),
                'totals': {'count': 0, 'download_bytes': 0, 'unpacked_bytes': 0, 'members': 0},
                'skipped': [{'package': f'{root["package"]}@{root["version"]}', 'optional': edges['optional']}] if edges['optional'] else [],
                'native_build': [''] if edges['native_build'] else []}
    picks, facts = _explore(root, now=now)
    root_fact = {'name': root['package'], 'version': root['version'], 'edges': edges, 'occupied': root['occupied']}
    tree = _place(root_fact, picks, facts)
    nodes = _ordered(tree)
    members = sum(n.fact['members'] for n in nodes) + root['members']
    unpacked = sum(n.fact['unpacked_bytes'] for n in nodes) + root['unpacked_bytes']
    if members > _artifact.MAX_TREE_MEMBERS or unpacked > _artifact.MAX_TREE_UNPACKED:
        raise _refuse('the package and its dependencies unpack to more than Clayrune accepts, so nothing was saved',
                      'closure_too_large', 413)
    deps = [{'name': n.name, 'version': n.version, 'integrity': n.fact['integrity'], 'path': n.path} for n in nodes]
    details = {n.path: {'size_bytes': n.fact['size_bytes'], 'unpacked_bytes': n.fact['unpacked_bytes'],
                        'members': n.fact['members'], 'licence': n.fact['licence'],
                        'registry_stated_digest': n.fact['stated'], 'deprecated': n.fact['deprecated']} for n in nodes}
    scripts = [s for n in nodes for s in _scripts_of(n.path, n.name, n.version, n.fact['edges']['scripts'])]
    scripts += _scripts_of('', root['package'], root['version'], edges['scripts'])
    skipped = [{'package': f'{n.name}@{n.version}', 'optional': n.fact['edges']['optional']}
               for n in [tree, *nodes] if (n.fact['edges']['optional'])]
    native = [n.path for n in nodes if n.fact['edges']['native_build']] + ([''] if edges['native_build'] else [])
    return {'dependencies': deps, 'details': details, 'scripts': scripts,
            'totals': {'count': len(nodes), 'download_bytes': sum(n.fact['size_bytes'] for n in nodes),
                       'unpacked_bytes': unpacked, 'members': members},
            'skipped': skipped, 'native_build': native}
