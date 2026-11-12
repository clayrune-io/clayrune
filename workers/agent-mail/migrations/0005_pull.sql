-- MC-1166 pull v1, unit 01. Apply after schema.sql and migrations 0001..0004.
-- Exact normative SQL: docs/AGENT_MAIL_PULL_CONTRACT.md v1.0 section 8 (27f84e17).
-- Foreign keys must be enabled. No legacy authority is copied or changed.
--
-- ROLLBACK PLAN (manual only; never executed by this migration):
-- Stop pull readers/writers and take a database snapshot first. Removing these
-- tables destroys all pull authority, queue, dedupe, receipts and owner-limit
-- evidence; after real use, restore a coordinated snapshot or retain the data
-- until its contractual retention obligations end. Legacy tables stay intact.
-- Execute the following DROP list in reverse dependency order with foreign keys
-- still enabled, in a single transaction (BEGIN; list below; COMMIT;).
-- DROP TABLE pull_audit_overflow;
-- DROP TABLE pull_audit;
-- DROP TABLE pull_sweep_cursors;
-- DROP TABLE pull_notifications;
-- DROP TABLE pull_receipts;
-- DROP TABLE pull_offers;
-- DROP TABLE pull_events;
-- DROP TABLE pull_ingress_holds;
-- DROP TABLE pull_dedupe;
-- DROP TABLE pull_threads;
-- DROP TABLE pull_intents;
-- DROP TABLE pull_tickets;
-- DROP TABLE pull_budgets;
-- DROP TABLE pull_nonces;
-- DROP TABLE pull_control_outbox;
-- DROP TABLE pull_owner_changes;
-- DROP TABLE pull_browser_sessions;
-- DROP TABLE pull_tokens;
-- DROP TABLE pull_fences;
-- DROP TABLE pull_operations;
-- DROP TABLE pull_flows;
-- DROP TABLE pull_owner_slots;
-- DROP TABLE pull_addresses;
-- DROP TABLE pull_generations;
-- DROP TABLE pull_installs;
-- DROP TABLE pull_version;

CREATE TABLE pull_version (
  slot INTEGER PRIMARY KEY CHECK(slot=1), version INTEGER NOT NULL CHECK(version=1)
);
INSERT INTO pull_version VALUES(1,1);

CREATE TABLE pull_installs (
  install_id TEXT NOT NULL PRIMARY KEY CHECK(length(install_id)=32),
  generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 999999999999999),
  owner_generation INTEGER NOT NULL CHECK(owner_generation BETWEEN 1 AND 999999999999999),
  state TEXT NOT NULL CHECK(state IN ('pending','verified','active','disabled','frozen','deleted')),
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64), owner_address TEXT,
  address_local TEXT NOT NULL UNIQUE CHECK(length(address_local)=30),
  key_id TEXT NOT NULL, public_key TEXT NOT NULL CHECK(length(public_key)=43),
  consent_version TEXT NOT NULL, active_since_ms INTEGER,
  epoch INTEGER NOT NULL DEFAULT 0 CHECK(epoch>=0), connection_id TEXT,
  next_seq INTEGER NOT NULL DEFAULT 1 CHECK(next_seq>0),
  created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
  CHECK((state='deleted' AND owner_address IS NULL) OR
        (state!='deleted' AND owner_address IS NOT NULL))
);
CREATE INDEX pull_install_owner ON pull_installs(owner_hash,state,install_id);

CREATE TABLE pull_generations (
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  generation INTEGER NOT NULL CHECK(generation>0), owner_generation INTEGER NOT NULL CHECK(owner_generation>0),
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64), key_id TEXT NOT NULL,
  public_key TEXT NOT NULL CHECK(length(public_key)=43),
  fence_hash TEXT NOT NULL CHECK(length(fence_hash)=64), flow_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('current','fenced')),
  created_ms INTEGER NOT NULL, fenced_ms INTEGER,
  PRIMARY KEY(install_id,generation), UNIQUE(install_id,generation,flow_id)
);
CREATE UNIQUE INDEX pull_one_current ON pull_generations(install_id) WHERE state='current';

CREATE TABLE pull_addresses (
  address_local TEXT NOT NULL PRIMARY KEY CHECK(length(address_local)=30),
  install_id TEXT REFERENCES pull_installs(install_id),
  state TEXT NOT NULL CHECK(state IN ('reserved','assigned','quarantined')),
  until_ms INTEGER, created_ms INTEGER NOT NULL,
  CHECK((state='assigned' AND until_ms IS NULL) OR (state!='assigned' AND until_ms IS NOT NULL))
);
CREATE INDEX pull_address_expiry ON pull_addresses(state,until_ms,address_local);

