import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

const migrations = new URL('../migrations/', import.meta.url);
const migration = readFileSync(new URL('0005_pull.sql', migrations), 'utf8');
const tables = [...migration.matchAll(/^CREATE TABLE (pull_\w+)/gm)].map((m) => m[1]);
const I = '1'.repeat(32); const H = 'a'.repeat(64); const OTHER = 'b'.repeat(64);
const NOW = 1_794_249_600_000; const LATER = NOW + 60_000;
const scope = { install_id: I, generation: 1, owner_generation: 1 };
const lifetime = { created_ms: NOW, expires_ms: LATER };

function insert(db, table, row) {
  const columns = Object.keys(row);
  db.prepare(`INSERT INTO ${table} (${columns.join(',')}) VALUES (${columns.map(() => '?').join(',')})`)
    .run(...Object.values(row));
}

function baseline(t) {
  const db = new DatabaseSync(':memory:'); t.after(() => db.close());
  db.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  for (const name of readdirSync(migrations).filter((n) => /^\d+.*\.sql$/.test(n)).sort()) {
    if (name === '0005_pull.sql') break;
    db.exec(readFileSync(new URL(name, migrations), 'utf8'));
  }
  assert.equal(db.prepare('PRAGMA foreign_keys').get().foreign_keys, 1);
  // Legacy rows, including a pending enrollment and cancellation, must not gain
  // pull authority or be affected by migration/rollback.
  insert(db, 'registrations', { install_id: 'legacy', generation: 2, active: 1,
    owner_verified: 1, owner_address: 'legacy@example.com', address_local: 'legacy',
    key_id: 'old-key', public_key: 'old-public-key' });
  insert(db, 'events', { event_id: 'legacy-event', install_id: 'legacy', generation: 2,
    thread_id: 'legacy-thread', incoming_id: 'legacy-mid', digest: H, payload: 'legacy body',
    state: 'pending', received_at: NOW, expires_at: LATER, next_attempt: NOW });
  insert(db, 'mail_enrollment_flows', { flow_id: 'legacy-flow', flow_digest: H,
    install_id: 'legacy', expected_generation: 2, challenge_id: 'legacy-challenge',
    transcript: '[]', r_digest: H, owner_address: 'legacy@example.com', code_digest: H,
    expires_at: LATER, fence_digest: OTHER });
  insert(db, 'mail_lifecycle_fences', { flow_id: 'cancelled-flow', fence_digest: H,
    install_id: 'legacy', generation: 1, created_ms: NOW });
  return db;
}

function legacySnapshot(db) {
  const schema = db.prepare(`SELECT type,name,tbl_name,sql FROM sqlite_schema
    WHERE tbl_name NOT GLOB 'pull_*' ORDER BY type,name`).all();
  const rows = Object.fromEntries(schema.filter((s) => s.type === 'table')
    .map((s) => [s.name, db.prepare(`SELECT * FROM "${s.name}" ORDER BY rowid`).all()]));
  // AUTOINCREMENT uses one shared internal table; pull_audit may add its own
  // sequence row, but the legacy sequences must remain unchanged.
  rows.sqlite_sequence = rows.sqlite_sequence.filter((r) => !r.name.startsWith('pull_'));
  return { schema, rows };
}

function migrated(t) {
  const db = baseline(t); db.exec(migration); return db;
}

