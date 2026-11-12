import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { OwnerManagementHTTP } from '../owner-management.js';
import { SignupHTTP } from '../signup-http.js';
import { VerificationMail, VERIFICATION_TTL } from '../verification-mail.js';
import { ownerIdentity } from '../owner-registry.js';
import { bytes, base64url, unbase64url, sha256 } from '../signing.js';

const NOW = 1_794_249_600_000;
const ORIGIN = 'https://mail.example.com';
const START = '/v1/pull/management/start';
const STATUS = '/v1/pull/management/status';
const COOKIE = '__Secure-clayrune-mail';
const SECRET = 'e'.repeat(64);
const FENCE = 'f'.repeat(64);
const id = (n) => n.toString(16).padStart(32, '0');
const hash = (s) => sha256(bytes(s));
const tables = ['pull_installs', 'pull_generations', 'pull_flows', 'pull_operations', 'pull_nonces',
  'pull_budgets', 'pull_tokens', 'pull_control_outbox', 'pull_browser_sessions', 'pull_owner_slots', 'pull_addresses'];
const rows = (f, table) => f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1`).all();
const one = (f, table) => rows(f, table)[0];
const snapshot = (f) => tables.map((table) => rows(f, table));

// Real migrations/SQLite, D1 transactional batches and explicit fault/barrier
// injection; no canned SQL/auth/crypto/quota verdicts. Races are interleavings
// on a local connection, not independent production D1 writers or a DO runtime.
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
      const result = statements.map((s, n) => { if (this.inBatch) this.inBatch(s, n, statements.length); return s.runSync(); });
      this.sql.exec('COMMIT'); return result;
    } catch (error) { this.sql.exec('ROLLBACK'); throw error; }
  }
}
async function keypair() {
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { key: pair.privateKey, verify: pair.publicKey,
    publicKey: base64url(await crypto.subtle.exportKey('raw', pair.publicKey)),
    privateKey: base64url(await crypto.subtle.exportKey('pkcs8', pair.privateKey)) };
}
async function fixture(t, purpose = 'owner_change') {
  const db = new D1(); t.after(() => db.sql.close());
  const current = await keypair(); const replacement = await keypair(); const service = await keypair();
  const owner = await ownerIdentity('Private.Owner+tag@EXAMPLE.com');
  const mail = new VerificationMail({ db, serviceOrigin: ORIGIN, sealKeyId: 'synthetic', sealKey: new Uint8Array(32).fill(6) });
  const f = { db, current, replacement, service, mail, owner, purpose, time: NOW, nonce: 100, source: 'test-edge-source' };
  f.config = { db, serviceOrigin: ORIGIN, domain: 'clayrune.example', verificationMail: mail,
    serviceKeyId: 'service-key', servicePrivateKey: service.privateKey, clock: () => f.time };
  f.http = new OwnerManagementHTTP(f.config);
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,created_ms,updated_ms) VALUES(?,7,3,?,?,?,?,'current',?,'v1',?,?)`)
    .run(id(1), purpose === 'resume' ? 'frozen' : 'active', owner.owner_hash, owner.owner_address, 'm' + 'a'.repeat(29), current.publicKey, NOW - 1000, NOW - 1000);
  db.sql.prepare(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,candidate_key_id,
    candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms)
    VALUES(?,?,'signup',7,3,'current',?,?,?,?,?,'completed','v1',8,?,?,?)`)
    .run(id(2), id(1), current.publicKey, owner.owner_hash, await hash(SECRET), await hash(FENCE), 'b'.repeat(64), 'c'.repeat(64), NOW - 1000, NOW + VERIFICATION_TTL - 1000);
  db.sql.prepare(`INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',?,NULL)`)
    .run(id(1), owner.owner_hash, current.publicKey, await hash(FENCE), id(2), NOW - 1000);
  db.sql.prepare(`INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)`).run('m' + 'a'.repeat(29), id(1), NOW - 1000);
  db.sql.prepare(`INSERT INTO pull_owner_slots VALUES(?,?,'active',?,NULL)`).run(owner.owner_hash, id(1), id(2));
  f.candidate = ['rotate', 'key_recovery'].includes(purpose) ? replacement : current;
  f.candidateId = f.candidate === current ? 'current' : 'replacement';
  f.value = await candidate(f);
  return f;
}
async function candidate(f, overrides = {}, options = {}) {
  const value = { op_id: id(10), flow_id: id(11), purpose: f.purpose, candidate_key_id: f.candidateId,
    candidate_public_key: f.candidate.publicKey, flow_secret_hash: await hash(SECRET), fence_hash: await hash(FENCE), ...overrides };
  const transcript = bytes(JSON.stringify(['clayrune-mail-pull-candidate', '1', ORIGIN, value.purpose, value.flow_id,
    id(1), '7', '3', value.candidate_key_id, value.candidate_public_key, value.fence_hash, value.flow_secret_hash]));
  return { ...value, candidate_signature: base64url(await crypto.subtle.sign('Ed25519', options.key ?? f.candidate.key, transcript)) };
}
// Literal independent wire transcript, including non-signup generations.
async function machine(f, path = START, value = f.value, options = {}) {
  const raw = options.raw ?? JSON.stringify(value);
  const signer = options.signer ?? (f.purpose === 'key_recovery' ? f.replacement : f.current);
  const fields = { version: '2', direction: 'install-to-pull', key_id: signer === f.current ? 'current' : 'replacement',
    install_id: id(1), generation: '7', owner_generation: '3', timestamp: String(f.time), nonce: id(f.nonce++),
    digest: await hash(raw), ...options.fields };
  const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', fields.direction, ORIGIN, 'POST', path,
    fields.key_id, fields.install_id, fields.generation, fields.owner_generation, fields.timestamp, fields.nonce, fields.digest]));
  const headers = new Headers({ 'content-type': 'application/json' });
  for (const [key, value] of Object.entries(fields)) headers.set(`x-mail-${key.replaceAll('_', '-')}`, value);
  headers.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', options.key ?? signer.key, transcript)));
  for (const [key, value] of Object.entries(options.headers ?? {})) headers.set(key, value);
  return new Request(`${options.origin ?? ORIGIN}${path}${options.query ?? ''}`, { method: 'POST', headers, body: raw });
}
const run = (f, request, source = f.source) => f.http.handle(request, { trustedSource: source });
async function signedValue(f, response, request) {
  const raw = new Uint8Array(await response.arrayBuffer()); const h = (name) => response.headers.get(`x-mail-${name}`);
  assert.equal(h('version'), '2'); assert.equal(h('direction'), 'pull-to-install'); assert.equal(h('key-id'), 'service-key');
  for (const name of ['install-id', 'generation', 'owner-generation', 'nonce']) assert.equal(h(name), request.headers.get(`x-mail-${name}`));
  assert.equal(h('digest'), await sha256(raw));
  const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', ORIGIN, 'POST',
    new URL(request.url).pathname, h('key-id'), h('install-id'), h('generation'), h('owner-generation'), h('timestamp'),
    h('nonce'), String(response.status), h('digest')]));
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(h('signature')), transcript), true);
  assert.equal(response.headers.get('cache-control'), 'no-store'); assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  return JSON.parse(new TextDecoder().decode(raw));
}
async function call(f, path = START, value = f.value, options = {}) {
  const request = await machine(f, path, value, options); const response = await run(f, request);
  const result = response.headers.has('x-mail-signature') ? await signedValue(f, response, request) : await response.json();
  return { request, response, result };
}
async function link(f) {
  const row = rows(f, 'pull_control_outbox').find((r) => r.flow_id === f.value.flow_id);
  const message = await f.mail.message(row.outbox_id, f.time);
  return /token=([a-f0-9]{64})/.exec(message.body)[1];
}
async function browser(f, token = undefined) {
  token ??= await link(f);
  const response = await run(f, new Request(`${ORIGIN}/mail/link?token=${token}`)); const text = await response.text();
  return { response, text, token, csrf: /name="csrf" value="([a-f0-9]{64})"/.exec(text)?.[1],
    cookie: response.headers.get('set-cookie')?.split(';')[0] };
}
function post(b, path = '/mail/link/confirm', options = {}) {
  return new Request(`${ORIGIN}${path}${options.query ?? ''}`, { method: options.method ?? 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ORIGIN, cookie: b.cookie, ...options.headers },
    body: options.body ?? (path === '/mail/logout' ? `csrf=${b.csrf}` : `token=${b.token}&csrf=${b.csrf}`) });
}
async function approved(f) {
  assert.equal((await call(f)).response.status, 200);
  const b = await browser(f); assert.equal(b.response.status, 200);
  const response = await run(f, post(b)); assert.equal(response.status, 200); return b;
}
async function manage(f, b) {
  const response = await run(f, new Request(`${ORIGIN}/mail/manage`, { headers: { cookie: b.cookie } }));
  const text = await response.text();
  return { response, text, b: { ...b, csrf: /name="csrf" value="([a-f0-9]{64})"/.exec(text)?.[1] } };
}

