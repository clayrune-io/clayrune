// Public entry dispatch only; module suites own their authentication/state machines.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorker } from '../worker.js';
import { SignupHTTP, SIGNUP_PATHS } from '../signup-http.js';
import { OwnerManagementHTTP, MANAGEMENT_PATHS } from '../owner-management.js';
import { OwnerChangeHTTP, OWNER_CHANGE_PATHS } from '../owner-change.js';
import { LinkRouter, LINK_PATHS } from '../link-router.js';
import { SessionAuth, TICKET_PATH, SOCKET_PATH } from '../session-auth.js';
import { PullSocketRoute } from '../pull-socket-route.js';
import { PullSendHTTP, SEND_PATH, SEND_STATUS_PATH } from '../pull-send-http.js';
import { PullActivateHTTP, ACTIVATE_PATH } from '../pull-activate-http.js';
import { PullStatusHTTP, STATUS_PATH } from '../pull-status-http.js';
import { PullFenceHTTP, FENCE_PATH } from '../pull-fence-http.js';
import { PullKeyRotateHTTP, KEY_ROTATE_PATH } from '../pull-key-rotate-http.js';
import { VerificationMail } from '../verification-mail.js';
import { ENROLLMENT_PREFIX, ENROLLMENT_ROUTES } from '../enrollment-http.js';
import { LIFECYCLE_PREFIX, LIFECYCLE_ROUTES } from '../lifecycle.js';
import { base64url, bytes, sha256 } from '../signing.js';
import { fixture, keypair, machine, signed, id, LOCAL, NOW, ORIGIN } from './fixtures/pull-lifecycle.js';
import { emailMessage } from './transport-support.js';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';

const EDGE = '192.0.2.10';
const TOKEN = 'abcdef0123456789abcdef0123456789';
const SENT = 'sent@example.com';
const direct = [
  ...SIGNUP_PATHS.filter((p) => !LINK_PATHS.includes(p)).map((p) => [p, SignupHTTP]),
  ...MANAGEMENT_PATHS.filter((p) => !LINK_PATHS.includes(p)).map((p) => [p, OwnerManagementHTTP]),
  ...OWNER_CHANGE_PATHS.filter((p) => !LINK_PATHS.includes(p)).map((p) => [p, OwnerChangeHTTP]),
  [TICKET_PATH, SessionAuth], [SEND_PATH, PullSendHTTP], [SEND_STATUS_PATH, PullSendHTTP],
  [ACTIVATE_PATH, PullActivateHTTP], [STATUS_PATH, PullStatusHTTP], [FENCE_PATH, PullFenceHTTP],
  [KEY_ROTATE_PATH, PullKeyRotateHTTP],
];
const obsolete = [
  ...ENROLLMENT_ROUTES.map((p) => `${ENROLLMENT_PREFIX}/${p}`),
  ...Object.keys(LIFECYCLE_ROUTES).map((p) => `${LIFECYCLE_PREFIX}/${p}`),
  '/v1/mail/send', '/v1/mail/reconcile',
];

async function envFor(db = { prepare() { assert.fail('dispatch must not touch storage'); } }) {
  const key = await keypair(), now = Date.now();
  return { MAIL_ENABLED: 'true', MAIL_DB: db, MAIL_DOMAIN: 'mail.example.net', MAIL_FROM: 'agent@mail.example.net',
    MAIL_SERVICE_ORIGIN: ORIGIN, MAIL_SERVICE_KEY_ID: 'service-key', MAIL_SERVICE_PRIVATE_KEY: key.privateKey,
    MAIL_PULL_OUTBOX_KEY_ID: 'synthetic', MAIL_PULL_OUTBOX_KEY: base64url(new Uint8Array(32).fill(7)),
    MAIL_SEND: { send() { assert.fail('no provider calls in dispatch checks'); } },
    MAIL_PULL_MAILBOX: { idFromName() { assert.fail('route adapter owns DO selection'); }, get() {} },
    MAIL_SERVICE_PIN_EXPIRES_MS: String(now + 86400000),
    MAIL_SERVICE_MANIFEST: JSON.stringify({ manifest: ['clayrune-mail-service-manifest', '1', ORIGIN, '8',
      String(now - 10000), String(now + 172800000), 'service-key', key.publicKey,
      String(now - 10000), String(now + 259200000), []], signature: 'A'.repeat(86) }) };
}
const request = (path, headers = {}) => new Request(ORIGIN + path, { method: 'POST',
  body: '{}', headers: { 'content-type': 'application/json', 'cf-connecting-ip': EDGE, ...headers } });

