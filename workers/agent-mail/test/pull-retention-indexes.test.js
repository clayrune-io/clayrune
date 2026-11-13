import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

const dir = new URL('../migrations/', import.meta.url);
function database(t, through = '0006') {
  const db = new DatabaseSync(':memory:'); t.after(() => db.close());
  db.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  for (const name of readdirSync(dir).filter((n) => /^\d+.*\.sql$/.test(n) && n.slice(0,4) <= through).sort()) {
    db.exec(readFileSync(new URL(name, dir), 'utf8'));
  }
  assert.equal(db.prepare('PRAGMA foreign_keys').get().foreign_keys, 1);
  return db;
}
const plan = (db, sql) => db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all().map((r) => r.detail).join('\n');
const checks = [
  ['token session reference', "SELECT 1 FROM pull_browser_sessions s WHERE s.token_hash='hash' LIMIT 1", 's', 'pull_browser_token'],
  ['change live envelope', "SELECT 1 FROM pull_control_outbox o WHERE o.change_id='change' AND o.expires_ms>0 AND o.envelope_sealed IS NOT NULL LIMIT 1", 'o', 'pull_control_change'],
  ['flow replay horizon', "SELECT 1 FROM pull_flows f WHERE f.install_id='install' AND f.expected_generation=1 AND f.expires_ms>0 LIMIT 1", 'f', 'pull_flow_generation_expiry'],
  ['generation thread horizon', "SELECT 1 FROM pull_threads t WHERE t.install_id='install' AND t.generation=1 AND t.dedupe_until_ms>0 LIMIT 1", 't', 'pull_thread_generation_replay'],
  ['intent thread reference', "SELECT 1 FROM pull_threads t WHERE t.install_id='install' AND t.generation=1 AND t.intent_id='intent' LIMIT 1", 't', 'pull_thread_intent'],
  ['change recovery record', "SELECT 1 FROM pull_owner_changes c WHERE c.change_id='change' AND (c.old_owner_sealed IS NOT NULL OR c.new_owner_sealed IS NOT NULL) LIMIT 1", 'c', 'sqlite_autoindex_pull_owner_changes_1'],
  ['token revert horizon', "SELECT 1 FROM pull_owner_changes c WHERE c.change_id='change' AND c.state='completed' AND c.revert_until_ms>0 LIMIT 1", 'c', 'sqlite_autoindex_pull_owner_changes_1'],
  ['install deletion state', "SELECT 1 FROM pull_installs i WHERE i.install_id='install' AND i.state='deleted' LIMIT 1", 'i', 'sqlite_autoindex_pull_installs_1'],
  ['receipt parent horizon', "SELECT 1 FROM pull_dedupe d WHERE d.event_id='event' AND d.dedupe_until_ms>0 LIMIT 1", 'd', 'sqlite_autoindex_pull_dedupe_1'],
  ['event child', "SELECT 1 FROM pull_events WHERE event_id='event' LIMIT 1", 'pull_events', 'sqlite_autoindex_pull_events_1'],
  ['offer child', "SELECT 1 FROM pull_offers WHERE event_id='event' LIMIT 1", 'pull_offers', 'pull_offer_event'],
  ['receipt child', "SELECT 1 FROM pull_receipts WHERE event_id='event' LIMIT 1", 'pull_receipts', 'sqlite_autoindex_pull_receipts_1'],
  ['dedupe thread reference', "SELECT 1 FROM pull_dedupe d WHERE d.install_id='install' AND d.generation=1 AND d.thread_id='thread' LIMIT 1", 'd', 'sqlite_autoindex_pull_dedupe_2'],
  ['hold thread reference', "SELECT 1 FROM pull_ingress_holds h WHERE h.install_id='install' AND h.generation=1 AND h.thread_id='thread' LIMIT 1", 'h', 'sqlite_autoindex_pull_ingress_holds_2'],
];
for (const [label, sql, alias, index] of checks) {
  test(`retention reference uses an index: ${label}`, (t) => {
    const p = plan(database(t), sql);
    assert.match(p, new RegExp(`SEARCH ${alias} USING (?:COVERING )?INDEX ${index}\\b`), p);
    assert.doesNotMatch(p, /\bSCAN\b/, p);
  });
}
test('generation flow join searches both primary keys', (t) => {
  const p = plan(database(t), `SELECT 1 FROM pull_generations g JOIN pull_flows f ON f.flow_id=g.flow_id
    WHERE g.install_id='install' AND g.generation=1 AND f.expires_ms>0 LIMIT 1`);
  for (const alias of ['g','f']) assert.match(p, new RegExp(`SEARCH ${alias} USING INDEX`), p);
  assert.doesNotMatch(p, /\bSCAN\b/, p);
});
for (const [table, key] of [['pull_tokens','token_hash'], ['pull_dedupe','event_id'],
  ['pull_threads','thread_id'], ['pull_intents','intent_id']]) {
  test(`retention FK checks search indexes: DELETE ${table}`, (t) => {
    const where = table === 'pull_threads' || table === 'pull_intents' ? "install_id='install' AND generation=1 AND " : '';
    const p = plan(database(t), `DELETE FROM ${table} WHERE ${where}${key}='id'`);
    assert.match(p, /SEARCH .* USING (?:COVERING )?INDEX/, p);
    assert.doesNotMatch(p, /\bSCAN\b/, p);
  });
}
test('0006 is additive, preserves rows/schema and can be rolled back by dropping its indexes', (t) => {
  const db = database(t, '0005');
  db.exec("INSERT INTO pull_budgets VALUES('kept',9,123)");
  const schema = () => db.prepare("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").all();
  const before = schema();
  const sql = readFileSync(new URL('0006_pull_retention_indexes.sql', dir), 'utf8');
  const indexes = [...sql.matchAll(/^CREATE INDEX (\w+)/gm)].map((m) => m[1]);
  assert.equal(indexes.length, 5);
  db.exec(sql);
  assert.deepEqual(db.prepare('SELECT * FROM pull_budgets').all().map((r) => ({...r})), [{bucket:'kept',count:9,expires_ms:123}]);
  for (const index of indexes) db.exec(`DROP INDEX ${index}`);
  assert.deepEqual(schema(), before);
});
