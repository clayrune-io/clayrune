"""Disabled-by-default, recorded-event laundering adapter (MC-1166 unit 5).

No scheduling, inbox fetch, follow-up, question consumption or provider delivery.
Only metadata leaves this adapter; ready envelopes stay in the dedicated store.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from mc.agent_mail_extract import Extraction, extract_reply
from mc.agent_mail_store import BLOCKED, LAUNDERING, READY, MailStore


class MailLaunder:
    def __init__(self, store: MailStore, *, enabled: bool = False,
                 provider: str = 'claude', model: str = 'haiku', timeout: int = 25):
        self.store = store
        self.enabled = enabled
        self.provider = provider
        self.model = model
        self.timeout = timeout

    @staticmethod
    def _metadata(row: sqlite3.Row) -> dict[str, str]:
        return {'event_id': row['event_id'], 'state': row['state'], 'reason': row['reason']}

    def _live(self, db: sqlite3.Connection, event_id: str, fence: str) -> Optional[sqlite3.Row]:
        """Check consent/expiry/lease under the same write lock as finalization."""
        self.store._reconcile_locked(db)
        row = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
        now = self.store.clock()
        if (row is None or row['state'] != LAUNDERING or row['fence_token'] != fence
                or row['lease_until'] <= now):
            return None
        reason = self.store._validate_target(db, row['registration_id'], row['thread_hash'], now)
        reg = db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                         (row['registration_id'],)).fetchone()
        if not reason and (reg is None or reg['generation'] != row['generation']):
            reason = 'stale_generation'
        if reason:
            self.store._fail_locked(db, row, reason, False)
            return None
        return row

    def _finish(self, event_id: str, fence: str, result: Extraction) -> dict[str, str]:
        with self.store._tx() as db:
            row = self._live(db, event_id, fence)
            if row is not None:
                if result.reason or result.envelope is None:
                    self.store._fail_locked(db, row, result.reason or 'extraction_failed', False)
                else:
                    envelope = dict(result.envelope, event_id=event_id)
                    self.store._move(db, event_id, LAUNDERING, READY, fence=fence,
                                     laundered=json.dumps(envelope, ensure_ascii=False,
                                                          separators=(',', ':'), sort_keys=True))
            current = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            if current is None:
                return {'event_id': event_id, 'state': 'unavailable', 'reason': 'event_unavailable'}
            return self._metadata(current)

    def process_next(self) -> dict[str, str]:
        """Process at most one persisted event, once. Holds need explicit review.

        Releasing a hold never resets attempts. Crash/lease recovery uses the
        store's existing bounded backoff; no raw-body or alternate-runtime retry.
        Storage failures propagate to the caller, never masquerade as readiness.
        """
        if self.enabled is not True:
            return {'state': 'disabled', 'reason': 'laundering_disabled'}
        claimed = self.store.claim_for_laundering()
        if claimed is None:
            return {'state': 'idle', 'reason': ''}
        event_id, fence = claimed['event_id'], claimed['fence_token']
        reason = ''
        kind = ''
        with self.store._tx() as db:
            row = self._live(db, event_id, fence)
            if row is not None:
                binding = db.execute('SELECT kind FROM thread_bindings WHERE thread_hash=?',
                                     (row['thread_hash'],)).fetchone()
                kind = binding['kind'] if binding is not None else ''
                if row['attempts'] >= self.store.config.max_attempts:
                    reason = 'retry_exhausted'
            else:
                reason = 'claim_lost'
        # Reserve finalization time using the actual remaining persisted lease.
        timeout = min(25, self.timeout)
        seconds = min(timeout, claimed['lease_until'] - self.store.clock() - 3)
        deadline = time.monotonic() + seconds
        if not reason and seconds < 1:
            reason = 'extraction_timeout'
        result = (Extraction(reason) if reason else extract_reply(
            claimed['body'], kind=kind, provider=self.provider, model=self.model,
            timeout=timeout, deadline=deadline,
            max_reply_bytes=self.store.config.max_reply_bytes))
        return self._finish(event_id, fence, result)
