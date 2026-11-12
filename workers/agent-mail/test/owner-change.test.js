import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { OwnerChangeHTTP } from '../owner-change.js';
import { OwnerManagementHTTP } from '../owner-management.js';
import { VerificationMail, VERIFICATION_TTL } from '../verification-mail.js';
import { SessionAuth, PULL_PROTOCOL } from '../session-auth.js';
import { ownerIdentity } from '../owner-registry.js';
import { bytes, sha256, base64url } from '../signing.js';

const NOW = 1_794_249_600_000;
const DAY = 86_400_000;
const WEEK = 7 * DAY;
const TTL = 600_000;
const ORIGIN = 'https://mail.example.com';
const SECRET = 'e'.repeat(64);
const FENCE = 'f'.repeat(64);
const id = (n) => n.toString(16).padStart(32, '0');
const hash = (s) => sha256(bytes(s));

// All migrations, constraints, crypto and SQL run for real. Barrier/fault
// interleavings use one local SQLite connection, not independent D1 writers.
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
  f.http = new OwnerChangeHTTP(f.config); f.management = new OwnerManagementHTTP(f.config); f.auth = new SessionAuth(f.config);
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
async function switched(f) {
  const b = await pending(f); await expectStatus(b.response, 200);
  await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token })), 200);
  return b;
}
async function reverted(f) {
  await switched(f); const b = await browser(f, await linkToken(f, 'old_notice'), true); await expectStatus(b.response, 200);
  await expectStatus(await run(f, post(b, '/mail/revert', { token: b.token })), 200); return b;
}
function revoke(f, deleted = false) {
  const i = install(f);
  f.db.sql.prepare('INSERT INTO pull_fences VALUES(?,?,?,?,?,?,?,?,?)').run(i.install_id, id(f.serial++), i.generation,
    'a'.repeat(64), 'revoke', id(f.serial++), id(f.serial++), 'b'.repeat(64), f.time);
  f.db.sql.prepare("UPDATE pull_generations SET state='fenced',fenced_ms=? WHERE install_id=? AND generation=?").run(f.time, i.install_id, i.generation);
  f.db.sql.prepare(`UPDATE pull_installs SET state=?,owner_address=CASE WHEN ? THEN NULL ELSE owner_address END,
    epoch=epoch+1,connection_id=NULL WHERE install_id=?`).run(deleted ? 'deleted' : 'disabled', Number(deleted), i.install_id);
  f.db.sql.exec("UPDATE pull_flows SET state='cancelled' WHERE state IN ('pending','verified','approved'); UPDATE pull_tokens SET state='invalidated' WHERE purpose!='revert'; UPDATE pull_browser_sessions SET state='invalidated'; UPDATE pull_tickets SET state='invalidated'");
  if (deleted) f.db.sql.exec("UPDATE pull_owner_changes SET new_owner_sealed=NULL; UPDATE pull_control_outbox SET envelope_sealed=NULL; DELETE FROM pull_owner_slots WHERE role!='revert'; UPDATE pull_addresses SET state='quarantined',until_ms=9999999999999");
}
function rotate(f) {
  const i = install(f);
  f.db.sql.prepare("UPDATE pull_generations SET state='fenced',fenced_ms=? WHERE install_id=?").run(f.time, i.install_id);
  f.db.sql.prepare("UPDATE pull_installs SET generation=generation+1,state='verified',epoch=epoch+1,connection_id=NULL WHERE install_id=?").run(i.install_id);
  f.db.sql.prepare("INSERT INTO pull_generations VALUES(?,?,?,?,?,?,?,?, 'current',?,NULL)").run(i.install_id,
    i.generation + 1, i.owner_generation, i.owner_hash, i.key_id, i.public_key, 'd'.repeat(64), id(2), f.time);
}
async function mint(f) {
  const response = await f.auth.handle(await machine(f, '/v1/pull/ticket', { op_id: id(f.serial++) }), { trustedSource: f.source });
  await expectStatus(response, 200); return (await response.json()).ticket;
}
const socket = (token) => new Request(`${ORIGIN}/v1/pull/socket`, { headers: { authorization: `MailTicket ${token}`,
  upgrade: 'websocket', 'sec-websocket-protocol': PULL_PROTOCOL } });
