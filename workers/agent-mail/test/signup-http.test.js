import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { SignupHTTP } from '../signup-http.js';
import { VerificationMail, VERIFICATION_TTL } from '../verification-mail.js';
import { bytes, base64url, unbase64url, sha256 } from '../signing.js';

const NOW = 1_794_249_600_000;
const ORIGIN = 'https://mail.example.com';
const COOKIE = '__Secure-clayrune-mail';
const START = '/v1/pull/signup/start';
const STATUS = '/v1/pull/signup/status';
const SECRET = 'e'.repeat(64);
const FENCE = 'f'.repeat(64);
const id = (n) => n.toString(16).padStart(32, '0');
const hash = (s) => sha256(bytes(s));

// Real SQLite with all migrations, transactional D1 batch semantics and optional
// fault/barrier injection. No canned SQL, crypto, auth or rate-limit verdicts.
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
const rows = (f, name) => f.db.sql.prepare(`SELECT * FROM ${name} ORDER BY 1`).all();
const one = (f, name) => rows(f, name)[0];
const count = (f, name) => rows(f, name).length;
const durableTables = ['pull_installs', 'pull_generations', 'pull_flows', 'pull_operations', 'pull_nonces',
  'pull_budgets', 'pull_tokens', 'pull_control_outbox', 'pull_owner_slots', 'pull_addresses', 'pull_browser_sessions'];
const snapshot = (f) => durableTables.map((name) => rows(f, name));

async function keypair() {
  const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { key: k.privateKey, publicKey: base64url(await crypto.subtle.exportKey('raw', k.publicKey)),
    privateKey: base64url(await crypto.subtle.exportKey('pkcs8', k.privateKey)), verify: k.publicKey };
}
async function fixture(t) {
  const db = new D1(); t.after(() => db.sql.close());
  const candidate = await keypair(); const service = await keypair();
  const mail = new VerificationMail({ db, serviceOrigin: ORIGIN, sealKeyId: 'synthetic', sealKey: new Uint8Array(32).fill(4) });
  const f = { db, candidate, service, mail, time: NOW, nonce: 100, source: 'test-edge-source' };
  f.http = new SignupHTTP({ db, serviceOrigin: ORIGIN, domain: 'clayrune.example', verificationMail: mail,
    serviceKeyId: 'service-key', servicePrivateKey: service.privateKey, clock: () => f.time });
  f.value = { op_id: id(1), flow_id: id(2), public_key: candidate.publicKey, key_id: 'candidate',
    owner: 'Private.Owner+tag@EXAMPLE.com', flow_secret_hash: await hash(SECRET), fence_hash: await hash(FENCE),
    consent_version: 'v1', manifest_sequence: 1, manifest_digest: 'a'.repeat(64) };
  return f;
}

