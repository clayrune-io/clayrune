"""Download-verify-install and system-copy adoption (MC-1022, spec §2, §3, §6).

Install shape, in order (spec §6 "Partial install"): free-space preflight,
download to `staging/<job>/*.part` while hashing, SHA-256 check against the
catalogue pin, extract ONLY the catalogued binaries (plus declared extra files)
into staging, set the exec bit, run the version probe, record each binary's
SHA-256, atomic rename into `<id>/<version>/`, and only then the caller writes
the manifest. Any failure deletes staging; nothing is retried or re-sourced.

A hash mismatch is a hard stop: the download is deleted, both hashes are
logged, and no other mirror or build is tried, because that would be a
substitution the card did not show.

Extraction never uses the archive's own paths: each wanted member is looked up
by its exact catalogued name, must be a regular file, and is copied out by
stream to a path this module builds. A hostile archive's `..`, absolute paths
and links therefore have nothing to act on (stricter than tarfile's
`filter='data'`, which the spec names; it also works on Python 3.10).
"""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Callable, IO

from mc.addons import manifest as mf
from mc.addons.manifest import AddonError
from mc.core import _log

ProgressCb = Callable[[str, int, int], None]
_CHUNK = 1 << 20
_PROBE_TIMEOUT = 30
_NET_TIMEOUT = 30


class ChecksumMismatch(AddonError):
    pass


class PinnedUrlGone(AddonError):
    """HTTP 404/410 on a pinned URL: the catalogue entry needs re-pinning."""


def _noop(_phase: str, _done: int, _total: int) -> None:
    return None


