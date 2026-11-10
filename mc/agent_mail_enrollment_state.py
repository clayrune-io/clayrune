"""Versioned lifecycle tables and cross-process manifest CAS in the inbox DB.

Use an outside-repository MailStore (default location below). All authority
changes share its BEGIN IMMEDIATE lock with ingress and follow-up submission.
"""
from __future__ import annotations

import re
from pathlib import Path

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_service_manifest import ManifestState
from mc.agent_mail_store import MailStore


def default_enrollment_folder() -> Path:
    return Path.home() / '.clayrune' / 'agent-mail'


def initialize(store: MailStore) -> None:
    """Explicit constructor migration only; schema marker cannot be reset."""
    with store._tx() as db:
        marker = db.execute("SELECT name FROM sqlite_master WHERE name='mail_enrollment_version'").fetchone()
        existing = db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'mail_enrollment_%'").fetchall()
        if marker:
            rows = db.execute('SELECT version FROM mail_enrollment_version').fetchall()
            if len(rows) != 1 or rows[0][0] != 1:
                raise EnrollmentRefused('lifecycle_schema_corrupt')
            required = {'mail_enrollment_version', 'mail_enrollment_flow', 'mail_enrollment_revocations',
                        'mail_enrollment_manifests'}
            if {r[0] for r in existing} != required:
                raise EnrollmentRefused('lifecycle_schema_corrupt')
            return
        if existing:
            raise EnrollmentRefused('lifecycle_schema_corrupt')
        for sql in (
            'CREATE TABLE mail_enrollment_version(version INTEGER NOT NULL)',
            'INSERT INTO mail_enrollment_version VALUES(1)',
            '''CREATE TABLE mail_enrollment_flow (
                slot INTEGER PRIMARY KEY CHECK(slot=1), flow TEXT NOT NULL UNIQUE,
                install TEXT NOT NULL, generation INTEGER NOT NULL, owner TEXT NOT NULL,
                key_id TEXT NOT NULL, public_key TEXT NOT NULL DEFAULT '',
                seal_identity TEXT NOT NULL, assigned INTEGER NOT NULL DEFAULT 0,
                stage TEXT NOT NULL, epoch INTEGER NOT NULL, candidate TEXT NOT NULL DEFAULT '',
                confirmed INTEGER NOT NULL DEFAULT 0, ownership INTEGER NOT NULL DEFAULT 0,
                ownership_expires_ms INTEGER NOT NULL DEFAULT 0)''',
            '''CREATE TABLE mail_enrollment_revocations (
                install TEXT NOT NULL, generation INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(install,generation))''',
            '''CREATE TABLE mail_enrollment_manifests (
                origin TEXT PRIMARY KEY, sequence INTEGER NOT NULL, digest TEXT NOT NULL)''',
        ):
            db.execute(sql)


class ManifestCAS:
    def __init__(self, store: MailStore):
        self.store = store

    @staticmethod
    def _state(row) -> ManifestState | None:
        if row is None:
            return None
        if type(row[0]) is not int or row[0] < 1 or not re.fullmatch('[a-f0-9]{64}', row[1]):
            raise EnrollmentRefused('manifest_state_corrupt')
        return ManifestState(row[0], row[1])

    def load(self, origin: str) -> ManifestState | None:
        with self.store._read() as db:
            return self._state(db.execute('SELECT sequence,digest FROM mail_enrollment_manifests WHERE origin=?',
                                          (origin,)).fetchone())

    def compare_and_set(self, origin: str, expected: ManifestState | None, value: ManifestState) -> bool:
        with self.store._tx() as db:
            old = self._state(db.execute('SELECT sequence,digest FROM mail_enrollment_manifests WHERE origin=?',
                                         (origin,)).fetchone())
            if old != expected:
                return False
            if old and (value.sequence < old.sequence or value.sequence == old.sequence and value.digest != old.digest):
                raise EnrollmentRefused('manifest_rollback')
            self._state((value.sequence, value.digest))
            db.execute('INSERT OR REPLACE INTO mail_enrollment_manifests VALUES(?,?,?)',
                       (origin, value.sequence, value.digest))
            return True
