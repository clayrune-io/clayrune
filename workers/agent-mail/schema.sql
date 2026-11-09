-- Dedicated mail D1 database. Enrollment (unit 7) is the only registration writer.
PRAGMA foreign_keys = ON;
CREATE TABLE registrations (
  install_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation > 0),
  active INTEGER NOT NULL DEFAULT 0, owner_verified INTEGER NOT NULL DEFAULT 0,
  owner_address TEXT NOT NULL, address_local TEXT NOT NULL UNIQUE,
  key_id TEXT NOT NULL, public_key TEXT NOT NULL,
  route_origin TEXT, route_path TEXT, route_generation INTEGER,
  route_verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE nonces (
  direction TEXT NOT NULL, install_id TEXT NOT NULL, generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL,
  PRIMARY KEY(direction, install_id, generation, key_id, nonce)
);
CREATE TABLE intents (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, intent_id TEXT NOT NULL,
  digest TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('sending','accepted','uncertain','revoked')),
  created_at INTEGER NOT NULL, finished_at INTEGER,
  PRIMARY KEY(install_id,generation,intent_id)
);
CREATE TABLE threads (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, thread_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, message_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(install_id,generation,thread_id)
);
CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, thread_id TEXT NOT NULL,
  incoming_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT,
  state TEXT NOT NULL CHECK(state IN ('pending','delivering','local_received','held','expired','revoked')),
  reason TEXT, received_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  next_attempt INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  lease_id TEXT, lease_until INTEGER, terminal_at INTEGER,
  UNIQUE(install_id,generation,thread_id,incoming_id)
);
CREATE INDEX events_due ON events(state,next_attempt,seq);
CREATE TABLE rejections (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE TABLE rejection_overflow (day INTEGER PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE rate_limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at INTEGER NOT NULL);
