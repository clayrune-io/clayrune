"""Write-ahead session spend, retried before any further API-credit work.

Only usage/cost metadata lives here. The journal is outside project records;
ledger receipts make a retry after a crash or journal-clear failure idempotent.
All operations share the budget lock, including the spend gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mc import api_credits_budget as budget
from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log

# Retain a failed journal write too; while it cannot be flushed, the gate stays
# closed. Key by storage path so wiring/tests cannot mix installations.
_unsaved: dict[Path, dict[str, dict[str, Any]]] = {}


def path() -> Path:
    return budget.ledger_path().with_name('api_credits_pending.json')


def _read() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(read_text_with_retry(path()))
    except FileNotFoundError:
        raw = {}
    if not isinstance(raw, dict) or any(not isinstance(v, dict) for v in raw.values()):
        raise ValueError('invalid pending accounting journal')
    return raw


def _save(rows: dict) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path(), rows, indent=1)


def record(**entry: Any) -> None:
    """Durably enqueue before touching the ledger; never discard failed writes.

    The caller's observed CLI baseline can advance because the full delta is
    retained independently of that child. A successful result is never replayed.
    """
    with budget._lock:
        settings = budget.load_settings()
        start, _ = budget.cycle_bounds(budget._now(), settings['reset_day'])
        row = dict(entry, event_id=uuid4().hex, cycle_start=start.isoformat(),
                   recorded_at=budget._iso(budget._now()))
        _unsaved.setdefault(path(), {})[row['event_id']] = row
        # flush also persists the queue before attempting any ledger write.
        flush()


def flush() -> bool:
    """Retry pending spend; false means every credit-spending path must stop.

    Strict reads: malformed/unreadable state must never look like zero spend.
    Receipt cleanup happens only after the empty journal is durably saved.
    """
    with budget._lock:
        try:
            rows = _read()
            unsaved = _unsaved.get(path(), {})
            if unsaved:
                rows.update(unsaved)
                _save(rows)
                _unsaved.pop(path(), None)
            for row in rows.values():
                budget.record(**row)
            if rows:
                _save({})
            # A crash before this cleanup leaves harmless receipts. They are
            # removed on the next gate check, after any pending rows are replayed.
            led = budget._load_ledger(strict=True)
            if led.pop('pending_receipts', None):
                budget._save_ledger(led)
            return True
        except Exception as exc:
            _log(f'[api-credits-session] pending accounting failed: {type(exc).__name__}', flush=True)
            return False
