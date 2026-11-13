import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { LinkRouter, LINK_PATHS } from '../link-router.js';
import { SignupHTTP } from '../signup-http.js';
import { OwnerManagementHTTP } from '../owner-management.js';
import { OwnerChangeHTTP } from '../owner-change.js';
import { VerificationMail } from '../verification-mail.js';
import { bytes, sha256 } from '../signing.js';

const NOW = 1_794_249_600_000;
const ORIGIN = 'https://mail.example.com';
const COOKIE = '__Secure-clayrune-mail';
const TOKEN = 'a'.repeat(64);
const SESSION = 'b'.repeat(64);
const CSRF = 'c'.repeat(64);
const ID = 'd'.repeat(32);
const FLOW = 'e'.repeat(32);
const hash = (s) => sha256(bytes(s));
const purposes = ['signup', 'stepup', 'new_owner'];

// Actual schema and SQLite queries; the fake D1 surface records every lookup
// and write. No provider/network. Handler spies isolate dispatch; separate tests
// use the real HTTP handlers and ledger for parsing and source accounting.
class D1 {
  constructor() {
    this.sql = new DatabaseSync(':memory:'); this.reads = []; this.writes = [];
    this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
    const dir = new URL('../migrations/', import.meta.url);
    for (const name of readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()) {
      this.sql.exec(readFileSync(new URL(name, dir), 'utf8'));
    }
  }
  prepare(sql) {
    const owner = this;
    return { args: [], sql, bind(...args) { this.args = args; return this; },
      async first() {
        owner.reads.push({ sql, args: this.args });
        if (owner.beforeRead) await owner.beforeRead();
        return owner.sql.prepare(sql).get(...this.args) ?? null;
      },
      async run() {
        owner.writes.push(sql);
        const r = owner.sql.prepare(sql).run(...this.args);
        return { success: true, meta: { changes: Number(r.changes) } };
      } };
  }
  async batch() { assert.fail('unexpected router batch'); }
}
async function fixture(t, purpose = 'signup') {
  const db = new D1(); t.after(() => db.sql.close());
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,created_ms,updated_ms)
    VALUES(?,1,1,'pending',?,'owner@example.com',?,'candidate',?,'v1',?,?)`)
    .run(ID, 'f'.repeat(64), 'm' + 'a'.repeat(29), 'A'.repeat(43), NOW, NOW);
  db.sql.prepare(`INSERT INTO pull_tokens(token_hash,install_id,flow_id,purpose,generation,owner_generation,
    owner_hash,binding_digest,state,created_ms,expires_ms) VALUES(?,?,?, ?,1,1,?,?,'pending',?,?)`)
    .run(await hash(TOKEN), ID, FLOW, purpose, 'f'.repeat(64), '1'.repeat(64), NOW, NOW + 600000);
  db.sql.prepare(`INSERT INTO pull_browser_sessions VALUES(?,?,?,?,?,'confirmation',1,1,'csrf',?,?)`)
    .run(await hash(SESSION), await hash(CSRF), ID, FLOW, await hash(TOKEN), NOW, NOW + 600000);
  const f = { db, time: NOW, calls: [], context: { trustedSource: 'synthetic-edge', extra: 'unchanged' } };
  const spy = (name) => ({ db, serviceOrigin: ORIGIN, now: () => f.time,
    async handle(request, context) { f.calls.push({ name, request, context }); return new Response(name); } });
  f.handlers = { signup: spy('signup'), management: spy('stepup'), ownerChange: spy('new_owner') };
  f.router = new LinkRouter(f.handlers);
  return f;
}
function get(token = TOKEN, suffix = '') { return new Request(`${ORIGIN}/mail/link?token=${token}${suffix}`); }
function post(options = {}) {
  const { body = `token=${TOKEN}&csrf=${CSRF}`, cookie = `${COOKIE}=${SESSION}`, headers = {}, url = `${ORIGIN}/mail/link/confirm` } = options;
  return new Request(url, { method: 'POST', body, duplex: 'half', headers: {
    origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded', cookie, ...headers } });
}
async function refused(f, request, expectedReads = 0) {
  const output = await f.router.handle(request, f.context);
  assert.equal(output.status, 404);
  assert.equal(await output.text(), '<!doctype html><html lang="en"><meta charset="utf-8"><title>Clayrune mail</title><p>not_found</p></html>');
  assert.equal(output.headers.get('cache-control'), 'no-store');
  assert.equal(output.headers.get('referrer-policy'), 'no-referrer');
  assert.match(output.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  assert.equal(output.headers.get('x-frame-options'), 'DENY');
  assert.equal(output.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(output.headers.get('set-cookie'), null);
  assert.deepEqual(f.calls, []);
  assert.equal(f.db.reads.length, expectedReads);
  assert.deepEqual(f.db.writes, []);
  assert.equal(request.bodyUsed, false);
}

for (const purpose of purposes) for (const method of ['GET', 'POST']) {
  test(`${method} selects only ${purpose}, one indexed read, original request and context`, async (t) => {
    const f = await fixture(t, purpose); const request = method === 'GET' ? get() : post();
    f.db.sql.exec('PRAGMA query_only=ON');
    const before = f.db.sql.prepare('SELECT total_changes() AS n').get().n;
    const response = await f.router.handle(request, f.context);
    assert.equal(await response.text(), purpose);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].request, request); assert.equal(f.calls[0].context, f.context);
    assert.equal(request.bodyUsed, false);
    assert.deepEqual(f.db.writes, []); assert.equal(f.db.reads.length, 1);
    const query = f.db.reads[0];
    assert.match(query.sql, /^SELECT /); assert.match(query.sql, /LIMIT 1$/);
    assert.equal(query.args[0], await hash(method === 'GET' ? TOKEN : SESSION));
    const plan = f.db.sql.prepare('EXPLAIN QUERY PLAN ' + query.sql).all(...query.args);
    assert.ok(plan.every((r) => r.detail.startsWith('SEARCH ')), JSON.stringify(plan));
    assert.equal(f.db.sql.prepare('SELECT total_changes() AS n').get().n, before);
  });
}
for (const [label, request] of [
  ['missing token', () => new Request(`${ORIGIN}/mail/link`)],
  ['short token', () => get('a'.repeat(63))],
  ['oversize token', () => get('a'.repeat(65))],
  ['oversize URL', () => get('a'.repeat(9000))],
  ['uppercase token', () => get('A'.repeat(64))],
  ['nonhex token', () => get('z'.repeat(64))],
  ['encoded token', () => get('%61' + TOKEN.slice(1))],
  ['duplicate token', () => get(TOKEN, `&token=${TOKEN}`)],
  ['extra query', () => get(TOKEN, '&purpose=signup')],
  ['fragment', () => get(TOKEN, '#extra')],
  ['trailing query separator', () => get(TOKEN, '&')],
  ['foreign origin', () => new Request(`https://other.example/mail/link?token=${TOKEN}`)],
  ['unclaimed path', () => new Request(`${ORIGIN}/mail/revert?token=${TOKEN}`)],
  ['wrong GET method', () => new Request(get(), { method: 'HEAD' })],
  ['GET upload', () => new Request(get(), { method: 'POST', body: 'extra' })],
  ['missing cookie', () => post({ cookie: '' })],
  ['short cookie', () => post({ cookie: `${COOKIE}=abc` })],
  ['oversize cookie', () => post({ cookie: `${COOKIE}=${SESSION}a` })],
  ['duplicate cookie', () => post({ cookie: `${COOKIE}=${SESSION}; ${COOKIE}=${SESSION}` })],
  ['extra cookie equals', () => post({ cookie: `${COOKIE}=${SESSION}=extra` })],
  ['foreign POST origin', () => post({ headers: { origin: 'https://other.example' } })],
  ['POST query', () => post({ url: `${ORIGIN}/mail/link/confirm?extra` })],
  ['bare POST query', () => post({ url: `${ORIGIN}/mail/link/confirm?` })],
  ['wrong confirm method', () => new Request(`${ORIGIN}/mail/link/confirm`)],
  ['wrong form type', () => post({ headers: { 'content-type': 'application/json' } })],
  ['encoded upload', () => post({ headers: { 'content-encoding': 'gzip' } })],
  ['oversize headers', () => post({ headers: { extra: 'x'.repeat(8192) } })],
  ['oversize content length', () => post({ headers: { 'content-length': '4097' } })],
  ['noncanonical length', () => post({ headers: { 'content-length': '01' } })],
  ['unsafe length', () => post({ headers: { 'content-length': '9007199254740993' } })],
]) test(`uniform refusal: ${label}`, async (t) => { await refused(await fixture(t), request()); });

