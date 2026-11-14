import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { Miniflare, Log, LogLevel } from 'miniflare';
import { sweep } from '../pull-sweep.js';
import { Ledger } from '../ledger.js';
import { SQL_NOW } from '../sql-clock.js';
import { base64url, unbase64url, bytes, sha256 } from '../signing.js';
import { emailMessage } from './transport-support.js';

const DAY = 86400000, MONTH = 30 * DAY, INSTALL = '1'.repeat(32);
const LOCAL = 'm' + 'a'.repeat(29), TOKEN = 'abcdef0123456789abcdef0123456789', SENT = 'sent@example.com';
const id = (n) => n.toString(16).padStart(32, '0');
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
    if (this.beforeBatch) await this.beforeBatch(statements);
    this.sql.exec('BEGIN IMMEDIATE');
    try { const r = statements.map((s, n) => { this.inBatch?.(s, n, statements); return s.runSync(); }); this.sql.exec('COMMIT'); return r; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}
const rows = (f, table) => f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all().map((r) => ({ ...r }));
const one = (f, table) => rows(f, table)[0];
const snapshot = (f) => f.db.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
  .all().map(({ name }) => [name, rows(f, name)]);
const sqlNow = (f) => f.db.sql.prepare(`SELECT ${SQL_NOW} AS n`).get().n;
async function seed(db, install = INSTALL) {
  const now = (await db.prepare(`SELECT ${SQL_NOW} AS n`).first()).n;
  const local = install === INSTALL ? LOCAL : 'm' + install.slice(3);
  const statements = [
    [`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,key_id,
      public_key,consent_version,created_ms,updated_ms) VALUES(?,7,3,'active',?,'owner@example.com',?,'key',?,'v1',?,?)`,
    install, 'a'.repeat(64), local, 'k'.repeat(43), now, now],
    ["INSERT INTO pull_generations VALUES(?,7,3,?,'key',?,?,?,'current',?,NULL)", install, 'a'.repeat(64), 'k'.repeat(43), 'f'.repeat(64), id(2), now],
    ["INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)", local, install, now],
    [`INSERT INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,thread_id,message_id,state,created_ms)
      VALUES(?,7,'intent',3,?,?,'thread',?,'accepted',?)`, install, 'a'.repeat(64), 'b'.repeat(64), SENT, now],
    ["INSERT INTO pull_threads VALUES(?,7,'thread',3,'intent',?,?,?,?,NULL)", install,
      await sha256(bytes(install === INSTALL ? TOKEN : install)), SENT, now + 90 * DAY, now + 120 * DAY],
  ];
  for (const [sql, ...args] of statements) await db.prepare(sql).bind(...args).run();
}
async function fixture(t) {
  const db = new D1(); t.after(() => db.sql.close()); await seed(db);
  const ledger = new Ledger(db).pull(); return { db, ledger, wakes: [] };
}
function events(f, count, { install = INSTALL, expiry = sqlNow(f) + MONTH, offset = 0, state = 'pending' } = {}) {
  const now = sqlNow(f);
  for (let j = 1; j <= count; j++) {
    const n = offset + j, eid = (install + id(n));
    f.db.sql.prepare(`INSERT INTO pull_dedupe VALUES(?,?,7,3,'thread',?,?,?,?,'pending',?,?,?)`)
      .run(eid, install, n.toString(16).padStart(64, '0'), 'd'.repeat(64), 'e'.repeat(64), n, now - MONTH, expiry, now + 120 * DAY);
    f.db.sql.prepare(`INSERT INTO pull_events VALUES(?,?,7,3,'thread',?,?,'text','body',4,?,'',?,?,?,?,?,?,NULL)`)
      .run(eid, install, n, `reply-${n}@example.com`, '{}', state, now - MONTH, expiry, 0,
        state === 'offered' ? id(n) : null, state === 'offered' ? now + 60000 : null);
  }
  f.db.sql.prepare('UPDATE pull_installs SET next_seq=MAX(next_seq,?) WHERE install_id=?').run(offset + count + 1, install);
}
function holds(f, count, { expiry = sqlNow(f) - 1000, offset = 0 } = {}) {
  const now = sqlNow(f);
  for (let j = 1; j <= count; j++) f.db.sql.prepare("INSERT INTO pull_ingress_holds VALUES(?,?,7,3,'thread',?,'invalid_message_id',?,?)")
    .run(id(offset + j), INSTALL, (offset + j).toString(16).padStart(64, '0'), now - MONTH, expiry);
}
function intent(f, install = INSTALL, due = 0, attempts = 0) {
  f.db.sql.prepare('INSERT INTO pull_notifications(install_id,revision,dirty_from_seq,next_attempt_ms,attempts) VALUES(?,1,1,?,?)')
    .run(install, due, attempts);
}
const run = (f, wake = async (i) => { f.wakes.push(i); return { connected: false }; }) => sweep({ ledger: f.ledger, wake });