for (const [path, Handler] of direct) test(`${path} dispatches original request to ${Handler.name}`, async (t) => {
  const env = await envFor(), req = request(path), sentinel = new Response('selected');
  let calls = 0;
  t.mock.method(Handler.prototype, 'handle', async function (received, context) {
    calls++;
    assert.equal(received, req); assert.equal(req.bodyUsed, false);
    assert.deepEqual(context, { trustedSource: `edge:${EDGE}` });
    assert.equal(this.db, env.MAIL_DB); assert.equal(this.serviceOrigin, ORIGIN);
    assert.equal(this.serviceKeyId, env.MAIL_SERVICE_KEY_ID);
    assert.equal(this.servicePrivateKey, env.MAIL_SERVICE_PRIVATE_KEY);
    assert.equal(this.clock(), NOW);
    // Default env has no MAIL_SIGNUP_ADMISSION: closed for signup, option absent elsewhere.
    assert.equal(this.freshSignupEnabled, this instanceof SignupHTTP ? false : undefined);
    if (this instanceof SignupHTTP) {
      assert.ok(this.mail instanceof VerificationMail); assert.equal(this.mail.db, env.MAIL_DB);
      assert.equal(this.mail.serviceOrigin, ORIGIN); assert.equal(this.mail.sealKeyId, 'synthetic');
    }
    if (this instanceof PullSendHTTP) {
      assert.equal(this.binding, env.MAIL_SEND);
      assert.deepEqual(this.config, { from: env.MAIL_FROM, domain: env.MAIL_DOMAIN });
      assert.equal(this.bodyTimeoutMs, 25);
    }
    return sentinel;
  });
  assert.equal(await createWorker({ clock: () => NOW, bodyTimeoutMs: 25 }).fetch(req, env), sentinel);
  assert.equal(calls, 1);
});

for (const path of LINK_PATHS) test(`${path} reaches only the shared link router`, async (t) => {
  const env = await envFor(), req = request(path), sentinel = new Response('router'); let calls = 0;
  for (const Handler of [SignupHTTP, OwnerManagementHTTP, OwnerChangeHTTP]) {
    t.mock.method(Handler.prototype, 'handle', () => assert.fail('shared links cannot bypass LinkRouter'));
  }
  t.mock.method(LinkRouter.prototype, 'handle', async function (received, context) {
    calls++; assert.equal(received, req); assert.equal(req.bodyUsed, false);
    assert.deepEqual(context, { trustedSource: `edge:${EDGE}` });
    assert.ok(this.signup instanceof SignupHTTP);
    assert.ok(this.handlers.get('stepup') instanceof OwnerManagementHTTP);
    assert.ok(this.handlers.get('new_owner') instanceof OwnerChangeHTTP);
    return sentinel;
  });
  assert.equal(await createWorker({ clock: () => NOW }).fetch(req, env), sentinel); assert.equal(calls, 1);
});

test('socket dispatch uses reviewed socket config and the bound namespace without a Node DO export', async (t) => {
  const env = await envFor(), req = new Request(ORIGIN + SOCKET_PATH), sentinel = new Response('socket'); let calls = 0;
  t.mock.method(PullSocketRoute.prototype, 'handle', async function (received) {
    calls++; assert.equal(received, req); assert.equal(this.mailboxes, env.MAIL_PULL_MAILBOX);
    assert.equal(this.config.db, env.MAIL_DB); assert.equal(this.config.serviceOrigin, ORIGIN);
    assert.equal(this.config.serviceKeyId, env.MAIL_SERVICE_KEY_ID);
    assert.equal(this.config.servicePrivateKey, env.MAIL_SERVICE_PRIVATE_KEY);
    assert.equal(this.config.serviceKeyExpiresMs, Number(env.MAIL_SERVICE_PIN_EXPIRES_MS));
    return sentinel;
  });
  assert.equal(await createWorker().fetch(req, env), sentinel); assert.equal(calls, 1);
  assert.deepEqual(Object.keys(await import('../worker.js')).sort(), ['createWorker', 'default']);
});