for (const method of ['GET', 'POST']) for (const [label, sql] of [
  ['unknown token', 'DELETE FROM pull_browser_sessions; DELETE FROM pull_tokens'],
  ['expired token', `UPDATE pull_tokens SET expires_ms=${NOW}`],
  ['consumed token', "UPDATE pull_tokens SET state='consumed',result_json='{\"state\":\"confirmed\"}'"],
  ['invalidated token', "UPDATE pull_tokens SET state='invalidated'"],
  ['expired state', "UPDATE pull_tokens SET state='expired'"],
  ['unrouted revert purpose', "UPDATE pull_tokens SET purpose='revert'"],
  ['unknown purpose', "PRAGMA ignore_check_constraints=ON; UPDATE pull_tokens SET purpose='unknown'"],
]) test(`${method} refuses ${label} without purpose disclosure or writes`, async (t) => {
  const f = await fixture(t); f.db.sql.exec(sql); f.db.sql.exec('PRAGMA query_only=ON');
  await refused(f, method === 'GET' ? get() : post(), 1);
});
for (const [label, sql] of [
  ['unknown session', 'DELETE FROM pull_browser_sessions'],
  ['expired session', `UPDATE pull_browser_sessions SET expires_ms=${NOW}`],
  ['future session', `UPDATE pull_browser_sessions SET created_ms=${NOW + 1}`],
  ['overlong session', `UPDATE pull_browser_sessions SET expires_ms=${NOW + 600001}`],
  ['consumed session', "UPDATE pull_browser_sessions SET state='consumed'"],
  ['approved session', "UPDATE pull_browser_sessions SET state='approved'"],
  ['invalidated session', "UPDATE pull_browser_sessions SET state='invalidated'"],
  ['nonconfirmation session', "UPDATE pull_browser_sessions SET purpose='revert'"],
  ['mismatched flow', "UPDATE pull_browser_sessions SET flow_id='other'"],
  ['mismatched generation', 'UPDATE pull_browser_sessions SET generation=2'],
  ['mismatched owner generation', 'UPDATE pull_browser_sessions SET owner_generation=2'],
]) test(`POST refuses ${label}`, async (t) => {
  const f = await fixture(t); f.db.sql.exec(sql); await refused(f, post(), 1);
});
for (const method of ['GET', 'POST']) test(`${method} expires during routing lookup`, async (t) => {
  const f = await fixture(t); f.db.beforeRead = () => { f.time += 600000; };
  await refused(f, method === 'GET' ? get() : post(), 1);
});