test('empty indexed passes persist independent cursors without per-install work', async (t) => {
  const f = await fixture(t); assert.deepEqual(await run(f), { notify: { claimed: 0, woken: 0 }, expire: { scanned: 0, events: 0, holds: 0 } });
  assert.deepEqual(rows(f, 'pull_sweep_cursors').map((c) => c.task), ['expire','notify']);
  assert.ok(rows(f, 'pull_sweep_cursors').every((c) => c.cursor_ms === 0 && c.cursor_id === '' && c.updated_ms <= sqlNow(f)));
  assert.deepEqual(f.wakes, []);
  assert.equal((await run(f)).notify.claimed, 0);
});

test('wake failure preserves intent with bounded backoff and releases lease', async (t) => {
  const f = await fixture(t); events(f, 1); intent(f); const before = sqlNow(f);
  const r = await run(f, async () => { throw new Error('private provider-like content'); });
  assert.deepEqual(r.notify, { claimed: 1, woken: 0 }); const n = one(f, 'pull_notifications');
  assert.equal(n.attempts, 1); assert.equal(n.revision, 1); assert.equal(n.lease_id, null);
  assert.ok(n.next_attempt_ms >= before + 1000 && n.next_attempt_ms <= sqlNow(f) + 1000);
  assert.equal(one(f, 'pull_events').text, 'body'); assert.equal(one(f, 'pull_dedupe').disposition, 'pending');
});

test('offline high-attempt retries cap at300s; successful wake retains pending work for60s', async (t) => {
  const f = await fixture(t); events(f, 1); intent(f, INSTALL, 0, Number.MAX_SAFE_INTEGER - 1);
  const before = sqlNow(f); await run(f); let n = one(f, 'pull_notifications');
  assert.equal(n.attempts, Number.MAX_SAFE_INTEGER); assert.ok(n.next_attempt_ms >= before + 1000 && n.next_attempt_ms <= sqlNow(f) + 300000);
  f.db.sql.exec("UPDATE pull_notifications SET next_attempt_ms=0; DELETE FROM pull_sweep_cursors WHERE task='notify'");
  await run(f, async () => ({ connected: true })); n = one(f, 'pull_notifications');
  assert.equal(n.attempts, Number.MAX_SAFE_INTEGER); assert.ok(n.next_attempt_ms >= before + 60000);
  assert.equal(one(f, 'pull_events').state, 'pending');
});

test('completed wake plus authoritative empty queue clears matching revision only', async (t) => {
  const f = await fixture(t); intent(f); await run(f, async () => ({ connected: true }));
  assert.equal(rows(f, 'pull_notifications').length, 0);
  intent(f); f.db.sql.exec("DELETE FROM pull_sweep_cursors WHERE task='notify'");
  await run(f, async () => { f.db.sql.exec('UPDATE pull_notifications SET revision=revision+1,lease_id=NULL,lease_until_ms=NULL'); return { connected: true }; });
  assert.equal(one(f, 'pull_notifications').revision, 2); assert.equal(one(f, 'pull_notifications').attempts, 0);
});

test('late wake settlement cannot overwrite replacement lease or expired lease', async (t) => {
  for (const replacement of [false, true]) {
    const f = await fixture(t); events(f, 1); intent(f);
    await run(f, async () => {
      f.db.sql.exec(`UPDATE pull_notifications SET lease_id='${id(90)}',lease_until_ms=${SQL_NOW}${replacement ? '+60000' : '-1'}`);
      return { connected: true };
    });
    const n = one(f, 'pull_notifications'); assert.equal(n.lease_id, id(90)); assert.equal(n.attempts, 0); assert.equal(n.next_attempt_ms, 0);
  }
});