for (const path of obsolete) test(`${path} is explicitly retired before storage, legacy flags or network`, async () => {
  const env = { MAIL_ENABLED: 'true', MAIL_ENROLLMENT_ENABLED: 'true', MAIL_LIFECYCLE_ENABLED: 'true' };
  Object.defineProperty(env, 'MAIL_DB', { get() { assert.fail('no legacy storage'); } });
  const response = await createWorker({ send() { assert.fail('no legacy send'); },
    fetcher() { assert.fail('no CP/tunnel'); }, enrollment: { preflight() { assert.fail('no preflight'); } } }).fetch(request(path), env);
  assert.equal(response.status, 410); assert.deepEqual(await response.json(), { code: 'legacy_retired' });
  assert.equal(response.headers.get('cache-control'), 'no-store'); assert.equal(response.headers.get('x-mail-signature'), null);
});

test('unknown paths and unsupported legacy spellings remain 404 without configuration', async () => {
  for (const [method, path] of [['GET', '/v1/mail/send'], ['POST', '/v1/mail/enrollment/begin?x=1'],
    ['POST', '/v1/mail/lifecycle/status/'], ['POST', '/v1/mail/lifecycle/other'], ['POST', '/v1/pull/unknown'],
    ['POST', '/v1/pull/enqueue']]) {
    const response = await createWorker().fetch(new Request(ORIGIN + path, { method }), { MAIL_ENABLED: 'true' });
    assert.equal(response.status, 404, path); assert.deepEqual(await response.json(), { code: 'not_found' });
  }
});

test('mail flag remains strict and gates fetch, SMTP and scheduled before dependencies', async () => {
  for (const flag of [undefined, 'false', 'TRUE', true]) {
    const env = { MAIL_ENABLED: flag }, worker = createWorker(), m = emailMessage();
    const response = await worker.fetch(request('/v1/mail/enrollment/begin'), env);
    assert.equal(response.status, 503); assert.deepEqual(await response.json(), { code: 'mail_disabled' });
    await worker.email(m, env); assert.deepEqual(m.rejects, ['Agent mail disabled']);
    assert.equal(await worker.scheduled({}, env), undefined);
  }
});

test('missing config fails closed with a sanitized error and no legacy fallback', async (t) => {
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  for (const path of [...direct.map(([p]) => p), ...LINK_PATHS, SOCKET_PATH]) {
    const response = await createWorker().fetch(request(path), { MAIL_ENABLED: 'true' });
    assert.equal(response.status, 503, path);
    assert.deepEqual(await response.json(), { v: 1, code: 'service_unavailable' });
  }
  assert.equal(logs.length, direct.length + LINK_PATHS.length + 1);
  assert.ok(logs.every((s) => s === '[agent-mail] pull_http_storage_or_configuration_failure'));
});

test('HTTP uses the existing edge-source resolver, ignoring caller forwarding/source/install hints', async (t) => {
  const env = await envFor(); const sources = [];
  t.mock.method(SessionAuth.prototype, 'handle', async (_r, context) => { sources.push(context.trustedSource); return new Response('ok'); });
  const worker = createWorker();
  await worker.fetch(request(TICKET_PATH, { 'x-forwarded-for': 'attacker', 'x-real-ip': 'attacker',
    'x-clayrune-socket-source': 'attacker', 'x-clayrune-socket-install': 'attacker' }), env);
  await worker.fetch(request(TICKET_PATH, { 'cf-worker': 'example.com', 'cf-connecting-ip': '203.0.113.1' }), env);
  assert.deepEqual(sources, [`edge:${EDGE}`, 'cloudflare-worker']);
});