for (const purpose of ['owner_change', 'key_recovery', 'resume', 'rotate', 'delete']) {
  test(`${purpose}: start, inert link, exact approval and scoped management form; no install mutation`, async (t) => {
    const f = await fixture(t, purpose); const install = rows(f, 'pull_installs'); const generations = rows(f, 'pull_generations');
    const start = await call(f); assert.equal(start.response.status, 200); assert.equal(start.result.state, 'pending');
    assert.equal(start.result.expires_ms, NOW + VERIFICATION_TTL); assert.equal(start.result.verification_state, 'queued');
    assert.deepEqual(Object.keys(start.result).sort(), ['v', 'code', 'request_id', 'flow_id', 'state', 'expires_ms', 'verification_state'].sort());
    const beforeGet = snapshot(f); const b = await browser(f); assert.equal(b.response.status, 200);
    assert.match(b.text, /<form method="post" action="\/mail\/link\/confirm">/);
    assert.match(b.text, /Owner: \*\*\*@example.com/); assert.doesNotMatch(b.text, /Private.Owner|<script|<img|<iframe/i);
    assert.equal(b.response.headers.get('referrer-policy'), 'no-referrer'); assert.equal(b.response.headers.get('x-frame-options'), 'DENY');
    assert.match(b.response.headers.get('set-cookie'), /; Path=\/mail; Secure; HttpOnly; SameSite=Strict; Max-Age=600$/);
    for (const name of tables.filter((n) => n !== 'pull_browser_sessions')) assert.deepEqual(rows(f, name), beforeGet[tables.indexOf(name)], name);
    const receipt = await run(f, post(b)); assert.equal(receipt.status, 200);
    const session = one(f, 'pull_browser_sessions'); assert.equal(session.purpose, purpose); assert.equal(session.state, 'approved');
    assert.equal(rows(f, 'pull_flows').find((r) => r.flow_id === f.value.flow_id).state, 'approved');
    assert.equal(one(f, 'pull_tokens').state, 'consumed');
    const m = await manage(f, b); assert.equal(m.response.status, 200);
    assert.match(m.text, purpose === 'rotate' ? /Key rotation approved/ : new RegExp(`action="/mail/${purpose.replace('_', '-')}"`));
    assert.deepEqual(rows(f, 'pull_installs'), install); assert.deepEqual(rows(f, 'pull_generations'), generations);
    assert.equal(rows(f, 'pull_control_outbox').length, 1); assert.equal(f.db.sql.prepare('PRAGMA foreign_key_check').all().length, 0);
    const state = await call(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
    assert.equal(state.response.status, 200); assert.equal(state.result.state, 'approved'); assert.equal(state.result.result, null);
    assert.doesNotMatch(JSON.stringify(state.result), /Private.Owner|csrf|cookie|token|candidate|public_key/);
    assert.ok(!JSON.stringify(snapshot(f)).includes(b.csrf)); assert.ok(!JSON.stringify(snapshot(f)).includes(b.cookie.slice(COOKIE.length + 1)));
  });
}

test('ordinary install/send keys and flow secret cannot authorize any hosted management action', async (t) => {
  const f = await fixture(t); await call(f); const before = snapshot(f);
  const signed = await machine(f, '/mail/manage');
  for (const request of [signed, new Request(`${ORIGIN}/mail/manage`),
    post({ cookie: `${COOKIE}=${SECRET}`, csrf: SECRET }, '/mail/logout')]) {
    const r = await run(f, request); assert.ok([400, 401].includes(r.status));
  }
  await assert.rejects(f.http.authorizeSession(post({ cookie: `${COOKIE}=${SECRET}`, csrf: SECRET }), SECRET, 'owner_change'), /session_required/);
  assert.deepEqual(snapshot(f), before);
});

test('candidate signature binds every management field; valid request signature is insufficient', async (t) => {
  const f = await fixture(t);
  for (const changes of [{ purpose: 'delete' }, { flow_id: id(99) }, { candidate_key_id: 'other' },
    { flow_secret_hash: 'd'.repeat(64) }, { fence_hash: 'd'.repeat(64) }, { candidate_public_key: f.replacement.publicKey }]) {
    const r = await call(f, START, { ...f.value, ...changes }); assert.equal(r.response.status, 401);
  }
  assert.equal(rows(f, 'pull_control_outbox').length, 0); assert.equal(rows(f, 'pull_nonces').length, 0);
});

for (const purpose of ['owner_change', 'resume', 'delete']) {
  test(`${purpose} must bind the CURRENT candidate key, even with valid replacement proofs`, async (t) => {
    const f = await fixture(t, purpose);
    const v = await candidate(f, { candidate_key_id: 'replacement', candidate_public_key: f.replacement.publicKey }, { key: f.replacement.key });
    const r = await call(f, START, v); assert.equal(r.response.status, 401); assert.equal(rows(f, 'pull_tokens').length, 0);
  });
}

test('rotation starting key remains old key; recovery requires candidate request signing', async (t) => {
  const f = await fixture(t, 'rotate');
  assert.equal((await call(f, START, f.value, { signer: f.replacement })).response.status, 401);
  assert.equal((await call(f)).response.status, 200);
  assert.equal((await call(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET }, { signer: f.replacement })).response.status, 401);
  const r = await fixture(t, 'key_recovery');
  assert.equal((await call(r, START, r.value, { signer: r.current })).response.status, 401);
  assert.equal((await call(r)).response.status, 200);
});