CREATE TABLE pull_owner_slots (
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  role TEXT NOT NULL CHECK(role IN ('pending','active','revert')),
  flow_id TEXT NOT NULL, until_ms INTEGER,
  PRIMARY KEY(owner_hash,install_id,role),
  CHECK((role='active' AND until_ms IS NULL) OR (role!='active' AND until_ms IS NOT NULL))
);
CREATE INDEX pull_slot_expiry ON pull_owner_slots(role,until_ms,owner_hash);

CREATE TABLE pull_flows (
  flow_id TEXT NOT NULL PRIMARY KEY CHECK(length(flow_id)=32),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  purpose TEXT NOT NULL CHECK(purpose IN ('signup','owner_change','key_recovery','resume','rotate','delete')),
  expected_generation INTEGER NOT NULL CHECK(expected_generation>0),
  expected_owner_generation INTEGER NOT NULL CHECK(expected_owner_generation>0),
  candidate_key_id TEXT NOT NULL, candidate_public_key TEXT NOT NULL,
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  flow_secret_hash TEXT NOT NULL CHECK(length(flow_secret_hash)=64),
  fence_hash TEXT NOT NULL CHECK(length(fence_hash)=64),
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('pending','verified','approved','completed','expired','cancelled','reverted')),
  consent_version TEXT NOT NULL, manifest_sequence INTEGER NOT NULL,
  manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
  result_json TEXT, created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  CHECK(expires_ms>created_ms)
);
CREATE UNIQUE INDEX pull_one_flow ON pull_flows(install_id,purpose)
  WHERE state IN ('pending','verified','approved');
CREATE INDEX pull_flow_expiry ON pull_flows(state,expires_ms,flow_id);

CREATE TABLE pull_operations (
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  generation INTEGER NOT NULL, path TEXT NOT NULL, op_id TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  result_json TEXT NOT NULL, status INTEGER NOT NULL CHECK(status BETWEEN 200 AND 599),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  PRIMARY KEY(install_id,generation,path,op_id)
);
CREATE INDEX pull_operation_expiry ON pull_operations(expires_ms);

CREATE TABLE pull_fences (
  install_id TEXT NOT NULL, flow_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
  fence_hash TEXT NOT NULL CHECK(length(fence_hash)=64),
  mode TEXT NOT NULL CHECK(mode IN ('cancel','revoke')),
  receipt_id TEXT NOT NULL UNIQUE, op_id TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64), created_ms INTEGER NOT NULL,
  PRIMARY KEY(install_id,flow_id,generation), UNIQUE(install_id,op_id)
);
CREATE INDEX pull_fence_install ON pull_fences(install_id,generation);

CREATE TABLE pull_tokens (
  token_hash TEXT NOT NULL PRIMARY KEY CHECK(length(token_hash)=64),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL, change_id TEXT,
  purpose TEXT NOT NULL CHECK(purpose IN ('signup','stepup','new_owner','revert')),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  binding_digest TEXT NOT NULL CHECK(length(binding_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('pending','consumed','expired','invalidated')),
  result_json TEXT, created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, consumed_ms INTEGER
);
CREATE INDEX pull_token_expiry ON pull_tokens(state,expires_ms,token_hash);

CREATE TABLE pull_browser_sessions (
  session_hash TEXT NOT NULL PRIMARY KEY CHECK(length(session_hash)=64),
  csrf_hash TEXT NOT NULL CHECK(length(csrf_hash)=64),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL, token_hash TEXT REFERENCES pull_tokens(token_hash),
  purpose TEXT NOT NULL CHECK(purpose IN ('confirmation','owner_change','key_recovery','resume','rotate','delete','revert')),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('csrf','approved','consumed','invalidated')),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL
);
CREATE INDEX pull_browser_expiry ON pull_browser_sessions(expires_ms,session_hash);

