"""The verified tarball of a curated MCP package (docs/DESK_CONNECT_BY_URL_SPEC.md,
slice 4; Wren's audit and Dave's pick C, 2026-10-05).

Clayrune fetches ONE file itself and runs nothing it did not check:

    1. download   the `tarball` address of the reviewed catalogue entry, over https, with
                  Python's own HTTP client. No npm, no npx, no `.npmrc`, no configured
                  registry and no dependency resolution are anywhere in the path: the
                  address is a fixed string in the catalogue (the validator pins it to
                  registry.npmjs.org and to this package and version).
    2. checksum   sha512 of the downloaded bytes must equal the catalogue's `integrity`
                  (npm's own `dist.integrity`). Nothing is written before it matches.
    3. extract    member by member into a Clayrune-owned directory outside the repo and
                  outside DATA_DIR (`~/.clayrune/mcp_packages/<id>/<version>/`). Anything
                  that is not a plain file or directory under `package/` (a link, a device,
                  an absolute or `..` path, a drive letter) refuses the WHOLE package.
    4. the entry  file the launch line runs (`entry` in the catalogue) must exist.

The catalogue marks the package `bundled: true`: it was reviewed as one self-contained file
that imports only Node's own modules, so nothing outside this directory is needed to run it.
If a future version imports something it does not carry, it crashes at start rather than
fetching anything, which is the safe direction. (Node resolves a bare import by walking up
the parent directories for a `node_modules`; a package that did import one would find only
what the account owner put there. The reviewed bundle imports none.)

The download has ONE deadline for the whole transfer (`DOWNLOAD_TIMEOUT`), not one per read: a
server that sends a byte a minute never reaches a per-read timeout. Two Saves of the same package
at once are serialised where the directory is replaced (a per-package lock): provisioning runs
outside the commit lock, and an unserialised pair raced `rmtree` against `os.replace`.

Every refusal is an `ActivationError` with a plain message; none carries package text or a
network error's body. The directory is replaced as a whole on each run, so what a launch
line points at is always what was just verified. The files are as trustworthy as the
account's own home directory, the same as the MCP configuration that names them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import shutil
import tarfile
import threading
import time
import urllib.request
import uuid
from pathlib import PurePosixPath

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect.mcp_errors import ActivationError

DOWNLOAD_TIMEOUT = 60                    # seconds, for the whole download
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024    # a guard against an endless response; the real file is a few MB
_CHUNK = 1 << 16

_locks_guard = threading.Lock()
_locks: dict[tuple, threading.Lock] = {}


def _package_lock(entry: dict) -> threading.Lock:
    """One lock per (package id, version): the directory that is replaced as a whole."""
    key = (entry['id'], entry['version'])
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def packages_root():
    """`~/.clayrune/mcp_packages`: operator state, never the repo and never `data/`."""
    return _vault.clayrune_home() / 'mcp_packages'


def package_dir(entry: dict):
    """`<root>/<id>/<version>`: one directory per reviewed version, so a bump never
    overwrites the version a running server may be using."""
    return packages_root() / entry['id'] / entry['version']


def entry_path(entry: dict):
    """The file the launch line runs: `<dir>/package/<entry>` (npm tarballs wrap
    everything in `package/`)."""
    return package_dir(entry) / 'package' / PurePosixPath(entry['entry'])


def _fetch(url: str, limit: int, timeout: float) -> bytes:
    """The one network call, finished within `timeout` seconds in total (TimeoutError
    otherwise). Indirection so tests supply bytes (or a failure) and the real registry is
    never touched by the suite."""
    if not url.startswith('https://'):
        raise ValueError('not an https address')
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, headers={'User-Agent': 'Clayrune', 'Accept': 'application/octet-stream'})
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('the download did not finish in time')
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            if len(buf) > limit:
                raise ValueError('the response is larger than any reviewed package')
    return bytes(buf)


def integrity_of(data: bytes) -> str:
    return 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode('ascii')


def _fetch_within(url: str, limit: int, timeout: float) -> bytes:
    """`_fetch`, abandoned after `timeout` seconds even if one read is blocked on the socket
    (the deadline inside `_fetch` is only checked between reads). The worker is a daemon and
    stops at its own next deadline check; its result is discarded."""
    out: dict = {}

    def run():
        try:
            out['data'] = _fetch(url, limit, timeout)
        except BaseException as e:                              # re-raised in the caller
            out['err'] = e

    t = threading.Thread(target=run, name='mcp-package-download', daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError('the download did not finish in time')
    if 'err' in out:
        raise out['err']
    return out['data']


def download(entry: dict) -> bytes:
    """The tarball's bytes, checked against the reviewed checksum. Raises ActivationError."""
    spec = f'{entry["package"]}@{entry["version"]}'
    try:
        data = _fetch_within(entry['tarball'], MAX_DOWNLOAD_BYTES, DOWNLOAD_TIMEOUT)
    except TimeoutError as e:                                   # socket.timeout is TimeoutError
        raise ActivationError(f'the package registry did not answer within {DOWNLOAD_TIMEOUT} seconds, so '
                              f'{spec} could not be downloaded. Save again later.', 'download_timeout', 504) from e
    except (OSError, ValueError) as e:                          # URLError and HTTPError are OSErrors
        _log(f'[desk_connect] MCP package download failed: {type(e).__name__}', flush=True)
        raise ActivationError(f'{spec} could not be downloaded from the package registry. Save again later.',
                              'download_failed', 502) from e
    if not hmac.compare_digest(integrity_of(data), entry['integrity']):
        raise ActivationError(f'the file the registry served for {spec} does not match the checksum that was '
                              f'reviewed, so it was NOT registered. Nothing from it was run.', 'pin_mismatch', 409)
    return data