function seedQueue(f) {
  const i = install(f); const g = i.generation; const og = i.owner_generation; const n = id(f.serial++); const h = '8'.repeat(64);
  f.db.sql.prepare("INSERT INTO pull_intents VALUES(?,?,?,?,?,?,?,?,'','reserved',?,NULL)")
    .run(i.install_id, g, n, og, i.owner_hash, h, n, '<old@example.com>', f.time);
  f.db.sql.prepare('INSERT INTO pull_threads VALUES(?,?,?,?,?,?,?,?,?,NULL)')
    .run(i.install_id, g, n, og, n, h, '<old@example.com>', f.time + 90 * DAY, f.time + 120 * DAY);
  f.db.sql.prepare("INSERT INTO pull_dedupe VALUES(?,?,?,?,?,?,?,?,1,'pending',?,?,?)")
    .run(h, i.install_id, g, og, n, h, h, h, f.time, f.time + DAY, f.time + 120 * DAY);
  f.db.sql.prepare(`INSERT INTO pull_events(event_id,install_id,generation,owner_generation,thread_id,seq,incoming_message_id,
    event_kind,text,body_bytes,auth_json,state,received_ms,expires_ms,next_attempt_ms,lease_id,lease_until_ms)
    VALUES(?,?,?,?,?,1,'<reply@example.com>','text','private reply',13,'{}','offered',?,?,?,?,?)`)
    .run(h, i.install_id, g, og, n, f.time, f.time + DAY, f.time, id(90), f.time + 60_000);
  f.db.sql.prepare('INSERT INTO pull_notifications VALUES(?,1,1,?,0,NULL,NULL)').run(i.install_id, f.time);
}
function retired(f, oldG) {
  for (const name of ['pull_flows', 'pull_tokens', 'pull_browser_sessions', 'pull_tickets']) {
    const scope = name === 'pull_flows' ? 'expected_generation' : 'generation';
    for (const row of rows(f, name).filter((r) => r[scope] <= oldG)) {
      if (name === 'pull_flows') assert.ok(!['pending', 'approved', 'verified'].includes(row.state));
      else if (name === 'pull_tokens' && row.purpose === 'revert') continue;
      else assert.equal(row.state, 'invalidated', `${name} stale authority`);
    }
  }
}

test('pending keeps current owner/privileges, seals addresses and binds exact 30-minute token/candidate', async (t) => {
  const f = await fixture(t); const before = install(f); const b = await pending(f);
  await expectStatus(b.response, 200); assert.deepEqual(install(f), before);
  const c = change(f); const token = rows(f, 'pull_tokens').find((r) => r.purpose === 'new_owner');
  assert.equal(c.expires_ms - c.created_ms, VERIFICATION_TTL); assert.equal(c.old_owner_hash, f.owner.owner_hash);
  assert.equal(c.new_owner_hash, f.next.owner_hash); assert.equal(token.change_id, c.change_id);
  assert.equal(token.generation, 7); assert.equal(token.owner_generation, 3); assert.equal(token.expires_ms, c.expires_ms);
  assert.equal(Buffer.from(c.old_owner_sealed).includes(Buffer.from(f.owner.owner_address)), false);
  assert.equal(Buffer.from(c.new_owner_sealed).includes(Buffer.from(f.next.owner_address)), false);
  assert.equal(rows(f, 'pull_owner_slots').length, 2);
  assert.match(b.response.headers.get('set-cookie'), /Secure; HttpOnly; SameSite=Strict/);
  assert.match(b.text, /\*\*\*@example.net/); assert.doesNotMatch(b.text, /New.Owner|Old.Owner/);
  assert.equal(rows(f, 'pull_control_outbox').length, 2);
});

test('session expiry after initiation does not cancel the fresh new-owner confirmation window', async (t) => {
  const f = await fixture(t); const b = await approved(f); const oldEnd = rows(f, 'pull_browser_sessions').find((s) => s.purpose === 'owner_change').expires_ms;
  f.time += 500_000; await pending(f, b); f.time = oldEnd + 1;
  const confirmation = await browser(f, await linkToken(f, 'new_owner')); await expectStatus(confirmation.response, 200);
  await expectStatus(await run(f, post(confirmation, '/mail/link/confirm', { token: confirmation.token })), 200);
  assert.equal(install(f).state, 'verified');
});

test('switch consumes once with exactly one notice and retires tickets, sessions, flows, threads and queue atomically', async (t) => {
  const f = await fixture(t); seedQueue(f); const ticket = await mint(f); const b = await pending(f); const old = install(f);
  // An unrelated, already-approved management purpose must also be retired.
  await approved(f, 'rotate');
  await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token })), 200);
  const i = install(f); const c = change(f); assert.equal(i.generation, 8); assert.equal(i.owner_generation, 4);
  assert.equal(i.owner_address, f.next.owner_address); assert.equal(i.state, 'verified'); assert.equal(i.active_since_ms, null);
  assert.equal(i.epoch, old.epoch + 1); assert.equal(i.connection_id, null); assert.equal(c.new_owner_sealed, null);
  assert.equal(c.revert_until_ms, f.time + WEEK); assert.equal(c.completed_ms, f.time);
  assert.equal(rows(f, 'pull_control_outbox').filter((r) => r.template === 'old_notice').length, 1);
  assert.equal(rows(f, 'pull_tokens').find((r) => r.purpose === 'revert').expires_ms, f.time + WEEK);
  assert.equal(rows(f, 'pull_owner_slots').find((r) => r.owner_hash === f.owner.owner_hash).role, 'revert');
  assert.equal(one(f, 'pull_threads').generation, 7); assert.equal(one(f, 'pull_threads').revoked_ms, f.time);
  assert.equal(one(f, 'pull_events').state, 'revoked'); assert.equal(one(f, 'pull_events').text, null); assert.equal(one(f, 'pull_events').body_bytes, 0);
  assert.equal(one(f, 'pull_dedupe').disposition, 'revoked'); assert.equal(rows(f, 'pull_notifications').length, 0); retired(f, 7);
  await assert.rejects(f.auth.admitSocket(socket(ticket), { trustedSource: f.source }), /ticket_invalid/);
  const before = snapshot(f); await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token })), 200);
  assert.deepEqual(snapshot(f), before);
});