test('operation retry is durable with original deadline/outbox, even after response loss and envelope purge', async (t) => {
  const f = await fixture(t); const first = await call(f); const tokens = rows(f, 'pull_tokens');
  f.db.sql.exec("UPDATE pull_control_outbox SET state='uncertain',envelope_sealed=NULL");
  f.time += 50_000;
  const second = await call(f); assert.equal(second.response.status, 200); assert.deepEqual(second.result, first.result);
  assert.deepEqual(rows(f, 'pull_tokens'), tokens); assert.equal(rows(f, 'pull_control_outbox').length, 1);
  for (const b of rows(f, 'pull_budgets').filter((r) => !r.bucket.includes('http:management'))) assert.equal(b.count, 1);
  const conflict = await call(f, START, await candidate(f, { flow_secret_hash: 'a'.repeat(64) }));
  assert.equal(conflict.response.status, 409); assert.equal(conflict.result.code, 'operation_conflict');
  const replay = await run(f, await machine(f, START, f.value, { fields: { nonce: first.request.headers.get('x-mail-nonce') } }));
  assert.equal(replay.status, 409); assert.equal((await replay.json()).code, 'nonce_replayed');
});

test('retired starting key reads its original status and exact operation only, never current owner', async (t) => {
  const f = await fixture(t); const first = await call(f);
  const result = { install_id: id(1), generation: 8, owner_generation: 4, state: 'verified', change_id: id(50) };
  f.db.sql.prepare("UPDATE pull_flows SET state='completed',result_json=? WHERE flow_id=?").run(JSON.stringify(result), f.value.flow_id);
  f.db.sql.exec("UPDATE pull_generations SET state='fenced'; UPDATE pull_installs SET generation=9,owner_generation=5,owner_address='Later@example.com'");
  const status = await call(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
  assert.equal(status.response.status, 200); assert.equal(status.result.state, 'completed'); assert.deepEqual(status.result.result, result);
  assert.doesNotMatch(JSON.stringify(status.result), /Later|generation":9/);
  assert.deepEqual((await call(f)).result, first.result);
  const fresh = await call(f, START, await candidate(f, { flow_id: id(77), op_id: id(78) }));
  assert.equal(fresh.response.status, 409); assert.equal(fresh.result.code, 'stale_generation');
  f.time = NOW + VERIFICATION_TTL + 30 * 86_400_000;
  assert.equal((await call(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET })).response.status, 410);
});

for (const fields of [{ version: '1' }, { direction: 'install-to-service' }, { generation: '07' }, { owner_generation: '4' },
  { key_id: 'foreign' }, { install_id: id(99) }, { timestamp: String(NOW - 300_001) }, { timestamp: '00' }, { nonce: 'bad' }, { digest: '0'.repeat(64) }]) {
  test(`signature header refuses ${JSON.stringify(fields)}`, async (t) => {
    const f = await fixture(t); const before = snapshot(f); const r = await call(f, START, f.value, { fields });
    assert.equal(r.response.status, 401); assert.equal(r.response.headers.has('x-mail-signature'), false); assert.deepEqual(snapshot(f), before);
  });
}

for (const changes of [{ extra: 'x' }, { op_id: 'bad' }, { flow_id: 'bad' }, { purpose: 'send' }, { candidate_key_id: '' }]) {
  test(`closed start body refuses ${JSON.stringify(changes)}`, async (t) => {
    const f = await fixture(t); const r = await call(f, START, { ...f.value, ...changes });
    assert.equal(r.response.status, 400); assert.equal(rows(f, 'pull_tokens').length, 0);
  });
}

for (const raw of ['{}garbage', '{"a":"b","a":"c"}', '{"a":"b","\\u0061":"c"}', '{"x":1}', '{"x":null}', '{"x":{}}',
  '{"x":"\\ud800"}', '\ufeff{}', '{"x":"a",}', '{"x":"a"}{}']) {
  test(`strict JSON refuses ${JSON.stringify(raw)}`, async (t) => {
    const f = await fixture(t); const r = await call(f, START, f.value, { raw });
    assert.equal(r.response.status, 400); assert.equal(r.result.code, 'invalid_json'); assert.equal(rows(f, 'pull_nonces').length, 0);
  });
}

test('status requires the bound flow secret/key and refuses extra fields', async (t) => {
  const f = await fixture(t); await call(f);
  for (const [value, options, expected] of [
    [{ flow_id: f.value.flow_id, flow_secret: '0'.repeat(64) }, {}, 401],
    [{ flow_id: id(888), flow_secret: SECRET }, {}, 401],
    [{ flow_id: f.value.flow_id, flow_secret: SECRET }, { signer: f.replacement }, 401],
    [{ flow_id: f.value.flow_id, flow_secret: SECRET, csrf: SECRET }, {}, 400]]) {
    assert.equal((await call(f, STATUS, value, options)).response.status, expected);
  }
});

test('pending expiry and cancellation are bounded status, not mailbox disclosures', async (t) => {
  const f = await fixture(t); await call(f); f.time += VERIFICATION_TTL;
  let r = await call(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
  assert.equal(r.result.state, 'expired'); assert.equal(r.result.result, null);
  f.db.sql.prepare("UPDATE pull_flows SET state='cancelled' WHERE flow_id=?").run(f.value.flow_id);
  r = await call(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET }); assert.equal(r.result.state, 'cancelled');
});

test('expired same-purpose flow can be replaced but never extends original token or session', async (t) => {
  const f = await fixture(t); await call(f); const token = one(f, 'pull_tokens');
  const before = snapshot(f); const other = await candidate(f, { flow_id: id(21), op_id: id(22) });
  assert.equal((await call(f, START, other)).response.status, 409); assert.deepEqual(snapshot(f), before);
  f.time += VERIFICATION_TTL;
  assert.equal((await call(f, START, other)).response.status, 200);
  assert.equal(rows(f, 'pull_flows').find((r) => r.flow_id === id(11)).state, 'expired');
  assert.equal(rows(f, 'pull_tokens').find((r) => r.token_hash === token.token_hash).expires_ms, token.expires_ms);
});

for (const kind of ['install', 'target', 'global']) {
  test(`combined ${kind} verification budget refuses atomically`, async (t) => {
    const f = await fixture(t); const day = Math.floor(NOW / 86_400_000);
    const bucket = kind === 'install' ? `pull:verify:install:${id(1)}:${day}`
      : kind === 'target' ? `pull:verify:target:${f.owner.owner_hash}:${day}` : `pull:send:global:${day}`;
    f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,?,?)').run(bucket, kind === 'global' ? 1000 : 3, NOW + 86_400_000);
    const before = snapshot(f); const r = await call(f); assert.equal(r.response.status, 429); assert.equal(r.result.code, 'verification_limit');
    assert.ok(Number(r.response.headers.get('retry-after')) > 0); assert.deepEqual(snapshot(f), before);
  });
}

