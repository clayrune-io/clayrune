CREATE TABLE IF NOT EXISTS installs (
  install_id TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,   -- unix seconds
  last_seen  INTEGER NOT NULL,
  version    TEXT NOT NULL,
  os         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS installs_last_seen ON installs (last_seen);