test('unexpected HTTP handler errors retain only the closed operational failure', async (t) => {
  const env = await envFor(), logs = [];
  t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  t.mock.method(SessionAuth.prototype, 'handle', async () => { throw new Error('private token SQL owner@example.com'); });
  const response = await createWorker().fetch(request(TICKET_PATH), env);
  assert.equal(response.status, 503); assert.deepEqual(await response.json(), { v: 1, code: 'service_unavailable' });
  assert.deepEqual(logs, ['[agent-mail] pull_http_storage_or_configuration_failure']);
});

async function real(t, kind = 'status') {
  const f = await fixture(t, kind);
  // Existing fixture has transactional first/run/batch; sweep additionally reads all().
  const prepare = f.db.prepare.bind(f.db);
  f.db.prepare = (query) => {
    const statement = prepare(query);
    statement.all = async () => ({ success: true, results: f.sql.prepare(query).all(...statement.args) });
    return statement;
  };
  f.env = await envFor(f.db);
  f.env.MAIL_SERVICE_PRIVATE_KEY = f.service.privateKey;
  f.env.MAIL_INGRESS_VERIFIED = 'true';
  f.wakes = [];
  f.env.MAIL_PULL_MAILBOX = { idFromName(install) { assert.equal(install, id(1)); return install; },
    get(install) { assert.equal(install, id(1)); return { async wake(received) {
      assert.equal(received, id(1)); f.wakes.push(received);
      if (f.onWake) return f.onWake();
      return { connected: false };
    } }; } };
  f.worker = createWorker({ clock: () => f.time });
  return f;
}

test('real signed status survives public entry with the original signature scope', async (t) => {
  const f = await real(t), req = await machine(f, {}); req.headers.set('cf-connecting-ip', EDGE);
  const proof = req.clone(), response = await f.worker.fetch(req, f.env);
  assert.equal(response.status, 200);
  const body = await signed(f, response, proof); assert.equal(body.code, 'ok');
  assert.equal(body.state, 'active'); assert.equal(f.sql.prepare('SELECT count(*) AS n FROM nonces').get().n, 0);
});

async function thread(f) {
  const i = f.sql.prepare('SELECT * FROM pull_installs').get();
  f.sql.prepare(`INSERT INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,thread_id,message_id,state,created_ms)
    VALUES(?,7,'intent',3,?,?,'thread',?,'accepted',?)`).run(id(1), i.owner_hash, 'b'.repeat(64), SENT, f.time);
  f.sql.prepare("INSERT INTO pull_threads VALUES(?,7,'thread',3,'intent',?,?,?,?,NULL)")
    .run(id(1), await sha256(bytes(TOKEN)), SENT, f.time + 90 * 86400000, f.time + 120 * 86400000);
}
function message(over = {}) {
  const m = emailMessage({ token: TOKEN, outId: SENT, from: 'Owner+tag@example.com', envelope: 'Owner+tag@example.com', ...over });
  m.to = `${LOCAL}+${TOKEN}@mail.example.net`; return m;
}

test('public SMTP commits only pull custody before DO wake, and scheduled retries that notification', async (t) => {
  const f = await real(t); await thread(f);
  f.onWake = () => {
    assert.equal(f.sql.prepare('SELECT text FROM pull_events').get().text, 'A fresh reply');
    assert.equal(f.sql.prepare('SELECT count(*) AS n FROM pull_notifications').get().n, 1);
    return { connected: false };
  };
  const m = message(); const result = await f.worker.email(m, f.env);
  assert.equal(result.status, 'queued'); assert.deepEqual(m.rejects, []); assert.deepEqual(f.wakes, [id(1)]);
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM events').get().n, 0);
  const sweep = await f.worker.scheduled({}, f.env);
  assert.deepEqual(sweep.notify, { claimed: 1, woken: 1 }); assert.deepEqual(f.wakes, [id(1), id(1)]);
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM pull_events').get().n, 1);
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM pull_notifications').get().n, 1);
});

