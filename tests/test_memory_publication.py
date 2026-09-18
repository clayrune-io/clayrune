"""Focused tests for the dormant receipt-aware memory publication kernel."""

import json
from pathlib import Path

import pytest

from mc.memory_publication import (
    MemoryPublicationError,
    MemoryTransaction,
    PublicationConflict,
    RecoveryRequired,
)


def _targets(vault: Path) -> dict[str, Path]:
    return {
        "archive": vault / "MEMORY_ARCHIVE.md",
        "SESSION_LOG.md": vault / "SESSION_LOG.md",
        "MEMORY.md": vault / "MEMORY.md",
    }


def _seed(vault: Path) -> dict[str, Path]:
    vault.mkdir(parents=True)
    targets = _targets(vault)
    targets["archive"].write_bytes(b"archive-before\n")
    targets["SESSION_LOG.md"].write_bytes(b"session-before\n")
    targets["MEMORY.md"].write_bytes(b"index-before\n")
    return targets


def test_prepare_commit_persists_receipt_and_cleans_intent(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "operator-state"
    after = {
        "archive": b"archive-after\n",
        "SESSION_LOG.md": b"session-after\n",
        "MEMORY.md": b"index-after\n",
    }
    with MemoryTransaction(state, targets=targets) as tx:
        tx.prepare("op-1", request_digest="digest-1", after_images=after,
                   canonical_source={"project": "p", "range": [1, 3]},
                   archive_batch_ids={"archive": "batch-1"})
        receipt = tx.commit()
        assert receipt["outcome"] == "applied"
        assert receipt["archive_batch_ids"] == ["batch-1"]
        assert tx.pending() is None
    assert not (state / ".memory-publication-intent.json").exists()
    assert (state / "receipts" / "op-1.json").exists()
    assert targets["MEMORY.md"].read_bytes() == b"index-after\n"


def test_same_operation_retry_is_receipt_idempotent_but_digest_conflicts(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    with MemoryTransaction(state, targets=targets) as tx:
        tx.prepare("op-1", request_digest="digest-1",
                   after_images={name: f"after-{name}".encode() for name in targets})
        first = tx.commit()
        second = tx.receipt("op-1", "digest-1")
        assert second == first
        with pytest.raises(PublicationConflict):
            tx.receipt("op-1", "different-request")


def test_pending_intent_never_replays_without_explicit_forward(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    after = {name: f"after-{name}".encode() for name in targets}
    interrupted = {"value": False}

    def fault(event: str) -> None:
        if event == "after_target_replace:archive" and not interrupted["value"]:
            interrupted["value"] = True
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        with MemoryTransaction(state, targets=targets, fault_hook=fault) as tx:
            tx.prepare("op-2", request_digest="digest-2", after_images=after)
            tx.commit()
    # A new writer sees the durable intent, but does not silently publish it.
    with MemoryTransaction(state, targets=targets) as tx:
        pending = tx.pending()
        assert pending is not None
        assert pending["phase"] == "applying"
        assert targets["SESSION_LOG.md"].read_bytes() == b"session-before\n"
        with pytest.raises(RecoveryRequired):
            tx.prepare("other-op", request_digest="other", after_images=after)
        receipt = tx.recover("forward", "op-2", "digest-2")
        assert receipt["outcome"] == "applied"
    assert targets["SESSION_LOG.md"].read_bytes() == after["SESSION_LOG.md"]


def test_external_edit_is_a_conflict_without_overwrite(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    after = {name: f"after-{name}".encode() for name in targets}
    with MemoryTransaction(state, targets=targets) as tx:
        tx.prepare("op-3", request_digest="digest-3", after_images=after)
    targets["MEMORY.md"].write_bytes(b"external-edit\n")
    with MemoryTransaction(state, targets=targets) as tx:
        with pytest.raises(PublicationConflict):
            tx.recover("forward", "op-3", "digest-3")
    assert targets["MEMORY.md"].read_bytes() == b"external-edit\n"


def test_abort_restores_non_archive_and_retains_published_archive(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    after = {
        "archive": b"archive-batch-1\n",
        "SESSION_LOG.md": b"session-after\n",
        "MEMORY.md": b"index-after\n",
    }

    def publisher(path: Path, data: bytes | None) -> None:
        if path == targets["SESSION_LOG.md"]:
            raise OSError("crash after archive publication")
        MemoryTransaction._default_publisher(path, data)

    with MemoryTransaction(state, targets=targets, publisher=publisher) as tx:
        tx.prepare("op-4", request_digest="digest-4", after_images=after,
                   archive_batch_ids={"archive": "batch-1"})
        with pytest.raises(OSError):
            tx.commit()
    with MemoryTransaction(state, targets=targets) as tx:
        receipt = tx.recover("abort", "op-4", "digest-4")
        assert receipt["outcome"] == "aborted"
        assert receipt["retained_archive_batch_ids"] == ["batch-1"]
    assert targets["archive"].read_bytes() == after["archive"]
    assert targets["MEMORY.md"].read_bytes() == b"index-before\n"


def test_staging_budget_fails_before_publication(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    before = {name: path.read_bytes() for name, path in targets.items()}
    with MemoryTransaction(state, targets=targets, max_bytes=1) as tx:
        with pytest.raises(MemoryPublicationError, match="max_bytes"):
            tx.prepare("op-5", request_digest="digest-5",
                       after_images={name: b"new" for name in targets})
    assert all(path.read_bytes() == before[name] for name, path in targets.items())
    assert not (state / ".memory-publication-intent.json").exists()


def test_equal_content_from_distinct_operations_get_distinct_receipts(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    after = {name: b"same-content" for name in targets}
    with MemoryTransaction(state, targets=targets) as tx:
        tx.prepare("op-6", request_digest="digest-6", after_images=after)
        tx.commit()
    # The second operation is still distinct even though its after-image is
    # byte-for-byte equal; this is intentionally not content deduplication.
    with MemoryTransaction(state, targets=targets) as tx:
        tx.prepare("op-7", request_digest="digest-7", after_images=after)
        tx.commit()
    assert (state / "receipts" / "op-6.json").exists()
    assert (state / "receipts" / "op-7.json").exists()


def test_receipt_survives_cleanup_interrupt_and_prevents_abort_rollback(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    after = {name: f"after-{name}".encode() for name in targets}

    def fault(event: str) -> None:
        if event == "after_receipt_persist":
            raise RuntimeError("crash after durable receipt")

    with pytest.raises(RuntimeError):
        with MemoryTransaction(state, targets=targets, fault_hook=fault) as tx:
            tx.prepare("op-8", request_digest="digest-8", after_images=after)
            tx.commit()
    with MemoryTransaction(state, targets=targets) as tx:
        assert tx.pending()["phase"] == "applied"
        receipt = tx.recover("abort", "op-8", "digest-8")
        assert receipt["outcome"] == "applied"
        assert targets["MEMORY.md"].read_bytes() == after["MEMORY.md"]
        tx.prepare("op-9", request_digest="digest-9",
                   after_images={name: b"next" for name in targets})
        tx.commit()


def test_tampered_staged_image_is_rejected_before_target_write(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    before = {name: path.read_bytes() for name, path in targets.items()}
    with MemoryTransaction(state, targets=targets) as tx:
        pending = tx.prepare(
            "op-10", request_digest="digest-10",
            after_images={name: f"after-{name}".encode() for name in targets},
        )
        staged = Path(pending["targets"]["archive"]["after_image"])
        staged.write_bytes(b"tampered")
        with pytest.raises(PublicationConflict, match="durable hash"):
            tx.commit()
    assert all(path.read_bytes() == before[name] for name, path in targets.items())


def test_corrupt_intent_cannot_drop_an_allowlisted_target(tmp_path: Path) -> None:
    targets = _seed(tmp_path / "vault")
    state = tmp_path / "state"
    with MemoryTransaction(state, targets=targets) as tx:
        tx.prepare(
            "op-11", request_digest="digest-11",
            after_images={name: f"after-{name}".encode() for name in targets},
        )
    intent_path = state / ".memory-publication-intent.json"
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    intent["targets"].pop("MEMORY.md")
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(PublicationConflict, match="target set"):
        with MemoryTransaction(state, targets=targets):
            pass
