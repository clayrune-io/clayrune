import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { Ledger } from '../ledger.js';
import { OwnerChangeHTTP } from '../owner-change.js';
import { pullOutbound } from '../outbound.js';
import { bytes, sha256 } from '../signing.js';

const NOW = 1_794_249_600_000, id = (n) => n.toString(16).padStart(32, '0');
const hex = (n) => n.toString(16).padStart(64, '0'), LOCAL = `m${'a'.repeat(29)}`;
const value = (extra = {}) => ({ op_id: id(1), intent_id: 'intent-1', thread_id: 'thread-1',
  reply_token: id(3), subject: 'A question', body: 'Which revision?', kind: 'question', ...extra });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { resolve, reject, promise };
};
const row = (f, table = 'pull_intents') => ({ ...f.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).get() });
const all = (f, table) => f.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all().map((r) => ({ ...r }));

// Real SQLite + every migration; deterministic D1 batch interleavings. No mail,
// HTTP handler, hosted browser ceremony or deployment is exercised here.
async function fixture(t, { send = async () => ({ messageId: 'provider-opaque-id' }), policy = {} } = {}) {
  const sql = new DatabaseSync(':memory:'); t.after(() => sql.close()); sql.exec('PRAGMA foreign_keys=ON');
  sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  const migrations = new URL('../migrations/', import.meta.url);
  for (const n of readdirSync(migrations).filter((s) => /^\d+.*\.sql$/.test(s)).sort()) {
    sql.exec(readFileSync(new URL(n, migrations), 'utf8'));
  }
  const f = { sql, time: NOW, calls: [], pending: new Map(), sequence: 0 };
  sql.function('strftime', (format, at) => {
    assert.equal(at, 'now'); return format === '%s' ? String(Math.floor(f.time / 1000))
      : `${String(Math.floor(f.time / 1000) % 60).padStart(2, '0')}.${String(f.time % 1000).padStart(3, '0')}`;
  });
  f.db = {
    prepare(query) {
      return { sql: query, args: [], bind(...args) { assert.ok(args.length <= 100); this.args = args; return this; },
        async first() { return sql.prepare(query).get(...this.args) ?? null; },
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
        const result = statements.map((s, n) => { if (this.inBatch) this.inBatch(s, n); return s.runSync(); });
        sql.exec('COMMIT'); return result;
      } catch (error) { sql.exec('ROLLBACK'); throw error; }
    },
  };
  f.ledger = new Ledger(f.db, policy).pull({ clock: () => f.time });
  sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,
    key_id,public_key,consent_version,epoch,created_ms,updated_ms) VALUES(?,7,3,'active',?,'Owner+tag@example.com',?,
    'key',?,'v1',4,?,?)`).run(id(1), hex(1), LOCAL, 'k'.repeat(43), NOW, NOW);
  sql.prepare("INSERT INTO pull_generations VALUES(?,7,3,?,'key',?,?,?,'current',?,NULL)")
    .run(id(1), hex(1), 'k'.repeat(43), hex(2), id(2), NOW);
  sql.prepare("INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)").run(LOCAL, id(1), NOW);
  f.reg = await f.ledger.registration(id(1));
  f.timers = {
    setTimeout(fn, delay) { const token = ++f.sequence; f.pending.set(token, { fn, delay }); return token; },
    clearTimeout(token) { f.pending.delete(token); },
  };
  f.binding = { async send(message) { f.calls.push(message); return send(message, f); } };
  f.send = (v = value(), extra = {}) => pullOutbound({ ledger: f.ledger, reg: f.reg, value: v,
    body: bytes(JSON.stringify(v)), config: { from: 'agents@mail.example.net', domain: 'mail.example.net' },
    binding: f.binding, clock: () => f.time, timers: f.timers, ...extra });
  f.switchOwner = async (restore = false) => {
    const i = row(f, 'pull_installs'), http = { stmt: f.ledger.stmt.bind(f.ledger), registry: f.ledger.registry };
    await f.db.batch([...OwnerChangeHTTP.prototype.retire.call(http, i, f.time),
      ...OwnerChangeHTTP.prototype.nextGeneration.call(http, i, { fence_hash: hex(2), flow_id: id(i.generation + 1) },
        { owner_hash: restore ? hex(1) : hex(9), owner_address: restore ? 'Owner+tag@example.com' : 'new@example.net' },
        restore ? 'frozen' : 'verified', f.time, true)]);
  };
  return f;
}

test('pull send durably reserves correlation and authorizes before structured owner-only provider call', async (t) => {
  const f = await fixture(t, { send: async (mail, x) => {
    assert.equal(row(x).state, 'sending'); assert.equal(all(x, 'pull_threads').length, 1);
    assert.equal(all(x, 'pull_budgets').length, 2);
    assert.deepEqual(mail, { from: 'agents@mail.example.net', to: 'Owner+tag@example.com',
      replyTo: `${LOCAL}+${id(3)}@mail.example.net`, subject: value().subject, text: value().body,
      headers: { 'Auto-Submitted': 'auto-generated', 'X-Clayrune-Agent-Mail': '1' } });
    return { messageId: ' opaque provider id ' };
  } });
  const result = await f.send();
  assert.equal(result.status, 200); assert.equal(result.state, 'accepted');
  assert.equal(result.provider_id, ' opaque provider id '); assert.equal(row(f).provider_id, result.provider_id);
  assert.equal(row(f).finished_ms, f.time);
  assert.equal(result.message_id, `${await sha256(bytes(`${id(1)}:7:intent-1`))}@mail.example.net`);
  assert.equal(JSON.stringify(all(f, 'pull_intents')).includes(value().body), false);
  assert.equal(row(f, 'pull_threads').token_hash, await sha256(bytes(id(3))));
  assert.deepEqual(await f.send(), result); assert.equal(f.calls.length, 1);
  assert.equal(f.pending.size, 0);
});

for (const over of [{ to: 'new@example.net' }, { raw: 'mime' }, { headers: {} }, { replyTo: 'new@example.net' },
  { op_id: 'bad' }, { subject: 'bad\r\nBcc: other@example.com' }, { reply_token: 'bad' }, { body: '\ud800' }]) {
  test(`pull validation refuses before reservation/provider ${JSON.stringify(over)}`, async (t) => {
    const f = await fixture(t); assert.equal((await f.send(value(over))).status, 400);
    assert.equal(f.calls.length, 0); assert.equal(all(f, 'pull_intents').length, 0);
  });
}

test('configuration refusal does not reserve, while exact body changes conflict even at identical parsed content', async (t) => {
  const f = await fixture(t);
  await assert.rejects(f.send(value(), { binding: {} }), { message: 'provider_config_required' });
  assert.equal(all(f, 'pull_intents').length, 0); await f.send();
  assert.equal((await f.send(value({ body: 'changed' }))).code, 'intent_conflict');
  assert.equal((await f.send(value(), { body: bytes(JSON.stringify(value()) + ' ') })).code, 'intent_conflict');
  assert.equal(f.calls.length, 1);
});

test('concurrent identical requests perform one structured provider write', async (t) => {
  const gate = deferred(), entered = deferred();
  const f = await fixture(t, { send: async () => { entered.resolve(); return gate.promise; } });
  const first = f.send(); await entered.promise;
  const retries = await Promise.all(Array.from({ length: 6 }, () => f.send()));
  assert.ok(retries.every((r) => r.state === 'sending')); gate.resolve({ messageId: 'id' });
  assert.equal((await first).state, 'accepted'); assert.equal(f.calls.length, 1);
  assert.ok(all(f, 'pull_budgets').every((r) => r.count === 1));
});

test('racing initial reservations return duplicates without a second authorization or provider call', async (t) => {
  const gate = deferred(), entered = deferred();
  const f = await fixture(t); let count = 0;
  f.db.beforeBatch = async () => { if (count++ === 0) { entered.resolve(); await gate.promise; } };
  const first = f.send(); await entered.promise; const winner = await f.send(); gate.resolve();
  const loser = await first; assert.equal(winner.state, 'accepted'); assert.equal(loser.state, 'accepted');
  assert.equal(f.calls.length, 1);
});

for (const phase of ['reserve', 'authorize']) for (const revert of [false, true]) {
  test(`owner ${revert ? 'revert' : 'switch'} before ${phase} commit prevents any provider call`, async (t) => {
    const f = await fixture(t); let batches = 0;
    f.db.beforeBatch = async () => {
      if (++batches !== (phase === 'reserve' ? 1 : 2)) return;
      f.db.beforeBatch = null; await f.switchOwner(); if (revert) await f.switchOwner(true);
    };
    const result = await f.send(); assert.equal(result.code, 'stale_generation'); assert.equal(f.calls.length, 0);
    if (phase === 'authorize') assert.equal(row(f).state, 'revoked');
    else assert.equal(all(f, 'pull_intents').length, 0);
    assert.equal(all(f, 'pull_budgets').length, 0);
  });
}

for (const revert of [false, true]) for (const phase of ['after_authorize', 'in_provider', 'before_finish']) {
  test(`acceptance after ${revert ? 'revert' : 'switch'} ${phase} stores evidence without reporting sent`, async (t) => {
    const f = await fixture(t, { send: async (mail, x) => {
      assert.equal(mail.to, 'Owner+tag@example.com');
      if (phase === 'in_provider') { await x.switchOwner(); if (revert) await x.switchOwner(true); }
      return { messageId: 'provider-race' };
    } });
    if (phase === 'after_authorize') {
      const original = f.ledger.authorizeSend.bind(f.ledger);
      f.ledger.authorizeSend = async (...args) => {
        const a = await original(...args); await f.switchOwner(); if (revert) await f.switchOwner(true);
        return a;
      };
    }
    if (phase === 'before_finish') {
      f.db.beforeBatch = async (statements) => {
        if (!statements.some((s) => s.sql.includes('SET state=?,provider_id='))) return;
        f.db.beforeBatch = null; await f.switchOwner(); if (revert) await f.switchOwner(true);
      };
    }
    const result = await f.send();
    assert.equal(result.status, 409); assert.equal(result.code, 'uncertain'); assert.equal(result.state, 'uncertain');
    assert.equal(result.provider_id, 'provider-race'); assert.equal(row(f).provider_id, 'provider-race');
    assert.equal(row(f).finished_ms, f.time); assert.equal(row(f).generation, 7); assert.equal(row(f).owner_generation, 3);
    assert.equal(row(f, 'pull_threads').revoked_ms, f.time); assert.equal(f.calls.length, 1);
    assert.equal((await f.send()).code, 'stale_generation'); assert.equal(f.calls.length, 1);
    assert.equal(await f.ledger.thread(row(f, 'pull_threads').token_hash), null);
  });
}

for (const kind of ['throw', 'invalid_id', 'timeout', 'quota', 'suppressed', 'permanent']) {
  test(`provider ${kind} persists a nonaccepted result and same intent never resubmits`, async (t) => {
    const entered = deferred(), gate = deferred();
    const f = await fixture(t, { send: async () => {
      if (kind === 'throw') throw new Error('private provider text');
      if (kind === 'invalid_id') return { messageId: '' };
      if (kind === 'timeout') { entered.resolve(); return gate.promise; }
      throw Object.assign(new Error('private provider text'), { code: kind === 'quota' ? 'E_RATE_LIMIT_EXCEEDED'
        : kind === 'suppressed' ? 'E_RECIPIENT_SUPPRESSED' : 'E_SENDER_NOT_VERIFIED' });
    } });
    const pending = f.send();
    if (kind === 'timeout') {
      await entered.promise; assert.equal(f.pending.size, 1); f.time += 10_000;
      for (const timer of f.pending.values()) { assert.equal(timer.delay, 10_000); timer.fn(); }
    }
    const result = await pending, state = ['quota', 'suppressed', 'permanent'].includes(kind) ? 'failed' : 'uncertain';
    assert.equal(result.status, 409); assert.equal(result.state, state); assert.equal(row(f).state, state);
    assert.equal(result.provider_id, ''); assert.equal(row(f).provider_id, '');
    assert.deepEqual(await f.send(), result); assert.equal(f.calls.length, 1);
    if (kind === 'timeout') {
      gate.resolve({ messageId: 'late-id' }); await gate.promise;
      for (let n = 0; n < 10; n++) await Promise.resolve();
      assert.equal(row(f).state, 'uncertain'); assert.equal(row(f).provider_id, '');
    }
  });
}

test('provider uncertainty after owner switch stays held without evidence or retry', async (t) => {
  const f = await fixture(t, { send: async (_mail, x) => { await x.switchOwner(); throw new Error('private'); } });
  assert.equal((await f.send()).state, 'uncertain'); assert.equal(row(f).state, 'uncertain');
  assert.equal(row(f).provider_id, ''); assert.equal((await f.send()).code, 'stale_generation');
  assert.equal(f.calls.length, 1);
});

test('last budget slot is charged atomically; denied intent remains a lookup on retry', async (t) => {
  const f = await fixture(t, { policy: { sendsPerDay: 1 } }); await f.send();
  const v = value({ intent_id: 'intent-2', thread_id: 'thread-2', reply_token: id(4) });
  assert.equal((await f.send(v)).code, 'send_limit'); assert.equal((await f.send(v)).state, 'reserved');
  assert.equal(f.calls.length, 1); assert.ok(all(f, 'pull_budgets').every((r) => r.count === 1));
});

test('storage failure persisting provider acceptance never returns accepted or submits again', async (t) => {
  const f = await fixture(t);
  f.db.inBatch = (s) => { if (s.sql.includes('SET state=?,provider_id=')) throw new Error('private disk failure'); };
  await assert.rejects(f.send(), { message: 'storage_unavailable' });
  assert.equal(row(f).state, 'sending'); assert.equal(row(f).provider_id, '');
  f.db.inBatch = null; assert.equal((await f.send()).state, 'sending'); assert.equal(f.calls.length, 1);
});

test('storage failure retaining a retired provider receipt never reports acceptance or revives state', async (t) => {
  const f = await fixture(t, { send: async (_mail, x) => { await x.switchOwner(); return { messageId: 'id' }; } });
  f.db.inBatch = (s) => { if (s.sql.includes('SET finished_ms=CASE')) throw new Error('private disk failure'); };
  await assert.rejects(f.send(), { message: 'storage_unavailable' });
  assert.equal(row(f).state, 'uncertain'); assert.equal(row(f).provider_id, ''); assert.equal(f.calls.length, 1);
});

test('caller mutation during awaits cannot select another owner, body or reply token', async (t) => {
  const f = await fixture(t), v = value(), gate = deferred(), entered = deferred();
  const original = f.ledger.intent.bind(f.ledger); let reads = 0;
  f.ledger.intent = async (...args) => { if (++reads === 1) { entered.resolve(); await gate.promise; } return original(...args); };
  const pending = f.send(v); await entered.promise;
  v.body = 'changed'; v.reply_token = id(99); f.reg.owner_address = 'other@example.com';
  gate.resolve(); assert.equal((await pending).state, 'accepted');
  assert.equal(f.calls[0].to, 'Owner+tag@example.com'); assert.equal(f.calls[0].text, value().body);
  assert.equal(f.calls[0].replyTo, `${LOCAL}+${id(3)}@mail.example.net`);
});