test('POST body stream is read exactly once by the handler, never cloned or buffered by router', async (t) => {
  const f = await fixture(t); const wire = `csrf=${CSRF}&token=${TOKEN}`; let reads = 0;
  const request = post({ body: new ReadableStream({ start(c) { c.enqueue(bytes(wire)); c.close(); } }) });
  const reader = request.body.getReader.bind(request.body);
  request.body.getReader = (...args) => { reads++; return reader(...args); };
  request.clone = () => assert.fail('router must not clone the request');
  f.handlers.signup.handle = async (original) => {
    assert.equal(original, request); assert.equal(original.bodyUsed, false); assert.equal(reads, 0);
    assert.equal(await original.text(), wire); return new Response('handled');
  };
  assert.equal(await (await f.router.handle(request, f.context)).text(), 'handled');
  assert.equal(reads, 1); assert.equal(request.bodyUsed, true); assert.equal(f.db.reads.length, 1);
});

test('only the two shared paths are exported; mixed handler stores/origins refuse construction', async (t) => {
  const f = await fixture(t); assert.deepEqual(LINK_PATHS, ['/mail/link', '/mail/link/confirm']);
  assert.ok(Object.isFrozen(LINK_PATHS));
  assert.throws(() => new LinkRouter({ ...f.handlers, ownerChange: { ...f.handlers.ownerChange, db: {} } }), /invalid_request/);
  assert.throws(() => new LinkRouter({ ...f.handlers, management: { ...f.handlers.management, serviceOrigin: 'https://other.example' } }), /invalid_request/);
});