// Independent literal v2 transcript; deliberately never calls the implementation
// to sign fixtures or verify service responses (legacy signature v1 must fail).
async function machine(f, path = START, value = f.value, options = {}) {
  const body = options.raw ?? JSON.stringify(value);
  const fields = { version: '2', direction: 'install-to-pull', key_id: 'candidate', install_id: id(3),
    generation: '1', owner_generation: '1', timestamp: String(f.time), nonce: id(f.nonce++),
    digest: await hash(body), ...options.fields };
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', fields.direction, ORIGIN,
    'POST', path, fields.key_id, fields.install_id, fields.generation, fields.owner_generation,
    fields.timestamp, fields.nonce, fields.digest]));
  const sig = base64url(await crypto.subtle.sign('Ed25519', options.key ?? f.candidate.key, input));
  const headers = new Headers({ 'content-type': 'application/json' });
  for (const [k, v] of Object.entries(fields)) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
  headers.set('x-mail-signature', sig);
  for (const [k, v] of Object.entries(options.headers ?? {})) headers.set(k, v);
  return new Request(`${options.origin ?? ORIGIN}${path}${options.query ?? ''}`, { method: 'POST', headers, body });
}
const run = (f, request, source = f.source) => f.http.handle(request, { trustedSource: source });
async function signedValue(f, response, request) {
  const raw = new Uint8Array(await response.arrayBuffer());
  const field = (n) => response.headers.get(`x-mail-${n}`);
  assert.equal(field('version'), '2'); assert.equal(field('direction'), 'pull-to-install');
  assert.equal(field('key-id'), 'service-key');
  for (const name of ['install-id', 'generation', 'owner-generation', 'nonce']) {
    assert.equal(field(name), request.headers.get(`x-mail-${name}`));
  }
  assert.equal(field('digest'), await sha256(raw));
  const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', ORIGIN,
    'POST', new URL(request.url).pathname, 'service-key', field('install-id'), field('generation'),
    field('owner-generation'), field('timestamp'), field('nonce'), String(response.status), field('digest')]));
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(field('signature')), transcript), true);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  return JSON.parse(new TextDecoder().decode(raw));
}
async function begin(f, overrides = {}, options = {}) {
  const request = await machine(f, START, { ...f.value, ...overrides }, options);
  const response = await run(f, request);
  const value = response.headers.has('x-mail-signature') ? await signedValue(f, response, request) : await response.json();
  return { request, response, value };
}
async function link(f) {
  const message = await f.mail.message(one(f, 'pull_control_outbox').outbox_id, f.time);
  return /token=([a-f0-9]{64})/.exec(message.body)[1];
}
async function browser(f, token = undefined) {
  token ??= await link(f);
  const request = new Request(`${ORIGIN}/mail/link?token=${token}`);
  const response = await run(f, request);
  const text = await response.text();
  return { response, text, token, csrf: /name="csrf" value="([a-f0-9]{64})"/.exec(text)?.[1],
    cookie: response.headers.get('set-cookie')?.split(';')[0] };
}
function post(b, options = {}) {
  return new Request(`${ORIGIN}/mail/link/confirm${options.query ?? ''}`, { method: options.method ?? 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ORIGIN, cookie: b.cookie, ...options.headers },
    body: options.body ?? `token=${b.token}&csrf=${b.csrf}` });
}

test('begin atomically reserves pending install/owner/address/flow/nonce/token/outbox, with signed evidence', async (t) => {
  const f = await fixture(t); const { value, response } = await begin(f);
  assert.equal(response.status, 200); assert.equal(value.code, 'ok'); assert.equal(value.request_id, id(1));
  assert.match(value.address, /^m[a-f0-9]{29}@clayrune\.example$/);
  assert.equal(value.state, 'pending'); assert.equal(value.expires_ms, NOW + VERIFICATION_TTL);
  for (const table of durableTables.filter((s) => s !== 'pull_browser_sessions')) assert.equal(count(f, table) >= 1, true, table);
  assert.equal(one(f, 'pull_installs').owner_address, 'Private.Owner+tag@example.com');
  assert.equal(one(f, 'pull_addresses').state, 'reserved');
  assert.equal(one(f, 'pull_owner_slots').role, 'pending');
  assert.equal(count(f, 'pull_budgets'), 4); assert.equal(one(f, 'pull_nonces').expires_ms, NOW + 300_001);
  assert.equal(one(f, 'pull_tokens').token_hash, await hash(await link(f)));
  assert.ok(!JSON.stringify(value).includes('Private.Owner'));
  assert.equal(f.db.sql.prepare('PRAGMA foreign_key_check').all().length, 0);
});

test('repeated begin returns original flow/address/deadline; accepted/uncertain/purged mail is never reissued', async (t) => {
  const f = await fixture(t); const first = await begin(f);
  const original = rows(f, 'pull_tokens');
  f.db.sql.exec("UPDATE pull_control_outbox SET state='uncertain',envelope_sealed=NULL");
  f.time += 50_000;
  const second = await begin(f);
  assert.equal(second.response.status, 200); assert.deepEqual(second.value, first.value);
  assert.equal(count(f, 'pull_control_outbox'), 1); assert.deepEqual(rows(f, 'pull_tokens'), original);
  for (const budget of rows(f, 'pull_budgets').filter((r) => !r.bucket.includes('http:management'))) assert.equal(budget.count, 1);
  assert.equal(count(f, 'pull_nonces'), 2);
});

test('same nonce is refused; same operation with changed body refuses without allocating', async (t) => {
  const f = await fixture(t); const first = await begin(f); const before = snapshot(f);
  const replay = await run(f, await machine(f, START, f.value, { fields: { nonce: first.request.headers.get('x-mail-nonce') } }));
  assert.equal(replay.status, 409); assert.equal((await replay.json()).code, 'nonce_replayed');
  const conflict = await begin(f, { owner: 'Other@example.com' });
  assert.equal(conflict.response.status, 409); assert.equal(conflict.value.code, 'operation_conflict');
  assert.deepEqual(snapshot(f), before);
});

