import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { OwnerChangeHTTP } from '../owner-change.js';
import { OwnerManagementHTTP } from '../owner-management.js';
import { VerificationMail, VERIFICATION_TTL } from '../verification-mail.js';
import { ownerIdentity } from '../owner-registry.js';
import { bytes, sha256, base64url } from '../signing.js';

const NOW = 1_794_249_600_000;
const DAY = 86_400_000;
const WEEK = 7 * DAY;
const ORIGIN = 'https://mail.example.com';
const SECRET = 'e'.repeat(64);
const FENCE = 'f'.repeat(64);
const id = (n) => n.toString(16).padStart(32, '0');
const hash = (s) => sha256(bytes(s));

// SQLite D1 harness mirrors owner-change.test.js: real migrations, constraints,
// crypto and HTTP handlers. Only the application and SQL clocks are replaced.
class D1 {
  constructor() {
    this.sql = new DatabaseSync(':memory:'); this.sql.exec('PRAGMA foreign_keys=ON');
    this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
    const dir = new URL('../migrations/', import.meta.url);
    for (const name of readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()) {
      this.sql.exec(readFileSync(new URL(name, dir), 'utf8'));
    }
  }
  prepare(sql) {
    const db = this.sql;
    return { sql, args: [], bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async run() { return this.runSync(); },
      runSync() { const r = db.prepare(sql).run(...this.args); return { success: true, meta: { changes: Number(r.changes) } }; } };
  }
  async batch(statements) {
    if (this.beforeBatch) await this.beforeBatch(statements);
    this.sql.exec('BEGIN IMMEDIATE');
    try {
      this.lastBatch = statements;
      const result = statements.map((s, n) => { if (this.inBatch) this.inBatch(s, n, statements.length); return s.runSync(); });
      this.sql.exec('COMMIT'); return result;
    } catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}
const rows = (f, table) => f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all();
const one = (f, table) => rows(f, table)[0];
const install = (f) => one(f, 'pull_installs');
const change = (f) => one(f, 'pull_owner_changes');
function snapshot(f) {
  return f.db.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pull_%' ORDER BY name")
    .all().map(({ name }) => [name, rows(f, name)]);
}
async function keypair() {
  const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { key: k.privateKey, publicKey: base64url(await crypto.subtle.exportKey('raw', k.publicKey)),
    privateKey: base64url(await crypto.subtle.exportKey('pkcs8', k.privateKey)) };
}
async function fixture(t, state = 'active', realTime = false) {
  const db = new D1(); t.after(() => db.sql.close());
  const key = await keypair(); const service = await keypair();
  const owner = await ownerIdentity('Old.Owner+tag@EXAMPLE.com');
  const next = await ownerIdentity('New.Owner@example.net');
  const f = { db, key, service, owner, next, time: realTime ? Date.now() : NOW, serial: 100, source: 'edge-source' };
  // Only clock functions are replaced; predicates/transactions remain SQLite.
  if (!realTime) db.sql.function('strftime', (format, at) => {
    assert.equal(at, 'now');
    return format === '%s' ? String(Math.floor(f.time / 1000))
      : `${String(Math.floor(f.time / 1000) % 60).padStart(2, '0')}.${String(f.time % 1000).padStart(3, '0')}`;
  });
  f.mail = new VerificationMail({ db, serviceOrigin: ORIGIN, sealKeyId: 'synthetic', sealKey: new Uint8Array(32).fill(6) });
  f.config = { db, serviceOrigin: ORIGIN, domain: 'clayrune.example', verificationMail: f.mail,
    serviceKeyId: 'service', servicePrivateKey: service.privateKey, clock: () => f.time };
  f.http = new OwnerChangeHTTP(f.config); f.management = new OwnerManagementHTTP(f.config);
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,
    key_id,public_key,consent_version,epoch,connection_id,active_since_ms,created_ms,updated_ms)
    VALUES(?,7,3,?,?,?,?, 'current',?,'v1',4,?, ?,?,?)`).run(id(1), state, owner.owner_hash, owner.owner_address,
    'm' + 'a'.repeat(29), key.publicKey, id(3), f.time - 1000, f.time - 1000, f.time - 1000);
  db.sql.prepare(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,candidate_key_id,
    candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms)
    VALUES(?,?,'signup',7,3,'current',?,?,?,?,?,'completed','v1',8,?,?,?)`).run(id(2), id(1), key.publicKey, owner.owner_hash,
    await hash(SECRET), await hash(FENCE), 'b'.repeat(64), 'c'.repeat(64), f.time - 1000, f.time + VERIFICATION_TTL - 1000);
  db.sql.prepare("INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',?,NULL)")
    .run(id(1), owner.owner_hash, key.publicKey, await hash(FENCE), id(2), f.time - 1000);
  db.sql.prepare("INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)").run('m' + 'a'.repeat(29), id(1), f.time - 1000);
  db.sql.prepare("INSERT INTO pull_owner_slots VALUES(?,?,'active',?,NULL)").run(owner.owner_hash, id(1), id(2));
  return f;
}
async function machine(f, path, value, options = {}) {
  const i = install(f); const raw = JSON.stringify(value);
  const m = { version: '2', direction: 'install-to-pull', key_id: i.key_id, install_id: i.install_id,
    generation: String(i.generation), owner_generation: String(i.owner_generation), timestamp: String(f.time),
    nonce: id(f.serial++), digest: await hash(raw), ...options.fields };
  const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction, ORIGIN, 'POST', path,
    m.key_id, m.install_id, m.generation, m.owner_generation, m.timestamp, m.nonce, m.digest]));
  const headers = new Headers({ 'content-type': 'application/json' });
  for (const [k, v] of Object.entries(m)) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
  headers.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', f.key.key, transcript)));
  return new Request(`${ORIGIN}${path}`, { method: 'POST', headers, body: raw });
}
const run = (f, r) => f.http.handle(r, { trustedSource: f.source });
const management = (f, r) => f.management.handle(r, { trustedSource: f.source });
function post(b, path, fields = {}, options = {}) {
  return new Request(`${options.origin ?? ORIGIN}${path}${options.query ?? ''}`, { method: options.method ?? 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ORIGIN, cookie: b.cookie, ...options.headers },
    body: options.body ?? new URLSearchParams({ csrf: b.csrf, ...fields }).toString() });
}
async function expectStatus(response, status) {
  assert.equal(response.status, status, await response.clone().text()); return response;
}
async function linkToken(f, template, flow = null) {
  const row = rows(f, 'pull_control_outbox').findLast((r) => r.template === template && r.envelope_sealed !== null && (!flow || r.flow_id === flow));
  return (await f.mail.openEnvelope(row)).token;
}
async function browser(f, token, revert = false) {
  const response = await run(f, new Request(`${ORIGIN}/mail/${revert ? 'revert' : 'link'}?token=${token}`));
  const text = await response.clone().text();
  return { response, text, token, cookie: response.headers.get('set-cookie')?.split(';')[0],
    csrf: /name="csrf" value="([a-f0-9]{64})"/.exec(text)?.[1] };
}
async function approved(f, purpose = 'owner_change') {
  const i = install(f); const flow = id(f.serial++);
  const value = { op_id: id(f.serial++), flow_id: flow, purpose, candidate_key_id: i.key_id,
    candidate_public_key: i.public_key, flow_secret_hash: await hash(SECRET), fence_hash: await hash(FENCE) };
  const transcript = bytes(JSON.stringify(['clayrune-mail-pull-candidate', '1', ORIGIN, purpose, flow, i.install_id,
    String(i.generation), String(i.owner_generation), i.key_id, i.public_key, value.fence_hash, value.flow_secret_hash]));
  value.candidate_signature = base64url(await crypto.subtle.sign('Ed25519', f.key.key, transcript));
  await expectStatus(await management(f, await machine(f, '/v1/pull/management/start', value)), 200);
  const token = await linkToken(f, 'stepup', flow);
  const response = await management(f, new Request(`${ORIGIN}/mail/link?token=${token}`)); await expectStatus(response, 200);
  const text = await response.text();
  let b = { token, cookie: response.headers.get('set-cookie').split(';')[0],
    csrf: /name="csrf" value="([a-f0-9]{64})"/.exec(text)[1], flow };
  await expectStatus(await management(f, post(b, '/mail/link/confirm', { token })), 200);
  const m = await management(f, new Request(`${ORIGIN}/mail/manage`, { headers: { cookie: b.cookie } })); await expectStatus(m, 200);
  b.csrf = /name="csrf" value="([a-f0-9]{64})"/.exec(await m.text())[1];
  return b;
}
async function pending(f, b = null) {
  b ??= await approved(f); const op = id(f.serial++);
  await expectStatus(await run(f, post(b, '/mail/owner-change', { op_id: op, new_owner: f.next.owner_address })), 200);
  return { ...await browser(f, await linkToken(f, 'new_owner')), management: b, op };
}