test('new-owner verification is insufficient without exact cookie/CSRF POST; scanner GET is inert', async (t) => {
  const f = await fixture(t); const b = await pending(f); const before = install(f); const tokens = rows(f, 'pull_tokens');
  for (let n = 0; n < 3; n++) await expectStatus((await browser(f, b.token)).response, 200);
  assert.deepEqual(install(f), before); assert.deepEqual(rows(f, 'pull_tokens'), tokens);
  for (const options of [{ headers: { origin: 'https://evil.example' } }, { headers: { cookie: '' } },
    { body: `token=${b.token}&csrf=${SECRET}` }]) {
    await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token }, options)), 401);
  }
  assert.deepEqual(install(f), before);
});

test('switch is counted at confirmation and replayed begin never charges/reissues or extends deadlines', async (t) => {
  const f = await fixture(t); const b = await pending(f); const before = snapshot(f); f.time += 50_000;
  await expectStatus(await run(f, post(b.management, '/mail/owner-change', { op_id: b.op, new_owner: 'New.Owner@EXAMPLE.NET' })), 200);
  assert.deepEqual(snapshot(f), before);
  await expectStatus(await run(f, post(b.management, '/mail/owner-change', { op_id: b.op, new_owner: 'other@example.net' })), 409);
  assert.equal(change(f).completed_ms, null);
});

test('revert restores old owner under fresh high-water g/og, freezes permanently and emits both fixed notices', async (t) => {
  const f = await fixture(t); await switched(f); f.db.sql.exec("UPDATE pull_installs SET state='active'");
  seedQueue(f); const ticket = await mint(f); const b = await browser(f, await linkToken(f, 'old_notice'), true);
  const before = install(f); await expectStatus(b.response, 200);
  await expectStatus(await run(f, post(b, '/mail/revert', { token: b.token })), 200);
  assert.equal(install(f).generation, 9); assert.equal(install(f).owner_generation, 5);
  assert.equal(install(f).owner_address, f.owner.owner_address); assert.equal(install(f).state, 'frozen');
  assert.equal(install(f).epoch, before.epoch + 1); retired(f, 8);
  assert.equal(one(f, 'pull_threads').generation, 8); assert.equal(one(f, 'pull_threads').revoked_ms, f.time);
  assert.equal(one(f, 'pull_events').text, null); assert.equal(change(f).state, 'reverted');
  assert.deepEqual(rows(f, 'pull_control_outbox').filter((r) => r.template.startsWith('revert_')).map((r) => r.template).sort(), ['revert_new', 'revert_old']);
  assert.equal(rows(f, 'pull_owner_slots').length, 1); assert.equal(one(f, 'pull_owner_slots').owner_hash, f.owner.owner_hash);
  await assert.rejects(f.auth.admitSocket(socket(ticket), { trustedSource: f.source }), /ticket_invalid/);
  const snapshotBefore = snapshot(f); await expectStatus(await run(f, post(b, '/mail/revert', { token: b.token })), 200);
  assert.deepEqual(snapshot(f), snapshotBefore); f.time += 10 * DAY;
  assert.equal(install(f).state, 'frozen');
  const response = await f.auth.handle(await machine(f, '/v1/pull/ticket', { op_id: id(f.serial++) }), { trustedSource: f.source });
  assert.equal(response.status, 401);
});

for (const lifecycle of ['rotation', 'revoke', 'deletion']) test(`open revert survives ${lifecycle} without restoring old message content`, async (t) => {
  const f = await fixture(t); await switched(f); const token = await linkToken(f, 'old_notice');
  if (lifecycle === 'rotation') { rotate(f); rotate(f); }
  else revoke(f, lifecycle === 'deletion');
  const before = install(f); const b = await browser(f, token, true); await expectStatus(b.response, 200);
  await expectStatus(await run(f, post(b, '/mail/revert', { token })), 200);
  const i = install(f); assert.equal(i.generation, before.generation + 1); assert.equal(i.owner_generation, before.owner_generation + 1);
  assert.equal(i.state, 'frozen'); assert.equal(i.owner_hash, f.owner.owner_hash); assert.equal(one(f, 'pull_addresses').state, 'assigned');
  const oldNotice = rows(f, 'pull_control_outbox').find((r) => r.template === 'revert_old'); assert.equal(oldNotice.state, 'queued');
  const newNotice = rows(f, 'pull_control_outbox').find((r) => r.template === 'revert_new');
  if (lifecycle === 'deletion') {
    assert.equal(newNotice.state, 'failed'); assert.equal(newNotice.envelope_sealed, null); assert.equal(newNotice.target_hash, f.next.owner_hash);
    assert.equal(newNotice.next_attempt_ms, newNotice.expires_ms); assert.equal(newNotice.attempts, 0);
    assert.equal(one(f, 'pull_audit').code, 'recipient_deleted'); assert.equal(one(f, 'pull_audit').related_id, newNotice.outbox_id);
    assert.equal(JSON.stringify(snapshot(f)).includes(f.next.owner_address), false);
    await assert.rejects(f.mail.message(newNotice.outbox_id, f.time), /invalid_state/);
    const beforeRetry = snapshot(f); await expectStatus(await run(f, post(b, '/mail/revert', { token })), 200); assert.deepEqual(snapshot(f), beforeRetry);
  } else assert.equal(newNotice.state, 'queued');
});

