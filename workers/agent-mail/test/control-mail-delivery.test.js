import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { VerificationMail, VERIFICATION_TTL, verificationBinding } from '../verification-mail.js';
import { deliverControlMail } from '../control-mail-delivery.js';
import { ownerIdentity } from '../owner-registry.js';
import { base64url, bytes, sha256 } from '../signing.js';

const NOW = 1_800_000_000_000, DAY = 86_400_000, WEEK = 7 * DAY;
const ORIGIN = 'https://mail.example.com', FROM = 'agents@mail.example.com', DOMAIN = 'mail.example.com';
const H = 'a'.repeat(64), TOKEN = 'b'.repeat(64), KEY = base64url(new Uint8Array(32).fill(2));
const id = (n) => n.toString(16).padStart(32, '0');
const sources = [readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'),
  ...readdirSync(new URL('../migrations/', import.meta.url)).filter((s) => /^\d+.*\.sql$/.test(s)).sort()
    .map((s) => readFileSync(new URL(`../migrations/${s}`, import.meta.url), 'utf8'))];

// Actual schema/SQL and atomic D1 batches. Override SQLite's time function,
// rather than editing production SQL, to exercise exact deadline boundaries.
class D1 {
  constructor() {
    this.now = NOW; this.sql = new DatabaseSync(':memory:');
    this.sql.function('strftime', (format, _source) => format === '%s' ? String(Math.floor(this.now / 1000))
      : `${String(Math.floor(this.now / 1000) % 60).padStart(2, '0')}.${String(this.now % 1000).padStart(3, '0')}`);
    this.sql.exec('PRAGMA foreign_keys=ON'); for (const source of sources) this.sql.exec(source);
  }
  prepare(sql) {
    const db = this.sql;
    return { sql, args: [], bind(...args) { assert.ok(args.length <= 100); this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return { success: true, results: db.prepare(sql).all(...this.args) }; },
      runSync() { const s = db.prepare(sql); return s.columns().length
        ? { success: true, results: s.all(...this.args), meta: { changes: 0 } }
        : { success: true, meta: { changes: Number(s.run(...this.args).changes) } }; } };
  }
  async batch(statements) {
    await this.beforeBatch?.(statements);
    this.sql.exec('BEGIN IMMEDIATE');
    let result;
    try { result = statements.map((s, n) => { this.inBatch?.(s, n); return s.runSync(); }); this.sql.exec('COMMIT'); }
    catch (error) { this.sql.exec('ROLLBACK'); throw error; }
    await this.afterBatch?.(statements);
    return result;
  }
}
function deferred() {
  let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const outbox = (f) => f.db.sql.prepare('SELECT * FROM pull_control_outbox WHERE outbox_id=?').get(id(4));
const count = (f, bucket) => f.db.sql.prepare('SELECT count FROM pull_budgets WHERE bucket=?').get(bucket)?.count ?? 0;
const audit = (f) => f.db.sql.prepare('SELECT code FROM pull_audit ORDER BY audit_id').all().map((r) => r.code);
const admission = (ss) => ss.some((s) => s.sql.includes('SET attempts=attempts+1'));
const settlement = (ss) => ss.some((s) => s.sql.includes('provider_id=CASE'));
async function fixture(t, template = 'signup', purpose = 'owner_change') {
  const db = new D1(); t.after(() => db.sql.close());
  const mail = new VerificationMail({ db, serviceOrigin: ORIGIN, sealKeyId: 'synthetic', sealKey: new Uint8Array(32).fill(42) });
  const owner = await ownerIdentity('Owner.Name+tag@EXAMPLE.com'), other = await ownerIdentity('New@example.net');
  const flow = { flow_id: id(2), install_id: id(1), purpose: template === 'signup' ? 'signup' : purpose,
    expected_generation: 1, expected_owner_generation: 1, candidate_key_id: 'candidate', candidate_public_key: KEY,
    owner_hash: owner.owner_hash, flow_secret_hash: H, fence_hash: H, input_digest: H,
    state: template === 'new_owner' ? 'approved' : 'pending', consent_version: 'v1', manifest_sequence: 1,
    manifest_digest: H, created_ms: NOW, expires_ms: NOW + VERIFICATION_TTL };
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,created_ms,updated_ms)
    VALUES(?,1,1,?,?,?,'m00000000000000000000000000001','candidate',?,'v1',?,?)`)
    .run(id(1), template === 'signup' ? 'pending' : 'active', owner.owner_hash, owner.owner_address, KEY, NOW, NOW);
  const keys = Object.keys(flow);
  db.sql.prepare(`INSERT INTO pull_flows(${keys}) VALUES(${keys.map(() => '?')})`).run(...Object.values(flow));
  let change = null;
  if (template === 'new_owner' || ['old_notice', 'revert_old', 'revert_new'].includes(template)) {
    change = { change_id: id(3), install_id: id(1), flow_id: id(2), old_owner_hash: owner.owner_hash,
      new_owner_hash: other.owner_hash, expected_generation: 1, expected_owner_generation: 1,
      state: 'pending', created_ms: NOW, expires_ms: NOW + VERIFICATION_TTL };
    const cols = Object.keys(change);
    db.sql.prepare(`INSERT INTO pull_owner_changes(${cols}) VALUES(${cols.map(() => '?')})`).run(...Object.values(change));
  }
  if (['signup', 'stepup', 'new_owner'].includes(template)) {
    const issued = await mail.issueStatements({ outbox_id: id(4), template,
      to: template === 'new_owner' ? other.owner_address : owner.owner_address, flow, change }, NOW);
    await db.batch(issued.statements);
  } else {
    const oldNotice = template === 'old_notice';
    db.sql.prepare(`UPDATE pull_installs SET generation=?,owner_generation=?,owner_hash=?,owner_address=?,state=?`)
      .run(oldNotice ? 2 : 3, oldNotice ? 2 : 3, oldNotice ? other.owner_hash : owner.owner_hash,
        oldNotice ? other.owner_address : owner.owner_address, oldNotice ? 'verified' : 'frozen');
    db.sql.prepare('UPDATE pull_flows SET state=?').run(oldNotice ? 'completed' : 'reverted');
    db.sql.prepare(`UPDATE pull_owner_changes SET state=?,switched_generation=2,switched_owner_generation=2,
      completed_ms=?,revert_until_ms=?,reverted_ms=?`).run(oldNotice ? 'completed' : 'reverted', NOW, NOW + WEEK, oldNotice ? null : NOW);
    const target = template === 'revert_new' ? other : owner;
    const row = { outbox_id: id(4), install_id: id(1), flow_id: id(2), template,
      target_hash: target.owner_hash, expires_ms: NOW + WEEK };
    const sealed = await mail.sealEnvelope(row, { to: target.owner_address, token: TOKEN, link_kind: oldNotice ? 'revert' : 'link' });
    db.sql.prepare(`INSERT INTO pull_control_outbox(outbox_id,install_id,flow_id,change_id,template,target_hash,
      idempotency_key,envelope_sealed,seal_key_id,state,next_attempt_ms,created_ms,expires_ms)
      VALUES(?,?,?,?,?,?,?,?,'synthetic','queued',?,?,?)`).run(id(4), id(1), id(2), id(3), template,
      target.owner_hash, `control:${template}`, sealed, NOW, NOW, NOW + WEEK);
    if (oldNotice) db.sql.prepare(`INSERT INTO pull_tokens(token_hash,install_id,flow_id,change_id,purpose,generation,
      owner_generation,owner_hash,binding_digest,state,created_ms,expires_ms) VALUES(?,?,?,?,'revert',2,2,?,?,'pending',?,?)`)
      .run(await sha256(bytes(TOKEN)), id(1), id(2), id(3), owner.owner_hash,
        await verificationBinding({ purpose: 'revert', flow_id: id(2), change_id: id(3), install_id: id(1), generation: 2,
          owner_generation: 2, owner_hash: owner.owner_hash, candidate_key_id: 'candidate', candidate_public_key: KEY, expires_ms: NOW + WEEK }), NOW, NOW + WEEK);
  }
  const calls = [], pending = new Map(), delays = [], logs = [];
  let timerSeq = 0, wall = NOW;
  const timers = {
    setTimeout(fn, ms) { const n = ++timerSeq; pending.set(n, fn); delays.push(ms); return n; },
    clearTimeout(n) { pending.delete(n); },
  };
  const f = { db, mail, owner, other, flow, change, calls, pending, delays, logs, timers,
    provider: async () => ({ messageId: 'opaque-acceptance-1' }),
    clock: () => wall,
    setWall(n) { wall = n; },
    timeout() { wall += 10_000; for (const fn of pending.values()) fn(); },
    run(options = {}) { return deliverControlMail({ mail, binding, from: FROM, domain: DOMAIN, clock: f.clock, timers,
      random: () => 0.5, ...options }); },
    async again() { db.sql.exec("UPDATE pull_sweep_cursors SET cursor_ms=0,cursor_id='' WHERE task='control'"); return f.run(); },
  };
  const binding = { send(message) { assert.equal(this, binding); calls.push(message); return f.provider(message); } };
  return f;
}

for (const template of ['signup', 'stepup', 'new_owner', 'old_notice', 'revert_old', 'revert_new']) {
  test(`${template}: exact fixed structured fields, recipient, acceptance evidence and envelope purge`, async (t) => {
    const f = await fixture(t, template), message = await f.mail.message(id(4), NOW);
    const original = outbox(f), originalBudgets = f.db.sql.prepare('SELECT * FROM pull_budgets ORDER BY bucket').all();
    assert.deepEqual(await f.run(), { scanned: 1, submitted: 1 });
    assert.deepEqual(f.calls, [{ from: FROM, to: message.to, subject: message.subject, text: message.body,
      headers: { 'Auto-Submitted': 'auto-generated', 'X-Clayrune-Agent-Mail': '1' } }]);
    assert.equal(message.to, (['new_owner', 'revert_new'].includes(template) ? f.other : f.owner).owner_address);
    const row = outbox(f);
    assert.equal(row.state, 'accepted'); assert.equal(row.provider_id, 'opaque-acceptance-1');
    assert.equal(row.envelope_sealed, null); assert.equal(row.attempts, 1);
    assert.equal(row.lease_id, null); assert.equal(row.lease_until_ms, null); assert.equal(row.finished_ms, NOW);
    assert.equal(row.expires_ms, original.expires_ms); assert.deepEqual(f.delays, [10_000]); assert.equal(f.pending.size, 0);
    if (['signup', 'stepup', 'new_owner'].includes(template)) {
      assert.deepEqual(f.db.sql.prepare('SELECT * FROM pull_budgets ORDER BY bucket').all(), originalBudgets);
    } else assert.equal(count(f, `pull:send:global:${Math.floor(NOW / DAY)}`), 1);
    await f.again(); assert.equal(f.calls.length, 1);
  });
}

test('competing pages and claims admit exactly one provider call', async (t) => {
  const f = await fixture(t); const receipt = deferred(), called = deferred();
  f.provider = () => { called.resolve(); return receipt.promise; };
  const first = f.run(); await called.promise;
  const second = await f.again(); assert.equal(second.submitted, 0); assert.equal(f.calls.length, 1);
  receipt.resolve({ messageId: 'only-once' }); await first;
  assert.equal(outbox(f).provider_id, 'only-once'); assert.equal(outbox(f).attempts, 1);
});

test('concurrent cursor snapshots cannot overwrite a competing committed page', async (t) => {
  const f = await fixture(t); const initial = deferred(); let readers = 0;
  const original = f.db.prepare.bind(f.db);
  f.db.prepare = (sql) => {
    const s = original(sql);
    if (sql === "SELECT * FROM pull_sweep_cursors WHERE task='control'") {
      const first = s.first.bind(s); s.first = async () => {
        const value = await first(); if (++readers <= 2) { if (readers === 2) initial.resolve(); await initial.promise; } return value;
      };
    }
    return s;
  };
  const result = await Promise.all([f.run(), f.run()]);
  assert.equal(result.reduce((n, r) => n + r.scanned, 0), 1); assert.equal(f.calls.length, 1);
});

const staleCases = [
  ['flow cancelled', "UPDATE pull_flows SET state='cancelled'"],
  ['flow completed', "UPDATE pull_flows SET state='completed'"],
  ['generation', 'UPDATE pull_installs SET generation=2'],
  ['owner generation', 'UPDATE pull_installs SET owner_generation=2'],
  ['owner hash', `UPDATE pull_installs SET owner_hash='${'d'.repeat(64)}'`],
  ['deleted', "UPDATE pull_installs SET state='deleted',owner_address=NULL"],
  ['frozen', "UPDATE pull_installs SET state='frozen'"],
  ['token consumed', "UPDATE pull_tokens SET state='consumed'"],
  ['token invalidated', "UPDATE pull_tokens SET state='invalidated'"],
  ['flow deadline', 'UPDATE pull_flows SET expires_ms=expires_ms+1'],
];
for (const [name, sql] of staleCases) {
  for (const race of [false, true]) test(`${name} ${race ? 'races admission CAS' : 'before claim'}: zero provider calls`, async (t) => {
    const f = await fixture(t);
    if (race) f.db.beforeBatch = (ss) => { if (admission(ss)) { f.db.beforeBatch = null; f.db.sql.exec(sql); } };
    else f.db.sql.exec(sql);
    await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'expired');
    assert.equal(outbox(f).envelope_sealed, null); assert.equal(outbox(f).attempts, 0);
  });
}

for (const mode of ['cancel', 'revoke']) {
  test(`${mode} fence racing admission aborts the whole batch`, async (t) => {
    const f = await fixture(t);
    f.db.beforeBatch = (ss) => {
      if (admission(ss)) {
        f.db.beforeBatch = null;
        f.db.sql.prepare('INSERT INTO pull_fences VALUES(?,?,1,?,?,?,?,?,?)')
          .run(id(1), mode === 'cancel' ? id(2) : id(90), H, mode, id(91), id(92), H, NOW);
      }
    };
    await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'expired'); assert.equal(outbox(f).attempts, 0);
  });
}

test('disabled resume step-up crosses only an older matching fenced generation', async (t) => {
  const f = await fixture(t, 'stepup', 'resume');
  f.db.sql.exec("UPDATE pull_installs SET state='disabled'");
  f.db.sql.prepare("INSERT INTO pull_generations VALUES(?,1,1,?,'candidate',?,?,?,'fenced',?,?)")
    .run(id(1), f.owner.owner_hash, KEY, H, id(9), NOW - 1000, NOW - 1);
  f.db.sql.prepare("INSERT INTO pull_fences VALUES(?,?,1,?,'revoke',?,?,?,?)")
    .run(id(1), id(9), H, id(10), id(11), H, NOW - 1);
  await f.run(); assert.equal(f.calls.length, 1); assert.equal(outbox(f).state, 'accepted');
});

for (const template of ['old_notice', 'revert_old', 'revert_new']) {
  test(`${template}: lifecycle change in admission batch refuses`, async (t) => {
    const f = await fixture(t, template);
    f.db.beforeBatch = (ss) => { if (admission(ss)) { f.db.beforeBatch = null; f.db.sql.exec("UPDATE pull_owner_changes SET state='cancelled'"); } };
    await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'expired');
  });
}

test('new-owner verification cannot use a flow whose management purpose changed before delivery', async (t) => {
  const f = await fixture(t, 'new_owner'); f.db.sql.exec("UPDATE pull_flows SET purpose='resume'");
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'expired');
});

test('verification with an invalid original flow lifetime refuses before admission', async (t) => {
  const f = await fixture(t); f.db.sql.exec('UPDATE pull_flows SET created_ms=created_ms-1');
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'expired');
});

test('new-owner verification lifetime remains guarded in the provider-admission transaction', async (t) => {
  const f = await fixture(t, 'new_owner');
  f.db.beforeBatch = (ss) => { if (admission(ss)) { f.db.beforeBatch = null;
    f.db.sql.exec('UPDATE pull_owner_changes SET created_ms=created_ms-1');
  } };
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'expired');
});

test('old-owner security link survives key generation changes and logical deletion during revert window', async (t) => {
  const f = await fixture(t, 'old_notice');
  f.db.sql.exec("UPDATE pull_installs SET generation=3,state='deleted',owner_address=NULL");
  await f.run(); assert.equal(f.calls.length, 1); assert.equal(f.calls[0].to, f.owner.owner_address);
});

for (const delta of [-1, 0]) test(`strict token expiry ${delta === 0 ? 'at boundary' : 'one ms before'}`, async (t) => {
  const f = await fixture(t); f.db.now = NOW + VERIFICATION_TTL + delta;
  await f.run(); assert.equal(f.calls.length, delta === 0 ? 0 : 1);
  assert.equal(outbox(f).state, delta === 0 ? 'expired' : 'accepted'); assert.equal(outbox(f).envelope_sealed, null);
});

test('expiry during decryption/admission never sends an expired link', async (t) => {
  const f = await fixture(t);
  f.db.beforeBatch = (ss) => { if (admission(ss)) { f.db.beforeBatch = null; f.db.now = NOW + VERIFICATION_TTL; } };
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).envelope_sealed, null);
});

for (const stage of ['claim', 'admission', 'provider']) {
  test(`crash after ${stage}: expired lease becomes uncertain, no automatic replay`, async (t) => {
    const f = await fixture(t);
    if (stage === 'provider') f.db.beforeBatch = (ss) => { if (settlement(ss)) throw new Error('synthetic crash after call'); };
    else f.db.afterBatch = (ss) => {
      if (stage === 'claim' ? ss.some((s) => s.sql.includes("SET state='sending',lease_id")) : admission(ss)) {
        f.db.afterBatch = null; throw new Error('synthetic crash before call');
      }
    };
    await assert.rejects(f.run(), /^Error: storage_unavailable$/);
    assert.equal(f.calls.length, stage === 'provider' ? 1 : 0); assert.equal(outbox(f).state, 'sending');
    f.db.beforeBatch = null; f.db.now += 60_000;
    await f.again(); assert.equal(outbox(f).state, 'uncertain');
    assert.equal(f.calls.length, stage === 'provider' ? 1 : 0);
    await f.again(); assert.equal(outbox(f).state, 'uncertain');
  });
}

test('expired lease before provider admission is uncertain even without a call', async (t) => {
  const f = await fixture(t);
  f.db.beforeBatch = (ss) => { if (admission(ss)) { f.db.beforeBatch = null; f.db.now += 60_000; } };
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'uncertain');
});

for (const providerId of ['', ' ', '\n', 'id\r\nheader', 'é', 'a'.repeat(257), null, 7, undefined]) {
  test(`invalid provider id ${JSON.stringify(providerId)} => uncertain without replay`, async (t) => {
    const f = await fixture(t); f.provider = async () => ({ messageId: providerId });
    await f.run(); assert.equal(outbox(f).state, 'uncertain'); assert.equal(outbox(f).provider_id, '');
    await f.again(); assert.equal(f.calls.length, 1);
  });
}

test('opaque printable 256-byte provider acceptance id is retained unchanged', async (t) => {
  const f = await fixture(t); const providerId = ' opaque/'.padEnd(256, 'Z');
  f.provider = async () => ({ messageId: providerId }); await f.run(); assert.equal(outbox(f).provider_id, providerId);
});

const refusals = [
  ['E_RATE_LIMIT_EXCEEDED', 'queued', 'provider_quota'], ['E_DAILY_LIMIT_EXCEEDED', 'queued', 'provider_quota'],
  ['E_RECIPIENT_SUPPRESSED', 'failed', 'provider_suppressed'],
  ...['E_VALIDATION_ERROR', 'E_FIELD_MISSING', 'E_TOO_MANY_RECIPIENTS', 'E_TOO_MANY_ATTACHMENTS',
    'E_SENDER_NOT_VERIFIED', 'E_RECIPIENT_NOT_ALLOWED', 'E_ACCOUNT_SUSPENDED', 'E_SENDER_DOMAIN_NOT_AVAILABLE',
    'E_CONTENT_TOO_LARGE', 'E_HEADER_NOT_ALLOWED', 'E_HEADER_USE_API_FIELD', 'E_HEADER_VALUE_INVALID',
    'E_HEADER_VALUE_TOO_LONG', 'E_HEADER_NAME_INVALID', 'E_HEADERS_TOO_LARGE', 'E_HEADERS_TOO_MANY']
    .map((code) => [code, 'failed', 'provider_unavailable']),
  ...['E_INTERNAL_SERVER_ERROR', 'E_DELIVERY_FAILED', 'ETIMEDOUT', 'ECONNRESET', undefined, 'FUTURE_CODE']
    .map((code) => [code, 'uncertain', 'uncertain']),
];
for (const [code, state, reason] of refusals) test(`${code}: closed ${state}/${reason} outcome`, async (t) => {
  const f = await fixture(t); f.provider = () => { throw Object.assign(new Error(`private ${TOKEN} ${f.owner.owner_address}`), { code }); };
  await f.run(); assert.equal(outbox(f).state, state); assert.deepEqual(audit(f), [reason]);
  if (state !== 'queued') { await f.again(); assert.equal(f.calls.length, 1); }
});

test('only real Error.code proves nonacceptance; exception text/plain objects cannot grant retry', async (t) => {
  for (const error of [new Error('E_RATE_LIMIT_EXCEEDED'), { code: 'E_RATE_LIMIT_EXCEEDED' }, 'E_DAILY_LIMIT_EXCEEDED', null]) {
    const f = await fixture(t); f.provider = () => { throw error; }; await f.run();
    assert.equal(outbox(f).state, 'uncertain'); await f.again(); assert.equal(f.calls.length, 1);
  }
});

test('quota retry keeps original envelope/token/id and budgets, with jittered not-before', async (t) => {
  const f = await fixture(t), original = outbox(f), global = `pull:send:global:${Math.floor(NOW / DAY)}`;
  f.provider = () => { throw Object.assign(new Error('quota'), { code: 'E_DAILY_LIMIT_EXCEEDED' }); };
  await f.run(); assert.equal(outbox(f).next_attempt_ms, NOW + 1000); assert.equal(count(f, global), 1);
  await f.again(); assert.equal(f.calls.length, 1);
  f.db.now += 1000; f.provider = async () => ({ messageId: 'retry-accepted' }); await f.again();
  assert.equal(outbox(f).state, 'accepted'); assert.equal(outbox(f).attempts, 2); assert.equal(count(f, global), 2);
  assert.deepEqual(f.calls[0], f.calls[1]); assert.equal(outbox(f).idempotency_key, original.idempotency_key);
  const verify = f.db.sql.prepare("SELECT count FROM pull_budgets WHERE bucket LIKE 'pull:verify:%'").all();
  assert.deepEqual(verify.map((r) => r.count), [1, 1]);
});

test('notice global exhaustion queues without provider admission, retry waits until next UTC day', async (t) => {
  const f = await fixture(t, 'old_notice'), day = Math.floor(NOW / DAY);
  f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,1000,?)').run(`pull:send:global:${day}`, (day + 2) * DAY);
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'queued');
  assert.equal(outbox(f).attempts, 0); assert.equal(outbox(f).next_attempt_ms, (day + 1) * DAY);
  f.db.now = (day + 1) * DAY; await f.again(); assert.equal(f.calls.length, 1);
  assert.equal(count(f, `pull:send:global:${day}`), 1000); assert.equal(count(f, `pull:send:global:${day + 1}`), 1);
});

test('late-day verification admission pays new UTC-day budget without recharging verification limits', async (t) => {
  const f = await fixture(t), day = Math.floor(NOW / DAY), end = (day + 1) * DAY;
  // A legitimate 30-minute link issued across midnight.
  const shift = end - NOW - 1000;
  f.db.sql.exec(`UPDATE pull_flows SET created_ms=created_ms+${shift},expires_ms=expires_ms+${shift};
    UPDATE pull_tokens SET created_ms=created_ms+${shift},expires_ms=expires_ms+${shift};`);
  const row = { ...outbox(f), created_ms: NOW + shift, expires_ms: NOW + shift + VERIFICATION_TTL };
  const clear = await f.mail.openEnvelope(outbox(f));
  const sealed = await f.mail.sealEnvelope(row, clear);
  const digest = await verificationBinding({ purpose: 'signup', install_id: id(1), flow_id: id(2), change_id: null,
    generation: 1, owner_generation: 1, owner_hash: row.target_hash, candidate_key_id: 'candidate', candidate_public_key: KEY, expires_ms: row.expires_ms });
  f.db.sql.prepare('UPDATE pull_tokens SET binding_digest=?').run(digest);
  f.db.sql.prepare('UPDATE pull_control_outbox SET created_ms=?,expires_ms=?,envelope_sealed=?')
    .run(row.created_ms, row.expires_ms, sealed);
  f.db.now = end; await f.run(); assert.equal(f.calls.length, 1); assert.equal(count(f, `pull:send:global:${day + 1}`), 1);
});

test('provider timeout is exactly 10s and late acceptance cannot requeue or change outcome', async (t) => {
  const f = await fixture(t), pending = deferred(), called = deferred();
  f.provider = () => { called.resolve(); return pending.promise; };
  const run = f.run(); await called.promise; assert.deepEqual(f.delays, [10_000]);
  f.timeout(); await run; assert.equal(outbox(f).state, 'uncertain'); assert.equal(f.pending.size, 0);
  pending.resolve({ messageId: 'late' }); await Promise.resolve(); await f.again();
  assert.equal(outbox(f).state, 'uncertain'); assert.equal(outbox(f).provider_id, ''); assert.equal(f.calls.length, 1);
});

test('late quota rejection also stays uncertain; no unhandled rejection or new attempt', async (t) => {
  const f = await fixture(t), pending = deferred(), called = deferred();
  f.provider = () => { called.resolve(); return pending.promise; };
  const run = f.run(); await called.promise; f.timeout(); await run;
  pending.reject(Object.assign(new Error('private'), { code: 'E_DAILY_LIMIT_EXCEEDED' }));
  await Promise.resolve(); await f.again(); assert.equal(f.calls.length, 1); assert.equal(outbox(f).state, 'uncertain');
});

test('completion clock fences receipt even when the timer callback is delayed', async (t) => {
  const f = await fixture(t); f.provider = async () => { f.setWall(NOW + 10_000); return { messageId: 'too-late' }; };
  await f.run(); assert.equal(outbox(f).state, 'uncertain');
});

for (const at of [NaN, Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER]) {
  test(`invalid initial clock ${at} cannot invoke provider or claim submission`, async (t) => {
    const f = await fixture(t); f.setWall(at); const result = await f.run();
    assert.equal(result.submitted, 0); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'uncertain');
    await f.again(); assert.equal(f.calls.length, 0);
  });
}

for (const at of [NaN, Infinity, NOW - 1]) {
  test(`invalid completion clock ${at} cannot establish acceptance`, async (t) => {
    const f = await fixture(t); f.provider = async () => { f.setWall(at); return { messageId: 'unproven' }; };
    await f.run(); assert.equal(f.calls.length, 1); assert.equal(outbox(f).state, 'uncertain');
  });
}

test('clock failure after provider call is held with a closed uncertain audit', async (t) => {
  const f = await fixture(t); let reads = 0;
  await f.run({ clock: () => { if (++reads === 2) throw new Error('private clock failure'); return NOW; } });
  assert.equal(f.calls.length, 1); assert.equal(outbox(f).state, 'uncertain'); assert.deepEqual(audit(f), ['uncertain']);
});

test('lifecycle retirement during provider call preserves evidence without resurrecting send state', async (t) => {
  const f = await fixture(t); f.provider = async () => {
    f.db.sql.exec("UPDATE pull_installs SET state='disabled'; UPDATE pull_control_outbox SET state='uncertain',envelope_sealed=NULL");
    return { messageId: 'accepted-after-disable' };
  };
  await f.run(); assert.equal(outbox(f).state, 'uncertain'); assert.equal(outbox(f).provider_id, 'accepted-after-disable');
  assert.equal(outbox(f).envelope_sealed, null); await f.again(); assert.equal(f.calls.length, 1);
});

test('lease loss while provider runs retains late positive evidence as uncertainty', async (t) => {
  const f = await fixture(t); f.provider = async () => { f.db.now += 60_000; return { messageId: 'lease-lost' }; };
  await f.run(); assert.equal(outbox(f).state, 'uncertain'); assert.equal(outbox(f).provider_id, 'lease-lost');
  assert.equal(outbox(f).envelope_sealed, null); await f.again(); assert.equal(f.calls.length, 1);
});

test('expired uncertain envelopes are purged while provider evidence remains', async (t) => {
  const f = await fixture(t);
  f.db.sql.exec("UPDATE pull_control_outbox SET state='uncertain',provider_id='evidence'");
  f.db.now = NOW + VERIFICATION_TTL;
  await f.run(); assert.equal(outbox(f).state, 'expired'); assert.equal(outbox(f).provider_id, 'evidence');
  assert.equal(outbox(f).envelope_sealed, null); assert.equal(f.calls.length, 0);
});

test('expiry during provider call purges envelope and keeps positive evidence without another call', async (t) => {
  const f = await fixture(t); f.provider = async () => { f.db.now = NOW + VERIFICATION_TTL; return { messageId: 'accepted-at-expiry' }; };
  await f.run(); assert.equal(outbox(f).state, 'expired'); assert.equal(outbox(f).provider_id, 'accepted-at-expiry');
  assert.equal(outbox(f).envelope_sealed, null); await f.again(); assert.equal(f.calls.length, 1);
});

test('missing or replaced lease cannot admit provider or requeue after a stale completion', async (t) => {
  const f = await fixture(t);
  f.db.beforeBatch = (ss) => { if (admission(ss)) {
    f.db.beforeBatch = null; f.db.sql.exec("UPDATE pull_control_outbox SET state='uncertain',lease_id=NULL,lease_until_ms=NULL");
  } };
  await f.run(); assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'uncertain');
});

test('admission transaction rolls back attempt and global charge on its last lifecycle guard', async (t) => {
  const f = await fixture(t, 'old_notice'); let changed = false;
  f.db.inBatch = (s) => { if (!changed && s.sql.includes('SET attempts=attempts+1')) {
    changed = true; f.db.sql.exec('UPDATE pull_installs SET owner_generation=9');
  } };
  await assert.rejects(f.run(), /^Error: storage_unavailable$/); f.db.inBatch = null;
  assert.equal(f.calls.length, 0); assert.equal(outbox(f).attempts, 0);
  assert.equal(count(f, `pull:send:global:${Math.floor(NOW / DAY)}`), 0);
  assert.equal(f.db.sql.prepare('SELECT owner_generation FROM pull_installs').get().owner_generation, 2);
  f.db.now += 60_000; await f.again(); assert.equal(outbox(f).state, 'uncertain');
});

test('UTC midnight race holds rather than bypassing a different day budget', async (t) => {
  const f = await fixture(t, 'old_notice'); f.db.now = (Math.floor(NOW / DAY) + 1) * DAY - 1;
  f.db.beforeBatch = (ss) => { if (admission(ss)) { f.db.beforeBatch = null; f.db.now++; } };
  await assert.rejects(f.run(), /^Error: storage_unavailable$/);
  assert.equal(f.calls.length, 0); assert.equal(outbox(f).attempts, 0);
  assert.equal(f.db.sql.prepare('SELECT count(*) AS n FROM pull_budgets').get().n, 0);
});

test('audit cap retains only daily overflow counts, never private exception data', async (t) => {
  const f = await fixture(t);
  f.db.sql.exec(`WITH RECURSIVE n(x) AS(SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000)
    INSERT INTO pull_audit(install_id,at_ms,code) SELECT '${id(1)}',${NOW},'existing' FROM n`);
  await f.run(); assert.equal(f.db.sql.prepare('SELECT count(*) AS n FROM pull_audit').get().n, 10000);
  assert.deepEqual({ ...f.db.sql.prepare('SELECT * FROM pull_audit_overflow').get() }, { day_ms: Math.floor(NOW / DAY) * DAY, count: 1 });
});

test('bounded 100-row pages persist fair passes and survive fresh consumer invocations', async (t) => {
  const f = await fixture(t), base = outbox(f); f.db.sql.exec('DELETE FROM pull_control_outbox');
  for (let n = 1; n <= 205; n++) f.db.sql.prepare(`INSERT INTO pull_control_outbox(outbox_id,install_id,flow_id,
    template,target_hash,idempotency_key,envelope_sealed,seal_key_id,state,next_attempt_ms,created_ms,expires_ms)
    VALUES(?,?,?,'signup',?,?,?,'synthetic','uncertain',?,?,?)`).run(id(n + 100), base.install_id, base.flow_id,
    base.target_hash, `fair:${n}`, base.envelope_sealed, NOW, NOW - VERIFICATION_TTL, NOW);
  assert.equal((await f.run()).scanned, 100); assert.equal((await f.run()).scanned, 100);
  assert.equal((await f.run()).scanned, 5); assert.equal((await f.run()).scanned, 0);
  assert.equal(f.db.sql.prepare('SELECT count(*) AS n FROM pull_control_outbox WHERE envelope_sealed IS NOT NULL').get().n, 0);
  assert.equal(f.calls.length, 0);
  assert.equal(f.db.sql.prepare("SELECT cursor_ms FROM pull_sweep_cursors WHERE task='control'").get().cursor_ms, 0);
});

test('poison envelope fails closed, never reaches provider, logs only a constant', async (t) => {
  const f = await fixture(t), messages = []; t.mock.method(console, 'error', (...args) => messages.push(args.join(' ')));
  const row = outbox(f); row.envelope_sealed[15] ^= 1;
  f.db.sql.prepare('UPDATE pull_control_outbox SET envelope_sealed=?').run(row.envelope_sealed);
  await f.run(); assert.equal(outbox(f).state, 'failed'); assert.equal(f.calls.length, 0);
  assert.deepEqual(messages, ['[agent-mail] control_delivery_message_unavailable']);
});

test('raw provider/DB exceptions, recipient, token and message body are absent from logs and audit', async (t) => {
  const f = await fixture(t), messages = []; t.mock.method(console, 'error', (...args) => messages.push(args.join(' ')));
  const message = await f.mail.message(id(4), NOW);
  const secret = `${message.to} ${message.body}`;
  f.provider = () => { throw new Error(secret); }; await f.run();
  const recorded = JSON.stringify({ messages, audit: f.db.sql.prepare('SELECT * FROM pull_audit').all(), row: outbox(f) });
  assert.equal(recorded.includes(message.to), false); assert.equal(recorded.includes(message.body), false);
  assert.equal(recorded.includes(/token=([a-f0-9]+)/.exec(message.body)[1]), false);
  f.db.beforeBatch = () => { throw new Error(secret); };
  await assert.rejects(f.run(), /^Error: storage_unavailable$/);
  assert.deepEqual(messages, ['[agent-mail] control_delivery_storage_unavailable']);
});

for (const options of [{ binding: {} }, { from: 'bad' }, { from: 'agents@wrong.example.com' }, { domain: 'MAIL.example.com' },
  { clock: 1 }, { timers: {} }, { random: null }]) {
  test(`invalid internal config (${Object.keys(options)}) fails before storage/provider work`, async (t) => {
    const f = await fixture(t); await assert.rejects(f.run(options), /^Error: provider_config_required$/);
    assert.equal(f.calls.length, 0); assert.equal(outbox(f).state, 'queued');
  });
}