test('missing DO binding keeps durable SMTP custody and scheduled notification for retry', async (t) => {
  const f = await real(t); await thread(f); delete f.env.MAIL_PULL_MAILBOX;
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  assert.equal((await f.worker.email(message(), f.env)).status, 'queued');
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM pull_notifications').get().n, 1);
  const result = await f.worker.scheduled({}, f.env);
  assert.deepEqual(result.notify, { claimed: 1, woken: 0 });
  assert.equal(f.sql.prepare('SELECT count(*) AS n FROM pull_notifications').get().n, 1);
  assert.deepEqual(logs, ['[agent-mail] pull_ingress_wake_failed', '[agent-mail] pull_sweep_wake_failed']);
});

test('SMTP and scheduled storage failures throw sanitized errors rather than accepting or replaying legacy work', async (t) => {
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  const env = { MAIL_ENABLED: 'true', MAIL_INGRESS_VERIFIED: 'true', MAIL_DOMAIN: 'mail.example.net',
    MAIL_DB: { prepare() { throw new Error('private storage detail'); } } };
  const worker = createWorker();
  await assert.rejects(worker.email(message(), env), { message: 'agent_mail_custody_failed' });
  await assert.rejects(worker.scheduled({}, env), { message: 'agent_mail_sweep_failed' });
  assert.ok(logs.every((s) => !s.includes('private storage detail')));
  assert.ok(logs.includes('[agent-mail] ingress_storage_or_capacity_failure'));
  assert.ok(logs.includes('[agent-mail] pull_sweep_storage_or_configuration_failure'));
});

// ---- U7: signup admission option and the control-mail consumer in the cron ----

test('admission reaches only SignupHTTP, and only the literal "open" admits (absent/other = closed)', async (t) => {
  const seen = [];
  for (const Handler of [SignupHTTP, OwnerManagementHTTP, OwnerChangeHTTP]) {
    t.mock.method(Handler.prototype, 'handle', async function () {
      seen.push([Handler.name, this.freshSignupEnabled]); return new Response('ok');
    });
  }
  const paths = [SIGNUP_PATHS[0], MANAGEMENT_PATHS.find((p) => !LINK_PATHS.includes(p)),
    OWNER_CHANGE_PATHS.find((p) => !LINK_PATHS.includes(p))];
  for (const [value, expected] of [['open', true], [undefined, false], ['', false], ['OPEN', false], ['true', false],
    ['closed', false], [true, false], [1, false]]) {
    seen.length = 0;
    const env = await envFor(); if (value !== undefined) env.MAIL_SIGNUP_ADMISSION = value;
    for (const path of paths) await createWorker({ clock: () => NOW }).fetch(request(path), env);
    // Management/new-owner extend SignupHTTP but are never given the option: always closed.
    assert.deepEqual(seen, [['SignupHTTP', expected], ['OwnerManagementHTTP', false], ['OwnerChangeHTTP', false]],
      String(value));
  }
});

