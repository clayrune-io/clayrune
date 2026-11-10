"""Firestore access for the mail enrollment grant route (MC-1166 7d-2e-1, contract 1.2).

One new collection, `mail_enrollment_grant_budget/{device_id}.{hour}`: a per-device,
per-UTC-hour grant counter. The CP stores no grant and no replay record (a grant is
deliberately not idempotent; the Worker's `jti` PK + `flow_id` UNIQUE are the replay
guard). Transaction shape copied from `DeliveryStore._mutate`.

Service-call nonces for `/preflight` live in the existing `mail_service_nonces`
collection owned by `DeliveryStore`; this store delegates to it rather than keep a
second copy of that keyspace.

`expires_at` is a timezone-aware datetime so a Firestore TTL policy can sweep it (the
policy itself is a deploy step, not done here).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from . import firestore as fs
from .mail_delivery_store import DeliveryStore, _dt_from_ms

COL_GRANT_BUDGET = "mail_enrollment_grant_budget"
BUDGET_WINDOW_MS = 3_600_000


def hour_key(ms: int) -> str:
    return time.strftime("%Y%m%d%H", time.gmtime(ms // 1000))


class EnrollmentStore:
    """`db` is a zero-arg callable returning a Firestore client."""

    def __init__(self, db: Optional[Callable[[], Any]] = None):
        self._db = db or (lambda: fs.db())
        self._delivery = DeliveryStore(db=self._db)

    def _mutate(self, col: str, doc_id: str, decide: Callable[[Optional[dict]], tuple]):
        """Atomically read one document, call `decide(current)` -> (new_or_None, result), write new."""
        from google.cloud import firestore  # type: ignore
        client = self._db()
        ref = client.collection(col).document(doc_id)

        @firestore.transactional
        def run(txn):
            snap = ref.get(transaction=txn)
            new, result = decide(snap.to_dict() if snap.exists else None)
            if new is not None:
                txn.set(ref, new)
            return result

        return run(client.transaction())

    def claim_service_nonce(self, key_id: str, nonce: str, expires_ms: int) -> bool:
        return self._delivery.claim_service_nonce(key_id, nonce, expires_ms)

    def consume_grant_budget(self, device_id: str, now_ms: int, *, limit: int) -> bool:
        """Count one grant against this device's current-hour cap.

        True when counted, False when the cap is already reached (a refused attempt
        is not counted).
        """
        def decide(cur):
            n = int((cur or {}).get("n", 0))
            if n >= limit:
                return None, False
            hour = hour_key(now_ms)
            return ({"n": n + 1, "hour": hour, "device_id": device_id,
                     "expires_at": _dt_from_ms(now_ms + 2 * BUDGET_WINDOW_MS)}, True)
        return self._mutate(COL_GRANT_BUDGET, f"{device_id}.{hour_key(now_ms)}", decide)