for (const state of ['frozen', 'disabled']) test(`fresh ${state} resume creates current unfenced g+1, keeps og and requires explicit enable`, async (t) => {
  const f = await fixture(t, state === 'disabled' ? 'active' : state);
  if (state === 'disabled') { revoke(f); f.time++; }
  const b = await approved(f, 'resume'); const before = install(f); const op = id(f.serial++);
  await expectStatus(await run(f, post(b, '/mail/resume', { op_id: op })), 200);
  assert.equal(install(f).state, 'verified'); assert.equal(install(f).generation, before.generation + 1);
  assert.equal(install(f).owner_generation, before.owner_generation); assert.equal(install(f).active_since_ms, null);
  assert.equal(rows(f, 'pull_generations').findLast((r) => r.state === 'current').generation, before.generation + 1); retired(f, before.generation);
  assert.equal(rows(f, 'pull_fences').filter((r) => r.generation === before.generation + 1).length, 0);
  const snapshotBefore = snapshot(f); await expectStatus(await run(f, post(b, '/mail/resume', { op_id: op })), 200);
  assert.deepEqual(snapshot(f), snapshotBefore);
  await expectStatus(await run(f, post(b, '/mail/resume', { op_id: id(f.serial++) })), 401);
  const refused = await f.auth.handle(await machine(f, '/v1/pull/ticket', { op_id: id(f.serial++) }), { trustedSource: f.source });
  assert.equal(refused.status, 401);
  // Stand in only for later unit's explicit local human enable; session auth
  // then accepts the actual generation produced by this resume transaction.
  f.db.sql.exec("UPDATE pull_installs SET state='active'"); const ticket = await mint(f);
  const attachment = await f.auth.admitSocket(socket(ticket), { trustedSource: f.source }); assert.equal(attachment.generation, before.generation + 1);
});

test('fresh resumed freeze never drains old queue or changes its thread generation', async (t) => {
  const f = await fixture(t); seedQueue(f); await reverted(f); f.time += DAY;
  const b = await approved(f, 'resume'); await expectStatus(await run(f, post(b, '/mail/resume', { op_id: id(f.serial++) })), 200);
  assert.equal(one(f, 'pull_threads').generation, 7); assert.equal(one(f, 'pull_events').state, 'revoked');
  assert.equal(one(f, 'pull_events').text, null); assert.equal(install(f).state, 'verified');
});