// Real SQLite/D1 semantics (transactions, rollback, SQL clock) under the real Worker entry.
function d1() {
  const sql = new DatabaseSync(':memory:'); sql.exec('PRAGMA foreign_keys=ON');
  sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  const dir = new URL('../migrations/', import.meta.url);
  for (const n of readdirSync(dir).filter((x) => /^\d+.*\.sql$/.test(x)).sort()) sql.exec(readFileSync(new URL(n, dir), 'utf8'));
  return { sql, prepare(query) {
    return { args: [], bind(...args) { this.args = args; return this; },
      async first() { return sql.prepare(query).get(...this.args) ?? null; },
      async all() { return { success: true, results: sql.prepare(query).all(...this.args) }; },
      async run() { return this.runSync(); },
      runSync() { const q = sql.prepare(query);
        return q.columns().length ? { success: true, results: q.all(...this.args), meta: { changes: 0 } }
          : { success: true, meta: { changes: Number(q.run(...this.args).changes) } }; } }; },
  async batch(statements) {
    sql.exec('BEGIN IMMEDIATE');
    try { const out = statements.map((s) => s.runSync()); sql.exec('COMMIT'); return out; }
    catch (error) { sql.exec('ROLLBACK'); throw error; }
  } };
}
async function signupFixture(t, over = {}) {
  const db = d1(); t.after(() => db.sql.close());
  const f = { db, time: NOW, nonce: 100, candidate: await keypair(), service: await keypair(), sent: [], wakes: [] };
  db.sql.function('strftime', (format, at) => {
    assert.equal(at, 'now');
    return format === '%s' ? String(Math.floor(f.time / 1000))
      : `${String(Math.floor(f.time / 1000) % 60).padStart(2, '0')}.${String(f.time % 1000).padStart(3, '0')}`;
  });
  f.env = { ...await envFor(db), MAIL_SIGNUP_ADMISSION: 'open', MAIL_SERVICE_PRIVATE_KEY: f.service.privateKey,
    MAIL_SEND: { send(message) { f.sent.push(message); return { messageId: `accepted-${f.sent.length}` }; } },
    MAIL_PULL_MAILBOX: { idFromName: (n) => n, get: () => ({ wake: async (n) => { f.wakes.push(n); } }) }, ...over };
  f.worker = createWorker({ clock: () => f.time });
  f.value = { op_id: id(1), flow_id: id(2), public_key: f.candidate.publicKey, key_id: 'candidate',
    owner: 'Owner@example.com', flow_secret_hash: await sha256(bytes('e'.repeat(64))),
    fence_hash: await sha256(bytes('f'.repeat(64))), consent_version: 'v1', manifest_sequence: 1, manifest_digest: 'a'.repeat(64) };
  return f;
}
// Independent literal v2 transcript, as in signup-http.test.js: never uses the implementation to sign.
async function sign(f, path, value) {
  const body = JSON.stringify(value), fields = { version: '2', direction: 'install-to-pull', key_id: 'candidate',
    install_id: id(3), generation: '1', owner_generation: '1', timestamp: String(f.time), nonce: id(f.nonce++),
    digest: await sha256(bytes(body)) };
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', fields.direction, ORIGIN, 'POST', path,
    fields.key_id, fields.install_id, '1', '1', fields.timestamp, fields.nonce, fields.digest]));
  const headers = new Headers({ 'content-type': 'application/json', 'cf-connecting-ip': EDGE,
    'x-mail-signature': base64url(await crypto.subtle.sign('Ed25519', f.candidate.key, input)) });
  for (const [k, v] of Object.entries(fields)) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
  return new Request(ORIGIN + path, { method: 'POST', headers, body });
}
const outbox = (f) => f.db.sql.prepare('SELECT * FROM pull_control_outbox').all();

test('real entry: signup start -> queued outbox -> cron -> fake MAIL_SEND -> link confirm -> signed status -> activate', async (t) => {
  const f = await signupFixture(t);
  const start = await f.worker.fetch(await sign(f, SIGNUP_PATHS[0], f.value), f.env);
  assert.equal(start.status, 200); assert.equal((await start.json()).state, 'pending');
  assert.deepEqual(outbox(f).map((r) => r.state), ['queued']); assert.equal(f.sent.length, 0);
  const result = await f.worker.scheduled({}, f.env);
  assert.deepEqual(result.control, { scanned: 1, submitted: 1 }); assert.deepEqual(result.notify, { claimed: 0, woken: 0 });
  assert.equal(f.sent.length, 1); assert.equal(f.sent[0].to, 'Owner@example.com');
  assert.equal(f.sent[0].from, 'agent@mail.example.net');
  assert.deepEqual(outbox(f).map((r) => [r.state, r.envelope_sealed]), [['accepted', null]]);
  const token = /token=([a-f0-9]{64})/.exec(f.sent[0].text)?.[1]; assert.ok(token);
  // The owner's browser: hosted GET (CSRF page) then POST, both through the public entry.
  const page = await f.worker.fetch(new Request(`${ORIGIN}/mail/link?token=${token}`, { headers: { 'cf-connecting-ip': EDGE } }), f.env);
  const text = await page.text(); assert.equal(page.status, 200);
  const csrf = /name="csrf" value="([a-f0-9]{64})"/.exec(text)[1], cookie = page.headers.get('set-cookie').split(';')[0];
  const confirm = await f.worker.fetch(new Request(`${ORIGIN}/mail/link/confirm`, { method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ORIGIN, cookie, 'cf-connecting-ip': EDGE },
    body: `token=${token}&csrf=${csrf}` }), f.env);
  assert.equal(confirm.status, 200);
  const status = await f.worker.fetch(await sign(f, SIGNUP_PATHS[1], { flow_id: id(2), flow_secret: 'e'.repeat(64) }), f.env);
  assert.equal(status.status, 200); assert.equal((await status.json()).state, 'verified');
  const activate = await f.worker.fetch(await sign(f, ACTIVATE_PATH,
    { op_id: id(10), flow_id: id(2), flow_secret: 'e'.repeat(64), consent_version: 'v1' }), f.env);
  assert.equal(activate.status, 200); assert.equal((await activate.json()).code, 'ok');
  assert.equal(f.db.sql.prepare('SELECT state FROM pull_installs').get().state, 'active');
  assert.equal(f.sent.length, 1, 'no second provider call');
});