CREATE TABLE pull_owner_changes (
  change_id TEXT NOT NULL PRIMARY KEY CHECK(length(change_id)=32),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL UNIQUE REFERENCES pull_flows(flow_id),
  old_owner_hash TEXT NOT NULL CHECK(length(old_owner_hash)=64),
  new_owner_hash TEXT NOT NULL CHECK(length(new_owner_hash)=64),
  old_owner_sealed BLOB, new_owner_sealed BLOB,
  expected_generation INTEGER NOT NULL, expected_owner_generation INTEGER NOT NULL,
  switched_generation INTEGER, switched_owner_generation INTEGER,
  state TEXT NOT NULL CHECK(state IN ('pending','completed','reverted','expired','cancelled')),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  completed_ms INTEGER, revert_until_ms INTEGER, reverted_ms INTEGER,
  CHECK(old_owner_hash!=new_owner_hash)
);
CREATE UNIQUE INDEX pull_one_change ON pull_owner_changes(install_id) WHERE state='pending';
CREATE INDEX pull_change_frequency ON pull_owner_changes(install_id,completed_ms);
CREATE INDEX pull_change_expiry ON pull_owner_changes(state,expires_ms,revert_until_ms);

CREATE TABLE pull_control_outbox (
  outbox_id TEXT NOT NULL PRIMARY KEY CHECK(length(outbox_id)=32),
  install_id TEXT NOT NULL REFERENCES pull_installs(install_id),
  flow_id TEXT NOT NULL, change_id TEXT,
  template TEXT NOT NULL CHECK(template IN ('signup','stepup','new_owner','old_notice','revert_old','revert_new')),
  target_hash TEXT NOT NULL CHECK(length(target_hash)=64),
  idempotency_key TEXT NOT NULL UNIQUE, envelope_sealed BLOB,
  seal_key_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('queued','sending','accepted','uncertain','failed','expired')),
  provider_id TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  lease_id TEXT, lease_until_ms INTEGER, next_attempt_ms INTEGER NOT NULL,
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, finished_ms INTEGER,
  CHECK(state NOT IN ('accepted','expired') OR envelope_sealed IS NULL)
);
CREATE INDEX pull_control_due ON pull_control_outbox(state,next_attempt_ms,outbox_id);
CREATE INDEX pull_control_expiry ON pull_control_outbox(expires_ms,outbox_id);

CREATE TABLE pull_nonces (
  direction TEXT NOT NULL, install_id TEXT NOT NULL, generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, nonce TEXT NOT NULL, expires_ms INTEGER NOT NULL,
  PRIMARY KEY(direction,install_id,generation,key_id,nonce)
);
CREATE INDEX pull_nonce_expiry ON pull_nonces(expires_ms);
CREATE TABLE pull_budgets (
  bucket TEXT NOT NULL PRIMARY KEY, count INTEGER NOT NULL CHECK(count>=0), expires_ms INTEGER NOT NULL
);
CREATE INDEX pull_budget_expiry ON pull_budgets(expires_ms,bucket);

CREATE TABLE pull_tickets (
  ticket_hash TEXT NOT NULL PRIMARY KEY CHECK(length(ticket_hash)=64),
  ticket_id TEXT NOT NULL UNIQUE, op_id TEXT NOT NULL,
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('issued','consumed','invalidated')),
  created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, consumed_ms INTEGER,
  FOREIGN KEY(install_id,generation) REFERENCES pull_generations(install_id,generation),
  UNIQUE(install_id,generation,op_id)
);
CREATE INDEX pull_ticket_expiry ON pull_tickets(expires_ms,ticket_hash);

CREATE TABLE pull_intents (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, intent_id TEXT NOT NULL,
  owner_generation INTEGER NOT NULL, owner_hash TEXT NOT NULL CHECK(length(owner_hash)=64),
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64), thread_id TEXT NOT NULL,
  message_id TEXT NOT NULL, provider_id TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK(state IN ('reserved','sending','accepted','uncertain','failed','revoked')),
  created_ms INTEGER NOT NULL, finished_ms INTEGER,
  PRIMARY KEY(install_id,generation,intent_id),
  FOREIGN KEY(install_id,generation) REFERENCES pull_generations(install_id,generation)
);
CREATE TABLE pull_threads (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, thread_id TEXT NOT NULL,
  owner_generation INTEGER NOT NULL, intent_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64), message_id TEXT NOT NULL,
  expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL, revoked_ms INTEGER,
  PRIMARY KEY(install_id,generation,thread_id),
  FOREIGN KEY(install_id,generation,intent_id) REFERENCES pull_intents(install_id,generation,intent_id)
);
CREATE INDEX pull_thread_expiry ON pull_threads(dedupe_until_ms,install_id,generation,thread_id);

