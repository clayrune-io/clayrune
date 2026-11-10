"""Flow-keyed fence tokens in the local plain store (MC-1166 unit 7d-2d, contract 5.3, decision D2).

`cancel` and `revoke` are authorized by a 32-byte token whose SHA-256 the Worker stored at `begin`.
The token lives in the same SQLite store as the flow but is NOT sealed: cancelling or revoking
must work with a locked or lost vault (`agent_mail_enrollment_revocations.py`). The compromise
this accepts is bounded: whoever can read this store already controls consent and the flow.

The table prefix is deliberately neither `mail_enrollment_` nor `mail_delivery_`: both prefixes
have exact-set schema checks (`agent_mail_enrollment_state.initialize`,
`agent_mail_delivery_schema.initialize`) that an extra table would trip.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from typing import Callable

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_store import MailStore

TABLE = 'mail_lifecycle_fence'
_COLUMNS = ('flow', 'token')
_TOKEN = re.compile(r'[a-f0-9]{64}')
_FLOW = re.compile(r'[a-f0-9]{32}')


class LifecycleFences:
    """`enabled=False` construction touches nothing; the table appears only on an enabled build."""

    def __init__(self, store: MailStore, *, token: Callable[[int], str] = secrets.token_hex,
                 enabled: bool = False):
        self.store, self._token, self.enabled = store, token, enabled
        if enabled:
            self.initialize()

    def initialize(self) -> None:
        with self.store._tx() as db:
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (TABLE,)).fetchone():
                columns = tuple(r[1] for r in db.execute(f'PRAGMA table_info({TABLE})'))
                if columns != _COLUMNS:
                    raise EnrollmentRefused('fence_schema_corrupt')
                return
            db.execute(f'CREATE TABLE {TABLE} (flow TEXT PRIMARY KEY, token TEXT NOT NULL '
                       'CHECK(length(token)=64))')

    @staticmethod
    def digest_of(token: str) -> str:
        """Exactly what the Worker computes: sha256 over the ASCII bytes of the 64-hex token."""
        return hashlib.sha256(token.encode('ascii')).hexdigest()

    def create_in_tx(self, db: sqlite3.Connection, flow: str) -> None:
        """Called in the transaction that writes the flow row, so every flow that can ever begin
        already has a token (and can therefore be cancelled). INSERT OR IGNORE: a flow's token
        never changes."""
        if not self.enabled:
            return
        token = self._token(32)
        if not _TOKEN.fullmatch(token):
            raise EnrollmentRefused('fence_token_invalid')
        db.execute(f'INSERT OR IGNORE INTO {TABLE}(flow,token) VALUES(?,?)', (flow, token))

    def _read_token(self, db: sqlite3.Connection, flow: str) -> str | None:
        row = db.execute(f'SELECT token FROM {TABLE} WHERE flow=?', (flow,)).fetchone()
        if row is None:
            return None
        if not isinstance(row[0], str) or not _TOKEN.fullmatch(row[0]):
            raise EnrollmentRefused('fence_token_invalid')
        return row[0]

    def token_for(self, flow: str) -> str | None:
        if not self.enabled or not _FLOW.fullmatch(flow):
            return None
        with self.store._read() as db:
            return self._read_token(db, flow)

    def digest_for(self, flow: str) -> str | None:
        """What `begin` sends to the Worker as `fence_digest`. Never the token itself."""
        token = self.token_for(flow)
        return None if token is None else self.digest_of(token)

    def tokens_for_assignment(self, install: str, generation: int) -> list[tuple[str, str]]:
        """(flow, token) for every flow the local revocation queue resolved to this exact
        assignment. Usually one; the Worker accepts only the token whose digest it stored."""
        if not self.enabled:
            return []
        with self.store._read() as db:
            flows = [r[0] for r in db.execute(
                'SELECT flow FROM mail_enrollment_revocations WHERE install=? AND generation=? ORDER BY flow',
                (install, generation))]
            out = []
            for flow in flows:
                token = self._read_token(db, flow) if _FLOW.fullmatch(flow) else None
                if token is not None:
                    out.append((flow, token))
            return out