test('old-owner revert slot is reserved at cap, so valid security revert never needs new admission', async (t) => {
  const f = await fixture(t); await switched(f);
  for (let n = 10; n < 12; n++) addSlot(f, f.owner.owner_hash, n);
  const b = await browser(f, await linkToken(f, 'old_notice'), true);
  await expectStatus(await run(f, post(b, '/mail/revert', { token: b.token })), 200);
  assert.equal(rows(f, 'pull_owner_slots').filter((r) => r.owner_hash === f.owner.owner_hash && r.role === 'active').length, 3);
});
function addSlot(f, owner, n) {
  f.db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,key_id,public_key,
    consent_version,created_ms,updated_ms) VALUES(?,1,1,'active',?,'synthetic@example.com',?,'key',?,'v1',?,?)`)
    .run(id(n), owner, 'm' + id(n).slice(3), f.key.publicKey, f.time, f.time);
  f.db.sql.prepare("INSERT INTO pull_owner_slots VALUES(?,?,'active',?,NULL)").run(owner, id(n), id(n + 20));
}

test('same canonical mailbox and three-install cap refuse before reservation/mail', async (t) => {
  const f = await fixture(t); const b = await approved(f); const before = snapshot(f);
  let r = await run(f, post(b, '/mail/owner-change', { op_id: id(40), new_owner: 'Old.Owner+tag@EXAMPLE.COM' }));
  await expectStatus(r, 409); assert.match(await r.text(), /same_owner/); assert.deepEqual(snapshot(f), before);
  for (let n = 10; n < 13; n++) addSlot(f, f.next.owner_hash, n);
  const atCap = snapshot(f); r = await run(f, post(b, '/mail/owner-change', { op_id: id(41), new_owner: f.next.owner_address }));
  await expectStatus(r, 409); assert.match(await r.text(), /owner_limit/); assert.deepEqual(snapshot(f), atCap);
});

function history(f, n, completed, state = 'completed', until = completed + WEEK) {
  const flow = id(2000 + n); const c = id(3000 + n);
  f.db.sql.prepare(`INSERT INTO pull_flows SELECT ?,install_id,'owner_change',expected_generation,expected_owner_generation,candidate_key_id,
    candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,'completed',consent_version,manifest_sequence,manifest_digest,
    NULL,created_ms,expires_ms FROM pull_flows WHERE flow_id=?`).run(flow, id(2));
  f.db.sql.prepare(`INSERT INTO pull_owner_changes(change_id,install_id,flow_id,old_owner_hash,new_owner_hash,expected_generation,
    expected_owner_generation,state,created_ms,expires_ms,completed_ms,revert_until_ms) VALUES(?,?,?,?,?,1,1,?,?,?,?,?)`)
    .run(c, id(1), flow, f.owner.owner_hash, f.next.owner_hash, state, completed - 1000, completed + 1000, completed, until);
}
for (const offset of [-1, 0, 1]) test(`seven-day change frequency boundary offset ${offset}ms`, async (t) => {
  const f = await fixture(t); history(f, 1, f.time - WEEK - offset); const b = await approved(f);
  const r = await run(f, post(b, '/mail/owner-change', { op_id: id(50), new_owner: f.next.owner_address }));
  await expectStatus(r, offset < 0 ? 409 : 200);
});
for (const offset of [-1, 0, 1]) test(`365-day completed-switch history boundary offset ${offset}ms includes reverted changes`, async (t) => {
  const f = await fixture(t);
  for (let n = 0; n < 5; n++) history(f, n, f.time - (n === 0 ? 365 * DAY + offset : (n + 1) * 10 * DAY), n === 2 ? 'reverted' : 'completed');
  const b = await approved(f); const r = await run(f, post(b, '/mail/owner-change', { op_id: id(50), new_owner: f.next.owner_address }));
  await expectStatus(r, offset < 0 ? 409 : 200);
});
test('no second normal change while any original revert is open even if frequency history is older', async (t) => {
  const f = await fixture(t); history(f, 1, f.time - 8 * DAY, 'completed', f.time + 1); const b = await approved(f);
  await expectStatus(await run(f, post(b, '/mail/owner-change', { op_id: id(50), new_owner: f.next.owner_address })), 409);
});
for (const offset of [-1, 0, 1]) test(`revert lifetime boundary offset ${offset}ms`, async (t) => {
  const f = await fixture(t); await switched(f); const token = await linkToken(f, 'old_notice');
  f.time = change(f).revert_until_ms + offset; const b = await browser(f, token, true);
  await expectStatus(b.response, offset < 0 ? 200 : 410);
  if (offset < 0) await expectStatus(await run(f, post(b, '/mail/revert', { token })), 200);
  else assert.equal(install(f).owner_hash, f.next.owner_hash);
});
for (const offset of [-1, 0, 1]) test(`new-owner verification lifetime offset ${offset}ms`, async (t) => {
  const f = await fixture(t); const original = await pending(f); f.time = change(f).expires_ms + offset;
  const b = await browser(f, original.token); await expectStatus(b.response, offset < 0 ? 200 : 410);
  if (offset < 0) await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token })), 200);
});

test('switch and revert notice intents commit even when global send budget is exhausted', async (t) => {
  const f = await fixture(t); const b = await pending(f);
  f.db.sql.prepare("UPDATE pull_budgets SET count=1000 WHERE bucket LIKE 'pull:send:global:%'").run();
  await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token })), 200);
  const r = await browser(f, await linkToken(f, 'old_notice'), true);
  await expectStatus(await run(f, post(r, '/mail/revert', { token: r.token })), 200);
  assert.equal(rows(f, 'pull_control_outbox').filter((x) => ['old_notice', 'revert_old', 'revert_new'].includes(x.template)).length, 3);
  assert.equal(rows(f, 'pull_budgets').find((x) => x.bucket.startsWith('pull:send:global:')).count, 1000);
});

test('pending cancellation releases only proposed slot and invalidates new-owner links without owner changes', async (t) => {
  const f = await fixture(t); const b = await pending(f); const before = install(f);
  await expectStatus(await run(f, post(b.management, '/mail/change/cancel', { change_id: change(f).change_id })), 200);
  assert.equal(change(f).state, 'cancelled'); assert.equal(change(f).old_owner_sealed, null); assert.equal(change(f).new_owner_sealed, null);
  assert.equal(rows(f, 'pull_owner_slots').length, 1); assert.deepEqual(install(f), before);
  assert.notEqual((await run(f, post(b, '/mail/link/confirm', { token: b.token }))).status, 200);
  const cancelled = snapshot(f);
  await expectStatus(await run(f, post(b.management, '/mail/change/cancel', { change_id: change(f).change_id })), 200);
  assert.deepEqual(snapshot(f), cancelled);
  // Cancellation frees the open-purpose slot; fresh mailbox proof is required.
  const fresh = await approved(f); assert.notEqual(fresh.flow, b.management.flow);
});

test('old change cannot target a later owner generation, while used links only replay original fixed receipt', async (t) => {
  const f = await fixture(t); const original = await switched(f); const token = await linkToken(f, 'old_notice');
  const b = await browser(f, token, true);
  f.db.sql.exec('UPDATE pull_installs SET owner_generation=owner_generation+1');
  await expectStatus(await run(f, post(b, '/mail/revert', { token })), 409);
  const before = snapshot(f); await expectStatus(await run(f, post(original, '/mail/link/confirm', { token: original.token })), 200);
  assert.deepEqual(snapshot(f), before);
});

for (const stage of ['begin', 'confirm', 'revert', 'resume']) test(`${stage} prepared concurrently has one winner, no second notice or generation`, async (t) => {
  const f = await fixture(t, stage === 'resume' ? 'frozen' : 'active'); let b; let path; let fields;
  if (stage === 'begin') { b = await approved(f); path = '/mail/owner-change'; fields = { op_id: id(55), new_owner: f.next.owner_address }; }
  if (stage === 'confirm') { b = await pending(f); path = '/mail/link/confirm'; fields = { token: b.token }; }
  if (stage === 'revert') { await switched(f); b = await browser(f, await linkToken(f, 'old_notice'), true); path = '/mail/revert'; fields = { token: b.token }; }
  if (stage === 'resume') { b = await approved(f, 'resume'); path = '/mail/resume'; fields = { op_id: id(55) }; }
  let release; let reached; let first = true;
  const waiting = new Promise((r) => { release = r; }); const ready = new Promise((r) => { reached = r; });
  f.db.beforeBatch = async () => { if (first) { first = false; reached(); await waiting; } };
  const a = run(f, post(b, path, fields)); await ready;
  const winner = await run(f, post(b, path, fields)); release(); const loser = await a;
  await expectStatus(winner, 200); await expectStatus(loser, 503);
  const before = snapshot(f); await expectStatus(await run(f, post(b, path, fields)), 200); assert.deepEqual(snapshot(f), before);
});

// Enumerate every statement dynamically rather than checking only a final
// failure: a zero-row guard or partial consume must never become success.
async function prepared(t, stage) {
  const f = await fixture(t, stage === 'resume' ? 'frozen' : 'active'); let b; let request;
  if (stage === 'begin') { b = await approved(f); request = () => post(b, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address }); }
  if (stage === 'get') { await pending(f); const token = await linkToken(f, 'new_owner'); request = () => new Request(`${ORIGIN}/mail/link?token=${token}`); }
  if (stage === 'confirm') { b = await pending(f); request = () => post(b, '/mail/link/confirm', { token: b.token }); }
  if (['getRevert', 'revert', 'deletedRevert'].includes(stage)) {
    await switched(f); const token = await linkToken(f, 'old_notice'); if (stage === 'deletedRevert') revoke(f, true);
    if (stage === 'getRevert') request = () => new Request(`${ORIGIN}/mail/revert?token=${token}`);
    else { b = await browser(f, token, true); request = () => post(b, '/mail/revert', { token }); }
  }
  if (stage === 'resume') { b = await approved(f, 'resume'); request = () => post(b, '/mail/resume', { op_id: id(55) }); }
  if (stage === 'cancel') { b = await pending(f); const c = change(f); request = () => post(b.management, '/mail/change/cancel', { change_id: c.change_id }); }
  return { f, action: () => run(f, request()) };
}
for (const stage of ['begin', 'get', 'confirm', 'getRevert', 'revert', 'deletedRevert', 'resume', 'cancel']) {
  test(`${stage}: fault at EVERY batch statement fully rolls back`, async (t) => {
    const probe = await prepared(t, stage); await expectStatus(await probe.action(), 200); const total = probe.f.db.lastBatch.length;
    for (let n = 0; n < total; n++) await t.test(`statement ${n + 1}/${total}`, async (sub) => {
      const { f, action } = await prepared(sub, stage); const before = snapshot(f);
      f.db.inBatch = (_s, at) => { if (at === n) throw new Error('private fault detail must never escape'); };
      const r = await action(); await expectStatus(r, 503); assert.deepEqual(snapshot(f), before);
      assert.doesNotMatch(await r.text(), /private fault|Owner|@|Error/);
    });
  });
}

for (const [stage, mutation] of [
  ['begin', "UPDATE pull_browser_sessions SET state='invalidated'"],
  ['begin', "UPDATE pull_flows SET state='cancelled' WHERE purpose='owner_change'"],
  ['begin', "UPDATE pull_installs SET generation=generation+1"],
  ['confirm', "UPDATE pull_tokens SET state='invalidated' WHERE purpose='new_owner'"],
  ['confirm', "UPDATE pull_owner_changes SET state='cancelled'"],
  ['confirm', "UPDATE pull_installs SET key_id='foreign'"],
  ['confirm', "UPDATE pull_installs SET state='disabled'"],
  ['revert', "UPDATE pull_installs SET owner_generation=owner_generation+1"],
  ['revert', "UPDATE pull_owner_changes SET state='reverted'"],
  ['revert', "UPDATE pull_tokens SET state='invalidated' WHERE purpose='revert'"],
  ['resume', "UPDATE pull_browser_sessions SET state='invalidated'"],
  ['resume', "UPDATE pull_flows SET state='cancelled' WHERE purpose='resume'"],
  ['resume', "UPDATE pull_installs SET generation=generation+1"]]) {
  test(`${stage}: read-to-commit race refuses ${mutation}`, async (t) => {
    const { f, action } = await prepared(t, stage); let afterMutation;
    f.db.beforeBatch = async () => { f.db.beforeBatch = null; f.db.sql.exec(mutation); afterMutation = snapshot(f); };
    await expectStatus(await action(), 503); assert.deepEqual(snapshot(f), afterMutation);
  });
}

for (const stage of ['begin', 'confirm', 'revert', 'resume']) for (const position of ['before', 'last']) {
  test(`${stage}: expiry at ${position} batch boundary rolls back`, async (t) => {
    const { f, action } = await prepared(t, stage); const before = snapshot(f);
    const expire = () => { f.time += TTL; };
    if (position === 'before') f.db.beforeBatch = expire;
    else f.db.inBatch = (_s, n, total) => { if (n === total - 1) expire(); };
    await expectStatus(await action(), 503); assert.deepEqual(snapshot(f), before);
  });
}

for (const options of [{ headers: { origin: '' } }, { headers: { origin: 'null' } }, { headers: { cookie: '' } },
  { headers: { cookie: `__Secure-clayrune-mail=${SECRET}; __Secure-clayrune-mail=${FENCE}` } },
  { headers: { 'content-type': 'application/json' } }, { headers: { 'content-encoding': 'gzip' } },
  { body: `csrf=${SECRET}&csrf=${FENCE}` }, { body: 'csrf=%GG' }, { body: ' '.repeat(4097) },
  { query: '?' }, { query: '?return_to=https://evil.example' }, { headers: { 'x-large': 'x'.repeat(8193) } }]) {
  test(`hosted closed form/header/Origin bounds refuse ${JSON.stringify(options).slice(0, 110)}`, async (t) => {
    const f = await fixture(t); const b = await approved(f); const before = snapshot(f);
    const r = await run(f, post(b, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address }, options));
    assert.ok([400, 401, 413].includes(r.status)); assert.deepEqual(snapshot(f), before);
  });
}

test('real unmodified SQLite wall clock can complete hosted initiation/confirm', async (t) => {
  const f = await fixture(t, 'active', true); f.config.clock = Date.now; f.http.clock = Date.now; f.management.clock = Date.now;
  const b = await pending(f); await expectStatus(await run(f, post(b, '/mail/link/confirm', { token: b.token })), 200);
  assert.equal(install(f).state, 'verified');
});

for (const kind of ['install', 'target', 'global']) test(`new-owner ${kind} verification budget refuses without leaking a slot/token/outbox`, async (t) => {
  const f = await fixture(t); const b = await approved(f); const day = Math.floor(f.time / DAY);
  const bucket = kind === 'install' ? `pull:verify:install:${id(1)}:${day}`
    : kind === 'target' ? `pull:verify:target:${f.next.owner_hash}:${day}` : `pull:send:global:${day}`;
  f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=excluded.count')
    .run(bucket, kind === 'global' ? 1000 : 3, f.time + DAY);
  const before = snapshot(f); const r = await run(f, post(b, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address }));
  await expectStatus(r, 429); assert.match(await r.text(), /verification_limit/); assert.ok(Number(r.headers.get('retry-after')) > 0);
  assert.deepEqual(snapshot(f), before);
});

for (const race of ['last-slot', 'last-budget', 'completion-frequency', 'candidate-key', 'candidate-public-key']) {
  test(`atomic guards catch concurrent ${race} after preparation`, async (t) => {
    const { f, action } = await prepared(t, race.startsWith('candidate-') ? 'confirm' : 'begin');
    if (race === 'last-slot') { addSlot(f, f.next.owner_hash, 10); addSlot(f, f.next.owner_hash, 11); }
    let after;
    f.db.beforeBatch = async () => {
      f.db.beforeBatch = null;
      if (race === 'last-slot') addSlot(f, f.next.owner_hash, 12);
      if (race === 'last-budget') f.db.sql.prepare("UPDATE pull_budgets SET count=3 WHERE bucket LIKE 'pull:verify:install:%'").run();
      if (race === 'completion-frequency') history(f, 1, f.time - 1);
      if (race === 'candidate-key') f.db.sql.exec("UPDATE pull_installs SET key_id='other'");
      if (race === 'candidate-public-key') f.db.sql.prepare('UPDATE pull_installs SET public_key=?').run('a'.repeat(43));
      after = snapshot(f);
    };
    await expectStatus(await action(), 503); assert.deepEqual(snapshot(f), after);
  });
}

test('confirm rechecks exact candidate even when registry key was already changed before request', async (t) => {
  const f = await fixture(t); const b = await pending(f); f.db.sql.exec("UPDATE pull_installs SET key_id='other'");
  const before = snapshot(f); const r = await run(f, post(b, '/mail/link/confirm', { token: b.token }));
  await expectStatus(r, 409); assert.match(await r.text(), /stale_generation/); assert.deepEqual(snapshot(f), before);
});

for (const field of ['generation', 'owner_generation', 'epoch']) test(`${field} overflow refuses the whole switch`, async (t) => {
  const f = await fixture(t);
  const n = field === 'epoch' ? Number.MAX_SAFE_INTEGER : 999999999999999;
  if (field !== 'epoch') {
    f.db.sql.exec(`UPDATE pull_installs SET ${field}=${n}; UPDATE pull_generations SET ${field}=${n}; UPDATE pull_flows SET expected_${field}=${n}`);
  } else f.db.sql.prepare('UPDATE pull_installs SET epoch=?').run(n);
  const b = await pending(f); const before = snapshot(f); const r = await run(f, post(b, '/mail/link/confirm', { token: b.token }));
  await expectStatus(r, 409); assert.deepEqual(snapshot(f), before);
});

for (const offset of [-1, 0, 1]) test(`management session absolute expiry ${offset}ms`, async (t) => {
  const f = await fixture(t); const b = await approved(f); const end = rows(f, 'pull_browser_sessions').find((s) => s.purpose === 'owner_change').expires_ms;
  f.time = end + offset; const before = snapshot(f);
  await expectStatus(await run(f, post(b, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address })), offset < 0 ? 200 : 401);
  if (offset >= 0) assert.deepEqual(snapshot(f), before);
});

test('browser GET admission bound is shared with management and expired sessions alone are pruned', async (t) => {
  const f = await fixture(t); const b = await pending(f); const before = install(f); const tokens = rows(f, 'pull_tokens');
  // There is one management session and one new-owner confirmation session.
  for (let n = 2; n < 30; n++) { f.source = `edge-${n}`; await expectStatus((await browser(f, b.token)).response, 200); }
  f.source = 'edge-extra'; await expectStatus((await browser(f, b.token)).response, 429);
  assert.equal(rows(f, 'pull_browser_sessions').length, 30); assert.deepEqual(install(f), before); assert.deepEqual(rows(f, 'pull_tokens'), tokens);
  f.time += TTL; await expectStatus((await browser(f, b.token)).response, 200);
  assert.equal(rows(f, 'pull_browser_sessions').length, 1);
});

test('a different scoped management session, raw machine proof and foreign confirmation session cannot switch ownership', async (t) => {
  const f = await fixture(t); const b = await approved(f, 'delete');
  await expectStatus(await run(f, post(b, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address })), 401);
  const rawProof = await machine(f, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address });
  await expectStatus(await run(f, rawProof), 401);
  const c = await pending(f); const other = await browser(f, c.token);
  await expectStatus(await run(f, post(other, '/mail/link/confirm', { token: c.token, csrf: c.csrf })), 401);
  assert.equal(install(f).owner_hash, f.owner.owner_hash);
});

test('failed or uncertain old notice never changes revert expiry or permits a second switch notice', async (t) => {
  const f = await fixture(t); const original = await switched(f); const c = change(f);
  f.db.sql.exec("UPDATE pull_control_outbox SET state='uncertain',envelope_sealed=NULL WHERE template='old_notice'");
  const before = snapshot(f); f.time += 10_000;
  await expectStatus(await run(f, post(original, '/mail/link/confirm', { token: original.token })), 200);
  assert.deepEqual(snapshot(f), before); assert.equal(change(f).revert_until_ms, c.revert_until_ms);
  assert.equal(rows(f, 'pull_control_outbox').filter((r) => r.template === 'old_notice').length, 1);
});

test('resume retains only the exact unexpired revert exception, never other-generation tokens or delayed approvals', async (t) => {
  const f = await fixture(t); await switched(f); const token = await linkToken(f, 'old_notice'); revoke(f); f.time++;
  const b = await approved(f, 'resume');
  await expectStatus(await run(f, post(b, '/mail/resume', { op_id: id(55) })), 200);
  const row = rows(f, 'pull_tokens').find((r) => r.purpose === 'revert'); assert.equal(row.state, 'pending');
  retired(f, 8); const r = await browser(f, token, true); await expectStatus(r.response, 200);
  await expectStatus(await run(f, post(r, '/mail/revert', { token })), 200);
  assert.equal(install(f).generation, 10); assert.equal(install(f).state, 'frozen');
});

test('resume cancels a pre-disable pending change and releases its proposed slot/sealed addresses', async (t) => {
  const f = await fixture(t); const old = await pending(f); revoke(f); f.time++;
  const b = await approved(f, 'resume'); await expectStatus(await run(f, post(b, '/mail/resume', { op_id: id(55) })), 200);
  assert.equal(change(f).state, 'cancelled'); assert.equal(change(f).new_owner_sealed, null); assert.equal(change(f).old_owner_sealed, null);
  assert.equal(rows(f, 'pull_owner_slots').filter((r) => r.role === 'pending').length, 0);
  assert.notEqual((await run(f, post(old, '/mail/link/confirm', { token: old.token }))).status, 200);
  assert.equal(install(f).owner_hash, f.owner.owner_hash);
});

test('initiation expiry cannot cross UTC day and charge the previous day verification budget', async (t) => {
  const f = await fixture(t); f.time = (Math.floor(f.time / DAY) + 1) * DAY - 1; const b = await approved(f); const before = snapshot(f);
  f.db.beforeBatch = async () => { f.db.beforeBatch = null; f.time++; };
  await expectStatus(await run(f, post(b, '/mail/owner-change', { op_id: id(55), new_owner: f.next.owner_address })), 503);
  assert.deepEqual(snapshot(f), before);
});
