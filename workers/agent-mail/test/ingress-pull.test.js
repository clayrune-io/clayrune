import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { Miniflare, Log, LogLevel } from 'miniflare';
import { ingress } from '../ingress.js';
import { Ledger } from '../ledger.js';
import { bytes, sha256, base64url } from '../signing.js';
import { SQL_NOW } from '../sql-clock.js';
import { emailMessage } from './transport-support.js';
import { cfAr } from './fixtures.js';

const DAY = 86400000, MONTH = 30 * DAY, INSTALL = '1'.repeat(32), LOCAL = 'm' + 'a'.repeat(29);
const TOKEN = 'abcdef0123456789abcdef0123456789', SENT = 'sent@example.com';
const migrationDir = new URL('../migrations/', import.meta.url);
const sources = [readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'),
  ...readdirSync(migrationDir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()
    .map((s) => readFileSync(new URL(s, migrationDir), 'utf8'))];
class D1 {
  constructor() { this.sql = new DatabaseSync(':memory:'); this.sql.exec('PRAGMA foreign_keys=ON'); for (const s of sources) this.sql.exec(s); }
  prepare(sql) {
    const db = this.sql;
    return { sql, args: [], bind(...args) { assert.ok(args.length <= 100); this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return { success: true, results: db.prepare(sql).all(...this.args) }; },
      async run() { return this.runSync(); },
      runSync() { const s = db.prepare(sql); return s.columns().length ? { success: true, results: s.all(...this.args), meta: { changes: 0 } }
        : { success: true, meta: { changes: Number(s.run(...this.args).changes) } }; } };
  }
  async batch(statements) {
    this.lastBatch = statements; if (this.beforeBatch) await this.beforeBatch(statements);
    this.sql.exec('BEGIN IMMEDIATE');
    try { const r = statements.map((s, n) => { this.inBatch?.(s, n); return s.runSync(); }); this.sql.exec('COMMIT'); return r; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}
const rows = (f, table) => f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all().map((r) => ({ ...r }));
const one = (f, table) => rows(f, table)[0];
const snapshot = (f) => f.db.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
  .all().map(({ name }) => [name, rows(f, name)]);
async function seed(db, now, publicKey = 'k'.repeat(43), install = INSTALL) {
  const local = install === INSTALL ? LOCAL : 'm' + install.slice(3);
  const statements = [
    [`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,key_id,
      public_key,consent_version,created_ms,updated_ms) VALUES(?,7,3,'active',?,'owner@example.com',?,'key',?,'v1',?,?)`,
      install, 'a'.repeat(64), local, publicKey, now, now],
    ["INSERT INTO pull_generations VALUES(?,7,3,?,'key',?,?,?,'current',?,NULL)", install, 'a'.repeat(64), publicKey, 'f'.repeat(64), '2'.repeat(32), now],
    ["INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)", local, install, now],
    [`INSERT INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,thread_id,message_id,state,created_ms)
      VALUES(?,7,'intent',3,?,?,'thread',?,'accepted',?)`, install, 'a'.repeat(64), 'b'.repeat(64), SENT, now],
    ['INSERT INTO pull_threads VALUES(?,7,\'thread\',3,\'intent\',?,?,?,?,NULL)', install,
      await sha256(bytes(install === INSTALL ? TOKEN : install)), SENT, now + 90 * DAY, now + 120 * DAY],
  ];
  for (const [sql, ...args] of statements) await db.prepare(sql).bind(...args).run();
}
async function fixture(t) {
  const db = new D1(); t.after(() => db.sql.close()); const now = Date.now(); await seed(db, now);
  const ledger = new Ledger(db).pull(); return { db, ledger, now, wakes: [], config: { domain: 'mail.example.net', ingressVerified: true } };
}
function message(over = {}) {
  const m = emailMessage({ outId: SENT, ...over }); m.to = `${LOCAL}+${over.token ?? TOKEN}@mail.example.net`; return m;
}
async function receive(f, over = {}, wake = async (id) => { f.wakes.push(id); }) {
  const m = message(over); const result = await ingress({ message: m, ledger: f.ledger, config: f.config, now: Date.now(), wake });
  return { result, m };
}
function budget(f, which, count) {
  const bucket = which === 'global' ? `pull:ingress:global:${Math.floor(Date.now() / DAY)}`
    : `pull:ingress:install:${INSTALL}:${Math.floor(Date.now() / DAY)}`;
  f.db.sql.prepare('INSERT OR REPLACE INTO pull_budgets VALUES(?,?,?)').run(bucket, count, Date.now() + 2 * DAY);
}
function fillHolds(f, n, install = INSTALL, expiry = Date.now() + MONTH) {
  // Additional install/thread is seeded when testing aggregate global capacity.
  f.db.sql.prepare(`WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<?)
    INSERT INTO pull_ingress_holds SELECT ?||printf('%08x',x),?,7,3,'thread',printf('%064x',x),
    'invalid_message_id',?,? FROM n`).run(n, install.slice(8), install, Date.now(), expiry);
}

test('pull ingress preserves authentication and commits stripped text plus notification before wake', async (t) => {
  const f = await fixture(t);
  const { result } = await receive(f, {}, async (id) => {
    assert.equal(id, INSTALL); assert.equal(one(f, 'pull_events').text, 'A fresh reply');
    assert.equal(one(f, 'pull_notifications').dirty_from_seq, 1); assert.equal(rows(f, 'pull_budgets').length, 2); f.wakes.push(id);
  });
  assert.equal(result.status, 'queued'); assert.deepEqual(f.wakes, [INSTALL]);
  const event = one(f, 'pull_events'); assert.equal(event.generation, 7); assert.equal(event.owner_generation, 3);
  assert.equal(event.seq, 1); assert.equal(one(f, 'pull_installs').next_seq, 2);
  assert.equal(event.auth_json, JSON.stringify({ ingress: 'cloudflare', spf: 'pass', dkim: 'pass', dmarc: 'pass', aligned: 'spf+dkim' }));
  assert.doesNotMatch(JSON.stringify(rows(f, 'pull_events')), /Authentication-Results|owner@|opaque1/);
  for (const table of ['events','intents','rate_limits','registrations']) assert.equal(rows(f, table).length, 0);
});

test('wake failure and absent wake leave durable intent and SMTP custody intact', async (t) => {
  const f = await fixture(t); const first = await receive(f, {}, async () => { throw new Error('private failure'); });
  assert.equal(first.result.status, 'queued'); const intent = one(f, 'pull_notifications');
  const m = message({ id: 'second@example.com' });
  assert.equal((await ingress({ message: m, ledger: f.ledger, config: f.config, now: Date.now() })).status, 'queued');
  assert.equal(one(f, 'pull_notifications').revision, intent.revision + 1); assert.equal(one(f, 'pull_notifications').dirty_from_seq, 1);
});

test('same MID dedupes without new budget, sequence, expiry or notification; conflict never succeeds', async (t) => {
  const f = await fixture(t); const a = await receive(f); const before = snapshot(f);
  const b = await receive(f); assert.equal(b.result.status, 'duplicate'); assert.equal(a.result.event_id, b.result.event_id);
  assert.deepEqual(snapshot(f), before);
  await assert.rejects(receive(f, { text: 'different body' }), { message: 'message_id_conflict' });
  assert.equal(one(f, 'pull_events').text, 'A fresh reply'); assert.equal(one(f, 'pull_installs').next_seq, 2);
  assert.ok(rows(f, 'pull_budgets').every((r) => r.count === 1));
  assert.equal(one(f, 'pull_audit').code, 'message_id_conflict');
  assert.equal((await receive(f, { id: 'second@example.com' })).result.status, 'queued');
});

for (const extra of [{ id: null }, { id: 'bad id' }, { extra: ['Message-ID: <ambiguous@example.com>'] }]) {
  test(`invalid MID retains only raw hash, no body/event/sequence/wake: ${JSON.stringify(extra)}`, async (t) => {
    const f = await fixture(t); const original = message(extra); const raw = new Uint8Array(await new Response(original.raw).arrayBuffer());
    const { result } = await receive(f, extra); assert.equal(result.status, 'held');
    const hold = one(f, 'pull_ingress_holds'); assert.equal(hold.raw_digest, await sha256(raw)); assert.equal(hold.reason, 'invalid_message_id');
    assert.equal(hold.expires_ms - hold.received_ms, MONTH);
    assert.equal(rows(f, 'pull_events').length, 0); assert.equal(rows(f, 'pull_dedupe').length, 0);
    assert.equal(rows(f, 'pull_notifications').length, 0); assert.equal(one(f, 'pull_installs').next_seq, 1); assert.deepEqual(f.wakes, []);
    assert.doesNotMatch(JSON.stringify(hold), /fresh|owner@|Authentication|Message-ID/);
    const before = snapshot(f); const retry = await receive(f, extra);
    assert.equal(retry.result.status, 'duplicate'); assert.equal(retry.result.hold_id, result.hold_id); assert.deepEqual(snapshot(f), before);
    assert.equal((await receive(f)).result.status, 'queued'); // Hold cannot shadow a valid MID.
  });
}

test('empty stripped body with valid MID is a held event with notification, zero body bytes', async (t) => {
  const f = await fixture(t); assert.equal((await receive(f, { text: ' \r\n ' })).result.status, 'held');
  assert.equal(one(f, 'pull_events').text, null); assert.equal(one(f, 'pull_events').body_bytes, 0);
  assert.equal(one(f, 'pull_events').reason, 'empty_body'); assert.ok(one(f, 'pull_notifications'));
  assert.equal(rows(f, 'pull_ingress_holds').length, 0);
});

test('wrong outbound Message-ID still commits pull custody with valid owner proof', async (t) => {
  const f = await fixture(t); const { result, m } = await receive(f, { outId: 'wrong@example.com' });
  assert.equal(result.status, 'queued'); assert.deepEqual(m.rejects, []);
  const event = one(f, 'pull_events'); assert.equal(event.thread_id, 'thread'); assert.equal(event.text, 'A fresh reply');
  assert.ok(one(f, 'pull_notifications')); assert.deepEqual(f.wakes, [INSTALL]);
  assert.equal(rows(f, 'pull_audit').length, 0);
});

for (const change of [{ from: 'stranger@example.com' }, { from: 'Owner@example.com' }, { ar: cfAr({ dmarc: 'fail' }) },
  { ar: null }, { token: 'z'.repeat(32) }, { type: 'text/html', text: '<b>hidden</b>' },
  { extra: ['Auto-Submitted: auto-replied'] }, { extra: ['Precedence: bulk'] }, { extra: ['X-Clayrune-Agent-Mail: 1'] },
  { envelope: '' }, { extra: ['Authentication-Results: mx.cloudflare.net; dmarc=pass'] },
  { text: 'x'.repeat(16385) }, { text: 'x'.repeat(70000) }]) {
  test(`existing auth/strip refusal remains content-free under pull: ${JSON.stringify(change).slice(0, 90)}`, async (t) => {
    const f = await fixture(t); const { result, m } = await receive(f, change); assert.equal(result.status, 'rejected'); assert.equal(m.rejects.length, 1);
    for (const table of ['pull_events','pull_dedupe','pull_ingress_holds','pull_budgets','pull_notifications']) assert.equal(rows(f, table).length, 0);
    assert.doesNotMatch(JSON.stringify(rows(f, 'pull_audit')), /stranger|owner@|hidden|fresh/);
  });
}

test('missing auth and missing MID cannot create a hold; header provenance and size lies still reject', async (t) => {
  const f = await fixture(t); assert.equal((await receive(f, { ar: null, id: null })).result.status, 'rejected');
  for (const kind of ['headers','size','recipient','gate']) {
    const m = message(kind === 'size' ? { text: 'x'.repeat(70000) } : {});
    if (kind === 'headers') m.headers.set('authentication-results', cfAr({ dmarc: 'fail' }));
    if (kind === 'size') m.rawSize = 1;
    if (kind === 'recipient') m.to = `opaque1+${TOKEN}@mail.example.net`;
    const r = await ingress({ message: m, ledger: f.ledger, config: { ...f.config, ingressVerified: kind !== 'gate' }, now: Date.now() });
    assert.equal(r.status, 'rejected');
  }
  assert.equal(rows(f, 'pull_ingress_holds').length, 0);
});

for (const held of [false, true]) {
  test(`fault EACH custody batch statement fully rolls back, never SMTP success (${held ? 'hold' : 'event'})`, async (t) => {
    const probe = await fixture(t); await receive(probe, held ? { id: null } : {}); const length = probe.db.lastBatch.length;
    assert.equal(length, held ? 12 : 18);
    for (let index = 0; index < length; index++) {
      const f = await fixture(t); const before = snapshot(f);
      f.db.inBatch = (_s, n) => { if (n === index) throw new Error('injected private SQL'); };
      await assert.rejects(receive(f, held ? { id: null } : {}), { message: 'storage_unavailable' });
      assert.deepEqual(snapshot(f), before, `statement ${index}/${length}`); assert.equal(f.wakes.length, 0);
    }
  });
  for (const which of ['install','global']) test(`exact ${which} ingress budget boundary is atomic (${held ? 'hold' : 'event'})`, async (t) => {
    const f = await fixture(t); const cap = which === 'install' ? 200 : 10000; budget(f, which, cap - 1);
    await receive(f, held ? { id: null } : {}); const before = snapshot(f);
    // Exact replay is still accepted while the budget is full.
    assert.equal((await receive(f, held ? { id: null } : {})).result.status, 'duplicate'); assert.deepEqual(snapshot(f), before);
    await assert.rejects(receive(f, held ? { id: null, text: 'other raw bytes' } : { id: 'next@example.com' }), { message: 'ingress_limit' });
    assert.deepEqual(snapshot(f), before);
  });
  test(`queue item 100 accepted, item 101 rejected with full rollback (${held ? 'hold' : 'event'})`, async (t) => {
    const f = await fixture(t); fillHolds(f, 99);
    await receive(f, held ? { id: null } : {}); const before = snapshot(f);
    await assert.rejects(receive(f, held ? { id: null, text: 'new raw' } : { id: 'next@example.com' }), { message: 'queue_full' });
    assert.deepEqual(snapshot(f), before);
  });
  test(`global queue item 10000 accepted and 10001 rejected independently of local cap (${held ? 'hold' : 'event'})`, async (t) => {
    const f = await fixture(t);
    // 100 unrelated installs each retain <=100 holds; target's local queue is empty.
    for (let n = 1; n <= 100; n++) {
      const remote = n.toString(16).padStart(32, '0'); await seed(f.db, Date.now(), 'k'.repeat(43), remote);
      fillHolds(f, n === 100 ? 99 : 100, remote);
    }
    assert.equal(rows(f, 'pull_ingress_holds').length, 9999);
    await receive(f, held ? { id: null } : {}); const before = snapshot(f);
    await assert.rejects(receive(f, held ? { id: null, text: 'new raw' } : { id: 'next@example.com' }), { message: 'queue_full' });
    assert.deepEqual(snapshot(f), before);
  });
}

test('expired holds stop counting; legal body byte maxima remain below both byte ceilings', async (t) => {
  const f = await fixture(t);
  // Expiry is governed by D1/SQLite, not the independently sampled Worker
  // clock. Windows SQLite VFS can lag Date.now() even in the same process.
  const expired = f.db.sql.prepare(`SELECT ${SQL_NOW}-1 AS ms`).get().ms;
  fillHolds(f, 100, INSTALL, expired);
  assert.equal(f.db.sql.prepare(`SELECT COUNT(*) AS n FROM pull_ingress_holds WHERE expires_ms>${SQL_NOW}`).get().n, 0);
  assert.equal((await receive(f, { text: 'x'.repeat(16384) })).result.status, 'queued');
  assert.equal(one(f, 'pull_events').body_bytes, 16384);
  // Count limits bind first for schema-valid bodies: byte caps cannot be hit
  // independently (100*16KiB<2MiB, 10000*16KiB<200MiB). No invalid fixture claim.
  assert.ok(100 * 16384 < 2097152); assert.ok(10000 * 16384 < 209715200);
});

test('100 maximum legal UTF-8 bodies fit exactly the count bound, rejecting 101 without byte or budget drift', async (t) => {
  const f = await fixture(t), text = 'é'.repeat(8192);
  for (let n = 1; n <= 100; n++) await receive(f, { id: `max-${n}@example.com`, text });
  assert.equal(f.db.sql.prepare('SELECT SUM(body_bytes) AS n FROM pull_events').get().n, 1638400);
  assert.equal(one(f, 'pull_notifications').revision, 100); const before = snapshot(f);
  await assert.rejects(receive(f, { id: 'max-101@example.com', text }), { message: 'queue_full' });
  assert.deepEqual(snapshot(f), before);
});

test('read/storage failures never fall back to legacy transport or SMTP success', async (t) => {
  const f = await fixture(t); f.db.prepare = () => { throw new Error('private database read failure'); };
  await assert.rejects(receive(f), { message: 'storage_unavailable' }); assert.equal(f.wakes.length, 0);
  assert.equal(rows(f, 'events').length, 0);
});

test('racing duplicate event and hold use durable idempotency after unique-insert rollback', async (t) => {
  for (const held of [false,true]) {
    const f = await fixture(t); const results = await Promise.all([receive(f, held ? { id: null } : {}), receive(f, held ? { id: null } : {})]);
    assert.deepEqual(results.map((r) => r.result.status).sort(), held ? ['duplicate','held'] : ['duplicate','queued']);
    assert.equal(rows(f, held ? 'pull_ingress_holds' : 'pull_events').length, 1); assert.ok(rows(f, 'pull_budgets').every((r) => r.count === 1));
  }
});

test('concurrent owner/fence/thread changes never commit old SMTP custody', async (t) => {
  for (const held of [false,true]) for (const mutation of [
    "UPDATE pull_installs SET owner_generation=4", "UPDATE pull_installs SET state='frozen'",
    "UPDATE pull_threads SET revoked_ms=1", "UPDATE pull_threads SET expires_ms=1",
    `INSERT INTO pull_fences VALUES('${INSTALL}','${'2'.repeat(32)}',7,'${'f'.repeat(64)}','revoke',
      '${'3'.repeat(32)}','${'4'.repeat(32)}','${'b'.repeat(64)}',1)`,
  ]) {
    const f = await fixture(t); let before;
    f.db.beforeBatch = async () => { f.db.beforeBatch = null; f.db.sql.exec(mutation); before = snapshot(f); };
    await assert.rejects(receive(f, held ? { id: null } : {})); assert.deepEqual(snapshot(f), before); assert.equal(f.wakes.length, 0);
  }
});

// Real workerd + D1 + mailbox DO RPC, no fake namespace/socket/storage. The
// unwired wrapper here is only a harness; unit14 owns production route wiring.
test('actual Miniflare mailbox wake after SMTP commit and DO eviction retains notification intent', async (t) => {
  const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
  const wrapper = `import { PullMailbox } from './pull-mailbox.js';
    import { Ledger } from './ledger.js'; import { ingress } from './ingress.js';
    export class TestMailbox extends PullMailbox {constructor(s,e){super(s,{db:e.DB,serviceOrigin:'https://mail.example.com',
      serviceKeyId:'service',servicePrivateKey:e.KEY,serviceKeyExpiresMs:Number(e.EXPIRY)});}}
    export default {async fetch(r,e){const raw=await r.arrayBuffer();const h=new Headers();
      const lines=new TextDecoder().decode(raw).split('\\r\\n\\r\\n')[0].split('\\r\\n');
      for(const l of lines){const i=l.indexOf(':');h.append(l.slice(0,i),l.slice(i+1).trim());}
      const m={raw:new Blob([raw]).stream(),rawSize:raw.byteLength,headers:h,to:'${LOCAL}+${TOKEN}@mail.example.net',
        from:'owner@example.com',setReject(){throw new Error('unexpected SMTP refusal');}};
      let wakeResult;const result=await ingress({message:m,ledger:new Ledger(e.DB).pull(),config:{domain:'mail.example.net',ingressVerified:true},
        now:Date.now(),wake:async id=>{wakeResult=await e.BOX.get(e.BOX.idFromName(id)).wake(id);return wakeResult;}});
      return Response.json({result,wakeResult});}};`;
  const modules = [{ type: 'ESModule', path: root + 'ingress-test-entry.js', contents: wrapper }], seen = new Set();
  function add(name) {
    if (seen.has(name)) return; seen.add(name); const contents = readFileSync(root + name, 'utf8');
    modules.push({ type: 'ESModule', path: root + name,
      contents: contents.replace("from 'postal-mime'", "from './node_modules/postal-mime/dist/esm/postal-mime.js'") });
    for (const m of contents.matchAll(/from ['"]\.\/([^'"]+)['"]/g)) add(m[1]);
    // PostalMime is the existing pinned dependency, included as real ESM.
    for (const m of contents.matchAll(/from ['"](postal-mime)['"]/g)) {
      const path = root + 'node_modules/postal-mime/dist/esm/postal-mime.js';
      function postal(p) { if (seen.has(p)) return; seen.add(p); const c = readFileSync(p, 'utf8');
        modules.push({ type: 'ESModule', path: p, contents: c });
        for (const x of c.matchAll(/(?:from\s*|import\s*)['"]\.\/([^'"]+)['"]/g)) postal(p.slice(0,p.lastIndexOf('/')+1)+x[1]); }
      postal(path);
    }
  }
  add('pull-mailbox.js'); add('ledger.js'); add('ingress.js');
  const key = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  const mf = new Miniflare({ name: 'ingress-test', modules, compatibilityDate: '2026-07-01', durableObjects: { BOX: 'TestMailbox' }, d1Databases: ['DB'],
    bindings: { KEY: base64url(await crypto.subtle.exportKey('pkcs8', key.privateKey)), EXPIRY: Date.now() + DAY }, log: new Log(LogLevel.ERROR) });
  t.after(() => mf.dispose()); const db = await mf.getD1Database('DB');
  for (const sql of sources) await db.exec(sql.replace(/--[^\n]*/g, '').replace(/[\r\n]/g, ' ')); await seed(db, Date.now());
  for (const id of ['first@example.com','second@example.com']) {
    const m = message({ id }); const r = await mf.dispatchFetch('https://mail.example.com/', { method: 'POST', body: await new Response(m.raw).arrayBuffer() });
    assert.equal(r.status, 200, await r.clone().text()); const value = await r.json();
    assert.equal(value.result.status, 'queued'); assert.deepEqual(value.wakeResult, { connected: false });
    assert.ok(await db.prepare('SELECT * FROM pull_notifications WHERE install_id=?').bind(INSTALL).first());
    await mf.unsafeEvictDurableObject('ingress-test', 'TestMailbox', { name: INSTALL, webSockets: 'hibernate' });
  }
  assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM pull_events').first()).n, 2);
});