test('management request limit and trusted-source/global refusal limits fail closed', async (t) => {
  const f = await fixture(t); const day = Math.floor(NOW / 86_400_000);
  f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,500,?)').run(`pull:http:management:${id(1)}:${day}`, NOW + 86_400_000);
  assert.equal((await call(f)).response.status, 429); assert.equal(rows(f, 'pull_tokens').length, 0);
  const source = await machine(f); const missing = await run(f, source, null); assert.equal(missing.status, 503);
  f.db.sql.prepare('INSERT INTO rate_limits VALUES(?,30,?) ON CONFLICT(bucket) DO UPDATE SET count=30')
    .run(`pull:http:source:${await hash(f.source)}:${Math.floor(NOW / 60_000)}`, NOW + 120_000);
  assert.equal((await call(f)).response.status, 429);
});

for (const options of [
  { headers: { origin: 'https://evil.example' } }, { headers: { origin: 'null' } }, { headers: { origin: '' } },
  { headers: { cookie: '' } }, { headers: { cookie: `${COOKIE}=${SECRET}; ${COOKIE}=${FENCE}` } },
  { headers: { cookie: `${COOKIE}=${SECRET}=more` } }, { headers: { 'content-type': 'application/json' } },
  { headers: { 'content-encoding': 'gzip' } }, { body: `token=${SECRET}&csrf=${SECRET}&csrf=${FENCE}` },
  { body: `token=%65${SECRET.slice(1)}&csrf=${SECRET}` }, { query: '?' }, { query: '?return_to=https://evil.example' }]) {
  test(`browser POST refuses ${JSON.stringify(options)}`, async (t) => {
    const f = await fixture(t); await call(f); const b = await browser(f); const before = snapshot(f);
    const response = await run(f, post(b, '/mail/link/confirm', options)); assert.ok([400, 401].includes(response.status));
    assert.deepEqual(snapshot(f), before); assert.doesNotMatch(await response.text(), /Private.Owner|exception|token=/);
  });
}