test('overlapping sweeps never wake an already leased notification', async (t) => {
  const f = await fixture(t); events(f, 1); intent(f); let release, started;
  const gate = new Promise((r) => { release = r; }); const entered = new Promise((r) => { started = r; });
  const first = run(f, async (i) => { f.wakes.push(i); started(); await gate; return { connected: false }; });
  await entered;
  // Simulate an independent scheduled pass from zero while the first awaits RPC.
  f.db.sql.exec("UPDATE pull_sweep_cursors SET cursor_id='' WHERE task='notify'");
  const second = await run(f); assert.equal(second.notify.claimed, 0); assert.deepEqual(f.wakes, [INSTALL]);
  const leased = one(f, 'pull_notifications'); assert.ok(leased.lease_until_ms <= sqlNow(f) + 60000);
  release(); await first; assert.deepEqual(f.wakes, [INSTALL]);
});

test('overlapping prepared pages roll back stale cursor instead of waking twice', async (t) => {
  const f = await fixture(t); events(f, 1); intent(f); let raced = false;
  f.db.beforeBatch = async (ss) => {
    if (!raced && ss.some((s) => s.sql.includes('SET lease_id=?'))) { raced = true; await run(f); }
  };
  const r = await run(f); assert.equal(r.notify.claimed, 0); assert.deepEqual(f.wakes, [INSTALL]);
  assert.equal(one(f, 'pull_notifications').attempts, 1);
});

test('expired notification lease recovers lost claimant on next persisted pass', async (t) => {
  const f = await fixture(t); events(f, 1); intent(f);
  f.db.sql.exec(`UPDATE pull_notifications SET lease_id='${id(3)}',lease_until_ms=${SQL_NOW}-1`);
  assert.equal((await run(f)).notify.claimed, 1); assert.deepEqual(f.wakes, [INSTALL]);
});

test('notification exact100 boundary, restart continuation and wrap across103 installs', async (t) => {
  const f = await fixture(t); const installs = [INSTALL];
  for (let n = 2; n <= 103; n++) { const i = id(n); await seed(f.db, i); installs.push(i); }
  for (const i of installs) intent(f, i);
  assert.equal((await run(f)).notify.claimed, 100); assert.equal(f.wakes.length, 100);
  // New facade/sweep invocation uses D1 cursor, not process-local state.
  f.ledger = new Ledger(f.db).pull();
  assert.equal((await run(f)).notify.claimed, 3); assert.equal(new Set(f.wakes).size, 103);
  assert.equal((await run(f)).notify.claimed, 0);
  assert.equal(rows(f, 'pull_sweep_cursors').find((c) => c.task === 'notify').cursor_id, '');
  f.db.sql.exec('UPDATE pull_notifications SET next_attempt_ms=0');
  assert.equal((await run(f)).notify.claimed, 100);
});

test('>=3 installs with skewed load retain fair stable-id passes despite changing retry times', async (t) => {
  const f = await fixture(t); const a = id(1), b = id(2);
  await seed(f.db, a); await seed(f.db, b);
  events(f, 100, { install: a }); events(f, 1, { install: b }); events(f, 1);
  for (const i of [a,b,INSTALL]) intent(f, i);
  // A low-id leased head is skipped initially, then revisited after wrap.
  f.db.sql.exec(`UPDATE pull_notifications SET lease_id='${id(5)}',lease_until_ms=${SQL_NOW}+60000 WHERE install_id='${a}'`);
  await run(f); assert.deepEqual(f.wakes, [b,INSTALL]);
  f.db.sql.exec(`UPDATE pull_notifications SET lease_until_ms=${SQL_NOW}-1,next_attempt_ms=0`);
  assert.equal((await run(f)).notify.claimed, 0); // wrap
  assert.equal((await run(f)).notify.claimed, 3);
  assert.deepEqual(f.wakes.slice(2), [a,b,INSTALL]);
  assert.equal(rows(f, 'pull_events').length, 102);
});