function populate(db) {
  const rows = {
    pull_installs: { ...scope, state: 'active', owner_hash: H, owner_address: 'owner@example.com',
      address_local: 'a'.repeat(30), key_id: 'key', public_key: 'a'.repeat(43),
      consent_version: '1', created_ms: NOW, updated_ms: NOW },
    pull_generations: { ...scope, owner_hash: H, key_id: 'key', public_key: 'a'.repeat(43),
      fence_hash: H, flow_id: I, state: 'current', created_ms: NOW },
    pull_addresses: { address_local: 'a'.repeat(30), install_id: I, state: 'assigned', until_ms: null, created_ms: NOW },
    pull_owner_slots: { owner_hash: H, install_id: I, role: 'active', flow_id: I, until_ms: null },
    pull_flows: { flow_id: I, install_id: I, purpose: 'signup', expected_generation: 1,
      expected_owner_generation: 1, candidate_key_id: 'key', candidate_public_key: 'a'.repeat(43),
      owner_hash: H, flow_secret_hash: H, fence_hash: H, input_digest: H, state: 'pending',
      consent_version: '1', manifest_sequence: 1, manifest_digest: H, ...lifetime },
    pull_operations: { install_id: I, generation: 1, path: '/v1/pull/start', op_id: I,
      input_digest: H, result_json: '{}', status: 200, ...lifetime },
    pull_fences: { install_id: I, flow_id: I, generation: 1, fence_hash: H, mode: 'cancel',
      receipt_id: I, op_id: I, input_digest: H, created_ms: NOW },
    pull_tokens: { token_hash: H, ...scope, flow_id: I, purpose: 'signup', owner_hash: H,
      binding_digest: H, state: 'pending', ...lifetime },
    pull_browser_sessions: { session_hash: H, csrf_hash: H, ...scope, flow_id: I,
      token_hash: H, purpose: 'confirmation', state: 'csrf', ...lifetime },
    pull_owner_changes: { change_id: I, install_id: I, flow_id: I, old_owner_hash: H,
      new_owner_hash: OTHER, expected_generation: 1, expected_owner_generation: 1,
      state: 'pending', ...lifetime },
    pull_control_outbox: { outbox_id: I, install_id: I, flow_id: I, template: 'signup',
      target_hash: H, idempotency_key: I, envelope_sealed: new Uint8Array([1]),
      seal_key_id: 'control-key', state: 'queued', next_attempt_ms: NOW, ...lifetime },
    pull_nonces: { direction: 'install-to-pull', install_id: I, generation: 1,
      key_id: 'key', nonce: I, expires_ms: LATER },
    pull_budgets: { bucket: 'global', count: 1, expires_ms: LATER },
    pull_tickets: { ticket_hash: H, ticket_id: I, op_id: I, ...scope,
      key_id: 'key', state: 'issued', ...lifetime },
    pull_intents: { ...scope, intent_id: I, owner_hash: H, input_digest: H,
      thread_id: I, message_id: 'sent@example.com', state: 'accepted', created_ms: NOW },
    pull_threads: { ...scope, thread_id: I, intent_id: I, token_hash: H,
      message_id: 'sent@example.com', expires_ms: LATER, dedupe_until_ms: LATER + 30 * 86400000 },
    pull_dedupe: { event_id: H, ...scope, thread_id: I, incoming_hash: H,
      body_digest: H, event_digest: H, seq: 1, disposition: 'pending', received_ms: NOW,
      expires_ms: LATER, dedupe_until_ms: LATER + 30 * 86400000 },
    pull_ingress_holds: { hold_id: I, ...scope, thread_id: I, raw_digest: H,
      reason: 'invalid_message_id', received_ms: NOW, expires_ms: LATER },
    pull_events: { event_id: H, ...scope, thread_id: I, seq: 1,
      incoming_message_id: 'reply@example.com', event_kind: 'text', text: 'reply', body_bytes: 5,
      auth_json: '{}', state: 'pending', received_ms: NOW, expires_ms: LATER, next_attempt_ms: NOW },
    pull_offers: { lease_id: I, event_id: H, install_id: I, generation: 1, epoch: 1,
      offered_ms: NOW, lease_until_ms: LATER, dedupe_until_ms: LATER + 30 * 86400000 },
    pull_receipts: { event_id: H, receipt_id: I, local_receipt_id: I, ...scope,
      event_digest: H, body_digest: H, committed_ms: NOW, dedupe_until_ms: LATER + 30 * 86400000 },
    pull_notifications: { install_id: I, revision: 1, dirty_from_seq: 1, next_attempt_ms: NOW },
    pull_sweep_cursors: { task: 'notify', updated_ms: NOW },
    pull_audit: { install_id: I, at_ms: NOW, code: 'ok' },
    pull_audit_overflow: { day_ms: NOW, count: 1 },
  };
  for (const [table, row] of Object.entries(rows)) insert(db, table, row);
  return rows;
}

function refuses(db, action, pattern) {
  db.exec('SAVEPOINT refusal');
  try { assert.throws(action, pattern); }
  finally { db.exec('ROLLBACK TO refusal; RELEASE refusal'); }
}

test('0005 copies frozen section 8 exactly and only adds pull objects', () => {
  const contract = readFileSync(new URL('../../../docs/AGENT_MAIL_PULL_CONTRACT.md', import.meta.url), 'utf8');
  const section = contract.split('## 8. NEW D1 schema:')[1].split('### 8.1')[0];
  const sql = section.match(/```sql\r?\n([\s\S]*?)```/)[1].replaceAll('\r\n', '\n');
  const executable = migration.replace(/^--.*(?:\r?\n|$)/gm, '').trim();
  assert.equal(executable.replaceAll('\r\n', '\n'), sql.trim());
  assert.equal(tables.length, 26);
  assert.equal([...executable.matchAll(/^CREATE (?:UNIQUE )?INDEX /gm)].length, 29);
  assert.doesNotMatch(executable, /(?:^|;)\s*(?:ALTER|DROP|UPDATE|DELETE)\b/i);
  assert.deepEqual([...executable.matchAll(/INSERT INTO (\w+)/g)].map((m) => m[1]), ['pull_version']);
});