test('admission absent: start is refused signed-503 signup_closed, nothing queued, cron sends nothing', async (t) => {
  const f = await signupFixture(t); delete f.env.MAIL_SIGNUP_ADMISSION;
  const response = await f.worker.fetch(await sign(f, SIGNUP_PATHS[0], f.value), f.env);
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, 'signup_closed');
  assert.equal(outbox(f).length, 0);
  assert.deepEqual((await f.worker.scheduled({}, f.env)).control, { scanned: 0, submitted: 0 });
  assert.equal(f.sent.length, 0);
});

test('consumer failure cannot suppress the notification sweep, and is surfaced after it', async (t) => {
  const f = await real(t); await thread(f);
  assert.equal((await f.worker.email(message(), f.env)).status, 'queued');
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  f.env.MAIL_SEND = { send() { assert.fail('provider must not be reached'); } };
  delete f.env.MAIL_FROM;  // consumer rejects its config before any storage or provider work
  const woken = f.wakes.length;
  await assert.rejects(f.worker.scheduled({}, f.env), { message: 'agent_mail_control_delivery_failed' });
  assert.equal(f.wakes.length, woken + 1, 'sweep still woke the pending notification');
  assert.deepEqual(logs, ['[agent-mail] control_delivery_failure']);
});

test('sweep failure cannot suppress control delivery, and the sweep error is the one surfaced', async (t) => {
  const f = await signupFixture(t);
  assert.equal((await f.worker.fetch(await sign(f, SIGNUP_PATHS[0], f.value), f.env)).status, 200);
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  const prepare = f.db.prepare.bind(f.db);
  f.db.prepare = (query) => {
    if (/FROM pull_notifications/.test(query)) throw new Error('private sweep storage detail');
    return prepare(query);
  };
  await assert.rejects(f.worker.scheduled({}, f.env), { message: 'agent_mail_sweep_failed' });
  assert.equal(f.sent.length, 1, 'control mail was delivered although the sweep failed');
  assert.ok(logs.includes('[agent-mail] pull_sweep_storage_or_configuration_failure'));
  assert.ok(logs.every((l) => !l.includes('private sweep storage detail') && !l.includes('Owner@example.com')));
});

