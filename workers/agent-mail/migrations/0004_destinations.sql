-- Durable identity attempts and create tombstones outlive enrollment flows/reservations.
CREATE TABLE mail_begin_nonces (
  device_id TEXT NOT NULL, nonce TEXT NOT NULL, expires_ms INTEGER NOT NULL,
  PRIMARY KEY(device_id, nonce)
);
CREATE INDEX mail_begin_nonce_expiry ON mail_begin_nonces(expires_ms);
CREATE TABLE mail_destination_attempts (
  device_id TEXT PRIMARY KEY, address TEXT NOT NULL, started_ms INTEGER NOT NULL,
  expires_ms INTEGER NOT NULL
);
CREATE TABLE mail_destination_creates (
  address TEXT PRIMARY KEY, device_id TEXT NOT NULL, created_ms INTEGER NOT NULL,
  observed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX mail_destination_create_time ON mail_destination_creates(created_ms);
CREATE TABLE mail_destination_lock (
  slot INTEGER PRIMARY KEY CHECK(slot=1), token TEXT NOT NULL, expires_ms INTEGER NOT NULL
);