test('0005 applies after all prior migrations, preserving legacy schema/data without backfill', (t) => {
  const db = baseline(t); const before = legacySnapshot(db);
  db.exec(migration);
  assert.deepEqual(legacySnapshot(db), before);
  assert.deepEqual({ ...db.prepare('SELECT * FROM pull_version').get() }, { slot: 1, version: 1 });
  for (const table of tables.filter((n) => n !== 'pull_version')) {
    assert.equal(db.prepare(`SELECT count(*) AS n FROM ${table}`).get().n, 0, table);
  }
  assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
  assert.equal(db.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
});

test('every pull table accepts a representative row with foreign keys enabled', (t) => {
  const db = migrated(t); populate(db);
  for (const table of tables) assert.equal(db.prepare(`SELECT count(*) AS n FROM ${table}`).get().n, 1, table);
  assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
  assert.equal(db.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
});

test('representative CHECK and NOT NULL constraints refuse invalid rows', async (t) => {
  const db = migrated(t); populate(db);
  const cases = [
    ['pull_version', 'version', 2], ['pull_version', 'slot', 2],
    ['pull_installs', 'install_id', null, /NOT NULL/],
    ['pull_installs', 'install_id', 'short'], ['pull_installs', 'generation', 0],
    ['pull_installs', 'generation', 1000000000000000], ['pull_installs', 'owner_generation', 0],
    ['pull_installs', 'owner_generation', 1000000000000000], ['pull_installs', 'state', 'legacy'],
    ['pull_installs', 'state', 'deleted'], ['pull_installs', 'owner_address', null],
    ['pull_installs', 'owner_hash', 'short'], ['pull_installs', 'public_key', 'short'],
    ['pull_installs', 'address_local', 'short'], ['pull_installs', 'epoch', -1], ['pull_installs', 'next_seq', 0],
    ['pull_generations', 'state', 'active'], ['pull_generations', 'fence_hash', 'short'],
    ['pull_addresses', 'until_ms', LATER], ['pull_addresses', 'state', 'quarantined'],
    ['pull_owner_slots', 'until_ms', LATER], ['pull_owner_slots', 'role', 'revert'],
    ['pull_flows', 'purpose', 'legacy'], ['pull_flows', 'expires_ms', NOW],
    ['pull_operations', 'status', 199], ['pull_operations', 'status', 600],
    ['pull_fences', 'mode', 'activate'], ['pull_tokens', 'purpose', 'send'],
    ['pull_browser_sessions', 'state', 'active'], ['pull_owner_changes', 'new_owner_hash', H],
    ['pull_control_outbox', 'template', 'custom'], ['pull_control_outbox', 'state', 'accepted'],
    ['pull_control_outbox', 'state', 'expired'], ['pull_budgets', 'count', -1],
    ['pull_tickets', 'state', 'active'], ['pull_intents', 'state', 'pending'],
    ['pull_threads', 'token_hash', 'short'], ['pull_dedupe', 'disposition', 'offered'],
    ['pull_ingress_holds', 'reason', 'free_text'], ['pull_events', 'body_bytes', -1],
    ['pull_events', 'body_bytes', 16385], ['pull_events', 'event_kind', 'held'],
    ['pull_events', 'state', 'local_received'], ['pull_events', 'state', 'expired'],
    ['pull_events', 'state', 'revoked'], ['pull_offers', 'lease_id', 'short'],
    ['pull_receipts', 'event_digest', 'short'], ['pull_notifications', 'revision', 0],
    ['pull_sweep_cursors', 'task', 'custom'], ['pull_audit_overflow', 'count', -1],
  ];
  for (const [table, column, value, pattern = /CHECK constraint failed/] of cases) {
    await t.test(`${table}.${column} refuses ${value}`, () => {
      refuses(db, () => db.prepare(`UPDATE ${table} SET ${column}=?`).run(value), pattern);
    });
  }
  db.prepare("UPDATE pull_events SET text=NULL,body_bytes=0,state='local_received'").run();
  db.prepare("UPDATE pull_control_outbox SET envelope_sealed=NULL,state='accepted'").run();
  db.prepare("UPDATE pull_installs SET state='deleted',owner_address=NULL").run();
});

test('partial unique indexes fence current generations, open flows and pending owner changes', (t) => {
  const db = migrated(t); const rows = populate(db);
  refuses(db, () => insert(db, 'pull_generations', { ...rows.pull_generations, generation: 2 }), /UNIQUE/);
  insert(db, 'pull_generations', { ...rows.pull_generations, generation: 2, state: 'fenced' });
  for (const state of ['pending', 'verified', 'approved']) {
    refuses(db, () => insert(db, 'pull_flows', { ...rows.pull_flows, flow_id: '2'.repeat(32), state }), /UNIQUE/);
  }
  insert(db, 'pull_flows', { ...rows.pull_flows, flow_id: '2'.repeat(32), state: 'completed' });
  refuses(db, () => insert(db, 'pull_owner_changes', { ...rows.pull_owner_changes,
    change_id: '2'.repeat(32), flow_id: '2'.repeat(32) }), /UNIQUE/);
  insert(db, 'pull_owner_changes', { ...rows.pull_owner_changes,
    change_id: '2'.repeat(32), flow_id: '2'.repeat(32), state: 'completed' });
});

test('unique identities refuse duplicate replay, sequence, ticket, receipt and outbox claims', (t) => {
  const db = migrated(t); const rows = populate(db);
  const cases = [
    ['pull_dedupe', { event_id: OTHER, seq: 2 }],
    ['pull_dedupe', { event_id: OTHER, incoming_hash: OTHER }],
    ['pull_tickets', { ticket_hash: OTHER, ticket_id: '2'.repeat(32) }],
    ['pull_control_outbox', { outbox_id: '2'.repeat(32) }],
    ['pull_fences', { flow_id: 'other', receipt_id: 'other' }],
    ['pull_nonces', {}], ['pull_operations', {}],
  ];
  for (const [table, changes] of cases) {
    refuses(db, () => insert(db, table, { ...rows[table], ...changes }), /UNIQUE/);
  }
  insert(db, 'pull_dedupe', { ...rows.pull_dedupe, event_id: OTHER, incoming_hash: OTHER, seq: 2 });
  refuses(db, () => insert(db, 'pull_events', { ...rows.pull_events, event_id: OTHER }), /UNIQUE/);
  refuses(db, () => insert(db, 'pull_receipts', { ...rows.pull_receipts, event_id: OTHER, receipt_id: 'other' }), /UNIQUE/);
  refuses(db, () => insert(db, 'pull_receipts', { ...rows.pull_receipts, event_id: OTHER, local_receipt_id: 'other' }), /UNIQUE/);
});

test('foreign keys refuse orphan authority, queue and receipt records; fences allow cancel-before-start', (t) => {
  const db = migrated(t); const rows = populate(db);
  for (const [table, row] of Object.entries(rows)) {
    for (const fk of db.prepare(`PRAGMA foreign_key_list(${table})`).all()) {
      refuses(db, () => db.prepare(`UPDATE ${table} SET ${fk.from}=?`).run(
        fk.from === 'generation' ? 2 : 'c'.repeat(64)), /FOREIGN KEY constraint failed/);
    }
  }
  insert(db, 'pull_fences', { ...rows.pull_fences, install_id: '2'.repeat(32), receipt_id: 'other' });
  refuses(db, () => db.prepare('DELETE FROM pull_installs').run(), /FOREIGN KEY/);
  refuses(db, () => db.prepare('DELETE FROM pull_threads').run(), /FOREIGN KEY/);
});

test('content-free dedupe, offer and ack evidence survives deletion of detailed event', (t) => {
  const db = migrated(t); populate(db);
  db.prepare("UPDATE pull_events SET text=NULL,body_bytes=0,state='local_received',terminal_ms=?").run(NOW);
  db.prepare("UPDATE pull_dedupe SET disposition='local_received'").run();
  db.prepare('DELETE FROM pull_events').run();
  for (const table of ['pull_dedupe', 'pull_offers', 'pull_receipts']) {
    assert.equal(db.prepare(`SELECT count(*) AS n FROM ${table}`).get().n, 1);
    assert.ok(db.prepare(`SELECT dedupe_until_ms FROM ${table}`).get().dedupe_until_ms > LATER);
  }
  refuses(db, () => db.prepare('DELETE FROM pull_dedupe').run(), /FOREIGN KEY/);
});

test('documented rollback drops populated pull tables in FK-safe order and preserves legacy data', (t) => {
  const db = baseline(t); const before = legacySnapshot(db);
  db.exec(migration); populate(db);
  const drops = [...migration.matchAll(/^-- (DROP TABLE (pull_\w+);)\r?$/gm)];
  assert.deepEqual(drops.map((m) => m[2]), [...tables].reverse());
  db.exec(`BEGIN;\n${drops.map((m) => m[1]).join('\n')}\nCOMMIT;`);
  assert.equal(db.prepare("SELECT count(*) AS n FROM sqlite_schema WHERE name LIKE 'pull_%'").get().n, 0);
  assert.deepEqual(legacySnapshot(db), before);
  assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
});
