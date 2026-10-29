"""Verified cloudflared updates, serialized with the connector's launch.

PROPRIETARY AND CONFIDENTIAL. Copyright (c) 2026 Clayrune.
No work runs at import or on status reads. The supervisor calls ensure().
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)
Version = tuple[int, int, int]
MINIMUM_VERSION: Version = (2026, 10, 2)
# cloudflared versions encode a month, not a publication day. Month-start
# deliberately errs on the early side; 90 days allows several monthly releases.
STALE_AFTER_DAYS = 90
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
RELEASE_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
DOWNLOAD_BASE = "https://github.com/cloudflare/cloudflared/releases/download"


def parse_version(text: str) -> Version:
    match = re.fullmatch(
        r"(?:cloudflared version )?v?(\d{4})\.(\d{1,2})\.(\d+)(?: \(built [^\r\n]+\))?",
        text.strip(),
    )
    if match is None:
        raise ValueError("unrecognized cloudflared version")
    version = tuple(int(part) for part in match.groups())
    date(version[0], version[1], 1)  # Reject invalid months/years.
    return version[0], version[1], version[2]


def version_string(version: Version) -> str:
    return ".".join(str(part) for part in version)


def update_due(version: Version, today: date) -> bool:
    return version < MINIMUM_VERSION or (
        today - date(version[0], version[1], 1)
    ).days >= STALE_AFTER_DAYS


def read_version(binary: Path) -> Version:
    result = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True,
        timeout=5, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return parse_version(result.stdout)


def release_checksum(body: str, artifact: str) -> str:
    # Cloudflare's release body lists `filename: sha256` inside a code fence.
    # Match the whole filename and require exactly one checksum, failing closed.
    hashes = re.findall(
        rf"^\s*`?{re.escape(artifact)}:\s*([0-9a-fA-F]{{64}})`?\s*$",
        body, flags=re.MULTILINE,
    )
    if len(hashes) != 1:
        raise ValueError(f"missing or ambiguous SHA256 checksum for {artifact}")
    return hashes[0].lower()


@dataclass(frozen=True)
class Release:
    version: Version
    url: str
    checksum: str


def fetch_release(artifact: str) -> Release:
    request = urllib.request.Request(
        RELEASE_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "Clayrune"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    tag = payload["tag_name"]
    if not isinstance(tag, str) or not re.fullmatch(r"v?\d{4}\.\d{1,2}\.\d+", tag):
        raise ValueError("invalid cloudflared release tag")
    version = parse_version(tag)
    if payload.get("draft") or payload.get("prerelease") or version < MINIMUM_VERSION:
        raise ValueError("latest cloudflared release is not an eligible stable version")
    # Pin to this tag: /latest/download could change between metadata and bytes.
    url = f"{DOWNLOAD_BASE}/{tag}/{artifact}"
    if not any(asset.get("name") == artifact and asset.get("browser_download_url") == url
               for asset in payload.get("assets", [])):
        raise ValueError(f"release has no matching asset: {artifact}")
    return Release(version, url, release_checksum(payload.get("body") or "", artifact))


def download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())


def _temp_path(target: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=".cloudflared-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    return Path(name)


def _remove_temp(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("cloudflared temporary file cleanup failed: %s", exc)


def _copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = _temp_path(target)
    try:
        shutil.copy2(source, staged)
        # Windows _commit/fsync requires a writable descriptor.
        with staged.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(staged, target)
    finally:
        _remove_temp(staged)


class Process(Protocol):
    def poll(self) -> int | None: ...


class RollbackError(RuntimeError):
    """Replacement validation failed and the previous binary could not be restored."""


class CloudflaredUpdater:
    def __init__(self, rollback_root: Path | None = None) -> None:
        # start() holds this through Popen + track_running. ensure() reenters it.
        self.lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._rollback_root = rollback_root or Path.home() / ".clayrune" / "cloudflared-rollback"
        self._running: dict[Path, Process] = {}
        self._blocked: set[Path] = set()
        self._target: Path | None = None
        self._release: Release | None = None
        self._next_check = 0.0
        self._next_attempt = 0.0
        self._state: dict[str, object] = {
            "installed_version": None, "latest_version": None,
            "minimum_version": version_string(MINIMUM_VERSION),
            "last_update_error": None, "out_of_date": False,
            "update_pending": False, "in_progress": False,
        }

    def _publish(self, **values: object) -> None:
        with self._state_lock:
            self._state.update(values)

    def status(self) -> dict[str, object]:
        """In-memory snapshot; never waits for the network/lifecycle lock."""
        with self._state_lock:
            return dict(self._state)

    def track_running(self, target: Path, process: Process) -> None:
        with self.lifecycle_lock:
            self._running[target.resolve()] = process

    def _is_running(self, target: Path) -> bool:
        proc = self._running.get(target)
        return proc is not None and proc.poll() is None

    def rollback_path(self, target: Path) -> Path:
        key = hashlib.sha256(str(target.resolve()).encode()).hexdigest()
        return self._rollback_root / key / target.name

    def rollback(self, target: Path) -> None:
        """Restore the previous binary atomically; refuse while it is in use."""
        target = target.resolve()
        with self.lifecycle_lock:
            if self._is_running(target):
                raise RuntimeError("cloudflared rollback deferred: connector is running")
            backup = self.rollback_path(target)
            version = read_version(backup)
            _copy_atomic(backup, target)
            self._blocked.discard(target)
            self._publish(installed_version=version_string(version), update_pending=False,
                          out_of_date=update_due(version, date.today()) or bool(
                              self._release and version < self._release.version))

    def _install(self, target: Path, release: Release, tarball: bool) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        archive = _temp_path(target)
        candidate = _temp_path(target)
        existed = target.is_file()
        swapped = False
        try:
            download(release.url, archive)
            digest = hashlib.sha256()
            with archive.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != release.checksum:
                raise ValueError("SHA256 checksum mismatch; refusing cloudflared update")
            if tarball:
                # Never extract paths, links or other archive members to disk.
                with tarfile.open(archive, "r:gz") as tf:
                    members = [m for m in tf.getmembers()
                               if m.isfile() and Path(m.name).name == "cloudflared"]
                    if len(members) != 1:
                        raise ValueError("archive must contain exactly one cloudflared binary")
                    inner = tf.extractfile(members[0])
                    if inner is None:
                        raise ValueError("cloudflared archive member unreadable")
                    with inner, candidate.open("wb") as out:
                        shutil.copyfileobj(inner, out)
                        out.flush()
                        os.fsync(out.fileno())
            else:
                shutil.copyfile(archive, candidate)
                with candidate.open("r+b") as handle:
                    os.fsync(handle.fileno())
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if read_version(candidate) != release.version:
                raise ValueError("downloaded binary version differs from release tag")
            if existed:
                # Keep the live name intact until the final single atomic replace.
                _copy_atomic(target, self.rollback_path(target))
            os.replace(candidate, target)
            swapped = True
            if read_version(target) != release.version:
                raise ValueError("installed cloudflared failed version validation")
        except Exception as exc:
            if swapped:
                try:
                    if existed:
                        _copy_atomic(self.rollback_path(target), target)
                        log.warning("cloudflared update validation failed; previous binary restored")
                    else:
                        target.unlink(missing_ok=True)
                except OSError as restore_error:
                    raise RollbackError(
                        f"validation failed ({exc}); rollback failed ({restore_error}); "
                        f"previous binary: {self.rollback_path(target)}"
                    ) from exc
            raise
        finally:
            _remove_temp(archive)
            _remove_temp(candidate)

    def ensure(self, target: Path, artifact: tuple[str, bool] | None) -> str | None:
        """Refresh metadata daily; replace only missing/unsupported/stale versions.

        Failures preserve the existing binary; failed restoration instead
        refuses launch. A live connector defers replacement to its next restart.
        """
        target = target.resolve()
        with self.lifecycle_lock:
            if target in self._blocked:
                return None  # A failed restore requires explicit recovery.
            if self._target != target:
                self._target, self._release = target, None
                self._next_check = self._next_attempt = 0.0
                self._publish(installed_version=None, latest_version=None, last_update_error=None,
                              out_of_date=False, update_pending=False)
            try:
                installed = read_version(target) if target.is_file() else None
                due = installed is None or update_due(installed, date.today())
                self._publish(installed_version=version_string(installed) if installed else None,
                              out_of_date=due or bool(self._release and installed is not None
                                                     and installed < self._release.version))
                if artifact is None:
                    raise ValueError("no published cloudflared release for this platform")
                artifact_url, tarball = artifact
                now = time.monotonic()
                if now >= self._next_check:
                    self._next_check = now + CHECK_INTERVAL_SECONDS
                    self._release = fetch_release(artifact_url.rsplit("/", 1)[-1])
                    self._publish(latest_version=version_string(self._release.version),
                                  last_update_error=None)
                release = self._release
                newer = release is not None and (installed is None or installed < release.version)
                self._publish(out_of_date=due or newer, update_pending=due and newer)
                if not due or not newer or release is None or self._is_running(target):
                    return str(target) if target.is_file() else None
                if now < self._next_attempt:
                    return str(target) if target.is_file() else None
                self._next_attempt = now + CHECK_INTERVAL_SECONDS
                self._publish(in_progress=True)
                self._install(target, release, tarball)
                self._publish(installed_version=version_string(release.version),
                              out_of_date=update_due(release.version, date.today()),
                              update_pending=False, last_update_error=None)
                log.info("cloudflared updated to %s", version_string(release.version))
            except Exception as exc:
                log.warning("cloudflared update failed: %s", exc)
                self._publish(last_update_error=str(exc))
                if isinstance(exc, RollbackError):
                    self._blocked.add(target)
                    self._publish(installed_version=None)
                    return None
            finally:
                self._publish(in_progress=False)
            return str(target) if target.is_file() else None


updater = CloudflaredUpdater()