def safe_member_parts(name: str) -> tuple[str, ...] | None:
    """The path parts of archive member `name` when it is safe (relative, under `package/`,
    no `..`, backslash, drive letter or NUL), else None. Pure: no file system is touched, so
    one rule judges a member read in memory and a member being extracted."""
    if not name or '\\' in name or ':' in name or '\x00' in name:
        return None
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != 'package':
        return None
    return p.parts


def _safe_target(root, name: str):
    """Where member `name` goes under `root`, or None when the name is not safe."""
    parts = safe_member_parts(name)
    if parts is None:
        return None
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _extract(data: bytes, dest, max_bytes: int, max_members: int | None = None) -> None:
    """Unpack the verified tarball into the empty directory `dest`. Raises ActivationError.
    `max_members` (default none: the reviewed catalogue's behaviour) refuses an archive with
    more members than that, files and directories together."""
    bad = ActivationError('the downloaded package has an unexpected layout, so it was NOT registered',
                          'package_invalid', 502)
    total = 0
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
            for m in tf:
                target = _safe_target(dest, m.name)
                if target is None or m.name in seen or not (m.isfile() or m.isdir()):
                    raise bad
                seen.add(m.name)
                if max_members is not None and len(seen) > max_members:
                    raise bad
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += m.size
                if total > max_bytes:
                    raise bad
                src = tf.extractfile(m)
                if src is None:
                    raise bad
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, 'xb') as out:                   # 'x': never overwrites, so a duplicate cannot replace a file
                    shutil.copyfileobj(src, out)
    except ActivationError:
        raise
    except (tarfile.TarError, EOFError, OSError, ValueError) as e:
        _log(f'[desk_connect] MCP package unpack failed: {type(e).__name__}', flush=True)
        raise bad from e


def install(entry: dict) -> str:
    """Download, verify and unpack `entry` into its directory, replacing what was there.
    Returns the path of the file the launch line runs. Raises ActivationError."""
    data = download(entry)
    final = package_dir(entry)
    tmp = final.parent / f'.{entry["version"]}.{uuid.uuid4().hex[:8]}.tmp'
    with _package_lock(entry):                      # a second Save of this package waits for the first
        try:
            tmp.mkdir(parents=True)
            _extract(data, tmp, max(entry['unpacked_bytes'], len(data)) * 2)
            if not (tmp / 'package' / PurePosixPath(entry['entry'])).is_file():
                raise ActivationError('the downloaded package does not contain the file Clayrune would run, so it was '
                                      'NOT registered', 'package_invalid', 502)
            if final.exists():
                shutil.rmtree(final)
            os.replace(tmp, final)
        except ActivationError:
            raise
        except OSError as e:
            _log(f'[desk_connect] MCP package could not be placed: {type(e).__name__}', flush=True)
            raise ActivationError('the downloaded package could not be written to Clayrune\'s own folder; see the '
                                  'server log', 'package_write_failed', 500) from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                final.parent.rmdir()                # the per-package folder this call created, if nothing was installed in it
            except OSError:
                pass
    return str(entry_path(entry))