test('expiry combined100 boundary, persisted continuation, wrap and SQL-clock decisions', async (t) => {
  const f = await fixture(t); const end = sqlNow(f) - 1000;
  events(f, 70, { expiry: end, state: 'offered' }); holds(f, 40, { expiry: end });
  events(f, 1, { expiry: sqlNow(f) + MONTH, offset: 70 }); holds(f, 1, { expiry: sqlNow(f) + MONTH, offset: 40 });
  intent(f, INSTALL, sqlNow(f) + DAY);
  // Caller clock is deliberately nonsense: expiry uses only real SQLite time.
  f.ledger.queue.clock = () => Number.MAX_SAFE_INTEGER;
  let r = await run(f); assert.deepEqual(r.expire, { scanned: 100, events: 70, holds: 30 });
  assert.equal(rows(f, 'pull_ingress_holds').length, 11); assert.equal(rows(f, 'pull_events').filter((e) => e.state === 'expired').length, 70);
  assert.ok(rows(f, 'pull_events').filter((e) => e.state === 'expired').every((e) => e.text === null && e.body_bytes === 0
    && e.lease_id === null && e.lease_until_ms === null && e.terminal_ms <= sqlNow(f)));
  assert.equal(rows(f, 'pull_dedupe').filter((d) => d.disposition === 'expired').length, 70);
  const retained = rows(f, 'pull_dedupe').map((d) => [d.event_id,d.body_digest,d.event_digest,d.dedupe_until_ms]);
  f.ledger = new Ledger(f.db).pull(); r = await run(f); assert.deepEqual(r.expire, { scanned: 10, events: 0, holds: 10 });
  assert.equal(rows(f, 'pull_ingress_holds').length, 1); assert.equal(one(f, 'pull_notifications').dirty_from_seq, 71);
  r = await run(f); assert.equal(r.expire.scanned, 0);
  events(f, 1, { expiry: end - 1000, offset: 71 });
  assert.equal((await run(f)).expire.events, 1); // earlier key, after wrap
  assert.deepEqual(rows(f, 'pull_dedupe').slice(0,71).map((d) => [d.event_id,d.body_digest,d.event_digest,d.dedupe_until_ms]), retained);
});

test('expiry releases body/count capacity without refunding accepted ingress budgets', async (t) => {
  const f = await fixture(t); events(f, 99, { expiry: sqlNow(f) - 1000 }); holds(f, 1);
  f.db.sql.exec(`INSERT INTO pull_budgets VALUES('already-accepted',100,${sqlNow(f) + DAY})`);
  const before = rows(f, 'pull_budgets'); assert.equal((await run(f)).expire.scanned, 100);
  assert.equal(rows(f, 'pull_ingress_holds').length, 0); assert.ok(rows(f, 'pull_events').every((e) => e.body_bytes === 0));
  assert.deepEqual(rows(f, 'pull_budgets'), before);
  assert.equal(rows(f, 'pull_notifications').length, 0);
  const reg = await f.ledger.registration(INSTALL);
  const e = await f.ledger.enqueue({ ...reg, connection_id: undefined }, { thread_id: 'thread', incoming_message_id: 'new@example.com', text: 'new',
    auth: { ingress: 'cloudflare', spf: 'pass', dkim: 'pass', dmarc: 'pass', aligned: 'spf+dkim' } });
  assert.equal(e.duplicate, false);
});

test('retired installs still expire; custody receipts/offers/dedupe retain their independent horizon', async (t) => {
  const f = await fixture(t); events(f, 1, { expiry: sqlNow(f) - 1000 }); holds(f, 1);
  const e = one(f, 'pull_events'), now = sqlNow(f);
  f.db.sql.prepare('INSERT INTO pull_offers VALUES(?,?,?,7,1,?,?,?)').run(id(3), e.event_id, INSTALL, now-MONTH, now-1000, now+MONTH);
  events(f, 1, { expiry: now - 1000, offset: 1 }); const receivedId = INSTALL + id(2);
  f.db.sql.prepare("UPDATE pull_events SET state='local_received',text=NULL,body_bytes=0,terminal_ms=? WHERE event_id=?").run(now-MONTH, receivedId);
  f.db.sql.prepare("UPDATE pull_dedupe SET disposition='local_received' WHERE event_id=?").run(receivedId);
  f.db.sql.prepare('INSERT INTO pull_receipts VALUES(?,?,?,?,7,3,?,?,?,?)')
    .run(receivedId, id(7), id(8), INSTALL, 'e'.repeat(64), 'd'.repeat(64), now-MONTH, now+MONTH);
  f.db.sql.exec("UPDATE pull_installs SET state='disabled'");
  const before = rows(f, 'pull_offers'), receipts = rows(f, 'pull_receipts'); const r = await run(f);
  assert.equal(r.expire.events, 1); assert.equal(r.expire.holds, 1); assert.deepEqual(rows(f, 'pull_offers'), before);
  assert.deepEqual(rows(f, 'pull_receipts'), receipts); assert.equal(rows(f, 'pull_events').length, 2);
  assert.equal(rows(f, 'pull_dedupe').length, 2); assert.equal(rows(f, 'pull_threads').length, 1);
});

