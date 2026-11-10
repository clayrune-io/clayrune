"""Firestore access for mail delivery authority (MC-1166 7d-2b, contract 1.5 and 6).

Kept out of `firestore.py` on purpose: one module owns these four collections.
Every write is a Firestore transaction (read, decide, write), so a claim is
create-if-absent and a binding update is compare-and-set. Firestore may re-run
a transaction body, so the `decide` callables passed in must be pure.

Collections:
  mail_delivery_bindings/{install_id}        CP binding: what generation is bound, to whom
  mail_delivery_issuances/{install}.{gen}.{nonce}   replay record, TTL 10 min
  mail_service_nonces/{key_id}.{nonce}       service-auth replay record, TTL skew+1 s
  mail_delivery_budgets/{i|g}.{...}.{day}    per-install and global daily issuance counters

`expires_at` fields are timezone-aware datetimes so a Firestore TTL policy can
sweep them (the TTL policies themselves are a deploy step, not done here).
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any, Callable, Optional

from . import firestore as fs

COL_BINDINGS = "mail_delivery_bindings"
COL_ISSUANCES = "mail_delivery_issuances"
COL_SERVICE_NONCES = "mail_service_nonces"
COL_BUDGETS = "mail_delivery_budgets"
ISSUANCE_TTL_MS = 600_000


def _dt_from_ms(ms: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc)


def day_key(ms: int) -> str:
    return time.strftime("%Y%m%d", time.gmtime(ms // 1000))


class DeliveryStore:
    """Transactional store. `db` is a zero-arg callable returning a Firestore client."""

    def __init__(self, db: Optional[Callable[[], Any]] = None):
        self._db = db or (lambda: fs.db())

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

    # ---- bindings ---------------------------------------------------------------------

    def get_binding(self, install_id: str) -> Optional[dict]:
        snap = self._db().collection(COL_BINDINGS).document(install_id).get()
        return (snap.to_dict() or None) if snap.exists else None

    def mutate_binding(self, install_id: str, decide: Callable[[Optional[dict]], tuple]):
        """Compare-and-set: `decide(current)` returns (new_record_or_None, result)."""
        return self._mutate(COL_BINDINGS, install_id, decide)

    # ---- replay ------------------------------------------------------------------------

    def claim_service_nonce(self, key_id: str, nonce: str, expires_ms: int) -> bool:
        """True only for the first claim of (key_id, nonce)."""
        def decide(cur):
            if cur is not None:
                return None, False
            return {"key_id": key_id, "nonce": nonce, "expires_at": _dt_from_ms(expires_ms)}, True
        return self._mutate(COL_SERVICE_NONCES, f"{key_id}.{nonce}", decide)

    def claim_issuance(self, install_id: str, generation: str, nonce: str, req_digest: str,
                       jti: str, mode: str, now_ms: int) -> str:
        """'created' | 'same_digest' (retry, re-sign allowed) | 'nonce_reused' (digest differs)."""
        def decide(cur):
            if cur is None:
                return ({"install_id": install_id, "generation": generation, "req_digest": req_digest,
                         "jti": jti, "mode": mode, "created_ms": now_ms,
                         "expires_at": _dt_from_ms(now_ms + ISSUANCE_TTL_MS)}, "created")
            return None, ("same_digest" if cur.get("req_digest") == req_digest else "nonce_reused")
        return self._mutate(COL_ISSUANCES, f"{install_id}.{generation}.{nonce}", decide)

    # ---- budgets -----------------------------------------------------------------------

    def consume_budget(self, install_id: str, now_ms: int, *, install_limit: int,
                       global_limit: int) -> str:
        """Count one issuance against today's per-install and global caps.

        Returns 'ok', 'install' or 'global'. A refused attempt is not counted.
        Both counters move in one transaction (or neither does).
        """
        from google.cloud import firestore  # type: ignore
        client = self._db()
        day = day_key(now_ms)
        i_ref = client.collection(COL_BUDGETS).document(f"i.{install_id}.{day}")
        g_ref = client.collection(COL_BUDGETS).document(f"g.{day}")
        expires = _dt_from_ms(now_ms + 2 * 86_400_000)

        @firestore.transactional
        def run(txn):
            i = i_ref.get(transaction=txn)
            g = g_ref.get(transaction=txn)
            i_n = (i.to_dict() or {}).get("n", 0) if i.exists else 0
            g_n = (g.to_dict() or {}).get("n", 0) if g.exists else 0
            if i_n >= install_limit:
                return "install"
            if g_n >= global_limit:
                return "global"
            txn.set(i_ref, {"n": i_n + 1, "day": day, "install_id": install_id, "expires_at": expires})
            txn.set(g_ref, {"n": g_n + 1, "day": day, "expires_at": expires})
            return "ok"

        return run(client.transaction())
