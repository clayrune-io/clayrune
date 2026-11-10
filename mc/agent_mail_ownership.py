"""Read-only question-channel ownership recovery and capacity (MC-1166 unit 7d-1).

The channel receipt in ``mail_outbound_question_channels`` is the compact,
durable owner tombstone of one question revision. This module only READS it and
counts it; ``Outbound.question_channel`` writes it. It is available while
outbound and enrollment are off and never sends, creates a table, or opens a
store that does not exist.

A lookup distinguishes an authoritative MISS (the store was never created, or
the table does not exist, or no row) from storage that cannot answer
(``OwnershipUnavailable``). The caller must hold on the latter, never fall
back to another channel.

Tombstones are released only by the destructive session/project deletion path
(``mc.agent_mail_deletion``); there is no age-based eviction. At capacity new
admissions are held, never evicted.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from mc.agent_mail_store import MailStore, MailStoreConfig

# Measured 2026-11-10 (scratch sizing, on-disk SQLite, WAL checkpointed):
# 24,000 rows = 7.30 MiB with 32-char ids but 17.59 MiB with 128-char ids, the
# latter over the 16 MB bar, so the conservative default is 2,400 rows
# (0.74 / 1.78 MiB). Override with config ``agent_mail_ownership_capacity``.
DEFAULT_CAPACITY = MailStoreConfig().max_ownership_receipts  # 2,400
CAPACITY_KEY = 'agent_mail_ownership_capacity'
TABLE = 'mail_outbound_question_channels'


class OwnershipUnavailable(Exception):
    """Storage could not answer. ``reason`` is a closed code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class OwnershipHeld(Exception):
    """A NEW ownership admission is held (capacity). Existing owners are never
    affected. ``reason`` is a closed code; the caller must not select a channel."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def capacity_from(config: dict[str, Any], default: int = DEFAULT_CAPACITY) -> int:
    value = config.get(CAPACITY_KEY)
    return value if type(value) is int and value >= 1 else default


def _table(db: Any) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def lookup(factory: Any, project_id: str, session_id: str, revision: str) -> str | None:
    """'legacy' | 'agent_mail' | None (authoritative miss). Raises
    OwnershipUnavailable if the answer cannot be trusted."""
    try:
        store = factory.open_store()
        if store is None:
            return None
        with store._read() as db:
            if not _table(db):
                return None
            row = db.execute(f'SELECT channel FROM {TABLE} WHERE project_id=? AND session_id=? AND revision=?',
                             (project_id, session_id, revision)).fetchone()
        return None if row is None else str(row['channel'])
    except OwnershipUnavailable:
        raise
    except Exception as exc:
        raise OwnershipUnavailable(getattr(exc, 'reason', None) or 'ownership_unavailable') from None


# Installed by the server at startup (``install_reader``); None means no recovery
# exists, which is exactly the pre-7d-1 behaviour. It is deliberately a plain
# callable so the notification path never imports a factory or a store.
_reader: Callable[[str, str, str], str | None] | None = None


def install_reader(reader: Callable[[str, str, str], str | None] | None) -> None:
    global _reader
    _reader = reader


def recover(project_id: str, session_id: str, revision: str) -> str | None:
    """Durable owner of one revision, read BEFORE channel eligibility.

    Returns 'legacy' | 'agent_mail' | None (authoritative miss, or no reader
    installed). Raises OwnershipUnavailable if storage cannot answer."""
    reader = _reader
    return None if reader is None else reader(project_id, session_id, revision)


def factory_reader(factory: Any) -> Callable[[str, str, str], str | None]:
    return lambda project_id, session_id, revision: lookup(factory, project_id, session_id, revision)


def count(store: MailStore) -> int:
    with store._read() as db:
        return int(db.execute(f'SELECT COUNT(*) FROM {TABLE}').fetchone()[0]) if _table(db) else 0


# Visible, process-local record of admissions held for capacity/storage. The
# durable truth is the row count; this only lets a status surface say "pending".
_held_lock = threading.Lock()
_held: dict[str, int] = {}


def record_hold(reason: str) -> None:
    with _held_lock:
        _held[reason] = _held.get(reason, 0) + 1


def held() -> dict[str, int]:
    with _held_lock:
        return dict(_held)
