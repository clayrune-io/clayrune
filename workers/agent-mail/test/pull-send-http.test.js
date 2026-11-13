import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { PullSendHTTP, SEND_PATH, SEND_STATUS_PATH, projectSendResult } from '../pull-send-http.js';
import { bytes, sha256, base64url, unbase64url } from '../signing.js';

const NOW = 1794249600000, DAY = 86400000, ORIGIN = 'https://mail.example.com';
const id = (n) => n.toString(16).padStart(32, '0'), LOCAL = `m${'a'.repeat(29)}`;
const value = (extra = {}) => ({ op_id: id(10), intent_id: 'intent-1', thread_id: 'thread-1',
  reply_token: id(3), subject: 'Which revision?', body: 'Please review revision two.', kind: 'question', ...extra });
const rows = (f, table) => f.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all().map((r) => ({ ...r }));
const one = (f, table = 'pull_intents') => rows(f, table)[0];
const snapshot = (f) => ['pull_installs', 'pull_generations', 'pull_addresses', 'pull_intents', 'pull_threads',
  'pull_nonces', 'pull_operations', 'pull_budgets'].map((s) => rows(f, s));
async function keypair() {
  const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { key: k.privateKey, verify: k.publicKey, publicKey: base64url(await crypto.subtle.exportKey('raw', k.publicKey)),
    privateKey: base64url(await crypto.subtle.exportKey('pkcs8', k.privateKey)) };
}
// Every migration, real SQLite constraints and rollback, real Ed25519 on both
// sides. Hooks model serialized D1 races, not independent deployed writers.
async function fixture(t, { send = async () => ({ messageId: 'provider-id' }), bodyTimeoutMs = 10000 } = {}) {
  const sql = new DatabaseSync(':memory:'); t.after(() => sql.close()); sql.exec('PRAGMA foreign_keys=ON');
  sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  const dir = new URL('../migrations/', import.meta.url);
  for (const n of readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()) sql.exec(readFileSync(new URL(n, dir), 'utf8'));
  const f = { sql, time: NOW, calls: [], nonce: 100, source: 'trusted-edge', pending: new Map(), sequence: 0,
    candidate: await keypair(), service: await keypair() };
  sql.function('strftime', (format, at) => {
    assert.equal(at, 'now'); return format === '%s' ? String(Math.floor(f.time / 1000))
      : `${String(Math.floor(f.time / 1000) % 60).padStart(2, '0')}.${String(f.time % 1000).padStart(3, '0')}`;
  });
  f.db = {
    prepare(query) {
      return { sql: query, args: [], bind(...args) { assert.ok(args.length <= 100); this.args = args; return this; },
        async first() { if (f.db.beforeRead) await f.db.beforeRead(query); return sql.prepare(query).get(...this.args) ?? null; },
        async run() { return this.runSync(); },
        runSync() {
          const q = sql.prepare(query);
          return q.columns().length ? { success: true, results: q.all(...this.args), meta: { changes: 0 } }
            : { success: true, meta: { changes: Number(q.run(...this.args).changes) } };
        } };
    },
    async batch(statements) {
      if (this.beforeBatch) await this.beforeBatch(statements);
      sql.exec('BEGIN IMMEDIATE');
      try {
        const out = statements.map((s, n) => { if (this.inBatch) this.inBatch(s, n, statements.length); return s.runSync(); });
        sql.exec('COMMIT'); return out;
      } catch (error) { sql.exec('ROLLBACK'); throw error; }
    },
  };
  sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,
    key_id,public_key,consent_version,epoch,created_ms,updated_ms) VALUES(?,7,3,'active',?,'Owner+tag@example.com',?,
    'current',?,'v1',4,?,?)`).run(id(1), 'a'.repeat(64), LOCAL, f.candidate.publicKey, NOW, NOW);
  sql.prepare("INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',?,NULL)")
    .run(id(1), 'a'.repeat(64), f.candidate.publicKey, 'f'.repeat(64), id(2), NOW);
  sql.prepare("INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)").run(LOCAL, id(1), NOW);
  f.timers = { setTimeout(fn, delay) { const token = ++f.sequence; f.pending.set(token, { fn, delay }); return token; },
    clearTimeout(token) { f.pending.delete(token); } };
  f.http = new PullSendHTTP({ db: f.db, serviceOrigin: ORIGIN, serviceKeyId: 'service-key',
    servicePrivateKey: f.service.privateKey, clock: () => f.time, bodyTimeoutMs, timers: f.timers,
    config: { from: 'agents@mail.example.net', domain: 'mail.example.net' },
    binding: { async send(mail) { f.calls.push(mail); return send(mail, f); } } });
  return f;
}
async function machine(f, v = value(), options = {}) {
  const raw = options.raw ?? bytes(JSON.stringify(v)), path = options.path ?? SEND_PATH;
  const m = { version: '2', direction: 'install-to-pull', key_id: 'current', install_id: id(1), generation: '7',
    owner_generation: '3', timestamp: String(f.time), nonce: id(f.nonce++), digest: await sha256(raw), ...options.fields };
  const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction, ORIGIN, 'POST',
    options.signPath ?? path, m.key_id, m.install_id, m.generation, m.owner_generation, m.timestamp, m.nonce, m.digest]));
  const headers = new Headers({ 'content-type': 'application/json' });
  for (const [k, v] of Object.entries(m)) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
  headers.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', options.key ?? f.candidate.key, transcript)));
  for (const [k, v] of Object.entries(options.headers ?? {})) headers.set(k, v);
  return new Request(`${options.origin ?? ORIGIN}${path}${options.suffix ?? ''}`,
    { method: options.method ?? 'POST', headers, body: raw });
}
async function signedValue(f, response, request) {
  const raw = new Uint8Array(await response.arrayBuffer()), field = (n) => response.headers.get(`x-mail-${n}`);
  assert.equal(field('version'), '2'); assert.equal(field('direction'), 'pull-to-install');
  assert.equal(field('key-id'), 'service-key'); assert.equal(field('digest'), await sha256(raw));
  for (const name of ['install-id', 'generation', 'owner-generation', 'nonce']) assert.equal(field(name), request.headers.get(`x-mail-${name}`));
  const transcript = (path, status = response.status) => bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1',
    'pull-to-install', ORIGIN, 'POST', path, 'service-key', field('install-id'), field('generation'), field('owner-generation'),
    field('timestamp'), field('nonce'), String(status), field('digest')]));
  const path = new URL(request.url).pathname, wrong = path === SEND_PATH ? SEND_STATUS_PATH : SEND_PATH;
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(field('signature')), transcript(path)), true);
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(field('signature')), transcript(wrong)), false);
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(field('signature')), transcript(path, 201)), false);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  return JSON.parse(new TextDecoder().decode(raw));
}
async function run(f, v = value(), options = {}) {
  const request = await machine(f, v, options);
  const response = await f.http.handle(request, { trustedSource: options.source ?? f.source });
  const body = response.headers.has('x-mail-signature') ? await signedValue(f, response, request) : await response.json();
  return { request, response, body };
}
const common = (body) => assert.deepEqual(Object.keys(body).sort(), ['v', 'code', 'request_id'].sort());
const statusValue = (extra = {}) => ({ intent_id: 'intent-1', intent_generation: 7, ...extra });

test('valid send signs frozen owner-only receipt after nonce/op/accounting and provider evidence commit', async (t) => {
  const f = await fixture(t, { send: async (mail, f) => {
    assert.equal(one(f).state, 'sending'); assert.equal(rows(f, 'pull_nonces').length, 1);
    assert.equal(rows(f, 'pull_operations').length, 1); assert.equal(rows(f, 'pull_budgets').length, 3);
    assert.deepEqual(mail, { from: 'agents@mail.example.net', to: 'Owner+tag@example.com', replyTo: `${LOCAL}+${id(3)}@mail.example.net`,
      subject: value().subject, text: value().body, headers: { 'Auto-Submitted': 'auto-generated', 'X-Clayrune-Agent-Mail': '1' } });
    return { messageId: ' opaque receipt ' };
  } });
  const r = await run(f);
  assert.equal(r.response.status, 200); assert.equal(r.body.code, 'accepted'); assert.equal(r.body.request_id, value().op_id);
  assert.deepEqual(Object.keys(r.body).sort(), ['v', 'code', 'request_id', 'intent_id', 'thread_id', 'message_id', 'state', 'provider_id'].sort());
  assert.equal(r.body.provider_id, ' opaque receipt '); assert.equal(one(f).provider_id, r.body.provider_id);
  const op = one(f, 'pull_operations'); assert.equal(op.input_digest, r.request.headers.get('x-mail-digest'));
  assert.equal(op.expires_ms, NOW + 120 * DAY);
  assert.equal(JSON.stringify(snapshot(f)).includes(value().body), false);
  assert.equal(JSON.stringify(snapshot(f)).includes(value().reply_token), false);
  const again = await run(f); assert.deepEqual(again.body, r.body); assert.equal(f.calls.length, 1);
  assert.equal(rows(f, 'pull_nonces').length, 2);
  assert.equal(rows(f, 'pull_budgets').find((r) => r.bucket.startsWith('pull:http:send:')).count, 2);
  assert.ok(rows(f, 'pull_budgets').filter((r) => r.bucket.startsWith('pull:send:')).every((r) => r.count === 1));
  assert.equal(f.pending.size, 0);
});
for (const existing of [false, true]) test(`replayed nonce is signed refusal on ${existing ? 'existing' : 'new'} intent`, async (t) => {
  const f = await fixture(t); if (existing) await run(f);
  const request = await machine(f), nonce = request.headers.get('x-mail-nonce');
  f.sql.prepare("INSERT INTO pull_nonces VALUES('install-to-pull',?,7,'current',?,?)").run(id(1), nonce, NOW + 300001);
  const before = snapshot(f), res = await f.http.handle(request, { trustedSource: f.source });
  const body = await signedValue(f, res, request);
  assert.equal(res.status, 409); assert.equal(body.code, 'nonce_replayed'); common(body);
  assert.deepEqual(snapshot(f), before); assert.equal(f.calls.length, existing ? 1 : 0);
});
for (const path of [SEND_PATH, SEND_STATUS_PATH]) test(`wrong path proof refuses before nonce at ${path}`, async (t) => {
  const f = await fixture(t), r = await run(f, path === SEND_PATH ? value() : statusValue(),
    { path, signPath: path === SEND_PATH ? SEND_STATUS_PATH : SEND_PATH });
  assert.equal(r.response.status, 401); assert.deepEqual(r.body, { v: 1, code: 'unauthorized' });
  assert.equal(r.response.headers.has('x-mail-signature'), false); assert.equal(rows(f, 'pull_nonces').length, 0);
});
for (const over of [{ to: 'another@example.com' }, { raw: 'mime' }, { headers: {} }, { subject: 'bad\r\nBcc:x' },
  { body: '\ud800' }, { op_id: 'bad' }, { subject: 'x'.repeat(201) }, { body: 'x'.repeat(16385) }, { kind: 'approval' }]) {
  test(`closed authenticated send refuses ${JSON.stringify(over).slice(0, 60)}`, async (t) => {
    const f = await fixture(t), r = await run(f, value(over)); assert.equal(r.response.status, 400);
    assert.ok(['invalid_json', 'invalid_send'].includes(r.body.code)); common(r.body);
    assert.equal(f.calls.length, 0); assert.equal(rows(f, 'pull_nonces').length, 0);
  });
}
for (const raw of ['{"op_id":"a","op_id":"b"}', '{"op_id":"a","\\u006fp_id":"b"}', '{}x', '\ufeff{}',
  '{"intent_generation":-0}', '{"intent_generation":1.5}', '{"intent_generation":9007199254740992}', '{"a":{"b":1}}']) {
  test(`strict scalar parsing refuses ${raw.slice(0, 60)}`, async (t) => {
    const f = await fixture(t), r = await run(f, value(), { raw: bytes(raw) });
    assert.equal(r.response.status, 400); assert.equal(r.body.code, 'invalid_json'); common(r.body);
    assert.equal(rows(f, 'pull_nonces').length, 0);
  });
}
for (const path of [SEND_PATH, SEND_STATUS_PATH]) test(`queued generation change refuses and rolls back ${path}`, async (t) => {
  const f = await fixture(t); let changed = false;
  f.db.beforeBatch = async () => {
    if (!changed) { changed = true; f.sql.prepare('UPDATE pull_installs SET generation=8').run(); }
  };
  const r = await run(f, path === SEND_PATH ? value() : statusValue(), { path });
  assert.equal(r.response.status, 409); assert.equal(r.body.code, 'stale_generation'); common(r.body);
  for (const name of ['pull_intents', 'pull_threads', 'pull_nonces', 'pull_operations', 'pull_budgets']) assert.equal(rows(f, name).length, 0);
  assert.equal(f.calls.length, 0);
});
for (const state of ['disabled', 'frozen']) test(`pre-reservation ${state} refusal is common-prefix only`, async (t) => {
  const f = await fixture(t); f.sql.prepare('UPDATE pull_installs SET state=?').run(state);
  const r = await run(f); assert.equal(r.response.status, 409);
  assert.equal(r.body.code, state === 'frozen' ? 'registration_frozen' : 'registration_inactive'); common(r.body);
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(f.calls.length, 0);
});
test('pre-reservation revoked refusal is common-prefix only, even though revoked is also a receipt state', async (t) => {
  const f = await fixture(t);
  f.sql.prepare('INSERT INTO pull_fences VALUES(?,?,?,?,?,?,?,?,?)').run(id(1), id(2), 7, 'f'.repeat(64), 'revoke', id(90), id(91), 'b'.repeat(64), NOW);
  const r = await run(f); assert.equal(r.response.status, 409); assert.equal(r.body.code, 'revoked'); common(r.body);
  assert.equal(rows(f, 'pull_intents').length, 0); assert.equal(f.calls.length, 0);
});
for (const state of ['reserved', 'sending', 'uncertain', 'failed', 'revoked']) test(`existing ${state} has the complete 409 receipt and never resends`, async (t) => {
  const f = await fixture(t); await run(f);
  f.sql.prepare("UPDATE pull_intents SET state=?,provider_id='' ").run(state);
  const r = await run(f); assert.equal(r.response.status, 409); assert.equal(r.body.code, state); assert.equal(r.body.state, state);
  assert.equal(r.body.intent_id, value().intent_id); assert.equal(r.body.thread_id, value().thread_id);
  assert.equal(r.body.message_id, one(f).message_id); assert.equal(r.body.provider_id, ''); assert.equal(f.calls.length, 1);
  assert.equal(Object.keys(r.body).length, 8);
});
test('explicit provider nonacceptance becomes failed409, fresh retry/status never resubmits', async (t) => {
  const f = await fixture(t, { send: async () => { throw Object.assign(new Error('private exception'), { code: 'E_RECIPIENT_SUPPRESSED' }); } });
  const r = await run(f); assert.equal(r.response.status, 409); assert.equal(r.body.code, 'failed'); assert.equal(r.body.state, 'failed');
  assert.equal(r.body.provider_id, ''); assert.equal((await run(f)).body.state, 'failed');
  const s = await run(f, statusValue(), { path: SEND_STATUS_PATH });
  assert.equal(s.response.status, 200); assert.equal(s.body.code, 'ok'); assert.equal(s.body.state, 'failed');
  assert.equal(s.body.request_id, s.request.headers.get('x-mail-nonce')); assert.equal(f.calls.length, 1);
});
test('provider timeout is uncertain409, late positive completion cannot resend or upgrade it', async (t) => {
  let resolve, entered; const ready = new Promise((r) => { entered = r; });
  const f = await fixture(t, { send: () => { entered(); return new Promise((r) => { resolve = r; }); } });
  const pending = run(f); await ready;
  for (const timer of f.pending.values()) timer.fn();
  const r = await pending; assert.equal(r.response.status, 409); assert.equal(r.body.code, 'uncertain');
  resolve({ messageId: 'late receipt' }); await Promise.resolve();
  assert.equal((await run(f)).body.state, 'uncertain'); assert.equal(one(f).provider_id, ''); assert.equal(f.calls.length, 1);
});
test('status lookup signs metadata only, cannot cross owner or install, and never calls provider', async (t) => {
  const f = await fixture(t); await run(f); const state = one(f);
  const s = await run(f, statusValue(), { path: SEND_STATUS_PATH }); assert.equal(s.response.status, 200);
  assert.deepEqual(s.body, { v: 1, code: 'ok', request_id: s.request.headers.get('x-mail-nonce'),
    ...Object.fromEntries(['intent_id', 'thread_id', 'message_id', 'state', 'provider_id'].map((k) => [k, state[k]])) });
  for (const change of ["owner_generation=2", "owner_hash='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'"]) {
    f.sql.prepare(`UPDATE pull_intents SET ${change}`).run();
    const r = await run(f, statusValue(), { path: SEND_STATUS_PATH });
    assert.equal(r.response.status, 404); assert.equal(r.body.code, 'unknown_intent'); common(r.body);
  }
  assert.equal(f.calls.length, 1);
});
test('current disabled/frozen key can read old-generation metadata of the same owner only', async (t) => {
  const f = await fixture(t); await run(f);
  f.sql.prepare("UPDATE pull_installs SET generation=8,state='disabled'").run();
  f.sql.prepare("UPDATE pull_generations SET state='fenced',fenced_ms=?").run(NOW);
  f.sql.prepare("INSERT INTO pull_generations VALUES(?,8,3,?,'current',?,?,?,'fenced',?,NULL)")
    .run(id(1), 'a'.repeat(64), f.candidate.publicKey, 'f'.repeat(64), id(8), NOW);
  for (const state of ['disabled', 'frozen']) {
    f.sql.prepare('UPDATE pull_installs SET state=?').run(state);
    const r = await run(f, statusValue(), { path: SEND_STATUS_PATH, fields: { generation: '8' } });
    assert.equal(r.response.status, 200); assert.equal(r.body.code, 'ok'); assert.equal(r.body.state, 'accepted');
  }
  assert.equal(f.calls.length, 1);
});
test('status nonce replay refuses and malformed status is closed', async (t) => {
  const f = await fixture(t), r = await run(f, statusValue(), { path: SEND_STATUS_PATH });
  assert.equal(r.response.status, 404); // A real authenticated lookup charges once.
  const replay = await run(f, statusValue(), { path: SEND_STATUS_PATH, fields: { nonce: r.request.headers.get('x-mail-nonce') } });
  assert.equal(replay.response.status, 409); assert.equal(replay.body.code, 'nonce_replayed'); common(replay.body);
  for (const extra of [{ op_id: id(1) }, { intent_generation: 0 }, { intent_generation: '7' }, { to: 'other@example.com' }]) {
    const invalid = await run(f, statusValue(extra), { path: SEND_STATUS_PATH }); assert.equal(invalid.response.status, 400);
  }
});
test('operation digest conflict and exact-body intent conflict are common errors without second charge', async (t) => {
  const f = await fixture(t); await run(f); const before = snapshot(f);
  const op = await run(f, value({ body: 'Changed' })); assert.equal(op.body.code, 'operation_conflict'); common(op.body);
  const intent = await run(f, value({ op_id: id(11) })); assert.equal(intent.body.code, 'intent_conflict'); common(intent.body);
  assert.deepEqual(snapshot(f), before); assert.equal(f.calls.length, 1);
});
for (const path of [SEND_PATH, SEND_STATUS_PATH]) test(`daily HTTP send budget is 200 and never charges ticket budgets at ${path}`, async (t) => {
  const f = await fixture(t), bucket = `pull:http:send:${id(1)}:${Math.floor(NOW / DAY)}`;
  f.sql.prepare('INSERT INTO pull_budgets VALUES(?,199,?)').run(bucket, NOW + 2 * DAY);
  await run(f, path === SEND_PATH ? value() : statusValue(), { path });
  const r = await run(f, path === SEND_PATH ? value() : statusValue(), { path });
  assert.equal(r.response.status, 429); assert.equal(r.body.code, 'request_limit');
  const midnight = (Math.floor(NOW / DAY) + 1) * DAY;
  assert.equal(r.body.retry_after_ms, midnight);
  assert.equal(r.response.headers.get('retry-after'), String(Math.ceil((midnight - NOW) / 1000)));
  assert.equal(rows(f, 'pull_budgets').find((r) => r.bucket === bucket).count, 200);
  assert.equal(rows(f, 'pull_budgets').some((r) => /management|ticket/.test(r.bucket)), false);
});
test('post-commit storage failure is storage_unavailable, never replay of its own nonce or a second send', async (t) => {
  const f = await fixture(t); f.db.beforeBatch = async (statements) => {
    if (statements.some((s) => s.sql.includes('UPDATE pull_intents SET state=?'))) throw new Error('storage fault');
  };
  const r = await run(f); assert.equal(r.response.status, 503); assert.equal(r.body.code, 'storage_unavailable');
  assert.equal(one(f).state, 'sending'); assert.equal(rows(f, 'pull_nonces').length, 1); assert.equal(f.calls.length, 1);
  assert.equal((await run(f)).body.state, 'sending'); assert.equal(f.calls.length, 1);
});
test('racing same nonce at first batch is nonce_replayed with no swallowed duplicate', async (t) => {
  const f = await fixture(t); let injected = false;
  f.db.beforeBatch = async (statements) => {
    if (!injected && statements.some((s) => s.sql.includes('INSERT INTO pull_intents'))) {
      injected = true; await run(f, value(), { fields: { nonce: id(100) } });
    }
  };
  const r = await run(f); assert.equal(r.response.status, 409); assert.equal(r.body.code, 'nonce_replayed'); common(r.body);
  assert.equal(rows(f, 'pull_nonces').length, 1); assert.equal(f.calls.length, 1);
});
test('racing distinct nonce reserves one intent and returns a guarded lookup to the loser', async (t) => {
  const f = await fixture(t); let injected = false;
  f.db.beforeBatch = async (statements) => {
    if (!injected && statements.some((s) => s.sql.includes('INSERT INTO pull_intents'))) {
      injected = true; await run(f);
    }
  };
  const r = await run(f); assert.equal(r.response.status, 200); assert.equal(r.body.code, 'accepted');
  assert.equal(rows(f, 'pull_nonces').length, 2); assert.equal(f.calls.length, 1);
  assert.ok(rows(f, 'pull_budgets').filter((r) => r.bucket.startsWith('pull:send:')).every((r) => r.count === 1));
});
test('maximally escaped 16KiB body remains within the 128KiB send upload ceiling', async (t) => {
  const f = await fixture(t), r = await run(f, value({ body: '\u0001'.repeat(16384) }));
  assert.equal(r.response.status, 200); assert.equal(f.calls.length, 1);
});

for (const path of [SEND_PATH, SEND_STATUS_PATH]) test(`every first-batch fault rolls back nonce, op and HTTP charge at ${path}`, async (t) => {
  const baseline = await fixture(t); let width;
  baseline.db.beforeBatch = async (statements) => { width ??= statements.length; };
  await run(baseline, path === SEND_PATH ? value() : statusValue(), { path });
  for (let index = 0; index < width; index++) await t.test(`statement ${index + 1}/${width}`, async (t) => {
    const f = await fixture(t), before = snapshot(f);
    f.db.inBatch = (s, n) => { if (n === index) throw new Error('synthetic storage fault'); };
    const r = await run(f, path === SEND_PATH ? value() : statusValue(), { path });
    assert.equal(r.response.status, 503); assert.equal(r.body.code, 'storage_unavailable');
    assert.deepEqual(snapshot(f), before); assert.equal(f.calls.length, 0);
  });
});
for (const path of [SEND_PATH, SEND_STATUS_PATH]) test(`queued expired proof cannot claim nonce at ${path}`, async (t) => {
  const f = await fixture(t); let queued = false;
  f.db.beforeBatch = async () => { if (!queued) { queued = true; f.time += 300001; } };
  const r = await run(f, path === SEND_PATH ? value() : statusValue(), { path });
  assert.equal(r.response.status, 401); assert.equal(r.body.code, 'unauthorized'); common(r.body);
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(rows(f, 'pull_operations').length, 0);
  assert.equal(rows(f, 'pull_budgets').length, 0); assert.equal(f.calls.length, 0);
});
test('competing last HTTP budget reservation rolls back our nonce, intent and operation', async (t) => {
  const f = await fixture(t), bucket = `pull:http:send:${id(1)}:${Math.floor(NOW / DAY)}`; let queued = false;
  f.sql.prepare('INSERT INTO pull_budgets VALUES(?,199,?)').run(bucket, NOW + 2 * DAY);
  f.db.beforeBatch = async () => {
    if (!queued) { queued = true; f.sql.prepare('UPDATE pull_budgets SET count=200 WHERE bucket=?').run(bucket); }
  };
  const r = await run(f); assert.equal(r.response.status, 429); assert.equal(r.body.code, 'request_limit');
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(rows(f, 'pull_intents').length, 0);
  assert.equal(rows(f, 'pull_operations').length, 0); assert.equal(f.calls.length, 0);
});
test('competing operation digest refuses instead of swallowing its failed guard as an intent duplicate', async (t) => {
  const f = await fixture(t); let queued = false;
  f.db.beforeBatch = async () => {
    if (!queued) {
      queued = true;
      f.sql.prepare("INSERT INTO pull_operations VALUES(?,7,?,?,?,'{}',200,?,?)")
        .run(id(1), SEND_PATH, value().op_id, 'b'.repeat(64), NOW, NOW + 120 * DAY);
    }
  };
  const r = await run(f); assert.equal(r.response.status, 409); assert.equal(r.body.code, 'operation_conflict'); common(r.body);
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(rows(f, 'pull_intents').length, 0);
  assert.equal(rows(f, 'pull_budgets').length, 0); assert.equal(f.calls.length, 0);
});
for (const path of [SEND_PATH, SEND_STATUS_PATH]) test(`current key/owner snapshot cannot change during ${path} transaction`, async (t) => {
  const f = await fixture(t); let queued = false;
  f.db.beforeBatch = async () => {
    if (!queued) { queued = true; f.sql.prepare("UPDATE pull_installs SET public_key=?").run('b'.repeat(43)); }
  };
  const r = await run(f, path === SEND_PATH ? value() : statusValue(), { path });
  assert.equal(r.response.status, 409); assert.equal(r.body.code, 'stale_generation'); common(r.body);
  assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(f.calls.length, 0);
});
for (const [name, options] of [
  ['legacy signature', { fields: { version: '1' } }], ['bad signature', { headers: { 'x-mail-signature': 'bad' } }],
  ['stale generation', { fields: { generation: '6' } }], ['stale owner', { fields: { owner_generation: '2' } }],
  ['foreign install', { fields: { install_id: id(2) } }], ['old proof', { fields: { timestamp: String(NOW - 300001) } }],
  ['future proof', { fields: { timestamp: String(NOW + 300001) } }], ['different origin', { origin: 'https://other.example.com' }],
  ['query', { suffix: '?x=1' }], ['bare query', { suffix: '?' }], ['fragment', { suffix: '#x' }],
  ['browser origin', { headers: { origin: ORIGIN } }], ['encoding', { headers: { 'content-encoding': 'gzip' } }],
  ['oversized declared body', { headers: { 'content-length': '131073' } }],
  ['oversized headers', { headers: { 'x-junk': 'x'.repeat(8192) } }],
]) test(`uniform unauthenticated refusal: ${name}`, async (t) => {
  const f = await fixture(t), r = await run(f, value(), options);
  assert.equal(r.response.status, 401); assert.deepEqual(r.body, { v: 1, code: 'unauthorized' });
  assert.equal(r.response.headers.has('x-mail-signature'), false); assert.equal(rows(f, 'pull_nonces').length, 0);
  assert.equal(rows(f, 'pull_budgets').length, 0); assert.equal(f.calls.length, 0);
});
test('upload limit applies to actual bytes and the status ceiling is 8KiB', async (t) => {
  for (const [path, size] of [[SEND_PATH, 131073], [SEND_STATUS_PATH, 8193]]) {
    const f = await fixture(t), r = await run(f, {}, { path, raw: bytes('x'.repeat(size)) });
    assert.equal(r.response.status, 401); assert.equal(rows(f, 'pull_nonces').length, 0); assert.equal(f.calls.length, 0);
  }
});
test('upload timeout is bounded and cancellation failure cannot extend it', async (t) => {
  const f = await fixture(t, { bodyTimeoutMs: 5 });
  const request = new Request(`${ORIGIN}${SEND_PATH}`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: new ReadableStream({ pull() { return new Promise(() => {}); }, cancel() { return new Promise(() => {}); } }), duplex: 'half' });
  const response = await f.http.handle(request, { trustedSource: f.source });
  assert.equal(response.status, 401); assert.deepEqual(await response.json(), { v: 1, code: 'unauthorized' });
  assert.equal(f.calls.length, 0);
});
test('invalid UTF8 is a signed invalid_json, with no nonce or private body in response', async (t) => {
  const f = await fixture(t), r = await run(f, {}, { raw: new Uint8Array([0xff]) });
  assert.equal(r.response.status, 400); assert.equal(r.body.code, 'invalid_json'); common(r.body);
  assert.equal(rows(f, 'pull_nonces').length, 0);
});
test('preflight provider configuration refusal creates no intent, nonce or operation', async (t) => {
  const f = await fixture(t); f.http.binding = {};
  const r = await run(f); assert.equal(r.response.status, 503); assert.equal(r.body.code, 'provider_unavailable');
  assert.equal(rows(f, 'pull_intents').length, 0); assert.equal(rows(f, 'pull_nonces').length, 0);
  assert.equal(rows(f, 'pull_operations').length, 0); assert.equal(f.calls.length, 0);
});
test('authenticated send-limit refusal is signed with the retry deadline; no second handoff', async (t) => {
  const f = await fixture(t), day = Math.floor(NOW / DAY);
  f.sql.prepare('INSERT INTO pull_budgets VALUES(?,20,?)').run(`pull:send:install:${id(1)}:${day}`, NOW + 2 * DAY);
  const r = await run(f); assert.equal(r.response.status, 429); assert.equal(r.body.code, 'send_limit');
  assert.equal(r.body.retry_after_ms, (day + 1) * DAY); assert.ok(r.response.headers.has('retry-after'));
  assert.equal(one(f).state, 'reserved'); assert.equal(f.calls.length, 0);
  const again = await run(f); assert.equal(again.body.state, 'reserved'); assert.equal(again.response.status, 409);
});
test('untrusted claimed install never acquires per-install rows; shared source/global refusal limits apply', async (t) => {
  const f = await fixture(t);
  f.sql.prepare('INSERT INTO rate_limits VALUES(?,30,?)')
    .run(`pull:http:source:${await sha256(bytes(f.source))}:${Math.floor(NOW / 60000)}`, NOW + 120000);
  const r = await run(f, value(), { fields: { install_id: id(99) } });
  assert.equal(r.response.status, 429); assert.deepEqual(r.body, { v: 1, code: 'abuse_limit' });
  assert.equal(rows(f, 'pull_budgets').length, 0); assert.equal(rows(f, 'pull_nonces').length, 0);
  f.sql.prepare('DELETE FROM rate_limits').run();
  f.sql.prepare('INSERT INTO rate_limits VALUES(?,10000,?)').run(`http:unauthenticated:${Math.floor(NOW / 60000)}`, NOW + 120000);
  const next = await run(f, value(), { fields: { install_id: id(99) } });
  assert.equal(next.response.status, 429); assert.equal(next.body.code, 'abuse_limit');
});

// Shared JSON vectors for unit21's Python verifier; no JS fixture dependency.
const vectors = JSON.parse(readFileSync(new URL('./fixtures/pull-send-receipt-vectors.json', import.meta.url), 'utf8'));
for (const vector of vectors.vectors) test(`shared receipt vector: ${vector.name}`, () => {
  const { v, request_id, ...fields } = vector.body;
  const project = () => projectSendResult({ status: vector.status, ...fields }, vectors.request, request_id);
  if (vector.valid) assert.deepEqual(project(), { status: vector.status, body: vector.body });
  else assert.throws(project, { message: 'storage_unavailable' });
  assert.equal(v, 1);
});