test('foreign browser/token/CSRF cannot confirm and scanners cannot gain approval', async (t) => {
  const f = await fixture(t); await call(f); const b = await browser(f); const c = await browser(f);
  const before = snapshot(f);
  for (const body of [`token=${b.token}&csrf=${SECRET}`, `token=${SECRET}&csrf=${b.csrf}`, `token=${b.token}&csrf=${c.csrf}`]) {
    assert.equal((await run(f, post(b, '/mail/link/confirm', { body }))).status, 401);
  }
  assert.equal((await run(f, new Request(`${ORIGIN}/mail/link/confirm?token=${b.token}`))).status, 400);
  assert.deepEqual(snapshot(f), before);
  assert.equal((await run(f, new Request(`${ORIGIN}/mail/manage`, { headers: { cookie: b.cookie } }))).status, 401);
});

test('link token and browser CSRF expire at exact boundaries; GET is bounded to 30 sessions', async (t) => {
  const f = await fixture(t); await call(f); const b = await browser(f);
  for (let n = 1; n < 30; n++) { f.source = `test-edge-${n}`; assert.equal((await browser(f)).response.status, 200); }
  assert.equal((await browser(f)).response.status, 429); assert.equal(rows(f, 'pull_browser_sessions').length, 30);
  f.time += 600_000; assert.equal((await run(f, post(b))).status, 401);
  assert.equal((await browser(f)).response.status, 200); assert.equal(rows(f, 'pull_browser_sessions').length, 1);
  f.time = NOW + VERIFICATION_TTL; assert.equal((await browser(f, b.token)).response.status, 410);
  assert.equal(one(f, 'pull_tokens').state, 'pending');
});

