import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { SessionAuth, TICKET_PATH, SOCKET_PATH, PULL_PROTOCOL } from '../session-auth.js';
import { bytes, base64url, unbase64url, sha256 } from '../signing.js';
const NOW = 1_794_249_600_000;
const ORIGIN = 'https://mail.example.com';
const DAY = 86_400_000;
const id = (n) => n.toString(16).padStart(32, '0');
const hash = (s) => sha256(bytes(s));
// Actual migrations, local SQLite transactions, signature verification and
// deterministic barriers. Not independent Cloudflare D1 writers or DO runtime.
class D1 {
  constructor() {
    this.sql = new DatabaseSync(':memory:');
    this.sql.exec('PRAGMA foreign_keys=ON');
    this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
    const dir = new URL('../migrations/', import.meta.url);
    for (const name of readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()) {
      this.sql.exec(readFileSync(new URL(name, dir), 'utf8'));
    }
  }
  prepare(sql) {
    const db = this.sql;
    return { args: [], sql, bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async run() { return this.runSync(); },
      runSync() { const r = db.prepare(sql).run(...this.args); return { success: true, meta: { changes: Number(r.changes) } }; } };
  }
  async batch(statements) {
    if (this.beforeBatch) await this.beforeBatch(statements);
    this.sql.exec('BEGIN IMMEDIATE');
    try {
      const result = statements.map((s, n) => { if (this.inBatch) this.inBatch(s, n); return s.runSync(); });
      this.sql.exec('COMMIT'); return result;
    } catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}

const tables = ['pull_installs','pull_generations','pull_tickets','pull_operations','pull_nonces','pull_budgets'];
const rows = (f, table) => f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1`).all();
const one = (f, table) => rows(f, table)[0];
const snapshot = (f) => tables.map((table) => rows(f, table));
async function keypair() {
  const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { key: k.privateKey, verify: k.publicKey,
    publicKey: base64url(await crypto.subtle.exportKey('raw', k.publicKey)),
    privateKey: base64url(await crypto.subtle.exportKey('pkcs8', k.privateKey)) };
}
async function fixture(t, generation = 7, ownerGeneration = 3, realTime = false) {
  const db = new D1(); t.after(() => db.sql.close());
  const candidate = await keypair(); const service = await keypair();
  const f = { db, candidate, service, time: realTime ? Date.now() : NOW, nonce: 100, op: 1000, source: 'edge-source', generation, ownerGeneration };
  // Inject ONLY SQLite's clock, matching the Worker clock. SQL predicates,
  // changes(), constraints and rollback still run in the real SQLite engine.
  if (!realTime) db.sql.function('strftime', (format, at) => {
    assert.equal(at, 'now');
    if (format === '%s') return String(Math.floor(f.time / 1000));
    assert.equal(format, '%f'); return `${String(Math.floor(f.time / 1000) % 60).padStart(2, '0')}.${String(f.time % 1000).padStart(3, '0')}`;
  });
  f.config = { db, serviceOrigin: ORIGIN, serviceKeyId: 'service-key', servicePrivateKey: service.privateKey, clock: () => f.time };
  f.http = new SessionAuth(f.config);
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,active_since_ms,created_ms,updated_ms)
    VALUES(?,?,?,'active',?,'Private.Owner@example.com',?,'current',?,'v1',?,?,?)`)
    .run(id(1), generation, ownerGeneration, 'a'.repeat(64), 'm' + 'a'.repeat(29), candidate.publicKey, NOW, NOW, NOW);
  db.sql.prepare(`INSERT INTO pull_generations VALUES(?,?,?,?,?,?,?,?,'current',?,NULL)`)
    .run(id(1), generation, ownerGeneration, 'a'.repeat(64), 'current', candidate.publicKey, 'f'.repeat(64), id(2), NOW);
  return f;
}
async function machine(f, value = { op_id: id(f.op++) }, options = {}) {
  const raw = options.raw ?? JSON.stringify(value);
  const path = options.path ?? TICKET_PATH;
  const m = { version: '2', direction: 'install-to-pull', key_id: 'current', install_id: id(1),
    generation: String(f.generation), owner_generation: String(f.ownerGeneration), timestamp: String(f.time),
    nonce: id(f.nonce++), digest: await hash(raw), ...options.fields };
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction, ORIGIN, 'POST', path,
    m.key_id, m.install_id, m.generation, m.owner_generation, m.timestamp, m.nonce, m.digest]));
  const headers = new Headers({ 'content-type': 'application/json' });
  for (const [k, v] of Object.entries(m)) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
  headers.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', options.key ?? f.candidate.key, input)));
  for (const [k, v] of Object.entries(options.headers ?? {})) headers.set(k, v);
  return new Request(`${options.origin ?? ORIGIN}${path}${options.query ?? ''}`, { method: options.method ?? 'POST', headers, body: raw });
}
const run = (f, request, source = f.source) => f.http.handle(request, { trustedSource: source });
async function signedValue(f, response, request) {
  const raw = new Uint8Array(await response.arrayBuffer());
  const field = (n) => response.headers.get(`x-mail-${n}`);
  assert.equal(field('version'), '2'); assert.equal(field('direction'), 'pull-to-install');
  assert.equal(field('key-id'), 'service-key');
  for (const name of ['install-id','generation','owner-generation','nonce']) assert.equal(field(name), request.headers.get(`x-mail-${name}`));
  assert.equal(field('digest'), await sha256(raw));
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', ORIGIN,
    'POST', TICKET_PATH, 'service-key', field('install-id'), field('generation'), field('owner-generation'),
    field('timestamp'), field('nonce'), String(response.status), field('digest')]));
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(field('signature')), input), true);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  return JSON.parse(new TextDecoder().decode(raw));
}
async function mint(f, value, options) {
  const request = await machine(f, value, options); const response = await run(f, request);
  const result = response.headers.has('x-mail-signature') ? await signedValue(f, response, request) : await response.json();
  return { request, response, result };
}
function socket(ticket, options = {}) {
  return new Request(`${options.origin ?? ORIGIN}${SOCKET_PATH}${options.query ?? ''}`, { method: options.method ?? 'GET',
    headers: { authorization: `MailTicket ${ticket}`, upgrade: 'websocket', 'sec-websocket-protocol': PULL_PROTOCOL, ...options.headers } });
}
const admit = (f, request, options = {}) => f.http.admitSocket(request, { trustedSource: f.source, ...options });
function fence(f, mode = 'revoke', flow = id(2)) {
  f.db.sql.prepare(`INSERT INTO pull_fences VALUES(?,?,?,?,?,?,?,?,?)`)
    .run(id(1), flow, f.generation, 'f'.repeat(64), mode, id(80), id(81), 'b'.repeat(64), f.time);
}
function rotate(f) {
  f.db.sql.prepare(`UPDATE pull_generations SET state='fenced',fenced_ms=? WHERE install_id=?`).run(f.time, id(1));
  f.db.sql.prepare(`UPDATE pull_installs SET generation=generation+1,state='verified',epoch=epoch+1,connection_id=NULL WHERE install_id=?`).run(id(1));
  f.db.sql.prepare(`INSERT INTO pull_generations VALUES(?,?,?,?,?,?,?,?,'current',?,NULL)`)
    .run(id(1), f.generation+1, f.ownerGeneration, 'a'.repeat(64), 'next-key', f.candidate.publicKey, 'e'.repeat(64), id(3), f.time);
}
test('mint signs exact result once, persists hashes only, scopes nonce and quotas', async (t) => {
  const f = await fixture(t); const m = await mint(f);
  assert.equal(m.response.status, 200); assert.equal(m.result.code, 'ok');
  assert.deepEqual(Object.keys(m.result).sort(), ['v','code','request_id','ticket','expires_ms','protocol'].sort());
  assert.match(m.result.ticket, /^[a-f0-9]{64}$/); assert.equal(m.result.expires_ms, NOW + 60000);
  assert.equal(m.result.protocol, PULL_PROTOCOL);
  const ticket = one(f, 'pull_tickets');
  assert.equal(ticket.ticket_hash, await hash(m.result.ticket)); assert.equal(ticket.state, 'issued');
  assert.equal(ticket.generation, 7); assert.equal(ticket.owner_generation, 3); assert.equal(ticket.key_id, 'current');
  assert.equal(one(f, 'pull_operations').result_json, '{}');
  assert.equal(JSON.stringify(snapshot(f)).includes(m.result.ticket), false);
  assert.equal(one(f, 'pull_nonces').expires_ms, NOW + 300001);
  assert.equal(one(f, 'pull_nonces').generation, 7);
  assert.deepEqual(rows(f, 'pull_budgets').map((r) => r.count), [1,1]);
});
test('admission atomically burns ticket and records one exact 24h connection attachment', async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  const a = await admit(f, socket(result.ticket), { install_id: id(1) });
  assert.deepEqual(Object.keys(a).sort(), ['v','install_id','generation','owner_generation','key_id','epoch','connection_id','authenticated_ms','reauth_ms'].sort());
  assert.equal(a.v, 1); assert.equal(a.install_id, id(1)); assert.equal(a.generation, 7); assert.equal(a.owner_generation, 3);
  assert.equal(a.epoch, 1); assert.equal(a.authenticated_ms, NOW); assert.equal(a.reauth_ms, NOW + DAY);
  assert.match(a.connection_id, /^[a-f0-9]{32}$/); assert.equal(a.connection_id, one(f, 'pull_installs').connection_id);
  assert.equal(one(f, 'pull_tickets').state, 'consumed'); assert.equal(one(f, 'pull_tickets').consumed_ms, NOW);
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  assert.equal(one(f, 'pull_installs').epoch, 1);
});
for (const g of [1, 999999999999999]) test(`current generation boundary ${g} mints and admits`, async (t) => {
  const f = await fixture(t, g, g); const { result, response } = await mint(f);
  assert.equal(response.status, 200); const a = await admit(f, socket(result.ticket));
  assert.equal(a.generation, g); assert.equal(a.owner_generation, g);
});
test('fresh nonce replays of op_id always conflict, including changed body and swept ticket', async (t) => {
  const f = await fixture(t); const value = { op_id: id(50) }; const first = await mint(f, value);
  for (const options of [{}, { raw: `{ "op_id": "${value.op_id}" }` }]) {
    const next = await mint(f, value, options);
    assert.equal(next.response.status, 409); assert.equal(next.result.code, 'operation_conflict');
    assert.equal('ticket' in next.result, false);
  }
  await admit(f, socket(first.result.ticket));
  f.db.sql.exec('DELETE FROM pull_tickets');
  f.time += 60001;
  const next = await mint(f, value);
  assert.equal(next.response.status, 409); assert.equal(next.result.code, 'operation_conflict');
  assert.equal(rows(f, 'pull_nonces').length, 1); assert.equal(rows(f, 'pull_tickets').length, 0);
});
test('same valid nonce is durable replay refusal; fresh nonce with fresh op succeeds', async (t) => {
  const f = await fixture(t); const request = await machine(f); const copy = request.clone();
  assert.equal((await run(f, request)).status, 200);
  const response = await run(f, copy); const value = await signedValue(f, response, copy);
  assert.equal(response.status, 409); assert.equal(value.code, 'nonce_replayed');
  assert.equal((await mint(f)).response.status, 200); assert.equal(rows(f, 'pull_nonces').length, 2);
});
for (const state of ['pending','verified','disabled','frozen','deleted']) test(`no ticket authority in ${state}`, async (t) => {
  const f = await fixture(t);
  f.db.sql.prepare(`UPDATE pull_installs SET state=?,owner_address=?`).run(state, state === 'deleted' ? null : 'Private.Owner@example.com');
  const before = snapshot(f); const m = await mint(f);
  assert.equal(m.response.status, 401); assert.deepEqual(m.result, { v: 1, code: 'unauthorized' });
  assert.deepEqual(snapshot(f), before);
});
for (const [label, change] of [
  ['generation', (f) => f.db.sql.exec('UPDATE pull_installs SET generation=8')],
  ['owner generation', (f) => f.db.sql.exec('UPDATE pull_installs SET owner_generation=4')],
  ['key id', (f) => f.db.sql.exec("UPDATE pull_installs SET key_id='other'")],
  ['generation key', (f) => f.db.sql.exec("UPDATE pull_generations SET key_id='other'")],
  ['owner hash', (f) => f.db.sql.exec("UPDATE pull_generations SET owner_hash='" + 'b'.repeat(64) + "'")],
  ['fenced generation', (f) => f.db.sql.exec("UPDATE pull_generations SET state='fenced'")],
  ['revoke', (f) => fence(f)], ['cancel current flow', (f) => fence(f, 'cancel')],
]) test(`mismatched/inactive registry ${label} cannot mint`, async (t) => {
  const f = await fixture(t); change(f); const m = await mint(f);
  assert.equal(m.response.status, 401); assert.equal(rows(f, 'pull_tickets').length, 0);
  assert.equal(rows(f, 'pull_nonces').length, 0);
});
test('cancel for unrelated management flow cannot revoke current active mail scope', async (t) => {
  const f = await fixture(t); fence(f, 'cancel', id(999)); assert.equal((await mint(f)).response.status, 200);
});
for (const [label, mutate] of [
  ['legacy version', { fields: { version: '1' } }], ['direction', { fields: { direction: 'pull-to-install' } }],
  ['old generation', { fields: { generation: '6' } }], ['future generation', { fields: { generation: '8' } }],
  ['owner scope', { fields: { owner_generation: '4' } }], ['foreign install', { fields: { install_id: id(2) } }],
  ['wrong key id', { fields: { key_id: 'arbitrary' } }], ['old timestamp', { fields: { timestamp: String(NOW - 300001) } }],
  ['future timestamp', { fields: { timestamp: String(NOW + 300001) } }], ['digest', { fields: { digest: 'c'.repeat(64) } }],
  ['browser Origin', { headers: { origin: ORIGIN } }], ['nonce grammar', { fields: { nonce: 'A'.repeat(32) } }],
  ['noncanonical signature', { headers: { 'x-mail-signature': 'a'.repeat(86) + '=' } }],
]) test(`invalid proof ${label} allocates no nonce/ticket`, async (t) => {
  const f = await fixture(t); const before = snapshot(f); const m = await mint(f, undefined, mutate);
  assert.equal(m.response.status, 401); assert.deepEqual(m.result, { v: 1, code: 'unauthorized' });
  assert.deepEqual(snapshot(f), before);
});
test('wrong-key signature and cookie/dashboard tokens never authenticate ticket', async (t) => {
  const f = await fixture(t); const other = await keypair();
  assert.equal((await mint(f, undefined, { key: other.key })).response.status, 401);
  const response = await run(f, new Request(ORIGIN + TICKET_PATH, { method: 'POST', body: JSON.stringify({ op_id: id(10) }),
    headers: { 'content-type': 'application/json', cookie: '__Secure-clayrune-mail=' + 'a'.repeat(64), authorization: 'Bearer dashboard-token' } }));
  assert.equal(response.status, 401); assert.equal(rows(f, 'pull_nonces').length, 0);
});
for (const [label, raw] of [
  ['empty', ''], ['duplicate', `{"op_id":"${id(1)}","op_id":"${id(1)}"}`],
  ['escaped duplicate', `{"op_id":"${id(1)}","op_\\u0069d":"${id(1)}"}`],
  ['BOM', '\ufeff{"op_id":"' + id(1) + '"}'], ['trailing', `{"op_id":"${id(1)}"}x`],
  ['nested', '{"op_id":{}}'], ['array', '[]'], ['null', '{"op_id":null}'], ['numeric', '{"op_id":1}'],
  ['unknown', `{"op_id":"${id(1)}","x":"y"}`], ['lone surrogate', '{"op_id":"\\ud800"}'],
]) test(`strict ticket body rejects ${label}`, async (t) => {
  const f = await fixture(t); const m = await mint(f, undefined, { raw });
  assert.ok([400,413].includes(m.response.status)); assert.equal(rows(f, 'pull_nonces').length, 0);
  assert.equal(rows(f, 'pull_tickets').length, 0);
});
test('escaped op_id key and JSON whitespace preserve exact signed body', async (t) => {
  const f = await fixture(t); const m = await mint(f, undefined, { raw: ` \n{ "op_\\u0069d" : "${id(100)}" }\t` });
  assert.equal(m.response.status, 200); assert.equal(m.result.request_id, id(100));
});
test('ten tickets/minute; refusals roll back nonce/daily charge and signed retry matches boundary', async (t) => {
  const f = await fixture(t);
  for (let n = 0; n < 10; n++) assert.equal((await mint(f)).response.status, 200);
  const before = snapshot(f); const m = await mint(f);
  assert.equal(m.response.status, 429); assert.equal(m.result.code, 'request_limit');
  assert.equal(m.result.retry_after_ms, (Math.floor(NOW / 60000) + 1) * 60000);
  assert.equal(Number(m.response.headers.get('retry-after')), Math.ceil((m.result.retry_after_ms - f.time) / 1000));
  assert.deepEqual(snapshot(f), before);
  f.time = m.result.retry_after_ms;
  assert.equal((await mint(f)).response.status, 200); assert.equal(rows(f, 'pull_nonces').length, 11);
});
test('500/day management bucket is shared across generations and ticket operations', async (t) => {
  const f = await fixture(t); const bucket = `pull:http:management:${id(1)}:${Math.floor(NOW / DAY)}`;
  f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,499,?)').run(bucket, NOW + 2 * DAY);
  assert.equal((await mint(f)).response.status, 200);
  const m = await mint(f); assert.equal(m.response.status, 429);
  assert.equal(m.result.retry_after_ms, (Math.floor(NOW / DAY) + 1) * DAY);
  assert.equal(Number(m.response.headers.get('retry-after')), Math.ceil((m.result.retry_after_ms - f.time) / 1000));
  assert.equal(rows(f, 'pull_tickets').length, 1); assert.equal(rows(f, 'pull_nonces').length, 1);
});
test('source caps use trusted argument, ignore spoofed forwarding headers; missing source refuses', async (t) => {
  const f = await fixture(t);
  const h = await hash(f.source); const bucket = `pull:http:source:${h}:${Math.floor(NOW / 60000)}`;
  f.db.sql.prepare('INSERT INTO rate_limits VALUES(?,30,?)').run(bucket, NOW + 120000);
  const m = await mint(f, undefined, { headers: { 'cf-connecting-ip': 'fake-source' } });
  assert.equal(m.response.status, 429); assert.deepEqual(m.result, { v: 1, code: 'abuse_limit' });
  assert.equal((await run(f, await machine(f), '')).status, 503);
  assert.equal(rows(f, 'pull_tickets').length, 0);
});
test('invalid traffic shares global refusal budget for ticket and socket paths', async (t) => {
  const f = await fixture(t); const bucket = `http:unauthenticated:${Math.floor(NOW / 60000)}`;
  f.db.sql.prepare('INSERT INTO rate_limits VALUES(?,10000,?)').run(bucket, NOW + 120000);
  const m = await mint(f, undefined, { fields: { version: '1' } });
  assert.equal(m.response.status, 429); assert.equal(m.result.code, 'abuse_limit');
  await assert.rejects(admit(f, socket('a'.repeat(64))), /abuse_limit/);
});
for (const [label, options, status] of [
  ['query', { query: '?x=1' }, 400], ['bare query', { query: '?' }, 400],
  ['fragment', { query: '#x' }, 400], ['foreign origin', { origin: 'https://other.example.com' }, 404],
  ['route', { path: '/v1/pull/other' }, 404], ['encoding', { headers: { 'content-encoding': 'gzip' } }, 400],
  ['content type', { headers: { 'content-type': 'text/plain' } }, 400],
  ['header cap', { headers: { 'x-large': 'x'.repeat(8192) } }, 413],
  ['declared body cap', { headers: { 'content-length': '8193' } }, 413],
  ['nondecimal length', { headers: { 'content-length': '8e3' } }, 413],
  ['actual body cap', { raw: 'x'.repeat(8193) }, 413],
  ['method', { method: 'PUT' }, 401],
]) test(`HTTP boundary ${label}`, async (t) => {
  const f = await fixture(t); const m = await mint(f, undefined, options);
  assert.equal(m.response.status, status); assert.equal(rows(f, 'pull_nonces').length, 0);
});
test('invalid UTF-8 bytes fail strict JSON without durable allocation', async (t) => {
  const f = await fixture(t);
  const request = new Request(ORIGIN + TICKET_PATH, { method: 'POST', headers: { 'content-type': 'application/json' }, body: new Uint8Array([0xff]) });
  const r = await run(f, request); assert.equal(r.status, 400); assert.equal((await r.json()).code, 'invalid_json');
  assert.equal(rows(f, 'pull_tickets').length, 0);
});
test('expired ticket at exact deadline is rejected without epoch/consume', async (t) => {
  const f = await fixture(t); const { result } = await mint(f); f.time = result.expires_ms;
  const before = snapshot(f); await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  assert.deepEqual(snapshot(f), before);
});
test('future-dated ticket, invalidated ticket and unknown ticket cannot admit', async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  f.db.sql.exec(`UPDATE pull_tickets SET created_ms=${NOW + 1}`);
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  f.db.sql.exec(`UPDATE pull_tickets SET created_ms=${NOW},state='invalidated'`);
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  await assert.rejects(admit(f, socket('a'.repeat(64))), /ticket_invalid/);
  assert.equal(one(f, 'pull_installs').epoch, 0);
});
test('foreign DO scope never burns a valid ticket', async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  await assert.rejects(admit(f, socket(result.ticket), { install_id: id(99) }), /ticket_invalid/);
  assert.equal(one(f, 'pull_tickets').state, 'issued');
  assert.equal((await admit(f, socket(result.ticket), { install_id: id(1) })).epoch, 1);
});
for (const [label, options, code] of [
  ['Origin', { headers: { origin: ORIGIN } }, 'ticket_invalid'],
  ['method', { method: 'POST' }, 'ticket_invalid'],
  ['wrong protocol', { headers: { 'sec-websocket-protocol': 'other' } }, 'version_unsupported'],
  ['multiple protocols', { headers: { 'sec-websocket-protocol': PULL_PROTOCOL + ', other' } }, 'version_unsupported'],
  ['missing upgrade', { headers: { upgrade: '' } }, 'version_unsupported'],
  ['cookie', { headers: { authorization: '', cookie: '__Secure-clayrune-mail=' + 'a'.repeat(64) } }, 'ticket_invalid'],
  ['Bearer', { headers: { authorization: 'Bearer ' + 'a'.repeat(64) } }, 'ticket_invalid'],
  ['wrong auth case', { headers: { authorization: 'mailticket ' + 'a'.repeat(64) } }, 'ticket_invalid'],
  ['query', { query: '?ticket=' + 'a'.repeat(64) }, 'invalid_request'],
  ['bare query', { query: '?' }, 'invalid_request'], ['foreign origin', { origin: 'https://other.example.com' }, 'not_found'],
  ['encoding', { headers: { 'content-encoding': 'gzip' } }, 'invalid_request'],
  ['header size', { headers: { 'x-large': 'a'.repeat(8192) } }, 'body_limit'],
]) test(`socket boundary ${label}`, async (t) => {
  const f = await fixture(t); const { result } = await mint(f); const before = snapshot(f);
  await assert.rejects(admit(f, socket(result.ticket, options)), new RegExp(code));
  assert.deepEqual(snapshot(f), before);
});
test('socket request with body cannot admit, even with valid ticket/protocol', async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  // Native Request forbids GET bodies; a Worker-like input still must reject it.
  const request = socket(result.ticket);
  Object.defineProperty(request, 'body', { value: new ReadableStream({ start(c) { c.close(); } }) });
  await assert.rejects(admit(f, request), /ticket_invalid/);
});
for (const change of ['rotate','revoke','disabled','frozen','deleted','fenced','key','owner']) test(`outstanding tickets lose authority after ${change}`, async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  if (change === 'rotate') rotate(f);
  else if (change === 'revoke') fence(f);
  else if (change === 'fenced') f.db.sql.exec("UPDATE pull_generations SET state='fenced'");
  else if (change === 'key') f.db.sql.exec("UPDATE pull_installs SET key_id='different'");
  else if (change === 'owner') f.db.sql.exec('UPDATE pull_installs SET owner_generation=4');
  else f.db.sql.prepare('UPDATE pull_installs SET state=?,owner_address=?').run(change, change === 'deleted' ? null : 'Private.Owner@example.com');
  const before = snapshot(f); await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  assert.deepEqual(snapshot(f), before);
});
test('connection epoch exhaustion refuses without consuming ticket', async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  f.db.sql.exec('UPDATE pull_installs SET epoch=999999999999999');
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  assert.equal(one(f, 'pull_tickets').state, 'issued');
});
for (let index = 0; index < 10; index++) test(`mint failure at batch statement ${index} rolls back entire mutation`, async (t) => {
  const f = await fixture(t); const before = snapshot(f);
  f.db.inBatch = (_s, n) => { if (n === index) throw new Error('synthetic private failure'); };
  const m = await mint(f); assert.equal(m.response.status, 503); assert.equal(m.result.code, 'storage_unavailable');
  assert.equal(JSON.stringify(m.result).includes('synthetic'), false); assert.deepEqual(snapshot(f), before);
  f.db.inBatch = undefined;
  assert.equal((await mint(f)).response.status, 200);
});
for (let index = 0; index < 7; index++) test(`admission failure at batch statement ${index} restores ticket and epoch`, async (t) => {
  const f = await fixture(t); const { result } = await mint(f); const before = snapshot(f);
  f.db.inBatch = (_s, n) => { if (n === index) throw new Error('synthetic private failure'); };
  await assert.rejects(admit(f, socket(result.ticket)), /^Error: storage_unavailable$/);
  assert.deepEqual(snapshot(f), before);
  f.db.inBatch = undefined; assert.equal((await admit(f, socket(result.ticket))).epoch, 1);
});
test('same-op race commits one secret and returns signed 409 to loser', async (t) => {
  const f = await fixture(t); const value = { op_id: id(55) }; let winner;
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; winner = await mint(f, value); };
  const loser = await mint(f, value);
  assert.equal(winner.response.status, 200); assert.equal(loser.response.status, 409);
  assert.equal(loser.result.code, 'operation_conflict'); assert.equal('ticket' in loser.result, false);
  assert.equal(rows(f, 'pull_tickets').length, 1); assert.equal(rows(f, 'pull_nonces').length, 1);
  assert.equal(one(f, 'pull_tickets').ticket_hash, await hash(winner.result.ticket));
  assert.deepEqual(rows(f, 'pull_budgets').map((r) => r.count), [1,1]);
});
test('same-nonce race leaves one operation, one nonce and one charge', async (t) => {
  const f = await fixture(t); const request = await machine(f); const copy = request.clone(); let winner;
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; winner = await run(f, copy); };
  const loser = await run(f, request); const value = await signedValue(f, loser, request);
  assert.equal(winner.status, 200); assert.equal(loser.status, 409); assert.equal(value.code, 'nonce_replayed');
  assert.equal(rows(f, 'pull_tickets').length, 1); assert.equal(rows(f, 'pull_nonces').length, 1);
  assert.deepEqual(rows(f, 'pull_budgets').map((r) => r.count), [1,1]);
});
test('last available quota race cannot overdraw or burn loser nonce', async (t) => {
  const f = await fixture(t); const bucket = `pull:ticket:${id(1)}:${Math.floor(NOW / 60000)}`;
  f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,9,?)').run(bucket, NOW + 120000); let winner;
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; winner = await mint(f); };
  const loser = await mint(f); assert.equal(winner.response.status, 200); assert.equal(loser.response.status, 429);
  assert.equal(rows(f, 'pull_nonces').length, 1); assert.equal(rows(f, 'pull_tickets').length, 1);
  assert.deepEqual(rows(f, 'pull_budgets').map((r) => r.count), [1,10]);
});
for (const mutation of ['rotate','revoke','keyswap']) test(`mint rechecks ${mutation} raced after verification`, async (t) => {
  const f = await fixture(t);
  f.db.beforeBatch = async () => {
    f.db.beforeBatch = undefined;
    if (mutation === 'rotate') rotate(f);
    else if (mutation === 'revoke') fence(f);
    else {
      const other = await keypair();
      f.db.sql.prepare('UPDATE pull_installs SET public_key=?').run(other.publicKey);
      f.db.sql.prepare('UPDATE pull_generations SET public_key=?').run(other.publicKey);
    }
  };
  const m = await mint(f); assert.equal(m.response.status, 401);
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(rows(f, 'pull_tickets').length, 0);
  assert.equal(rows(f, 'pull_operations').length, 0); assert.equal(rows(f, 'pull_budgets').length, 0);
});
test('double-consume race has exactly one winner and one epoch', async (t) => {
  const f = await fixture(t); const { result } = await mint(f); let winner;
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; winner = await admit(f, socket(result.ticket)); };
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  assert.equal(winner.epoch, 1); assert.equal(one(f, 'pull_installs').epoch, 1);
  assert.equal(one(f, 'pull_installs').connection_id, winner.connection_id);
  assert.equal(one(f, 'pull_tickets').state, 'consumed');
});
test('different-ticket admission race retries epoch CAS without burning loser ticket', async (t) => {
  const f = await fixture(t); const first = await mint(f); const second = await mint(f); let earlier;
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; earlier = await admit(f, socket(second.result.ticket)); };
  const later = await admit(f, socket(first.result.ticket));
  assert.equal(earlier.epoch, 1); assert.equal(later.epoch, 2); assert.notEqual(earlier.connection_id, later.connection_id);
  assert.equal(one(f, 'pull_installs').connection_id, later.connection_id);
  assert.deepEqual(rows(f, 'pull_tickets').map((r) => r.state), ['consumed','consumed']);
});
for (const mutation of ['rotate','revoke']) test(`admission rechecks ${mutation} at batch boundary`, async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; mutation === 'rotate' ? rotate(f) : fence(f); };
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/);
  assert.equal(one(f, 'pull_tickets').state, 'issued');
  assert.equal(one(f, 'pull_installs').connection_id, null);
});
test('queued mint cannot commit if timestamp expires before D1 executes', async (t) => {
  const f = await fixture(t); const before = snapshot(f);
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; f.time += 300001; };
  const m = await mint(f); assert.equal(m.response.status, 401); assert.deepEqual(snapshot(f), before);
});
test('queued admission cannot commit a ticket that expires before D1 executes', async (t) => {
  const f = await fixture(t); const { result } = await mint(f); const before = snapshot(f);
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; f.time = result.expires_ms; };
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/); assert.deepEqual(snapshot(f), before);
});
test('last-statement time guard rolls back mint and admission after their writes', async (t) => {
  const f = await fixture(t); const before = snapshot(f);
  f.db.inBatch = (_s, n) => { if (n === 9) f.time += 60000; };
  const m = await mint(f); assert.equal(m.response.status, 503); assert.deepEqual(snapshot(f), before);
  f.db.inBatch = undefined; f.time = NOW;
  const { result } = await mint(f); const issued = snapshot(f);
  f.db.inBatch = (_s, n) => { if (n === 6) f.time = result.expires_ms; };
  await assert.rejects(admit(f, socket(result.ticket)), /ticket_invalid/); assert.deepEqual(snapshot(f), issued);
});
test('lost plaintext response cannot be recovered by retry or service signing failure', async (t) => {
  const f = await fixture(t); const value = { op_id: id(100) };
  f.http.servicePrivateKey = base64url(new Uint8Array(8));
  const lost = await mint(f, value); assert.equal(lost.response.status, 503);
  assert.equal(rows(f, 'pull_tickets').length, 1); assert.equal('ticket' in lost.result, false);
  f.http = new SessionAuth(f.config);
  const retry = await mint(f, value); assert.equal(retry.response.status, 409); assert.equal(retry.result.code, 'operation_conflict');
  assert.equal((await mint(f)).response.status, 200);
});
test('socket admission storage outage never masquerades as invalid ticket or leaks diagnostics', async (t) => {
  const f = await fixture(t); const { result } = await mint(f);
  const original = f.http.first;
  f.http.first = async () => { throw new Error('private database path'); };
  await assert.rejects(admit(f, socket(result.ticket)), /^Error: storage_unavailable$/);
  f.http.first = original; assert.equal(one(f, 'pull_tickets').state, 'issued');
});
test('mint/admission also execute against unmodified SQLite wall clock functions', async (t) => {
  const f = await fixture(t, 7, 3, true);
  const m = await mint(f); assert.equal(m.response.status, 200);
  const a = await admit(f, socket(m.result.ticket));
  assert.equal(a.epoch, 1); assert.equal(one(f, 'pull_tickets').state, 'consumed');
});
for (const offset of [-300000,300000]) test(`accepted timestamp boundary ${offset} retains nonce through its whole window`, async (t) => {
  const f = await fixture(t);
  const m = await mint(f, undefined, { fields: { timestamp: String(f.time + offset) } });
  assert.equal(m.response.status, 200);
  assert.equal(one(f, 'pull_nonces').expires_ms, NOW + offset + 300001);
});
test('refusal accounting outages are fixed failures for both public seams', async (t) => {
  const f = await fixture(t); let calls = 0;
  const limit = f.http.ledger.limit.bind(f.http.ledger);
  f.http.ledger.limit = async (...args) => {
    if (args[0] === 'http:unauthenticated') { calls++; throw new Error('private storage failure'); }
    return limit(...args);
  };
  const m = await mint(f, undefined, { fields: { version: '1' } });
  assert.equal(m.response.status, 503); assert.deepEqual(m.result, { v: 1, code: 'service_unavailable' });
  await assert.rejects(admit(f, socket('a'.repeat(64))), /^Error: storage_unavailable$/);
  assert.equal(calls, 2); assert.equal(rows(f, 'pull_nonces').length, 0);
});
test('reconnect changes only connection epoch, preserving registration, custody and prior ticket consumption', async (t) => {
  const f = await fixture(t); const first = await mint(f); const a = await admit(f, socket(first.result.ticket));
  const second = await mint(f); const b = await admit(f, socket(second.result.ticket));
  assert.equal(b.epoch, a.epoch + 1); assert.notEqual(b.connection_id, a.connection_id);
  const i = one(f, 'pull_installs'); assert.equal(i.generation, 7); assert.equal(i.owner_generation, 3); assert.equal(i.state, 'active');
  assert.equal(i.connection_id, b.connection_id); assert.equal(rows(f, 'pull_tickets').filter((r) => r.state === 'consumed').length, 2);
  assert.equal(one(f, 'pull_generations').state, 'current');
});
test('ticket operation replay stays 409 even when the minute/day budget is full', async (t) => {
  const f = await fixture(t); const value = { op_id: id(100) };
  assert.equal((await mint(f, value)).response.status, 200);
  f.db.sql.exec('UPDATE pull_budgets SET count=500');
  const replay = await mint(f, value);
  assert.equal(replay.response.status, 409); assert.equal(replay.result.code, 'operation_conflict');
  assert.equal('ticket' in replay.result, false); assert.equal(rows(f, 'pull_nonces').length, 1);
});
test('queued mint crossing UTC budget boundary retries with the commit-time day/minute buckets', async (t) => {
  const f = await fixture(t);
  f.time = (Math.floor(NOW / DAY) + 1) * DAY - 500;
  f.db.beforeBatch = async () => { f.db.beforeBatch = undefined; f.time += 1000; };
  const m = await mint(f); assert.equal(m.response.status, 200);
  assert.deepEqual(rows(f, 'pull_budgets').map((r) => r.bucket).sort(), [
    `pull:http:management:${id(1)}:${Math.floor(f.time / DAY)}`,
    `pull:ticket:${id(1)}:${Math.floor(f.time / 60000)}`,
  ].sort());
  assert.equal(rows(f, 'pull_nonces').length, 1); assert.equal(rows(f, 'pull_tickets').length, 1);
});