CREATE TABLE pull_dedupe (
  event_id TEXT NOT NULL PRIMARY KEY CHECK(length(event_id)=64),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, incoming_hash TEXT NOT NULL CHECK(length(incoming_hash)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64), seq INTEGER NOT NULL,
  disposition TEXT NOT NULL CHECK(disposition IN ('pending','local_received','expired','revoked')),
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,thread_id,incoming_hash), UNIQUE(install_id,seq),
  FOREIGN KEY(install_id,generation,thread_id) REFERENCES pull_threads(install_id,generation,thread_id)
);
CREATE INDEX pull_dedupe_expiry ON pull_dedupe(dedupe_until_ms,event_id);

CREATE TABLE pull_ingress_holds (
  hold_id TEXT NOT NULL PRIMARY KEY CHECK(length(hold_id)=32),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, raw_digest TEXT NOT NULL CHECK(length(raw_digest)=64),
  reason TEXT NOT NULL CHECK(reason='invalid_message_id'),
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,thread_id,raw_digest),
  FOREIGN KEY(install_id,generation,thread_id) REFERENCES pull_threads(install_id,generation,thread_id)
);
CREATE INDEX pull_ingress_hold_expiry ON pull_ingress_holds(expires_ms,hold_id);

CREATE TABLE pull_events (
  event_id TEXT NOT NULL PRIMARY KEY REFERENCES pull_dedupe(event_id),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, seq INTEGER NOT NULL, incoming_message_id TEXT NOT NULL,
  event_kind TEXT NOT NULL CHECK(event_kind IN ('text','held')),
  text TEXT, body_bytes INTEGER NOT NULL CHECK(body_bytes BETWEEN 0 AND 16384),
  auth_json TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK(state IN ('pending','offered','held','local_received','expired','revoked')),
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
  next_attempt_ms INTEGER NOT NULL, lease_id TEXT, lease_until_ms INTEGER,
  terminal_ms INTEGER,
  UNIQUE(install_id,seq),
  CHECK((event_kind='held' AND text IS NULL AND body_bytes=0) OR event_kind='text'),
  CHECK(state NOT IN ('local_received','expired','revoked') OR (text IS NULL AND body_bytes=0))
);
CREATE INDEX pull_event_due ON pull_events(install_id,generation,state,next_attempt_ms,seq);
CREATE INDEX pull_event_expiry ON pull_events(state,expires_ms,event_id);
CREATE INDEX pull_event_terminal ON pull_events(terminal_ms,event_id);

CREATE TABLE pull_offers (
  lease_id TEXT NOT NULL PRIMARY KEY CHECK(length(lease_id)=32),
  event_id TEXT NOT NULL REFERENCES pull_dedupe(event_id),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL,
  offered_ms INTEGER NOT NULL, lease_until_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL
);
CREATE INDEX pull_offer_event ON pull_offers(event_id,offered_ms,lease_id);
CREATE INDEX pull_offer_expiry ON pull_offers(dedupe_until_ms,lease_id);

CREATE TABLE pull_receipts (
  event_id TEXT NOT NULL PRIMARY KEY REFERENCES pull_dedupe(event_id),
  receipt_id TEXT NOT NULL UNIQUE, local_receipt_id TEXT NOT NULL,
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  committed_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,local_receipt_id)
);
CREATE INDEX pull_receipt_expiry ON pull_receipts(dedupe_until_ms,event_id);

CREATE TABLE pull_notifications (
  install_id TEXT NOT NULL PRIMARY KEY REFERENCES pull_installs(install_id),
  revision INTEGER NOT NULL CHECK(revision>0), dirty_from_seq INTEGER NOT NULL,
  next_attempt_ms INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  lease_id TEXT, lease_until_ms INTEGER
);
CREATE INDEX pull_notify_due ON pull_notifications(next_attempt_ms,install_id);
CREATE TABLE pull_sweep_cursors (
  task TEXT NOT NULL PRIMARY KEY CHECK(task IN ('notify','expire','control','metadata')),
  cursor_ms INTEGER NOT NULL DEFAULT 0, cursor_id TEXT NOT NULL DEFAULT '', updated_ms INTEGER NOT NULL
);
CREATE TABLE pull_audit (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, install_id TEXT NOT NULL,
  at_ms INTEGER NOT NULL, code TEXT NOT NULL, related_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX pull_audit_time ON pull_audit(at_ms,audit_id);
CREATE TABLE pull_audit_overflow (
  day_ms INTEGER PRIMARY KEY, count INTEGER NOT NULL CHECK(count>=0)
);
