"""The archive of a user-chosen, self-contained npm package (docs/DESK_SERVICE_PROFILES_SPEC.md,
section 6.2, slice U2a). The custom counterpart of `mcp_package_store`, which stays the
reviewed catalogue's: same direct download, same safe-member rule, same extractor, with
the pin taken from the bytes Clayrune itself read instead of from a catalogue.

    resolve   Review time. The registry's version document (U1's reader, one document, no
              redirect) names an EXACT version; Clayrune downloads that one tarball itself,
              hashes it, and reads the archive WITHOUT unpacking it or running anything:
              every member is checked, `package.json` is read, the entry file is chosen from
              files that are really in the archive. The result is the facts the approval
              card shows and the pin the approval covers.
    install   Save time. The same tarball is downloaded again and must hash to the approved
              digest (a mismatch is an error, never a new pin), then is unpacked into a
              DIGEST-ADDRESSED directory `~/.clayrune/mcp_custom_packages/<digest>/` that is
              never overwritten: an approved version is immutable, so no later Save can change
              bytes a running server uses.

Self-contained means the archive needs nothing else to run: `dependencies` is empty, or every
entry is bundled in the archive itself. Anything else needs dependency installation, which is
slice U2b, and is refused here with that reason: it is never "approved" and then launched with
whatever `npm` would fetch. No npm, npx, `.npmrc` or install script is anywhere in this path;
lifecycle scripts the package declares are listed on the card and are NOT run.

Limits are enforced before any byte is written: download size and total time, decompressed
size (a gzip bomb), declared unpacked size, member count and depth; a link, device, absolute
or `..` member refuses the whole package.

Every refusal is an `ActivationError` with a plain message; none carries package text.
"""
from __future__ import annotations

import base64
import gzip
import hmac
import io
import json
import os
import re
import shutil
import tarfile
import threading
import uuid
import zlib
from pathlib import PurePosixPath

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import mcp_package_store as _store
from mc.desk_connect import parameter_parsers as _pp
from mc.desk_connect import parameter_schema as _ps
from mc.desk_connect import parameter_sources as _sources
from mc.desk_connect.mcp_errors import ActivationError

REGISTRY = 'https://registry.npmjs.org/'
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_MEMBERS = 4000
MAX_DEPTH = 16
MAX_MANIFEST = 1024 * 1024
MAX_DECOMPRESSED = MAX_UNPACKED + MAX_MEMBERS * 1024 + (1 << 20)     # data + tar headers + slack
DOWNLOAD_S = 60
VERIFIED_MARKER = '.clayrune-verified.json'
INSTALL_SCRIPTS = ('preinstall', 'install', 'postinstall', 'prepare', 'prepublish', 'prepublishOnly')
_ENTRY_RE = re.compile(r'^[A-Za-z0-9_@+-][A-Za-z0-9._@+-]{0,80}(/[A-Za-z0-9_@+-][A-Za-z0-9._@+-]{0,80}){0,7}\.(mjs|cjs|js)$')
_INTEGRITY_RE = re.compile(r'^sha512-[A-Za-z0-9+/]{86}==$')

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def tarball_url(package: str, version: str) -> str:
    """Where npm publishes `package@version`: the only address a custom package is fetched from."""
    return f'{REGISTRY}{package}/-/{package.rsplit("/", 1)[-1]}-{version}.tgz'


def integrity_of(data: bytes) -> str:
    return _store.integrity_of(data)


def digest_id(integrity: str) -> str:
    """A directory name for an approved digest: the first 40 hex digits of its sha512."""
    return base64.b64decode(integrity.split('-', 1)[1]).hex()[:40]


def packages_root():
    return _vault.clayrune_home() / 'mcp_custom_packages'


def package_dir(op: dict):
    return packages_root() / digest_id(op['integrity'])


def entry_path(op: dict):
    return package_dir(op) / 'package' / PurePosixPath(op['entry'])


def _bad(msg: str = 'the downloaded package has an unexpected layout, so it was NOT saved',
         code: str = 'package_invalid', status: int = 502) -> ActivationError:
    return ActivationError(msg, code, status)


# ── download ─────────────────────────────────────────────────────────────────

def _download(url: str, spec: str) -> bytes:
    """The one network call of an archive, finished within `DOWNLOAD_S` seconds in total."""
    try:
        return _store._fetch_within(url, MAX_DOWNLOAD, DOWNLOAD_S)
    except TimeoutError as e:
        raise ActivationError(f'the package registry did not answer within {DOWNLOAD_S} seconds, so {spec} could '
                              f'not be downloaded. Try again later.', 'download_timeout', 504) from e
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] custom npm download failed: {type(e).__name__}', flush=True)
        raise ActivationError(f'{spec} could not be downloaded from the package registry. Try again later.',
                              'download_failed', 502) from e


# ── reading the archive without unpacking it ─────────────────────────────────