def _open_url(url: str) -> IO[bytes]:
    """The one place a download starts; tests replace it."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Clayrune-addons/1'})
    return urllib.request.urlopen(req, timeout=_NET_TIMEOUT)


def _free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def preflight(src: dict) -> None:
    need = 2 * (src['download_bytes'] + src['installed_bytes'])
    free = _free_bytes(mf.addons_root())
    if free < need:
        raise AddonError(f'not enough free disk space: needs {need // (1 << 20)} MB, '
                         f'{free // (1 << 20)} MB free')


def _download(url: str, dest: Path, want_sha: str, total: int, progress: ProgressCb) -> None:
    h = hashlib.sha256()
    done = 0
    try:
        resp = _open_url(url)
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise PinnedUrlGone(f'download failed: HTTP {e.code} (the pinned build is gone; '
                                f'the catalogue entry needs re-pinning)')
        raise AddonError(f'download failed: HTTP {e.code}')
    except (urllib.error.URLError, OSError) as e:
        raise AddonError(f'download failed: {getattr(e, "reason", e)}')
    try:
        with resp, open(dest, 'wb') as f:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress('downloading', done, total)
    except OSError as e:
        if getattr(e, 'errno', None) == errno.ENOSPC:
            raise AddonError('the disk filled up during the download')
        raise AddonError(f'download failed: {e}')
    got = h.hexdigest()
    if got != want_sha:
        _log(f'[addons] checksum mismatch for {url}: expected {want_sha}, got {got}', flush=True)
        raise ChecksumMismatch('checksum mismatch: the download does not match the catalogue pin, '
                               'so it was deleted and nothing was installed')


def _copy_stream(src: IO[bytes], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as out:
        shutil.copyfileobj(src, out, _CHUNK)


def _extract(archive: Path, kind: str, wanted: dict[str, Path], progress: ProgressCb) -> None:
    """Copy each `member name -> destination` out of the archive. A wanted
    member that is absent or not a regular file fails the whole install."""
    pending = dict(wanted)
    try:
        if kind == 'zip':
            with zipfile.ZipFile(archive) as z:
                names = {i.filename: i for i in z.infolist()}
                for name, dest in wanted.items():
                    info = names.get(name)
                    if info is None or info.is_dir() or stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                        raise AddonError(f'the archive has no regular file {name}')
                    with z.open(info) as s:
                        _copy_stream(s, dest)
                    pending.pop(name)
                    progress('extracting', len(wanted) - len(pending), len(wanted))
        else:
            with tarfile.open(archive, 'r:xz') as t:
                for m in t:
                    dest = pending.get(m.name)
                    if dest is None:
                        continue
                    if not m.isreg():
                        raise AddonError(f'{m.name} in the archive is not a regular file')
                    s = t.extractfile(m)
                    if s is None:
                        raise AddonError(f'{m.name} in the archive cannot be read')
                    with s:
                        _copy_stream(s, dest)
                    pending.pop(m.name)
                    progress('extracting', len(wanted) - len(pending), len(wanted))
                    if not pending:
                        break
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as e:
        raise AddonError(f'the archive could not be read: {e}')
    except OSError as e:
        if getattr(e, 'errno', None) == errno.ENOSPC:
            raise AddonError('the disk filled up while unpacking')
        raise AddonError(f'unpacking failed: {e}')
    if pending:
        raise AddonError('the archive is missing ' + ', '.join(sorted(pending)))


def probe_version(binary: Path, probe: dict) -> str:
    """Run the catalogued version probe; raise `AddonError` if it does not run
    or does not print what the catalogue expects. Returns the first output line."""
    try:
        r = subprocess.run([str(binary)] + [str(a) for a in probe['args']], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=_PROBE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise AddonError(f'{binary.name} did not answer its version check in {_PROBE_TIMEOUT}s')
    except OSError as e:
        raise AddonError(f'{binary.name} could not be started ({e}); on Windows this is usually antivirus '
                         f'quarantining the file')
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode != 0 or probe['expect'] not in out:
        raise AddonError(f'{binary.name} ran but its version check failed (exit {r.returncode})')
    return out.strip().splitlines()[0][:200] if out.strip() else ''


def _binary_info(path: Path) -> dict:
    return {'path': str(path), 'sha256': mf.sha256_file(path), 'size': path.stat().st_size}


def install_from_catalogue(entry: dict, src: dict, *, request_id: str, requested_by: dict | None,
                           approved_at: str, progress: ProgressCb = _noop) -> dict:
    """Install the catalogued static build for this host and write the manifest
    entry. Raises `AddonError` (or a subclass); staging is always cleaned."""
    aid, version = entry['id'], entry['version']
    stage = mf.staging_dir() / f'{request_id or uuid.uuid4().hex}'
    try:
        progress('checking', 0, 1)
        preflight(src)
        stage.mkdir(parents=True, exist_ok=True)
        part = stage / 'download.part'
        _download(src['url'], part, src['sha256'], src['download_bytes'], progress)
        out = stage / 'out'
        wanted: dict[str, Path] = {}
        for bname, rel in src['binary_paths'].items():
            wanted[src['archive_root'] + rel] = out / Path(rel).name
        for rel in src.get('extra_files') or []:
            wanted[src['archive_root'] + rel] = out / Path(rel).name
        _extract(part, src['archive'], wanted, progress)
        part.unlink()
        progress('verifying', 0, 1)
        infos: dict[str, dict] = {}
        for bname, rel in src['binary_paths'].items():
            p = out / Path(rel).name
            if os.name != 'nt':
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            infos[bname] = _binary_info(p)
        probe = entry['probe']
        probe_version(out / Path(src['binary_paths'][probe['binary']]).name, probe)
        dest = mf.install_dir(aid, version)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(out, dest)
        for bname, info in infos.items():
            info['path'] = str(dest / Path(src['binary_paths'][bname]).name)
        size_on_disk = sum(f.stat().st_size for f in dest.iterdir() if f.is_file())
        progress('done', 1, 1)
        return {
            'id': aid, 'name': entry['name'], 'version': version, 'source': 'catalogue',
            'binaries': infos, 'licence': entry['licence'], 'size_on_disk': size_on_disk,
            'installed_at': mf.now_iso(), 'approved_at': approved_at, 'requested_by': requested_by,
            'request_id': request_id, 'status': 'ok', 'status_detail': '',
            'last_verified': entry['last_verified'], 'source_url': src['url'],
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ── adoption of a copy already on this machine ──────────────────────────────

def inspect_system(entry: dict) -> dict:
    """What the adoption card shows: absolute path, version and SHA-256 of each
    system binary found on PATH. Raises `AddonError` when the required (probe)
    binary is not there or does not run."""
    probe = entry['probe']
    found: dict[str, dict] = {}
    for name in entry.get('system_binaries') or []:
        located = shutil.which(name)
        if not located:
            continue
        p = Path(located).resolve()
        if p.is_file():
            found[name] = _binary_info(p)
    need = probe['binary']
    if need not in found:
        raise AddonError(f'no {need} was found on this computer\'s PATH')
    version_line = probe_version(Path(found[need]['path']), probe)
    return {'binaries': found, 'version': version_line}


def adopt_system(entry: dict, expected: dict, *, request_id: str, requested_by: dict | None,
                 approved_at: str) -> dict:
    """Pin the system copy the user saw on the card. Re-hashes first: if a
    binary changed between the card and the approval, nothing is pinned."""
    now = inspect_system(entry)
    for name, info in expected['binaries'].items():
        cur = now['binaries'].get(name)
        if cur is None or cur['sha256'] != info['sha256'] or cur['path'] != info['path']:
            raise AddonError(f'{name} changed after the card was shown; a new card is needed')
    binaries = {n: now['binaries'][n] for n in expected['binaries']}
    return {
        'id': entry['id'], 'name': entry['name'], 'version': now['version'], 'source': 'system',
        'binaries': binaries, 'licence': entry['licence'],
        'size_on_disk': sum(b['size'] for b in binaries.values()),
        'installed_at': mf.now_iso(), 'approved_at': approved_at, 'requested_by': requested_by,
        'request_id': request_id, 'status': 'ok', 'status_detail': '',
        'last_verified': mf.now_iso()[:10], 'source_url': '',
        'note': 'installed outside Clayrune, licence not checked',
    }