test('absent seal config skips the consumer: provisioned installs keep their sweep, no provider call, no error', async (t) => {
  const f = await real(t); await thread(f);
  assert.equal((await f.worker.email(message(), f.env)).status, 'queued');
  for (const drop of [['MAIL_PULL_OUTBOX_KEY'], ['MAIL_PULL_OUTBOX_KEY_ID'], ['MAIL_PULL_OUTBOX_KEY', 'MAIL_PULL_OUTBOX_KEY_ID']]) {
    const env = { ...f.env }; for (const k of drop) delete env[k];
    env.MAIL_SEND = { send() { assert.fail('no provider call without seal config'); } };
    const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
    const result = await f.worker.scheduled({}, env);
    assert.equal(result.notify.claimed, 1); assert.equal(result.notify.woken, 1); assert.equal(result.control, undefined);
    assert.deepEqual(logs, []); t.mock.restoreAll();
    f.sql.prepare('UPDATE pull_notifications SET lease_id=NULL,lease_until_ms=NULL,next_attempt_ms=0').run();
    f.sql.prepare("DELETE FROM pull_sweep_cursors WHERE task='notify'").run();
  }
});

test('sweep result and disabled flag keep their contracts with the consumer wired in', async (t) => {
  const f = await real(t);
  const result = await f.worker.scheduled({}, f.env);
  assert.deepEqual(result.notify, { claimed: 0, woken: 0 }); assert.deepEqual(result.control, { scanned: 0, submitted: 0 });
  assert.equal(await f.worker.scheduled({}, { ...f.env, MAIL_ENABLED: 'false' }), undefined);
});

// W3: retain() had no caller, so no expired nonce/operation/audit row was ever deleted.
test('scheduled runs metadata retention as its own job: one family per tick, a full rotation reaches every table', async (t) => {
  const f = await real(t);
  f.sql.prepare("INSERT INTO pull_nonces VALUES('install-to-pull',?,7,'key','dead-nonce',1)").run(id(1));
  f.sql.prepare("INSERT INTO pull_nonces VALUES('install-to-pull',?,7,'key','live-nonce',?)").run(id(1), f.time + 86_400_000);
  const first = await f.worker.scheduled({}, f.env);
  assert.equal(first.retention.family, 'control'); assert.equal(typeof first.retention.scanned, 'number');
  assert.deepEqual(first.notify, { claimed: 0, woken: 0 });
  assert.ok(f.sql.prepare("SELECT 1 FROM pull_sweep_cursors WHERE task='metadata'").get(), 'retention cursor persisted');
  const seen = [first.retention.family];
  for (let n = 1; n < 17; n++) seen.push((await f.worker.scheduled({}, f.env)).retention.family);
  assert.equal(new Set(seen).size, 17, 'seventeen ticks visit seventeen distinct families');
  const nonces = f.sql.prepare('SELECT nonce FROM pull_nonces').all().map((r) => r.nonce);
  assert.ok(!nonces.includes('dead-nonce'), 'expired nonce deleted by the scheduled handler');
  assert.ok(nonces.includes('live-nonce'), 'unexpired nonce kept');
});

test('retention failure cannot suppress the sweep or control delivery, and is surfaced after both', async (t) => {
  const f = await real(t); await thread(f);
  assert.equal((await f.worker.email(message(), f.env)).status, 'queued');
  f.sql.prepare("INSERT INTO pull_sweep_cursors(task,cursor_ms,cursor_id,updated_ms) VALUES('metadata',0,'not json',1)").run();
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  const woken = f.wakes.length;
  await assert.rejects(f.worker.scheduled({}, f.env), { message: 'agent_mail_retention_failed' });
  assert.equal(f.wakes.length, woken + 1, 'sweep still woke the pending notification');
  assert.ok(logs.includes('[agent-mail] pull_retention_failure'));
  assert.ok(logs.every((l) => !l.includes('not json')));
});

test('sweep failure cannot suppress retention, and the sweep error is the one surfaced', async (t) => {
  const f = await real(t);
  const logs = []; t.mock.method(console, 'error', (...args) => logs.push(args.join(' ')));
  const prepare = f.db.prepare.bind(f.db);
  f.db.prepare = (query) => {
    if (/FROM pull_notifications/.test(query)) throw new Error('private sweep storage detail');
    return prepare(query);
  };
  await assert.rejects(f.worker.scheduled({}, f.env), { message: 'agent_mail_sweep_failed' });
  assert.ok(f.sql.prepare("SELECT 1 FROM pull_sweep_cursors WHERE task='metadata'").get(), 'retention ran despite the sweep failure');
});