test('another install cannot choose an owned address or reuse a foreign flow/install identity', async (t) => {
  const f = await fixture(t); await begin(f); const before = snapshot(f);
  const foreign = await begin(f, {}, { fields: { install_id: id(44) } });
  assert.equal(foreign.response.status, 409); assert.equal(foreign.value.code, 'operation_conflict');
  const alias = await begin(f, { op_id: id(55), flow_id: id(56), address: one(f, 'pull_installs').address_local }, { fields: { install_id: id(44) } });
  assert.equal(alias.response.status, 400); assert.equal(alias.value.code, 'invalid_request');
  const overwrite = await begin(f, { op_id: id(66), flow_id: id(67), owner: 'Other@example.com' });
  assert.equal(overwrite.response.status, 409);
  for (const v of [foreign.value, alias.value, overwrite.value]) {
    assert.deepEqual(Object.keys(v).sort(), ['code', 'request_id', 'v']);
    assert.ok(!JSON.stringify(v).includes('Private.Owner'));
  }
  assert.deepEqual(snapshot(f), before);
});

test('status requires the exact stored candidate key AND flow secret; unsigned callers learn nothing', async (t) => {
  const f = await fixture(t); await begin(f);
  const value = { flow_id: f.value.flow_id, flow_secret: SECRET };
  const request = await machine(f, STATUS, value);
  const response = await run(f, request); assert.equal(response.status, 200);
  const status = await signedValue(f, response, request);
  assert.equal(status.state, 'pending'); assert.ok(!JSON.stringify(status).includes('Private.Owner'));
  assert.equal(status.request_id, request.headers.get('x-mail-nonce'));
  const wrong = await keypair();
  for (const req of [new Request(`${ORIGIN}${STATUS}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(value) }),
    await machine(f, STATUS, { ...value, flow_secret: 'd'.repeat(64) }),
    await machine(f, STATUS, value, { key: wrong.key }),
    await machine(f, STATUS, { ...value, flow_id: id(98) }),
    await machine(f, STATUS, value, { fields: { install_id: id(99) } })]) {
    const res = await run(f, req); assert.equal(res.status, 401);
    assert.equal(res.headers.has('x-mail-signature'), false); assert.deepEqual(await res.json(), { v: 1, code: 'unauthorized' });
  }
  assert.equal(count(f, 'pull_tokens'), 1); assert.equal(count(f, 'pull_nonces'), 2);
});

test('GET confirmation is inert, bounded, masked, and emits an explicit secure POST form', async (t) => {
  const f = await fixture(t); await begin(f);
  const tables = durableTables.filter((s) => s !== 'pull_browser_sessions');
  const before = tables.map((name) => rows(f, name));
  const b = await browser(f); assert.equal(b.response.status, 200);
  assert.equal(count(f, 'pull_browser_sessions'), 1);
  assert.deepEqual(tables.map((name) => rows(f, name)), before);
  assert.ok(!b.text.includes('Private.Owner')); assert.match(b.text, /Owner: \*\*\*@example.com/);
  assert.match(b.text, /<form method="post" action="\/mail\/link\/confirm">/);
  assert.match(b.text, new RegExp(await sha256(unbase64url(f.candidate.publicKey))));
  assert.doesNotMatch(b.text, /<script|<iframe|<img|http-equiv/i);
  assert.match(b.response.headers.get('set-cookie'), new RegExp(`^${COOKIE}=[a-f0-9]{64}; Path=/mail; Secure; HttpOnly; SameSite=Strict; Max-Age=600$`));
  assert.ok(!b.response.headers.get('set-cookie').includes('Domain='));
  assert.equal(b.response.headers.get('referrer-policy'), 'no-referrer');
  assert.equal(b.response.headers.get('x-frame-options'), 'DENY');
  assert.match(b.response.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  const s = one(f, 'pull_browser_sessions');
  assert.equal(s.session_hash, await hash(b.cookie.split('=')[1])); assert.equal(s.csrf_hash, await hash(b.csrf));
  assert.ok(!JSON.stringify(rows(f, 'pull_browser_sessions')).includes(b.csrf));
});

test('POST commits token+install+flow+session together, keeps address reserved, never activates or sends twice', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f);
  const res = await run(f, post(b)); assert.equal(res.status, 200);
  assert.equal(one(f, 'pull_tokens').state, 'consumed'); assert.equal(one(f, 'pull_flows').state, 'verified');
  assert.equal(one(f, 'pull_installs').state, 'verified'); assert.equal(one(f, 'pull_browser_sessions').state, 'consumed');
  assert.equal(one(f, 'pull_addresses').state, 'reserved'); assert.equal(one(f, 'pull_installs').active_since_ms, null);
  assert.equal(one(f, 'pull_owner_slots').role, 'pending');
  const before = snapshot(f); const replay = await run(f, post(b)); assert.equal(replay.status, 200);
  assert.equal(await replay.text(), await res.text()); assert.deepEqual(snapshot(f), before);
  const other = { ...b, cookie: `${COOKIE}=${'d'.repeat(64)}` };
  assert.equal((await run(f, post(other))).status, 401);
  assert.equal((await browser(f)).response.status, 409);
  const request = await machine(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
  const status = await signedValue(f, await run(f, request), request); assert.equal(status.state, 'verified');
});

for (const [name, options, status] of [
  ['wrong Origin', { headers: { origin: 'https://evil.example' } }, 401],
  ['null Origin', { headers: { origin: 'null' } }, 401],
  ['missing CSRF', { body: `token=${'a'.repeat(64)}` }, 400],
  ['mismatched CSRF', { body: `token=TOKEN&csrf=${'d'.repeat(64)}` }, 401],
  ['missing cookie', { headers: { cookie: '' } }, 401],
  ['mismatched cookie', { headers: { cookie: `${COOKIE}=${'d'.repeat(64)}` } }, 401],
  ['duplicate cookie', { headers: { cookie: 'DUPLICATE' } }, 401],
  ['duplicate form key', { body: 'token=TOKEN&csrf=CSRF&csrf=CSRF' }, 400],
  ['unknown form key', { body: 'token=TOKEN&csrf=CSRF&return_to=https://evil.example' }, 400],
  ['encoded secret', { body: 'token=%61TOKEN&csrf=CSRF' }, 400],
  ['form query', { query: '?token=unused' }, 400],
  ['JSON content type', { headers: { 'content-type': 'application/json' } }, 400],
]) test(`confirmation refuses ${name} with fixed response and no lifecycle mutation`, async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f); const before = snapshot(f);
  const o = { ...options, headers: { ...options.headers } };
  if (o.body) o.body = o.body.replaceAll('TOKEN', b.token).replaceAll('CSRF', b.csrf);
  if (o.headers.cookie === 'DUPLICATE') o.headers.cookie = `${b.cookie}; ${b.cookie}`;
  const res = await run(f, post(b, o)); const text = await res.text();
  assert.equal(res.status, status); assert.ok(!text.includes(b.token)); assert.ok(!text.includes(b.csrf));
  assert.ok(!text.includes('Private.Owner')); assert.deepEqual(snapshot(f), before);
});

test('an unrelated browser session cannot replay another session consumption', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f); const other = await browser(f);
  assert.equal((await run(f, post(b))).status, 200);
  const before = snapshot(f);
  const replay = await run(f, post(other)); assert.equal(replay.status, 409); assert.deepEqual(snapshot(f), before);
});

test('expiry is absolute for links, browser sessions and pending signup', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f);
  f.time += 600_000;
  assert.equal((await run(f, post(b))).status, 401);
  const near = await browser(f); assert.equal(near.response.status, 200);
  f.time = NOW + VERIFICATION_TTL;
  assert.equal((await browser(f, b.token)).response.status, 410);
  assert.equal((await run(f, post(near))).status, 410);
  const req = await machine(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
  const status = await signedValue(f, await run(f, req), req); assert.equal(status.state, 'expired');
  assert.equal(one(f, 'pull_installs').state, 'pending'); assert.equal(count(f, 'pull_control_outbox'), 1);
});

test('failed guard after token consumption rolls back token, verification and session; retry succeeds', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f); const before = snapshot(f);
  f.db.inBatch = (s) => {
    if (s.sql.startsWith('UPDATE pull_flows')) f.db.sql.exec('INSERT INTO pull_version VALUES(1,0)');
  };
  const res = await run(f, post(b)); assert.equal(res.status, 503);
  assert.deepEqual(snapshot(f), before);
  f.db.inBatch = undefined;
  assert.equal((await run(f, post(b))).status, 200);
});

test('late start guard failure rolls back budgets, owner/address, pending identity, token and outbox', async (t) => {
  const f = await fixture(t); const before = snapshot(f);
  f.db.inBatch = (s) => { if (s.sql.startsWith('INSERT INTO pull_operations')) f.db.sql.exec('INSERT INTO pull_version VALUES(1,0)'); };
  const res = await begin(f); assert.equal(res.response.status, 503); assert.deepEqual(snapshot(f), before);
  f.db.inBatch = undefined; assert.equal((await begin(f)).response.status, 200);
});

test('prepared concurrent duplicate starts converge on one operation, one address, one mail charge', async (t) => {
  const f = await fixture(t);
  let arrivals = 0; let release;
  const barrier = new Promise((resolve) => { release = resolve; });
  f.db.beforeBatch = async (statements) => {
    if (statements.some((s) => s.sql.startsWith('INSERT INTO pull_installs')) && ++arrivals <= 2) {
      if (arrivals === 2) release(); await barrier;
    }
  };
  const a = await machine(f); const b = await machine(f);
  const responses = await Promise.all([run(f, a), run(f, b)]);
  assert.deepEqual(responses.map((r) => r.status), [200, 200]);
  assert.deepEqual(await signedValue(f, responses[0], a), await signedValue(f, responses[1], b));
  for (const table of ['pull_installs', 'pull_addresses', 'pull_tokens', 'pull_control_outbox', 'pull_operations']) assert.equal(count(f, table), 1);
  for (const row of rows(f, 'pull_budgets').filter((r) => !r.bucket.includes('http:management'))) assert.equal(row.count, 1);
});

test('competing prepared confirmation batches consume at most once', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f);
  let arrivals = 0; let release;
  const barrier = new Promise((resolve) => { release = resolve; });
  f.db.beforeBatch = async (statements) => {
    if (statements.some((s) => s.sql.startsWith('UPDATE pull_tokens')) && ++arrivals <= 2) {
      if (arrivals === 2) release(); await barrier;
    }
  };
  const responses = await Promise.all([run(f, post(b)), run(f, post(b))]);
  assert.equal(responses.filter((r) => r.status === 200).length, 1);
  assert.equal(one(f, 'pull_installs').state, 'verified'); assert.equal(one(f, 'pull_tokens').state, 'consumed');
  assert.equal(count(f, 'pull_control_outbox'), 1);
  f.db.beforeBatch = undefined;
  assert.equal((await run(f, post(b))).status, 200);
});

test('preexisting cancel fence refuses begin without any allocated signup authority', async (t) => {
  const f = await fixture(t);
  f.db.sql.prepare('INSERT INTO pull_fences VALUES(?,?,1,?,\'cancel\',?,?,?,?)')
    .run(id(3), id(2), await hash(FENCE), id(9), id(10), 'b'.repeat(64), NOW);
  const before = snapshot(f); const res = await begin(f);
  assert.equal(res.response.status, 409); assert.equal(res.value.code, 'fenced'); assert.deepEqual(snapshot(f), before);
});

test('key substitution or cancellation between GET and POST cannot verify', async (t) => {
  for (const mutation of ["UPDATE pull_flows SET candidate_public_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'",
    "UPDATE pull_installs SET key_id='substituted'",
    `INSERT INTO pull_fences VALUES('${id(3)}','${id(2)}',1,'${'b'.repeat(64)}','cancel','${id(9)}','${id(10)}','${'b'.repeat(64)}',${NOW})`]) {
    const f = await fixture(t); await begin(f); const b = await browser(f);
    f.db.sql.exec(mutation); const before = snapshot(f);
    const res = await run(f, post(b)); assert.notEqual(res.status, 200); assert.deepEqual(snapshot(f), before);
  }
});

test('owner admission and verification caps apply before any new install and do not leak installation identities', async (t) => {
  const f = await fixture(t);
  for (let n = 0; n < 3; n++) {
    const result = await begin(f, { op_id: id(10 + n), flow_id: id(20 + n) }, { fields: { install_id: id(30 + n) } });
    assert.equal(result.response.status, 200);
  }
  const before = snapshot(f);
  const fourth = await begin(f); assert.equal(fourth.response.status, 409); assert.equal(fourth.value.code, 'owner_limit');
  assert.deepEqual(Object.keys(fourth.value).sort(), ['code', 'request_id', 'v']); assert.deepEqual(snapshot(f), before);
  f.db.sql.exec("UPDATE pull_owner_slots SET until_ms=0");
  const budget = await begin(f); assert.equal(budget.response.status, 429); assert.equal(budget.value.code, 'verification_limit');
  assert.equal(count(f, 'pull_installs'), 3);
});

test('source abuse is bounded, cannot charge a forged install, and shares the existing global refusal budget', async (t) => {
  const f = await fixture(t); const req = () => new Request(`${ORIGIN}${START}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
  for (let n = 0; n < 30; n++) assert.equal((await run(f, req())).status, 401);
  const refused = await run(f, req()); assert.equal(refused.status, 429); assert.equal(refused.headers.get('retry-after'), '60');
  assert.equal(count(f, 'pull_budgets'), 0); assert.equal(count(f, 'pull_nonces'), 0);
  assert.equal(f.db.sql.prepare("SELECT count FROM rate_limits WHERE bucket LIKE 'http:unauthenticated:%'").get().count, 31);
  f.db.sql.prepare('UPDATE rate_limits SET count=10000 WHERE bucket LIKE ?').run('http:unauthenticated:%');
  const global = await run(f, req(), 'another-edge'); assert.equal(global.status, 429);
  assert.deepEqual(await global.json(), { v: 1, code: 'abuse_limit' });
  const noSource = await f.http.handle(req()); assert.equal(noSource.status, 503);
  // Saturated shared refusal accounting cannot prevent a valid candidate from
  // recovering its own flow from another trusted edge source.
  const valid = await machine(f);
  const success = await run(f, valid, 'valid-edge'); assert.equal(success.status, 200);
});

for (const [name, raw] of [
  ['duplicate keys', '{"op_id":"a","op_id":"b"}'],
  ['escaped duplicate keys', '{"owner":"a","ow\\u006eer":"b"}'],
  ['BOM', '\ufeff{}'], ['trailing bytes', '{}x'], ['negative zero', '{"x":-0}'],
  ['fraction', '{"x":1.5}'], ['overflow', '{"x":9007199254740992}'],
  ['lone surrogate', '{"x":"\\ud800"}'], ['nested object', '{"x":{}}'], ['array', '[]'],
  ['NaN', '{"x":NaN}'], ['trailing comma', '{"x":"a",}'],
]) test(`strict JSON refuses ${name} without nonce or identity allocation`, async (t) => {
  const f = await fixture(t); const res = await run(f, await machine(f, START, {}, { raw }));
  assert.equal(res.status, 400); assert.deepEqual(await res.json(), { v: 1, code: 'invalid_json' });
  assert.equal(count(f, 'pull_nonces'), 0); assert.equal(count(f, 'pull_installs'), 0);
});

for (const [name, options] of [
  ['legacy version', { fields: { version: '1' } }], ['wrong direction', { fields: { direction: 'install-to-service' } }],
  ['stale timestamp', { fields: { timestamp: String(NOW - 300_001) } }],
  ['future timestamp', { fields: { timestamp: String(NOW + 300_001) } }],
  ['wrong generation', { fields: { generation: '2' } }], ['wrong owner generation', { fields: { owner_generation: '2' } }],
  ['wrong digest', { fields: { digest: 'b'.repeat(64) } }], ['browser Origin', { headers: { origin: ORIGIN } }],
  ['noncanonical base64 signature', { headers: { 'x-mail-signature': 'AAAA=' } }],
]) test(`candidate proof refuses ${name} without consuming nonce or owner quota`, async (t) => {
  const f = await fixture(t); const res = await begin(f, {}, options);
  assert.equal(res.response.status, 401); assert.deepEqual(res.value, { v: 1, code: 'unauthorized' });
  assert.equal(count(f, 'pull_nonces'), 0); assert.equal(count(f, 'pull_budgets'), 0);
});

test('request size, URL, headers and form limits are independent of valid proof', async (t) => {
  const f = await fixture(t);
  for (const options of [{ raw: ' '.repeat(8193) }, { query: '?' }, { query: '?x=1' },
    { headers: { 'content-encoding': 'gzip' } }, { headers: { 'x-large': 'a'.repeat(8200) } },
    { headers: { 'content-length': '9000' } }, { headers: { 'content-type': 'text/plain' } }]) {
    const res = await begin(f, {}, options); assert.ok([400, 413].includes(res.response.status));
    assert.equal(count(f, 'pull_installs'), 0);
  }
  await begin(f); const b = await browser(f);
  const large = await run(f, post(b, { body: 'a'.repeat(4097) })); assert.equal(large.status, 413);
  for (const query of ['?token=' + b.token + '&token=' + b.token, '?token=%61' + b.token, '?token=' + b.token + '&x=1']) {
    const res = await run(f, new Request(`${ORIGIN}/mail/link${query}`)); assert.equal(res.status, 400);
  }
});

test('CSRF session creation is capped at 30/install across edge sources; expired state is pruned, never refreshed', async (t) => {
  const f = await fixture(t); await begin(f); const token = await link(f);
  const get = () => new Request(`${ORIGIN}/mail/link?token=${token}`);
  for (let n = 0; n < 30; n++) assert.equal((await run(f, get(), `edge-${n}`)).status, 200);
  const before = rows(f, 'pull_browser_sessions');
  const full = await run(f, get(), 'overflow-edge'); assert.equal(full.status, 429);
  assert.deepEqual(rows(f, 'pull_browser_sessions'), before);
  f.time += 600_000;
  assert.equal((await run(f, get(), 'later-edge')).status, 200);
  assert.equal(count(f, 'pull_browser_sessions'), 1);
  assert.equal(one(f, 'pull_flows').expires_ms, NOW + VERIFICATION_TTL);
  assert.equal(count(f, 'pull_control_outbox'), 1);
});

test('lost confirmation response after successful commit recovers only via its original browser receipt', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f);
  const originalBatch = f.db.batch.bind(f.db);
  f.db.batch = async (statements) => {
    const value = await originalBatch(statements);
    if (statements.some((s) => s.sql.startsWith('UPDATE pull_tokens'))) throw new Error('simulated lost D1 response');
    return value;
  };
  assert.equal((await run(f, post(b))).status, 503);
  const before = snapshot(f); assert.equal(one(f, 'pull_installs').state, 'verified');
  assert.equal((await run(f, post(b))).status, 200);
  assert.deepEqual(snapshot(f), before);
});

test('wrong-key signature cannot retrieve an already committed begin result', async (t) => {
  const f = await fixture(t); await begin(f); const other = await keypair(); const before = snapshot(f);
  const res = await begin(f, {}, { key: other.key }); assert.equal(res.response.status, 401);
  assert.deepEqual(res.value, { v: 1, code: 'unauthorized' }); assert.deepEqual(snapshot(f), before);
});

test('candidate status never binds undefined or accepts unknown fields', async (t) => {
  const f = await fixture(t); await begin(f);
  const missing = await run(f, await machine(f, STATUS, {})); assert.equal(missing.status, 401);
  const extra = await run(f, await machine(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET, owner: 'any@example.com' }));
  assert.equal(extra.status, 400); assert.equal(count(f, 'pull_nonces'), 1);
});