test('approval is single-use, repeat receipt has no cookie/deadline refresh, second browser cannot reuse', async (t) => {
  const f = await fixture(t); await call(f); const b = await browser(f); const c = await browser(f);
  assert.equal((await run(f, post(b))).status, 200); const before = snapshot(f);
  f.time += 20_000; const duplicate = await run(f, post(b)); assert.equal(duplicate.status, 200);
  assert.equal(duplicate.headers.has('set-cookie'), false); assert.deepEqual(snapshot(f), before);
  assert.equal((await run(f, post(c))).status, 409); assert.deepEqual(snapshot(f), before);
  assert.equal((await browser(f, b.token)).response.status, 409);
});

test('manage GET rotates CSRF but preserves absolute expiry; only exact purpose can authorize', async (t) => {
  const f = await fixture(t); const b = await approved(f); const original = one(f, 'pull_browser_sessions');
  f.time += 300_000; const m = await manage(f, b); assert.equal(m.response.status, 200);
  const session = one(f, 'pull_browser_sessions'); assert.equal(session.created_ms, original.created_ms); assert.equal(session.expires_ms, original.expires_ms);
  await assert.rejects(f.http.authorizeSession(post(b), b.csrf, 'owner_change'), /session_required/);
  await assert.rejects(f.http.authorizeSession(post(m.b), m.b.csrf, 'delete'), /session_required/);
  await assert.rejects(f.http.authorizeSession(post(m.b, '/mail/link/confirm', { headers: { origin: 'https://evil.example' } }), m.b.csrf, 'owner_change'), /csrf_failed/);
  const scope = await f.http.authorizeSession(post(m.b), m.b.csrf, 'owner_change');
  assert.equal(scope.install.install_id, id(1)); assert.equal(scope.flow.flow_id, f.value.flow_id); await f.db.batch(scope.statements);
  f.time = original.expires_ms; assert.equal((await manage(f, m.b)).response.status, 401);
});