async function completion(t, purpose) {
  const f = await fixture(t, purpose === 'resume' ? 'frozen' : 'active');
  const b = purpose === 'resume' ? await approved(f, purpose) : await pending(f);
  if (purpose === 'owner_change') await expectStatus(b.response, 200);
  const flowId = purpose === 'resume' ? b.flow : b.management.flow;
  const flow = () => rows(f, 'pull_flows').find((r) => r.flow_id === flowId);
  assert.equal(flow().expires_ms, NOW + 30 * 60_000);
  f.time = NOW + 6 * 60_000;
  const request = () => purpose === 'resume'
    ? post(b, '/mail/resume', { op_id: id(55) })
    : post(b, '/mail/link/confirm', { token: b.token });
  return { f, flow, request };
}

for (const purpose of ['resume', 'owner_change']) {
  test(`${purpose}: delayed completion grants exactly 30 minutes for final enable`, async (t) => {
    const { f, flow, request } = await completion(t, purpose);
    await expectStatus(await run(f, request()), 200);
    assert.equal(flow().state, 'completed');
    assert.equal(flow().expires_ms, NOW + 36 * 60_000);
    assert.equal(install(f).state, 'verified');
    assert.equal(JSON.parse(flow().result_json).generation, install(f).generation);
    // Replaying the same receipt later must not refresh the final-enable clock.
    const beforeReplay = snapshot(f);
    f.time += 60_000;
    await expectStatus(await run(f, request()), 200);
    assert.deepEqual(snapshot(f), beforeReplay);
  });

  test(`${purpose}: completion preserves existing session/token/link deadlines`, async (t) => {
    const { f, request } = await completion(t, purpose);
    const deadlines = (table, key) => rows(f, table).map((r) => [r[key], r.expires_ms]);
    const sessions = deadlines('pull_browser_sessions', 'session_hash');
    const tokens = deadlines('pull_tokens', 'token_hash');
    const changes = deadlines('pull_owner_changes', 'change_id');
    await expectStatus(await run(f, request()), 200);
    for (const [table, key, before] of [['pull_browser_sessions', 'session_hash', sessions],
      ['pull_tokens', 'token_hash', tokens], ['pull_owner_changes', 'change_id', changes]]) {
      for (const [identity, expires] of before) {
        assert.equal(rows(f, table).find((r) => r[key] === identity).expires_ms, expires, `${table} deadline`);
      }
    }
    if (purpose === 'owner_change') {
      const changeRow = change(f);
      const revert = rows(f, 'pull_tokens').find((r) => r.purpose === 'revert');
      assert.equal(changeRow.revert_until_ms, f.time + WEEK);
      assert.equal(revert.expires_ms, changeRow.revert_until_ms);
    }
  });

  test(`${purpose}: completion leaves signup's original activation deadline unchanged`, async (t) => {
    const { f, flow, request } = await completion(t, purpose);
    const signup = () => rows(f, 'pull_flows').find((r) => r.purpose === 'signup');
    const before = signup();
    assert.equal(before.expires_ms, NOW + VERIFICATION_TTL - 1000);
    await expectStatus(await run(f, request()), 200);
    assert.deepEqual(signup(), before);
    assert.notEqual(signup().expires_ms, flow().expires_ms);
  });

  test(`${purpose}: final batch failure rolls back completion and its deadline together`, async (t) => {
    const { f, flow, request } = await completion(t, purpose);
    const before = snapshot(f);
    f.db.inBatch = (_statement, position, count) => {
      if (position === count - 1) throw new Error('synthetic last-statement failure');
    };
    await expectStatus(await run(f, request()), 503);
    assert.deepEqual(snapshot(f), before);
    assert.equal(flow().expires_ms, NOW + 30 * 60_000);
  });
}