def _check_decompressed(data: bytes) -> None:
    """Refuse a gzip stream that expands past `MAX_DECOMPRESSED` (a bomb) before anything reads it as a tar."""
    total = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            while True:
                chunk = g.read(1 << 16)
                if not chunk:
                    return
                total += len(chunk)
                if total > MAX_DECOMPRESSED:
                    raise _bad('the package expands to far more data than a package of this kind should, so it was '
                               'NOT saved', 'package_too_large', 413)
    except ActivationError:
        raise
    except (OSError, EOFError, zlib.error) as e:
        raise _bad() from e


def inspect(data: bytes) -> dict:
    """`{manifest, files, unpacked_bytes, members}` read from the archive in memory. Raises
    ActivationError for a layout the extractor would also refuse, or a size over the limits."""
    _check_decompressed(data)
    files: dict[str, int] = {}
    seen: set[str] = set()
    total = 0
    manifest = None
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
            for m in tf:
                parts = _store.safe_member_parts(m.name)
                if parts is None or m.name in seen or len(parts) > MAX_DEPTH or not (m.isfile() or m.isdir()):
                    raise _bad()
                seen.add(m.name)
                if len(seen) > MAX_MEMBERS:
                    raise _bad('the package has more files than Clayrune accepts, so it was NOT saved',
                               'package_too_large', 413)
                if m.isdir():
                    continue
                total += m.size
                if total > MAX_UNPACKED:
                    raise _bad('the package unpacks to more than Clayrune accepts, so it was NOT saved',
                               'package_too_large', 413)
                path = '/'.join(parts)
                files[path] = m.size
                if path == 'package/package.json':
                    if m.size > MAX_MANIFEST:
                        raise _bad()
                    src = tf.extractfile(m)
                    manifest = src.read(MAX_MANIFEST + 1) if src else None
    except ActivationError:
        raise
    except (tarfile.TarError, EOFError, OSError, ValueError) as e:
        raise _bad() from e
    if manifest is None:
        raise _bad('the package has no package.json, so it was NOT saved')
    try:
        doc = json.loads(manifest.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise _bad('the package.json inside the package cannot be read, so it was NOT saved') from e
    if not isinstance(doc, dict):
        raise _bad('the package.json inside the package cannot be read, so it was NOT saved')
    return {'manifest': doc, 'files': files, 'unpacked_bytes': total, 'members': len(seen)}


def _norm_entry(p) -> str | None:
    if not isinstance(p, str):
        return None
    q = PurePosixPath(p.replace('\\', '/'))
    parts = [x for x in q.parts if x != '.']
    if q.is_absolute() or '..' in parts or not parts:
        return None
    return '/'.join(parts)


def _entry_candidates(manifest: dict, files: dict) -> list[str]:
    raw = manifest.get('bin')
    found: list[str] = []
    if isinstance(raw, str):
        found.append(raw)
    elif isinstance(raw, dict):
        found.extend(v for _name, v in sorted(raw.items()) if isinstance(v, str))
    elif manifest.get('main'):
        found.append(manifest['main'])
    out: list[str] = []
    for p in found:
        n = _norm_entry(p)
        if n and _ENTRY_RE.match(n) and f'package/{n}' in files and n not in out:
            out.append(n)
    return out


def _dependency_gap(manifest: dict, files: dict) -> list[str]:
    deps = manifest.get('dependencies')
    if not isinstance(deps, dict) or not deps:
        return []
    bundled = manifest.get('bundledDependencies', manifest.get('bundleDependencies'))
    if bundled is True:
        names = set(deps)
    elif isinstance(bundled, list):
        names = {b for b in bundled if isinstance(b, str)}
    else:
        names = set()
    return sorted(d[:80] for d in deps if d not in names or f'package/node_modules/{d}/package.json' not in files)


def _claimed(doc: dict) -> str | None:
    """Who the registry says published this version. A claim, never verification."""
    npm_user = doc.get('_npmUser')
    name = npm_user.get('name') if isinstance(npm_user, dict) else None
    return _ps.clean_text(name, 80) if isinstance(name, str) else None


def _licence(manifest: dict) -> str | None:
    lic = manifest.get('license')
    if isinstance(lic, dict):
        lic = lic.get('type')
    return _ps.clean_text(lic, 60) if isinstance(lic, str) else None


# ── review time ──────────────────────────────────────────────────────────────

def resolve(spec_text: str, entry: str | None = None) -> dict:
    """The facts and the pin for the package a person typed, read without running anything.
    Raises ActivationError (with `code` `needs_dependencies`, `entry_choice_needed`, ...)."""
    try:
        spec = _pp.parse_package_spec('npm', spec_text)
    except _pp.InputError as e:
        raise ActivationError(str(e), e.code, 400) from e
    try:
        doc = _sources.fetch_npm(spec)
    except _sources.SourceError as e:
        status = 404 if e.code == 'source_not_found' else 502
        raise ActivationError(str(e), e.code, status) from e
    name, version = doc.get('name'), doc.get('version')
    if name != spec['name'] or not isinstance(name, str) or not isinstance(version, str) or not _pp._EXACT_NPM.match(version) \
            or (spec['version'] and version != spec['version']):
        raise ActivationError('the registry answered with a different package or version than the one asked for, '
                              'so nothing was saved', 'registry_mismatch', 502)
    url = tarball_url(name, version)
    dist: dict = doc.get('dist') if isinstance(doc.get('dist'), dict) else {}  # type: ignore[assignment]
    if dist.get('tarball') not in (None, url):
        raise ActivationError('the registry points at an archive address Clayrune does not use for this package, so '
                              'nothing was saved', 'registry_address_unexpected', 502)
    label = f'{name}@{version}'
    data = _download(url, label)
    integrity = integrity_of(data)
    stated = dist.get('integrity')
    if stated is not None and (not isinstance(stated, str) or not hmac.compare_digest(stated, integrity)):
        raise ActivationError(f'the archive the registry served for {label} does not match the checksum the registry '
                              f'states for it, so nothing was saved', 'pin_mismatch', 409)
    seen = inspect(data)
    manifest = seen['manifest']
    if manifest.get('name') != name or manifest.get('version') != version:
        raise _bad('the package.json inside the archive names a different package or version than the registry, '
                   'so nothing was saved')
    gap = _dependency_gap(manifest, seen['files'])
    if gap:
        raise ActivationError(f'{label} needs other packages installed ({", ".join(gap[:5])}'
                              f'{" and more" if len(gap) > 5 else ""}). Clayrune can run only a package that is one '
                              f'self-contained archive for now; installing dependencies is not available yet.',
                              'needs_dependencies', 422)
    candidates = _entry_candidates(manifest, seen['files'])
    if entry is not None:
        chosen = _norm_entry(entry)
        if not chosen or not _ENTRY_RE.match(chosen) or f'package/{chosen}' not in seen['files']:
            raise ActivationError('that file is not a .js, .mjs or .cjs file inside the package', 'bad_entry', 400)
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        raise ActivationError('Clayrune could not tell which file starts this package. Choose one.' if candidates else
                              'the package does not name a .js, .mjs or .cjs file to start. Name one to run.',
                              'entry_choice_needed', 422)
    scripts = manifest.get('scripts') if isinstance(manifest.get('scripts'), dict) else {}
    return {'package': name, 'version': version, 'tarball': url, 'integrity': integrity, 'entry': chosen,
            'entry_candidates': candidates, 'size_bytes': len(data), 'unpacked_bytes': seen['unpacked_bytes'],
            'members': seen['members'], 'licence': _licence(manifest), 'publisher_claimed': _claimed(doc),
            'registry_stated_integrity': stated is not None,
            'install_scripts': [s for s in INSTALL_SCRIPTS if s in scripts]}


# ── save time ────────────────────────────────────────────────────────────────

def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def is_installed(op: dict) -> bool:
    """True when the digest's directory exists, carries our verified marker for exactly this
    digest, and holds the entry file."""
    try:
        marker = json.loads((package_dir(op) / VERIFIED_MARKER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(marker, dict) and marker.get('integrity') == op['integrity'] and entry_path(op).is_file()


def install(op: dict) -> str:
    """Download `op`'s archive again, hold it to the approved digest and unpack it into its own
    digest directory (kept as it is when already there). Returns the entry file's path."""
    spec = f'{op["package"]}@{op["version"]}'
    if op['tarball'] != tarball_url(op['package'], op['version']) or not _INTEGRITY_RE.match(op['integrity']):
        raise _bad('the approved package details are not valid, so nothing was saved', 'package_invalid', 400)
    final = package_dir(op)
    with _lock_for(final.name):                    # a second Save of this digest waits for the first
        if is_installed(op):
            return str(entry_path(op))
        if final.exists():
            raise ActivationError('a folder for this package digest exists but is not the verified package. Clayrune '
                                  'does not overwrite it: remove it from Clayrune\'s own folder, then save again.',
                                  'package_store_conflict', 409)
        data = _download(op['tarball'], spec)
        if not hmac.compare_digest(integrity_of(data), op['integrity']):
            raise ActivationError(f'the file the registry served for {spec} does not match the checksum you approved, '
                                  f'so it was NOT saved. Nothing from it was run.', 'pin_mismatch', 409)
        _check_decompressed(data)
        tmp = final.parent / f'.{final.name}.{uuid.uuid4().hex[:8]}.tmp'
        try:
            tmp.mkdir(parents=True)
            _store._extract(data, tmp, MAX_UNPACKED, MAX_MEMBERS)
            if not (tmp / 'package' / PurePosixPath(op['entry'])).is_file():
                raise _bad('the downloaded package does not contain the file you approved to run, so it was NOT saved')
            (tmp / VERIFIED_MARKER).write_text(json.dumps({'integrity': op['integrity'], 'package': op['package'],
                                                           'version': op['version']}), encoding='utf-8')
            os.replace(tmp, final)
        except ActivationError:
            raise
        except OSError as e:
            _log(f'[desk_connect] custom npm package could not be placed: {type(e).__name__}', flush=True)
            raise ActivationError('the package could not be written to Clayrune\'s own folder; see the server log',
                                  'package_write_failed', 500) from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return str(entry_path(op))