test('lookup failure is observable, fixed and contains no raw storage error', async (t) => {
  const f = await fixture(t); const log = t.mock.method(console, 'error', () => {});
  f.db.beforeRead = () => { throw new Error(`private storage details ${TOKEN}`); };
  const output = await f.router.handle(get(), f.context);
  assert.equal(output.status, 503); assert.match(await output.text(), /<p>storage_unavailable<\/p>/);
  assert.deepEqual(log.mock.calls.map((c) => c.arguments), [['[agent-mail] link_router_lookup_failed']]);
  assert.equal(f.calls.length, 0); assert.deepEqual(f.db.writes, []);
});

test('handler refusal or rejection never tries another handler', async (t) => {
  const f = await fixture(t); const refusal = new Response('not_found', { status: 404 }); let calls = 0;
  f.handlers.signup.handle = async () => { calls++; return refusal; };
  assert.equal(await f.router.handle(get(), f.context), refusal); assert.equal(calls, 1);
  const error = new Error('handler_failure');
  f.handlers.signup.handle = async () => { calls++; throw error; };
  await assert.rejects(f.router.handle(get(), f.context), (e) => e === error);
  assert.equal(calls, 2); assert.equal(f.calls.length, 0); assert.equal(f.db.reads.length, 2);
});

// The real HTTP handlers own sourceLimit, request-body parsing and refusalLimit.
// Only the final purpose action is replaced: no flow mutation/provider needed
// to prove that router discovery adds zero budget charges and zero body reads.
async function realHandlers(f) {
  const mail = new VerificationMail({ db: f.db, serviceOrigin: ORIGIN, sealKeyId: 'synthetic', sealKey: new Uint8Array(32).fill(4) });
  const config = { db: f.db, serviceOrigin: ORIGIN, domain: 'clayrune.example', verificationMail: mail,
    serviceKeyId: 'synthetic', servicePrivateKey: 'AQ', clock: () => f.time };
  const handlers = { signup: new SignupHTTP(config), management: new OwnerManagementHTTP(config), ownerChange: new OwnerChangeHTTP(config) };
  f.router = new LinkRouter(handlers); return handlers;
}
for (const purpose of purposes) for (const method of ['GET', 'POST']) {
  test(`${method} ${purpose}: real handler parses once and charges one source budget`, async (t) => {
    const f = await fixture(t, purpose); const handlers = await realHandlers(f); const actions = [];
    for (const [name, h] of Object.entries(handlers)) {
      h.getConfirmation = async (token) => { actions.push(name); assert.equal(token, TOKEN); return new Response('ok'); };
      h.confirm = async (request, token, csrf) => {
        actions.push(name); assert.equal(token, TOKEN); assert.equal(csrf, CSRF); assert.equal(request.bodyUsed, true);
        return new Response('ok');
      };
    }
    const request = method === 'GET' ? get() : post();
    assert.equal(await (await f.router.handle(request, f.context)).text(), 'ok');
    assert.deepEqual(actions, [{ signup: 'signup', stepup: 'management', new_owner: 'ownerChange' }[purpose]]);
    const budget = f.db.sql.prepare('SELECT * FROM rate_limits').all();
    assert.equal(budget.length, 1); assert.equal(budget[0].count, 1);
    assert.equal(budget[0].bucket, `pull:http:source:${await hash(f.context.trustedSource)}:${Math.floor(NOW / 60000)}`);
    assert.equal(f.db.writes.length, 1); assert.equal(f.db.reads.length, 1);
  });
}
for (const purpose of purposes) test(`${purpose}: wrong body CSRF stays the real handler's refusal, with one source charge`, async (t) => {
  const f = await fixture(t, purpose); await realHandlers(f);
  const result = await f.router.handle(post({ body: `token=${TOKEN}&csrf=${'0'.repeat(64)}` }), f.context);
  assert.equal(result.status, 401); assert.match(await result.text(), /csrf_failed/);
  const budgets = f.db.sql.prepare('SELECT * FROM rate_limits ORDER BY bucket').all();
  assert.equal(budgets.length, 2); assert.ok(budgets.every((b) => b.count === 1));
  assert.equal(f.db.sql.prepare('SELECT state FROM pull_tokens').get().state, 'pending');
});