test('verification and machine HTTP daily limits provide signed absolute retry times with matching headers', async (t) => {
  for (const [bucket, max, code] of [
    [`pull:verify:install:${id(3)}:${Math.floor(NOW / 86_400_000)}`, 3, 'verification_limit'],
    [`pull:send:global:${Math.floor(NOW / 86_400_000)}`, 1000, 'verification_limit'],
    [`pull:http:management:${id(3)}:${Math.floor(NOW / 86_400_000)}`, 500, 'request_limit'],
  ]) {
    const f = await fixture(t);
    f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,?,?)').run(bucket, max, NOW + 2 * 86_400_000);
    const before = snapshot(f); const result = await begin(f);
    assert.equal(result.response.status, 429); assert.equal(result.value.code, code);
    assert.equal(result.value.retry_after_ms, (Math.floor(NOW / 86_400_000) + 1) * 86_400_000);
    assert.equal(Number(result.response.headers.get('retry-after')), Math.ceil((result.value.retry_after_ms - NOW) / 1000));
    assert.deepEqual(snapshot(f), before);
  }
});

test('candidate signature must still be fresh after verification-mail encryption completes', async (t) => {
  const f = await fixture(t); const original = f.mail.issueStatements.bind(f.mail);
  f.mail.issueStatements = async (...args) => { const result = await original(...args); f.time += 300_001; return result; };
  const res = await begin(f); assert.equal(res.response.status, 401); assert.equal(count(f, 'pull_installs'), 0);
  assert.equal(count(f, 'pull_nonces'), 0); assert.equal(count(f, 'pull_control_outbox'), 0);
});

