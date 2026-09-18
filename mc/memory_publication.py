"""Crash-recoverable publication kernel for the memory writer family.

This module deliberately has no knowledge of ``mc.memory`` or the canonical
conversation database.  A trusted composition layer supplies the target paths,
the logical operation identity, and the already-computed after-images.  The
kernel owns only the durable publication protocol:

* stage before/after images under an operator-state directory;
* persist one intent per vault and a permanent receipt per operation;
* publish through an atomic target writer in deterministic order; and
* require an explicit ``forward`` or ``abort`` decision when recovering a
  pending intent.

The state directory must be outside the memory corpus.  ``os.replace`` and
file fsyncs are used where the platform provides them; directory fsync is
best-effort on Windows, so this module does not claim power-loss guarantees
from the primitive alone.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from typing import Any, Callable, Mapping, Optional
import uuid


FORMAT_VERSION = 1
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_INTENT_NAME = ".memory-publication-intent.json"
_LOCK_NAME = ".memory-publication.lock"
_RECEIPTS_NAME = "receipts"
_STAGING_NAME = "staging"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class MemoryPublicationError(RuntimeError):
    """Base class for publication protocol failures."""


class PublicationConflict(MemoryPublicationError):
    """The requested operation or a target no longer matches its images."""


class RecoveryRequired(MemoryPublicationError):
    """A pending canonical intent requires an explicit recovery decision."""


class LockTimeout(MemoryPublicationError):
    """The finite vault-lock timeout elapsed."""


@dataclass(frozen=True)
class MemoryTarget:
    """Trusted target description used by composition code.

    ``archive_batch_id`` is metadata only.  The target path still comes from
    the caller's allowlist and never from a persisted manifest.
    """

    path: Path
    archive_batch_id: Optional[str] = None


Publisher = Callable[[Path, Optional[bytes]], None]
FaultHook = Callable[[str], None]


def _sha256(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> Any:
    """Return a JSON-compatible copy, rejecting non-deterministic values."""

    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical_source must be JSON-serializable") from exc


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    """Flush a directory where supported; Windows does not expose this."""

    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            # Directory fsync is unavailable on Windows and some filesystems.
            pass
    finally:
        os.close(fd)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace one JSON state file in the same directory."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.",
                                     suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=False,
                      indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryPublicationError(f"cannot read publication state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MemoryPublicationError(f"publication state {path} is not an object")
    return value


def _read_target(path: Path) -> Optional[bytes]:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MemoryPublicationError(f"cannot read target {path}: {exc}") from exc


def _pid_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return True
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _VaultLock:
    """Cooperative O_EXCL lease used by publication-aware writers.

    This is not claimed as an OS-backed exclusive file lock.  Cross-process
    takeover and PID-reuse behavior remains an activation gate.
    """

    def __init__(self, path: Path, timeout: float) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self._held = False

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                              "token": uuid.uuid4().hex})
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, payload.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._held = True
                _fsync_dir(self.path.parent)
                return
            except FileExistsError:
                # A process that crashed can leave only the lease marker.  It
                # is safe to reclaim it when its recorded PID is definitely
                # gone; a live owner is never broken by a timeout.
                try:
                    owner = _read_json(self.path)
                except (FileNotFoundError, MemoryPublicationError):
                    owner = {}
                if owner and not _pid_alive(owner.get("pid")):
                    try:
                        self.path.unlink()
                        _fsync_dir(self.path.parent)
                        continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"timed out acquiring vault lock {self.path}")
                time.sleep(0.01)

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self.path.unlink()
            _fsync_dir(self.path.parent)
        except FileNotFoundError:
            pass


class MemoryTransaction:
    """One crash-recoverable publication transaction for a resolved vault.

    ``targets`` is the trusted logical-name allowlist.  A persisted intent is
    never allowed to introduce a new path: recovery verifies each persisted
    binding against this allowlist before reading or writing any target.
    """

    def __init__(
        self,
        root: Path,
        *,
        targets: Mapping[str, Path | MemoryTarget],
        timeout: float = 5.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        publisher: Optional[Publisher] = None,
        fault_hook: Optional[FaultHook] = None,
    ) -> None:
        if timeout <= 0 or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive finite number")
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        if not targets:
            raise ValueError("at least one publication target is required")
        self.root = Path(root).resolve()
        self.targets = self._normalize_targets(targets)
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self.publisher = publisher or self._default_publisher
        self.fault_hook = fault_hook
        self._lock = _VaultLock(self.root / _LOCK_NAME, self.timeout)
        self._entered = False
        self._intent: Optional[dict[str, Any]] = None

    @staticmethod
    def _normalize_targets(
        targets: Mapping[str, Path | MemoryTarget],
    ) -> dict[str, MemoryTarget]:
        result: dict[str, MemoryTarget] = {}
        for name, value in targets.items():
            if not isinstance(name, str) or not name or name in (".", ".."):
                raise ValueError(f"invalid logical target name: {name!r}")
            if "/" in name or "\\" in name or "\x00" in name:
                raise ValueError(f"logical target name must not be a path: {name!r}")
            target = value if isinstance(value, MemoryTarget) else MemoryTarget(Path(value))
            result[name] = MemoryTarget(Path(target.path).resolve(), target.archive_batch_id)
        return result

    def __enter__(self) -> "MemoryTransaction":
        if self._entered:
            raise RuntimeError("transaction context cannot be entered twice")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / _RECEIPTS_NAME).mkdir(parents=True, exist_ok=True)
        (self.root / _STAGING_NAME).mkdir(parents=True, exist_ok=True)
        self._lock.acquire()
        self._entered = True
        try:
            self._intent = self._load_intent()
        except Exception:
            self._lock.release()
            self._entered = False
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self._lock.release()
        self._entered = False
        return False

    def _require_entered(self) -> None:
        if not self._entered:
            raise RuntimeError("MemoryTransaction must be used as a context manager")

    def _hook(self, event: str) -> None:
        if self.fault_hook:
            self.fault_hook(event)

    @property
    def _intent_path(self) -> Path:
        return self.root / _INTENT_NAME

    def _load_intent(self) -> Optional[dict[str, Any]]:
        try:
            intent = _read_json(self._intent_path)
        except FileNotFoundError:
            return None
        if intent.get("format_version") != FORMAT_VERSION:
            raise MemoryPublicationError("unsupported publication intent version")
        if intent.get("vault_root") != str(self.root):
            raise MemoryPublicationError("publication intent belongs to another vault")
        self._validate_persisted_bindings(intent)
        return intent

    def _validate_persisted_bindings(self, intent: Mapping[str, Any]) -> None:
        records = intent.get("targets")
        if not isinstance(records, dict):
            raise MemoryPublicationError("publication intent has no target manifest")
        if set(records) != set(self.targets):
            raise PublicationConflict("publication intent target set changed")
        for name, record in records.items():
            if name not in self.targets or not isinstance(record, dict):
                raise PublicationConflict(f"intent target is not allowlisted: {name!r}")
            persisted = record.get("path")
            if persisted != str(self.targets[name].path):
                raise PublicationConflict(f"target binding changed for {name!r}")

    def _ensure_identity(self, operation_id: str, request_digest: str) -> None:
        if not isinstance(operation_id, str) or not _SAFE_ID.fullmatch(operation_id):
            raise ValueError("operation_id must be a safe non-empty identifier")
        if not isinstance(request_digest, str) or not request_digest:
            raise ValueError("request_digest must be a non-empty string")

    def _receipt_path(self, operation_id: str) -> Path:
        self._ensure_identity(operation_id, "digest")
        return self.root / _RECEIPTS_NAME / f"{operation_id}.json"

    def receipt(self, operation_id: str, request_digest: str) -> Optional[dict[str, Any]]:
        """Return a permanent receipt, rejecting same-ID/different-request reuse."""

        self._require_entered()
        self._ensure_identity(operation_id, request_digest)
        try:
            receipt = _read_json(self._receipt_path(operation_id))
        except FileNotFoundError:
            receipt = None
        if receipt is not None and receipt.get("request_digest") != request_digest:
            raise PublicationConflict("operation ID already has a different request digest")
        if self._intent and self._intent.get("operation_id") == operation_id:
            if self._intent.get("request_digest") != request_digest:
                raise PublicationConflict("pending operation ID has a different request digest")
        return receipt

    def pending(self) -> Optional[dict[str, Any]]:
        """Return pending durable intent metadata; never replays it automatically."""

        self._require_entered()
        if self._intent is None:
            return None
        return json.loads(json.dumps(self._intent))

    def _write_intent(self, intent: dict[str, Any], event: Optional[str] = None) -> None:
        if event:
            self._hook(event)
        _atomic_json(self._intent_path, intent)
        self._intent = intent

    def _stage_path(self, operation_id: str, index: int, kind: str) -> Path:
        return self.root / _STAGING_NAME / operation_id / f"{index:04d}.{kind}.bin"

    def _stage_bytes(self, path: Path, data: bytes, event: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._hook(event)
        with path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(path.parent)
        self._hook(f"after_{event}")

    def prepare(
        self,
        operation_id: str,
        *,
        request_digest: str,
        after_images: Mapping[str, Optional[bytes]],
        canonical_source: Optional[Mapping[str, Any]] = None,
        operation_kind: str = "memory_publication",
        archive_batch_ids: Optional[Mapping[str, str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Stage a logical publication and persist its durable ``prepared`` intent."""

        self._require_entered()
        self._ensure_identity(operation_id, request_digest)
        if not isinstance(operation_kind, str) or not operation_kind:
            raise ValueError("operation_kind must be a non-empty string")
        if set(after_images) != set(self.targets):
            raise ValueError("after_images must contain exactly the allowlisted targets")
        source = _canonical_json(canonical_source) if canonical_source is not None else None
        archive_ids = dict(archive_batch_ids or {})
        if set(archive_ids) - set(self.targets):
            raise ValueError("archive_batch_ids contains an unknown target")

        receipt = self.receipt(operation_id, request_digest)
        if receipt is not None:
            if (self._intent is not None and
                    self._intent.get("operation_id") == operation_id and
                    self._intent.get("phase") in {"applied", "aborted"}):
                self._discard_terminal_intent(operation_id)
            return receipt
        if self._intent is not None:
            if self._intent.get("phase") in {"applied", "aborted"}:
                # A crash can occur after receipt fsync but before cleanup. A
                # new logical operation may retire that stale intent; the
                # permanent receipt is already durable.
                old_operation = self._intent.get("operation_id")
                old_digest = self._intent.get("request_digest")
                old_receipt = None
                if isinstance(old_operation, str) and isinstance(old_digest, str):
                    try:
                        old_receipt = self.receipt(old_operation, old_digest)
                    except (TypeError, ValueError, PublicationConflict):
                        old_receipt = None
                    if old_receipt is not None:
                        self._discard_terminal_intent(old_operation)
            if (self._intent is not None and
                    (self._intent.get("operation_id") != operation_id or
                     self._intent.get("request_digest") != request_digest)):
                raise RecoveryRequired("another publication intent is pending")
            if self._intent is None:
                pass
            elif self._intent.get("phase") in {"applied", "aborted"}:
                raise RecoveryRequired("operation has a terminal intent but no usable receipt")
            else:
                # Re-entering prepare is idempotent only if the requested
                # images are the same.  Equal bytes under a different operation remain
                # distinct because each operation owns its own receipt.
                requested_hashes = {
                    name: _sha256(after_images[name]) for name in self.targets
                }
                existing_hashes = {
                    name: record.get("after_sha256")
                    for name, record in self._intent.get("targets", {}).items()
                }
                if requested_hashes != existing_hashes:
                    raise PublicationConflict("same operation has different after-images")
                return self.pending()

        total = 0
        before_images: dict[str, Optional[bytes]] = {}
        for name in sorted(self.targets):
            before = _read_target(self.targets[name].path)
            after = after_images[name]
            if after is not None and not isinstance(after, bytes):
                raise TypeError(f"after image for {name!r} must be bytes or None")
            before_images[name] = before
            total += len(before or b"") + len(after or b"")
        if total > self.max_bytes:
            raise MemoryPublicationError(
                f"staged publication is {total} bytes, exceeding max_bytes={self.max_bytes}")

        staging_dir = self.root / _STAGING_NAME / operation_id
        try:
            records: dict[str, Any] = {}
            for index, name in enumerate(sorted(self.targets)):
                target = self.targets[name]
                before = before_images[name]
                after = after_images[name]
                before_path = None
                after_path = None
                if before is not None:
                    before_path = self._stage_path(operation_id, index, "before")
                    self._stage_bytes(before_path, before, f"stage_before:{name}")
                if after is not None:
                    after_path = self._stage_path(operation_id, index, "after")
                    self._stage_bytes(after_path, after, f"stage_after:{name}")
                records[name] = {
                    "path": str(target.path),
                    "original_exists": before is not None,
                    "before_sha256": _sha256(before),
                    "after_sha256": _sha256(after),
                    "before_image": str(before_path) if before_path else None,
                    "after_image": str(after_path) if after_path else None,
                    "archive_batch_id": archive_ids.get(name) or target.archive_batch_id,
                }
            intent = {
                "format_version": FORMAT_VERSION,
                "vault_root": str(self.root),
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "request_digest": request_digest,
                "phase": "prepared",
                "canonical_source": source,
                "targets": records,
                "applied_targets": [],
                "created_at_ns": time.time_ns(),
            }
            self._write_intent(intent, "before_intent_persist")
            self._hook("after_intent_persist")
            return self.pending()
        except Exception:
            # An intent is the durable recovery boundary.  If it was not
            # persisted, staged bytes are only temporary and may be removed.
            if not self._intent_path.exists():
                self._remove_tree(staging_dir)
            raise

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()

    @staticmethod
    def _default_publisher(path: Path, data: Optional[bytes]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            _fsync_dir(path.parent)
            return
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.",
                                         suffix=".publish", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp), str(path))
            _fsync_dir(path.parent)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _target_order(name: str) -> tuple[int, str]:
        lower = name.lower()
        if "archive" in lower:
            rank = 0
        elif "session_log" in lower or lower == "session-log.md":
            rank = 1
        elif lower == "memory.md" or lower.endswith("/memory.md"):
            rank = 2
        else:
            rank = 3
        return rank, name

    def _staged(self, record: Mapping[str, Any], key: str) -> Optional[bytes]:
        staged = record.get(key)
        expected_key = "after_sha256" if key == "after_image" else "before_sha256"
        if staged is None:
            if record.get(expected_key) is not None:
                raise PublicationConflict(f"staged {key} is missing")
            return None
        path = Path(staged).resolve()
        if self.root not in path.parents:
            raise PublicationConflict("staged image escapes the vault state directory")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise MemoryPublicationError(f"cannot read staged image {path}: {exc}") from exc
        if _sha256(data) != record.get(expected_key):
            raise PublicationConflict(f"staged {key} does not match its durable hash")
        return data

    def _matches(self, path: Path, expected: Optional[str]) -> bool:
        return _sha256(_read_target(path)) == expected

    def _assert_pending_identity(self, operation_id: str, request_digest: str) -> dict[str, Any]:
        self._require_entered()
        self._ensure_identity(operation_id, request_digest)
        intent = self._intent
        if intent is None:
            raise RecoveryRequired("no publication intent is pending")
        if intent.get("operation_id") != operation_id or intent.get("request_digest") != request_digest:
            raise PublicationConflict("recovery identity does not match pending intent")
        self._validate_persisted_bindings(intent)
        return intent

    def _receipt(self, intent: Mapping[str, Any], outcome: str,
                 retained_archive_batch_ids: Optional[list[str]] = None) -> dict[str, Any]:
        records = intent.get("targets", {})
        return {
            "format_version": FORMAT_VERSION,
            "operation_id": intent["operation_id"],
            "request_digest": intent["request_digest"],
            "operation_kind": intent.get("operation_kind"),
            "outcome": outcome,
            "canonical_source": intent.get("canonical_source"),
            "target_hashes": {
                name: record.get("after_sha256")
                for name, record in records.items()
            },
            "archive_batch_ids": [
                record["archive_batch_id"]
                for record in records.values()
                if record.get("archive_batch_id")
            ],
            "retained_archive_batch_ids": retained_archive_batch_ids or [],
            "completed_at_ns": time.time_ns(),
        }

    def _finish_receipt(self, intent: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        self._hook("before_receipt_persist")
        _atomic_json(self._receipt_path(intent["operation_id"]), receipt)
        self._hook("after_receipt_persist")
        staging_dir = self.root / _STAGING_NAME / intent["operation_id"]
        try:
            self._remove_tree(staging_dir)
            _fsync_dir(self.root / _STAGING_NAME)
        except OSError:
            # Receipt is the permanent recovery record; an orphaned staging
            # directory is safe and can be garbage-collected later.
            pass
        try:
            self._intent_path.unlink()
            _fsync_dir(self.root)
        except FileNotFoundError:
            pass
        self._intent = None
        return receipt

    def _discard_terminal_intent(self, operation_id: str) -> None:
        """Remove an intent whose permanent receipt already exists.

        This is housekeeping only: it never publishes or replays targets.  It
        closes the crash window between receipt fsync and the best-effort
        intent/staging cleanup so a later operation is not blocked forever.
        """

        staging_dir = self.root / _STAGING_NAME / operation_id
        try:
            self._remove_tree(staging_dir)
        except OSError:
            pass
        try:
            self._intent_path.unlink()
            _fsync_dir(self.root)
        except FileNotFoundError:
            pass
        self._intent = None

    def commit(self) -> dict[str, Any]:
        """Publish a prepared intent; retries are hash-aware and idempotent."""

        self._require_entered()
        intent = self._intent
        if intent is None:
            raise RecoveryRequired("prepare must be called before commit")
        phase = intent.get("phase")
        if phase == "applied":
            receipt = self.receipt(intent["operation_id"], intent["request_digest"])
            if receipt is not None:
                return receipt
            raise RecoveryRequired("applied intent has no receipt")
        if phase in {"aborting", "aborted"}:
            raise RecoveryRequired("aborting or aborted publication cannot resume forward")
        if phase not in {"prepared", "applying"}:
            raise MemoryPublicationError(f"invalid publication phase: {phase!r}")
        if phase == "prepared":
            intent["phase"] = "applying"
            self._write_intent(intent, "before_phase:applying")
            self._hook("after_phase:applying")

        records = intent["targets"]
        applied = list(intent.get("applied_targets", []))
        for name in sorted(records, key=self._target_order):
            record = records[name]
            path = self.targets[name].path
            current = _read_target(path)
            current_hash = _sha256(current)
            after_hash = record.get("after_sha256")
            before_hash = record.get("before_sha256")
            if current_hash == after_hash:
                if name not in applied:
                    applied.append(name)
                    intent["applied_targets"] = applied
                    self._write_intent(intent, f"after_target:{name}")
                continue
            if current_hash != before_hash:
                raise PublicationConflict(f"target changed externally during publication: {name}")
            after = self._staged(record, "after_image")
            self._hook(f"before_target:{name}")
            self.publisher(path, after)
            self._hook(f"after_target_replace:{name}")
            if not self._matches(path, after_hash):
                raise MemoryPublicationError(f"publisher did not produce expected bytes for {name}")
            if name not in applied:
                applied.append(name)
            intent["applied_targets"] = applied
            self._write_intent(intent, f"after_target:{name}")

        intent["phase"] = "applied"
        self._write_intent(intent, "before_phase:applied")
        self._hook("after_phase:applied")
        receipt = self._receipt(intent, "applied")
        return self._finish_receipt(intent, receipt)

    def recover(self, action: str, operation_id: str, request_digest: str) -> dict[str, Any]:
        """Explicitly recover the exact pending operation by ``forward`` or ``abort``."""

        intent = self._assert_pending_identity(operation_id, request_digest)
        if action == "forward":
            return self.commit()
        if action == "abort":
            return self.abort(operation_id, request_digest)
        raise ValueError("recovery action must be 'forward' or 'abort'")

    def abort(self, operation_id: str, request_digest: str) -> dict[str, Any]:
        """Abort explicitly, restoring non-archive targets only when safe."""

        intent = self._assert_pending_identity(operation_id, request_digest)
        existing_receipt = self.receipt(operation_id, request_digest)
        if existing_receipt is not None:
            # A receipt is the durable publication boundary.  Canonical
            # acknowledgment may still be pending, but abort must not erase a
            # fully published derivative after that boundary has been flushed.
            return existing_receipt
        if intent.get("phase") == "aborted":
            raise RecoveryRequired("aborted intent has no permanent receipt")
        if intent.get("phase") == "applied":
            # A fully applied intent can still be revoked before canonical
            # acknowledgment; rollback below is hash-guarded.
            pass
        intent["phase"] = "aborting"
        self._write_intent(intent, "before_phase:aborting")
        self._hook("after_phase:aborting")
        retained: list[str] = []
        for name in sorted(intent["targets"], key=self._target_order):
            record = intent["targets"][name]
            path = self.targets[name].path
            current_hash = _sha256(_read_target(path))
            after_hash = record.get("after_sha256")
            before_hash = record.get("before_sha256")
            if current_hash == before_hash:
                continue
            if current_hash != after_hash:
                raise PublicationConflict(f"target changed externally during abort: {name}")
            batch_id = record.get("archive_batch_id")
            if batch_id or "archive" in name.lower():
                if batch_id:
                    retained.append(batch_id)
                continue
            before = self._staged(record, "before_image")
            self._hook(f"before_abort_target:{name}")
            self.publisher(path, before)
            self._hook(f"after_abort_target:{name}")
            if not self._matches(path, before_hash):
                raise MemoryPublicationError(f"abort publisher did not restore {name}")
        intent["phase"] = "aborted"
        intent["retained_archive_batch_ids"] = retained
        self._write_intent(intent, "before_phase:aborted")
        self._hook("after_phase:aborted")
        receipt = self._receipt(intent, "aborted", retained)
        return self._finish_receipt(intent, receipt)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "LockTimeout",
    "MemoryPublicationError",
    "MemoryTarget",
    "MemoryTransaction",
    "PublicationConflict",
    "RecoveryRequired",
]