test('expiry continues across three skewed installs instead of repeating the first100', async (t) => {
  const f = await fixture(t); const a = id(1), b = id(2), expiry = sqlNow(f) - 1000;
  await seed(f.db, a); await seed(f.db, b);
  events(f, 100, { install: a, expiry }); events(f, 1, { install: b, expiry }); events(f, 1, { expiry });
  assert.equal((await run(f)).expire.events, 100);
  f.ledger = new Ledger(f.db).pull(); assert.equal((await run(f)).expire.events, 2);
  assert.ok(rows(f, 'pull_events').every((e) => e.state === 'expired'));
});

test('SQL-clock equality expires while future rows and not-yet-due notifications remain untouched', async (t) => {
  const f = await fixture(t); events(f, 1); holds(f, 1, { expiry: sqlNow(f) + DAY });
  f.db.sql.exec(`UPDATE pull_events SET expires_ms=${SQL_NOW}`);
  intent(f, INSTALL, sqlNow(f) + DAY);
  const r = await run(f); assert.equal(r.notify.claimed, 0); assert.equal(r.expire.events, 1); assert.equal(r.expire.holds, 0);
  assert.deepEqual(f.wakes, []); assert.equal(rows(f, 'pull_ingress_holds').length, 1);
});

test('new revision on a later claimed row prevents stale wake while first RPC awaits', async (t) => {
  const f = await fixture(t); const a = id(1); await seed(f.db, a); events(f, 1, { install: a }); events(f, 1);
  intent(f, a); intent(f);
  const r = await run(f, async (i) => {
    f.wakes.push(i); f.db.sql.prepare('UPDATE pull_notifications SET revision=2,lease_id=NULL,lease_until_ms=NULL WHERE install_id=?').run(INSTALL);
    return { connected: true };
  });
  assert.deepEqual(r.notify, { claimed: 2, woken: 1 }); assert.deepEqual(f.wakes, [a]);
  assert.equal(rows(f, 'pull_notifications').find((n) => n.install_id === INSTALL).attempts, 0);
});

for (const phase of ['claim','settle','expire']) {
  // Discover the exact production batch length, then fail every statement.
  test(`every ${phase} batch statement rolls back all tables including cursor`, async (t) => {
    const matches = (ss) => ss.some((s) => s.sql.includes(phase === 'claim' ? 'SET lease_id=?'
      : phase === 'settle' ? 'SET attempts=?' : "SET disposition='expired'"));
    async function setup(t) { const f = await fixture(t); events(f, 2, { expiry: sqlNow(f) + (phase === 'expire' ? -1000 : MONTH) });
      holds(f, 1); intent(f, INSTALL, phase === 'expire' ? sqlNow(f) + DAY : 0); return f; }
    const base = await setup(t); let length;
    base.db.beforeBatch = async (ss) => { if (matches(ss)) length = ss.length; };
    await run(base); assert.ok(length > 0);
    for (let fail = 0; fail < length; fail++) {
      const f = await setup(t); let before, hit = false;
      f.db.beforeBatch = async (ss) => { if (matches(ss)) before = snapshot(f); };
      f.db.inBatch = (s, n, ss) => { if (matches(ss) && n === fail) { hit = true; throw new Error('fault'); } };
      await assert.rejects(run(f), { message: 'storage_unavailable' }); assert.ok(hit);
      assert.deepEqual(snapshot(f), before, `statement${fail}/${length}`);
    }
  });
}