test('CSRF state cannot expire during async consume preparation and then commit', async (t) => {
  const f = await fixture(t); await begin(f); const b = await browser(f); const before = snapshot(f);
  const original = f.mail.consumeStatements.bind(f.mail);
  f.mail.consumeStatements = async (...args) => { const result = await original(...args); f.time += 600_000; return result; };
  assert.equal((await run(f, post(b))).status, 401); assert.deepEqual(snapshot(f), before);
});

test('counter storage and signing failures return fixed refusals without raw provider/runtime text', async (t) => {
  const f = await fixture(t);
  f.http.sourceLimit = async () => { throw new Error('private storage diagnostic'); };
  let res = await begin(f); assert.equal(res.response.status, 503);
  assert.deepEqual(res.value, { v: 1, code: 'storage_unavailable' });
  f.http.sourceLimit = async () => {};
  f.http.refusalLimit = async () => { throw new Error('private rate diagnostic'); };
  res = await begin(f, {}, { fields: { version: '1' } }); assert.equal(res.response.status, 503);
  assert.deepEqual(res.value, { v: 1, code: 'storage_unavailable' });
  f.http.refusalLimit = async () => true;
  f.http.servicePrivateKey = 'AAAA';
  res = await begin(f); assert.equal(res.response.status, 503);
  assert.deepEqual(res.value, { v: 1, code: 'service_unavailable' });
  f.time = NaN;
  const req = new Request(`${ORIGIN}${START}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
  const badClock = await run(f, req); assert.equal(badClock.status, 503);
  assert.deepEqual(await badClock.json(), { v: 1, code: 'service_unavailable' });
});

test('invalid UTF-8 form and JSON bodies are fixed grammar refusals', async (t) => {
  const f = await fixture(t);
  for (const [path, contentType, code] of [[START, 'application/json', 'invalid_json'],
    ['/mail/link/confirm', 'application/x-www-form-urlencoded', 'invalid_request']]) {
    const req = new Request(`${ORIGIN}${path}`, { method: 'POST', headers: { 'content-type': contentType }, body: new Uint8Array([0xff]) });
    const res = await run(f, req); assert.equal(res.status, 400); assert.ok((await res.text()).includes(code));
  }
  assert.equal(count(f, 'pull_installs'), 0);
});

test('stalled upload hits the existing ten-second body deadline without allocating an identity', async (t) => {
  const f = await fixture(t); let cancelled = false;
  const body = new ReadableStream({ start(controller) { controller.enqueue(bytes('{')); }, cancel() { cancelled = true; } });
  const req = new Request(`${ORIGIN}${START}`, { method: 'POST', duplex: 'half', headers: { 'content-type': 'application/json' }, body });
  const res = await run(f, req); assert.equal(res.status, 408); assert.equal((await res.json()).code, 'body_timeout');
  assert.equal(cancelled, true); assert.equal(count(f, 'pull_installs'), 0);
});

test('confirmed-but-unactivated signup expires at its original deadline without extending reservation or mail', async (t) => {
  const f = await fixture(t); const original = await begin(f); const b = await browser(f);
  f.time += 500_000; assert.equal((await run(f, post(b))).status, 200);
  assert.equal(one(f, 'pull_flows').expires_ms, original.value.expires_ms);
  f.time = original.value.expires_ms;
  const req = await machine(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
  const res = await run(f, req); assert.equal(res.status, 200);
  const value = await signedValue(f, res, req); assert.equal(value.state, 'expired');
  assert.equal(one(f, 'pull_installs').state, 'verified'); assert.equal(one(f, 'pull_installs').active_since_ms, null);
  assert.equal(one(f, 'pull_addresses').until_ms, original.value.expires_ms);
  assert.equal(count(f, 'pull_control_outbox'), 1);
});

test('status rechecks immutable key scope in its nonce batch; key swap leaves no claimed nonce or false evidence', async (t) => {
  const f = await fixture(t); await begin(f);
  const req = await machine(f, STATUS, { flow_id: f.value.flow_id, flow_secret: SECRET });
  f.db.beforeBatch = async (statements) => {
    if (statements.some((s) => s.sql.startsWith('INSERT INTO pull_nonces'))) {
      f.db.sql.exec("UPDATE pull_installs SET key_id='rotated'");
    }
  };
  const res = await run(f, req); assert.equal(res.status, 503);
  assert.equal((await signedValue(f, res, req)).code, 'storage_unavailable');
  assert.equal(count(f, 'pull_nonces'), 1);
  assert.equal(rows(f, 'pull_budgets').find((r) => r.bucket.includes('http:management')).count, 1);
});
