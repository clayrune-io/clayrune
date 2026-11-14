"""Durable outbound intent metadata; payloads are sealed outside repo custody."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_store import MailStore


def _expiry_receipt_schema(db: sqlite3.Connection) -> None:
    """Keep correlation immutable; only an accepted send can replace its TTL."""
    db.execute('DROP TRIGGER mail_outbound_immutable')
    db.execute('''CREATE TRIGGER mail_outbound_immutable BEFORE UPDATE OF
        intent_id,request_key,request_digest,project_id,session_id,install,generation,
        flow,epoch,thread_id,thread_hash,question_revision,digest,created ON mail_outbound_intents
        BEGIN SELECT RAISE(ABORT,'immutable outbound correlation'); END''')
    db.execute('''CREATE TRIGGER mail_outbound_expiry BEFORE UPDATE OF expires ON mail_outbound_intents
        WHEN NEW.expires IS NOT OLD.expires AND NOT
          (OLD.state IN ('sending','uncertain') AND NEW.state='accepted' AND NEW.message_id!='')
        BEGIN SELECT RAISE(ABORT,'outbound expiry requires acceptance'); END''')
    db.execute('UPDATE mail_outbound_version SET version=2')


def initialize(store: MailStore) -> None:
    with store._tx() as db:
        names = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mail_outbound_%'")}
        if names:
            if names != {'mail_outbound_version', 'mail_outbound_intents', 'mail_outbound_question_channels'}:
                raise EnrollmentRefused('outbound_schema_corrupt')
            version = [r[0] for r in db.execute('SELECT version FROM mail_outbound_version')]
            if version not in ([1], [2]):
                raise EnrollmentRefused('outbound_schema_corrupt')
            triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'mail_outbound_%'")}
            expected = {'mail_outbound_immutable', 'mail_outbound_payload'}
            if version == [2]:
                expected.add('mail_outbound_expiry')
            if triggers != expected:
                raise EnrollmentRefused('outbound_schema_corrupt')
            # A partial schema must not be mistaken for an empty outbox.
            db.execute('SELECT intent_id,request_key,request_digest,project_id,session_id,'
                       'install,generation,flow,epoch,thread_id,thread_hash,question_revision,payload,digest,'
                       'state,reason,attempt,message_id,created,updated,expires '
                       'FROM mail_outbound_intents LIMIT 0')
            if version == [1]:
                _expiry_receipt_schema(db)
            return
        db.execute('CREATE TABLE mail_outbound_version(version INTEGER NOT NULL)')
        db.execute('INSERT INTO mail_outbound_version VALUES(1)')
        db.execute('''CREATE TABLE mail_outbound_question_channels(
            project_id TEXT NOT NULL, session_id TEXT NOT NULL, revision TEXT NOT NULL,
            channel TEXT NOT NULL CHECK(channel IN ('legacy','agent_mail')), expires REAL NOT NULL,
            PRIMARY KEY(project_id,session_id,revision))''')
        db.execute('''CREATE TABLE mail_outbound_intents(
            intent_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, request_digest TEXT NOT NULL,
            project_id TEXT NOT NULL, session_id TEXT NOT NULL, install TEXT NOT NULL,
            generation INTEGER NOT NULL, flow TEXT NOT NULL, epoch INTEGER NOT NULL,
            thread_id TEXT NOT NULL UNIQUE, thread_hash TEXT NOT NULL UNIQUE,
            question_revision TEXT NOT NULL,
            payload BLOB, digest TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
            attempt TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
            UNIQUE(project_id,session_id,request_key))''')
        db.execute('''CREATE TRIGGER mail_outbound_immutable BEFORE UPDATE OF
            intent_id,request_key,request_digest,project_id,session_id,install,generation,
            flow,epoch,thread_id,thread_hash,question_revision,digest,created,expires ON mail_outbound_intents
            BEGIN SELECT RAISE(ABORT,'immutable outbound correlation'); END''')
        db.execute('''CREATE TRIGGER mail_outbound_payload BEFORE UPDATE OF payload
            ON mail_outbound_intents WHEN NEW.payload IS NOT NULL
            BEGIN SELECT RAISE(ABORT,'immutable outbound payload'); END''')
        _expiry_receipt_schema(db)


def aad(row: dict[str, Any]) -> bytes:
    return json.dumps(['clayrune-mail-outbound', '1', row['intent_id'], row['install'],
                       row['generation'], row['flow'], row['epoch'], row['project_id'],
                       row['session_id'], row['thread_id'], row['thread_hash'], row['question_revision'], row['digest']],
                      separators=(',', ':')).encode()


def seal(row: dict[str, Any], raw: bytes, key: bytes, nonce: bytes) -> bytes:
    return nonce + AESGCM(key).encrypt(nonce, raw, aad(row))


def unseal(row: dict[str, Any], key: bytes) -> bytes:
    packed = bytes(row['payload'])
    return AESGCM(key).decrypt(packed[:12], packed[12:], aad(row))


def metadata(row: dict[str, Any] | sqlite3.Row) -> dict[str, str]:
    # No mailbox, token, body, question, native identity or remote error text.
    return dict({key: row[key] for key in ('intent_id', 'state', 'reason', 'message_id')},
                transport_state='accepted' if row['message_id'] else row['state'])
