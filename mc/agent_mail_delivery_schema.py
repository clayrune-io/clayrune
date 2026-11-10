"""Local tables for proof of machine custody (MC-1166 unit 7d-2c, contract sections 4 and 6).

Prefix is deliberately `mail_delivery_`, never `mail_enrollment_`: the lifecycle schema
check in `agent_mail_enrollment_state.initialize` requires that prefix to equal exactly its
four tables. This module has its own version marker and its own exact-set check, and
mirrors that marker discipline: it can neither be reset nor silently recreated.
"""
from __future__ import annotations

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_store import MailStore

VERSION = 1
REQUIRED = frozenset({'mail_delivery_version', 'mail_delivery_proof', 'mail_delivery_activation'})


def initialize(store: MailStore) -> None:
    """Explicit constructor migration only, serialized by the store's write lock."""
    with store._tx() as db:
        marker = db.execute("SELECT name FROM sqlite_master WHERE name='mail_delivery_version'").fetchone()
        existing = db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'mail\\_delivery\\_%' ESCAPE '\\'").fetchall()
        if marker:
            rows = db.execute('SELECT version FROM mail_delivery_version').fetchall()
            if len(rows) != 1 or rows[0][0] != VERSION or {r[0] for r in existing} != REQUIRED:
                raise EnrollmentRefused('delivery_schema_corrupt')
            return
        if existing:
            raise EnrollmentRefused('delivery_schema_corrupt')
        for sql in (
            'CREATE TABLE mail_delivery_version(version INTEGER NOT NULL)',
            f'INSERT INTO mail_delivery_version VALUES({VERSION})',
            # One row per install+generation. A consumed row is final: re-arming over it is refused.
            '''CREATE TABLE mail_delivery_proof (
                install TEXT NOT NULL, generation INTEGER NOT NULL, flow TEXT NOT NULL,
                epoch INTEGER NOT NULL, challenge TEXT NOT NULL, proof_id TEXT NOT NULL,
                manifest_sequence INTEGER NOT NULL, manifest_digest TEXT NOT NULL,
                service_key_id TEXT NOT NULL, service_public_key_sha256 TEXT NOT NULL,
                mail_key_id TEXT NOT NULL, route_origin TEXT NOT NULL, owner_address_sha256 TEXT NOT NULL,
                ownership_expires_ms INTEGER NOT NULL, armed_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('armed','proved','invalidated','consumed')),
                receipt_digest TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(install, generation))''',
            # Written in the same transaction as enable(); the supervisor (7d-2d) drives it to 'acked'.
            '''CREATE TABLE mail_delivery_activation (
                install TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','acked')),
                receipt_digest TEXT NOT NULL, created_ms INTEGER NOT NULL,
                PRIMARY KEY(install, generation, epoch))''',
        ):
            db.execute(sql)