test('lost SMTP wake recovered from notification alone by actual D1 and DO RPC across eviction', async (t) => {
  const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
  const wrapper = `import { PullMailbox } from './pull-mailbox.js'; import { Ledger } from './ledger.js';
    import { ingress } from './ingress.js'; import { sweep } from './pull-sweep.js';
    export class TestMailbox extends PullMailbox {constructor(s,e){super(s,{db:e.DB,serviceOrigin:'https://mail.example.com',
      serviceKeyId:'service',servicePrivateKey:e.KEY,serviceKeyExpiresMs:Number(e.EXPIRY)});}
      async fetch(r){return this.admit(r,{trustedSource:'trusted-edge',install_id:'${INSTALL}'});}}
    export default {async fetch(r,e){if(new URL(r.url).pathname==='/v1/pull/socket')return e.BOX.get(e.BOX.idFromName('${INSTALL}')).fetch(r);
      if(new URL(r.url).pathname==='/sweep'){
      let wakeResult;const result=await sweep({ledger:new Ledger(e.DB).pull(),wake:async id=>{
        wakeResult=await e.BOX.get(e.BOX.idFromName(id)).wake(id);return wakeResult;}});return Response.json({result,wakeResult});}
      const raw=await r.arrayBuffer(),h=new Headers();
      for(const l of new TextDecoder().decode(raw).split('\\r\\n\\r\\n')[0].split('\\r\\n')){const i=l.indexOf(':');h.append(l.slice(0,i),l.slice(i+1).trim());}
      const m={raw:new Blob([raw]).stream(),rawSize:raw.byteLength,headers:h,to:'${LOCAL}+${TOKEN}@mail.example.net',
        from:'owner@example.com',setReject(){throw new Error('unexpected SMTP refusal');}};
      return Response.json(await ingress({message:m,ledger:new Ledger(e.DB).pull(),config:{domain:'mail.example.net',ingressVerified:true},
        now:Date.now(),wake:async()=>{throw new Error('lost wake');}}));}};`;
  const modules = [{ type: 'ESModule', path: root + 'sweep-test-entry.js', contents: wrapper }], seen = new Set();
  function add(path) {
    if (seen.has(path)) return; seen.add(path); const contents = readFileSync(path, 'utf8');
    modules.push({ type: 'ESModule', path, contents: contents.replace("from 'postal-mime'", "from './node_modules/postal-mime/dist/esm/postal-mime.js'") });
    for (const m of contents.matchAll(/(?:from\s*|import\s*)['"]\.\/([^'"]+)['"]/g)) add(path.slice(0,path.lastIndexOf('/')+1)+m[1]);
    if (contents.includes("from 'postal-mime'")) add(root + 'node_modules/postal-mime/dist/esm/postal-mime.js');
  }
  for (const n of ['pull-mailbox.js','ledger.js','ingress.js','pull-sweep.js']) add(root + n);
  const key = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  const mf = new Miniflare({ name: 'sweep-test', modules, compatibilityDate: '2026-07-01', durableObjects: { BOX: 'TestMailbox' }, d1Databases: ['DB'],
    bindings: { KEY: base64url(await crypto.subtle.exportKey('pkcs8', key.privateKey)), EXPIRY: Date.now() + DAY }, log: new Log(LogLevel.ERROR) });
  t.after(() => mf.dispose()); const db = await mf.getD1Database('DB');
  for (const sql of sources) await db.exec(sql.replace(/--[^\n]*/g, '').replace(/[\r\n]/g, ' ')); await seed(db);
  const m = emailMessage({ outId: SENT });
  const accepted = await mf.dispatchFetch('https://mail.example.com/ingress', { method: 'POST', body: await new Response(m.raw).arrayBuffer() });
  assert.equal((await accepted.json()).status, 'queued');
  assert.equal((await db.prepare('SELECT * FROM pull_notifications').first()).attempts, 0);
  for (let n = 0; n < 2; n++) {
    const r = await mf.dispatchFetch('https://mail.example.com/sweep'); assert.equal(r.status, 200, await r.clone().text());
    const v = await r.json(); assert.deepEqual(v.result.notify, { claimed: 1, woken: 1 }); assert.deepEqual(v.wakeResult, { connected: false });
    const intent = await db.prepare('SELECT * FROM pull_notifications').first(); assert.equal(intent.attempts, n + 1);
    assert.equal((await db.prepare('SELECT * FROM pull_events').first()).text, 'A fresh reply');
    await db.prepare('UPDATE pull_notifications SET next_attempt_ms=0').run();
    // One invocation completes/wraps the persisted pass before the next retry.
    const wrap = await mf.dispatchFetch('https://mail.example.com/sweep'); assert.equal((await wrap.json()).result.notify.claimed, 0);
    await mf.unsafeEvictDurableObject('sweep-test', 'TestMailbox', { name: INSTALL, webSockets: 'hibernate' });
  }
  // Trusted fixture inserts a ticket; actual admission consumes it atomically.
  // Ticket creation/signature endpoints are covered by units06/09, not replaced.
  const secret = '9'.repeat(64), now = (await db.prepare(`SELECT ${SQL_NOW} AS n`).first()).n;
  await db.prepare("INSERT INTO pull_tickets VALUES(?,?,?,?,7,3,'key','issued',?,?,NULL)")
    .bind(await sha256(bytes(secret)), id(90), id(91), INSTALL, now - 1000, now + 60000).run();
  const upgrade = await mf.dispatchFetch('https://mail.example.com/v1/pull/socket', { headers: { Upgrade: 'websocket',
    Authorization: `MailTicket ${secret}`, 'Sec-WebSocket-Protocol': 'clayrune-mail-pull.v1' } });
  assert.equal(upgrade.status, 101, upgrade.status === 101 ? '' : await upgrade.text());
  const ws = upgrade.webSocket, messages = [], waiters = [];
  ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (waiters.length) waiters.shift()(m); else messages.push(m); });
  ws.accept();
  const next = async () => messages.length ? messages.shift() : new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('socket message timeout')), 5000);
    waiters.push((v) => { clearTimeout(timer); resolve(v); });
  });
  async function verified(wire) {
    const raw = unbase64url(wire.payload); assert.equal(await sha256(raw), wire.payload_digest);
    const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-frame','1','https://mail.example.com',wire.type,wire.key_id,wire.install_id,
      String(wire.generation),String(wire.owner_generation),String(wire.epoch),wire.frame_id,String(wire.issued_ms),wire.payload_digest]));
    assert.equal(await crypto.subtle.verify('Ed25519', key.publicKey, unbase64url(wire.signature), input), true);
    return JSON.parse(new TextDecoder().decode(raw));
  }
  const hello = await next(); assert.equal(hello.type, 'hello'); await verified(hello);
  const send = (type, payload, request = 100) => ws.send(JSON.stringify({ v: 1, type, install_id: INSTALL, generation: 7,
    owner_generation: 3, epoch: hello.epoch, request_id: id(request), payload }));
  send('resume', { after_seq: 0, limit: 10, available_bytes: 524288 });
  const first = await next(); assert.equal(first.type, 'event'); const offer = await verified(first);
  assert.equal((await next()).type, 'status');
  send('custody_ack', { event_id: offer.event.event_id, event_digest: offer.event.event_digest, body_digest: offer.event.body_digest,
    lease_id: offer.lease_id, local_receipt_id: id(101) }, 102);
  assert.equal((await next()).type, 'ack_committed'); assert.equal((await next()).type, 'status');
  assert.equal(await db.prepare('SELECT * FROM pull_notifications').first(), null);
  // Connected, resumed install misses the immediate SMTP wake, then hibernates.
  const secondMail = emailMessage({ outId: SENT, id: 'second@example.com' });
  const secondAccepted = await mf.dispatchFetch('https://mail.example.com/ingress', { method: 'POST', body: await new Response(secondMail.raw).arrayBuffer() });
  assert.equal((await secondAccepted.json()).status, 'queued');
  await mf.unsafeEvictDurableObject('sweep-test', 'TestMailbox', { name: INSTALL, webSockets: 'hibernate' });
  const recovered = await mf.dispatchFetch('https://mail.example.com/sweep'); const result = await recovered.json();
  assert.deepEqual(result.result.notify, { claimed: 1, woken: 1 }); assert.deepEqual(result.wakeResult, { connected: true });
  const hint = await next(); assert.equal(hint.type, 'wake'); assert.deepEqual(await verified(hint), {});
  send('resume', { after_seq: 0, limit: 10, available_bytes: 524288 }, 103);
  const second = await next(); assert.equal(second.type, 'event'); const recoveredOffer = await verified(second);
  assert.equal(recoveredOffer.event.incoming_message_id, 'second@example.com'); assert.equal(recoveredOffer.event.text, 'A fresh reply');
  assert.equal((await next()).type, 'status');
  assert.equal((await db.prepare('SELECT state FROM pull_events WHERE event_id=?').bind(recoveredOffer.event.event_id).first()).state, 'offered');
  assert.ok(await db.prepare('SELECT * FROM pull_notifications').first());
  assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n, 1);
  ws.close(1000, 'test complete');
});