test('logout invalidates one session only and no later cookie can restore it', async (t) => {
  const f = await fixture(t); const b = await approved(f); const m = await manage(f, b); const before = rows(f, 'pull_installs');
  const response = await run(f, post(m.b, '/mail/logout')); assert.equal(response.status, 200);
  assert.match(response.headers.get('set-cookie'), /Max-Age=0$/); assert.equal(one(f, 'pull_browser_sessions').state, 'invalidated');
  assert.equal((await manage(f, m.b)).response.status, 401); assert.equal((await run(f, post(m.b, '/mail/logout'))).status, 401);
  assert.deepEqual(rows(f, 'pull_installs'), before); assert.equal(rows(f, 'pull_flows').find((r) => r.flow_id === f.value.flow_id).state, 'approved');
});

test('signup/new-owner tokens are never accepted by management link dispatch', async (t) => {
  const f = await fixture(t); await call(f); const token = await link(f);
  f.db.sql.exec("UPDATE pull_tokens SET purpose='signup'");
  assert.notEqual((await browser(f, token)).response.status, 200);
  f.db.sql.exec("UPDATE pull_tokens SET purpose='new_owner'");
  assert.notEqual((await browser(f, token)).response.status, 200);
  const signup = new SignupHTTP(f.config);
  f.db.sql.exec("UPDATE pull_tokens SET purpose='stepup'");
  assert.equal((await signup.handle(new Request(`${ORIGIN}/mail/link?token=${token}`), { trustedSource: f.source })).status, 404);
});

for (const stage of ['start', 'get', 'confirm', 'manage', 'logout']) {
  test(`${stage}: final SQL failure rolls back every statement, including nonce/budgets/token/session`, async (t) => {
    const f = await fixture(t); let b;
    if (stage !== 'start') { await call(f); if (stage !== 'get') b = await browser(f); }
    if (['manage', 'logout'].includes(stage)) { await run(f, post(b)); if (stage === 'logout') b = (await manage(f, b)).b; }
    const token = stage === 'get' ? await link(f) : null;
    const before = snapshot(f);
    f.db.inBatch = (_statement, index, total) => { if (index === total - 1) throw new Error('synthetic private storage error'); };
    let response;
    if (stage === 'start') response = (await call(f)).response;
    else if (stage === 'get') response = (await browser(f, token)).response;
    else if (stage === 'confirm') response = await run(f, post(b));
    else if (stage === 'manage') response = (await manage(f, b)).response;
    else response = await run(f, post(b, '/mail/logout'));
    assert.equal(response.status, 503); assert.deepEqual(snapshot(f), before);
  });
}

for (const change of ["UPDATE pull_installs SET generation=8", "UPDATE pull_installs SET owner_generation=4",
  "UPDATE pull_installs SET owner_hash='" + 'a'.repeat(64) + "'", "UPDATE pull_installs SET key_id='other'",
  "UPDATE pull_installs SET public_key='" + 'a'.repeat(43) + "'", "UPDATE pull_installs SET state='disabled'",
  "UPDATE pull_installs SET consent_version='v2'"]) {
  test(`confirmation read-to-commit race rolls back token/session: ${change}`, async (t) => {
    const f = await fixture(t); await call(f); const b = await browser(f);
    const beforeTokens = rows(f, 'pull_tokens'); const beforeSessions = rows(f, 'pull_browser_sessions');
    f.db.beforeBatch = async () => { f.db.beforeBatch = null; f.db.sql.exec(change); };
    const response = await run(f, post(b)); assert.equal(response.status, 503);
    assert.deepEqual(rows(f, 'pull_tokens'), beforeTokens); assert.deepEqual(rows(f, 'pull_browser_sessions'), beforeSessions);
  });
}

test('guarded authorization seam closes session invalidation between validation and actual mutation', async (t) => {
  const f = await fixture(t); const b = await approved(f); const m = await manage(f, b);
  const scope = await f.http.authorizeSession(post(m.b), m.b.csrf, 'owner_change');
  const before = rows(f, 'pull_installs');
  f.db.sql.exec("UPDATE pull_browser_sessions SET state='invalidated'");
  await assert.rejects(f.db.batch([f.http.stmt("UPDATE pull_installs SET state='disabled'"), ...scope.statements]));
  assert.deepEqual(rows(f, 'pull_installs'), before);
});

test('start race with generation/fence change never commits a prefix', async (t) => {
  const f = await fixture(t); const before = snapshot(f);
  f.db.beforeBatch = async () => { f.db.beforeBatch = null; f.db.sql.exec('UPDATE pull_installs SET generation=8'); };
  const r = await call(f); assert.equal(r.response.status, 409); assert.equal(r.result.code, 'stale_generation');
  for (const name of tables.filter((n) => n !== 'pull_installs')) assert.deepEqual(rows(f, name), before[tables.indexOf(name)]);
});

test('parallel prepared starts share one operation/outbox/budget; parallel confirmation consumes once', async (t) => {
  const f = await fixture(t); let release; let reached;
  const waiting = new Promise((resolve) => { release = resolve; });
  const ready = new Promise((resolve) => { reached = resolve; });
  let first = true;
  f.db.beforeBatch = async () => { if (first) { first = false; reached(); await waiting; } };
  const a = call(f); await ready; const b = await call(f); release(); const c = await a;
  assert.equal(b.response.status, 200); assert.equal(c.response.status, 200); assert.deepEqual(b.result, c.result);
  assert.equal(rows(f, 'pull_control_outbox').length, 1); assert.equal(rows(f, 'pull_tokens').length, 1);
  const browserState = await browser(f);
  let releaseConfirm; let reachedConfirm; first = true;
  const waitingConfirm = new Promise((resolve) => { releaseConfirm = resolve; });
  const readyConfirm = new Promise((resolve) => { reachedConfirm = resolve; });
  f.db.beforeBatch = async () => { if (first) { first = false; reachedConfirm(); await waitingConfirm; } };
  const pending = run(f, post(browserState)); await readyConfirm;
  const committed = await run(f, post(browserState)); releaseConfirm(); const raced = await pending;
  assert.equal(committed.status, 200); assert.equal(raced.status, 503);
  assert.equal(one(f, 'pull_tokens').state, 'consumed'); assert.equal(one(f, 'pull_browser_sessions').state, 'approved');
  assert.equal((await run(f, post(browserState))).status, 200);
});

test('machine request path/query/Origin/compression/header/body bounds are enforced', async (t) => {
  const f = await fixture(t);
  for (const options of [{ query: '?' }, { query: '?x=1' }, { headers: { origin: ORIGIN } },
    { headers: { 'content-encoding': 'gzip' } }, { headers: { 'content-type': 'text/plain' } },
    { headers: { 'x-large': 'a'.repeat(8193) } }, { headers: { 'content-length': '8193' } }, { raw: ' '.repeat(8193) }]) {
    const response = (await call(f, START, f.value, options)).response;
    assert.ok([400, 401, 413].includes(response.status));
  }
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(rows(f, 'pull_control_outbox').length, 0);
});

test('signing, clock and refusal storage failures return fixed terminal refusals without private diagnostics', async (t) => {
  const f = await fixture(t); f.http.servicePrivateKey = 'bad';
  const r = await call(f); assert.equal(r.response.status, 503); assert.equal(r.response.headers.has('x-mail-signature'), false);
  assert.deepEqual(r.result, { v: 1, code: 'service_unavailable' });
  f.http.clock = () => NaN;
  const response = await run(f, new Request(`${ORIGIN}/mail/manage`)); assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /Private.Owner|NaN|Error|bad_key/);
});
