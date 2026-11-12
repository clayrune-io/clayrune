import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { OwnerRegistry, ownerIdentity, newAddressLocal } from '../owner-registry.js';

const NOW = 1_794_249_600_000;
const DAY = 86_400_000;
const END = NOW + 30 * 60_000;
const HASH = 'a'.repeat(64);
const OTHER = 'b'.repeat(64);
const id = (n) => n.toString(16).padStart(32, '0');
const address = (n) => `m${n.toString(16).padStart(29, '0')}`;
const claim = (n, over = {}) => ({ owner_hash: HASH, install_id: id(n), flow_id: id(n), until_ms: END, ...over });

// Same node:sqlite engine/schema/migration ordering as pull-migration.test.js;
// the facade adds D1's all-or-nothing batch contract, not canned SQL answers.
class D1 {
  constructor(path = ':memory:', init = true) {
    this.sql = new DatabaseSync(path);
    this.sql.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000');
    if (init) {
      this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
      const dir = new URL('../migrations/', import.meta.url);
      for (const name of readdirSync(dir).filter((n) => /^\d+.*\.sql$/.test(n)).sort()) {
        this.sql.exec(readFileSync(new URL(name, dir), 'utf8'));
      }
    }
  }
  prepare(sql) {
    const db = this.sql;
    return { args: [], bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      runSync() { const r = db.prepare(sql).run(...this.args); return { success: true, meta: { changes: Number(r.changes) } }; } };
  }
  async batch(statements) {
    this.sql.exec('BEGIN IMMEDIATE');
    try { const r = statements.map((s) => s.runSync()); this.sql.exec('COMMIT'); return r; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}

function fixture(t, path) {
  const db = new D1(path); t.after(() => db.sql.close());
  return { db, registry: new OwnerRegistry(db) };
}
function install(r, n, over = {}) {
  const row = { install_id: id(n), generation: 1, owner_generation: 1, state: 'pending',
    owner_hash: HASH, owner_address: 'Owner@example.com', address_local: address(n),
    key_id: 'key', public_key: 'a'.repeat(43), consent_version: '1', created_ms: NOW, updated_ms: NOW, ...over };
  const columns = Object.keys(row);
  return r.stmt(`INSERT INTO pull_installs(${columns.join(',')}) VALUES(${columns.map(() => '?').join(',')})`, ...Object.values(row));
}
function row(db, sql, ...args) { return db.sql.prepare(sql).get(...args); }
function count(db, table) { return row(db, `SELECT COUNT(*) AS n FROM ${table}`).n; }
function budgets(db) { return db.sql.prepare('SELECT * FROM pull_budgets ORDER BY bucket').all(); }
function slotStatements(r, n, over = {}, now = NOW) { return r.reserveSlotStatements(claim(n, over), now); }
function verification(r, n, hash = HASH, now = NOW) {
  return r.verificationStatements({ install_id: id(n), target_hash: hash }, now);
}
function reservation(r, n, over = {}, now = NOW) {
  return r.reserveAddressStatements({ address_local: address(n), install_id: id(n), until_ms: END, ...over }, now);
}

// Representative callers own these records, not OwnerRegistry. Their writes
// deliberately straddle registry guards so tests catch zero-row partial commits.
function signup(r, n, over = {}) {
  const local = over.address_local ?? address(n);
  const hash = over.owner_hash ?? HASH;
  return [install(r, n, { owner_hash: hash, address_local: local }),
    r.stmt(`INSERT INTO pull_generations VALUES(?,1,1,?,'key',?,?,?,'current',?,NULL)`,
      id(n), hash, 'a'.repeat(43), HASH, id(n), NOW),
    ...slotStatements(r, n, { owner_hash: hash }),
    ...verification(r, n, hash),
    ...reservation(r, n, { address_local: local }),
    r.stmt(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,
      candidate_key_id,candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,
      consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms)
      VALUES(?,?,'signup',1,1,'key',?,?,?,?,?,'pending','1',1,?,?,?)`,
    id(n), id(n), 'a'.repeat(43), hash, HASH, HASH, HASH, HASH, NOW, END),
    r.stmt(`INSERT INTO pull_control_outbox(outbox_id,install_id,flow_id,template,target_hash,idempotency_key,
      envelope_sealed,seal_key_id,state,next_attempt_ms,created_ms,expires_ms)
      VALUES(?,?,?,'signup',?,?,?,'control-key','queued',?,?,?)`,
    id(n), id(n), id(n), hash, id(n), new Uint8Array([1, 2, 3]), NOW, NOW, END),
  ];
}

// Separate workers/connections contend for the SAME file, start barrier and
// real SQLite write lock. Promise.all on synchronous in-memory writes would
// serialize in JS and would not test independent concurrent writers.
async function race(t, path, action, contenders) {
  const gate = new SharedArrayBuffer(4);
  const workers = contenders.map((n) => new Worker(new URL(import.meta.url), { workerData: { path, action, n, gate } }));
  t.after(async () => { await Promise.all(workers.map((w) => w.terminate())); });
  const ready = []; const done = [];
  for (const worker of workers) {
    ready.push(new Promise((resolve, reject) => {
      worker.on('message', (m) => { if (m.ready) resolve(); }); worker.once('error', reject);
      worker.once('exit', (code) => { if (code !== 0) reject(new Error(`race worker exit ${code}`)); });
    }));
    done.push(new Promise((resolve, reject) => {
      worker.on('message', (m) => { if ('ok' in m) resolve(m); }); worker.once('error', reject);
      worker.once('exit', (code) => { if (code !== 0) reject(new Error(`race worker exit ${code}`)); });
    }));
  }
  await Promise.all(ready);
  Atomics.store(new Int32Array(gate), 0, 1); Atomics.notify(new Int32Array(gate), 0);
  return Promise.all(done);
}

if (!isMainThread) {
  const db = new D1(workerData.path, false); const r = new OwnerRegistry(db);
  const n = workerData.n;
  const statements = workerData.action === 'slot' ? signup(r, n)
    : workerData.action === 'address' ? signup(r, n, { address_local: address(99), owner_hash: n === 3 ? HASH : OTHER })
      : verification(r, n);
  parentPort.postMessage({ ready: true });
  Atomics.wait(new Int32Array(workerData.gate), 0, 0);
  try { await db.batch(statements); parentPort.postMessage({ ok: true, n }); }
  catch (e) { parentPort.postMessage({ ok: false, n, error: e.message }); }
  finally { db.sql.close(); }
} else {
  test('owner identity hashes canonical domain, preserves local case/dots/plus and rejects non-mailboxes', async () => {
    const canonical = await ownerIdentity('Owner+tag@EXAMPLE.COM');
    assert.equal(canonical.owner_address, 'Owner+tag@example.com');
    // Independently calculated SHA-256 vector, not a call to the implementation helper.
    assert.equal(canonical.owner_hash, 'd866269fba7f551d51568a854e650e777e119f93b53900462c79c262a588e215');
    assert.deepEqual(await ownerIdentity('Owner+tag@example.com'), canonical);
    for (const mailbox of ['owner+tag@example.com', 'Owner@example.com', 'Ow.ner+tag@example.com']) {
      assert.notEqual((await ownerIdentity(mailbox)).owner_hash, canonical.owner_hash);
    }
    for (const bad of [null, '', 'Name <o@example.com>', 'a@example.com,b@example.com', ' a@example.com',
      'a@example.com\n', 'a@localhost', 'a@éxample.com', '"a"@example.com']) {
      await assert.rejects(ownerIdentity(bad), /invalid_request/);
    }
    const locals = new Set(Array.from({ length: 30 }, newAddressLocal));
    assert.equal(locals.size, 30);
    for (const local of locals) assert.match(local, /^m[a-f0-9]{29}$/);
  });

  test('verification charges install + canonical target + global atomically with no refund API', async (t) => {
    const { db, registry: r } = fixture(t);
    for (let n = 0; n < 3; n++) await db.batch(verification(r, 1));
    assert.deepEqual(budgets(db).map((b) => b.count), [3, 3, 3]);
    const before = budgets(db);
    await assert.rejects(db.batch(verification(r, 1)), /CHECK constraint failed/);
    assert.deepEqual(budgets(db), before);
    // A new install cannot bypass the target's shared budget, and the failed
    // later target guard must roll back its earlier install budget insertion.
    await assert.rejects(db.batch(verification(r, 2)), /CHECK constraint failed/);
    assert.deepEqual(budgets(db), before);
    await assert.rejects(db.batch(verification(r, 1, OTHER)), /CHECK constraint failed/);
    assert.deepEqual(budgets(db), before);
    await db.batch(verification(r, 2, OTHER));
    assert.equal(row(db, "SELECT count FROM pull_budgets WHERE bucket LIKE 'pull:send:global:%'").count, 4);
  });

  test('global exhaustion rolls back both verification budgets and all signup records', async (t) => {
    const { db, registry: r } = fixture(t); const day = Math.floor(NOW / DAY);
    db.sql.prepare('INSERT INTO pull_budgets VALUES(?,999,?)').run(`pull:send:global:${day}`, NOW + 2 * DAY);
    await db.batch(signup(r, 1));
    const before = budgets(db);
    await assert.rejects(db.batch(signup(r, 2, { owner_hash: OTHER })), /CHECK constraint failed/);
    assert.deepEqual(budgets(db), before);
    for (const table of ['pull_installs', 'pull_generations', 'pull_addresses', 'pull_owner_slots', 'pull_flows', 'pull_control_outbox']) {
      assert.equal(count(db, table), 1, table);
    }
  });

  test('UTC budget boundary resets counts without sliding expiry or altering prior evidence', async (t) => {
    const { db, registry: r } = fixture(t); const tomorrow = (Math.floor(NOW / DAY) + 1) * DAY;
    for (let n = 0; n < 3; n++) await db.batch(verification(r, 1, HASH, tomorrow - 1));
    const before = budgets(db);
    await db.batch(verification(r, 1, HASH, tomorrow));
    assert.equal(budgets(db).length, 6);
    for (const prior of before) assert.deepEqual(row(db, 'SELECT * FROM pull_budgets WHERE bucket=?', prior.bucket), prior);
    assert.equal(row(db, 'SELECT SUM(count) AS n FROM pull_budgets').n, 12);
  });

  test('three distinct live installs includes pending/active/revert; expired slots do not count', async (t) => {
    const { db, registry: r } = fixture(t);
    for (let n = 1; n <= 4; n++) await db.batch([install(r, n)]);
    await db.batch(slotStatements(r, 1));
    await db.batch(r.activateSlotStatements(claim(1), NOW));
    await db.batch(slotStatements(r, 1, { role: 'revert', until_ms: NOW + 7 * DAY }));
    await db.batch(slotStatements(r, 2));
    await db.batch(slotStatements(r, 3, { role: 'revert', until_ms: NOW + 7 * DAY }));
    assert.equal(count(db, 'pull_owner_slots'), 4); // roles != distinct installs
    await assert.rejects(db.batch(slotStatements(r, 4)), /CHECK constraint failed/);
    await db.batch(slotStatements(r, 4, { until_ms: END + 1 }, END));
    assert.equal(count(db, 'pull_owner_slots'), 5);
  });

  test('replay cannot extend/replace a live slot, and activation rechecks expiry and cap', async (t) => {
    const { db, registry: r } = fixture(t);
    await db.batch([install(r, 1), ...slotStatements(r, 1)]);
    await db.batch(slotStatements(r, 1));
    await assert.rejects(db.batch(slotStatements(r, 1, { until_ms: END + 1 }, NOW + 1)), /CHECK constraint failed/);
    await assert.rejects(db.batch(slotStatements(r, 1, { flow_id: id(99) })), /CHECK constraint failed/);
    await assert.rejects(db.batch(r.activateSlotStatements(claim(1), END)), /CHECK constraint failed/);
    // An expired slot can only be replaced by a fresh flow, not extended by replay.
    await assert.rejects(db.batch(slotStatements(r, 1, { until_ms: END + 1 }, END)), /CHECK constraint failed/);
    await db.batch(slotStatements(r, 1, { flow_id: id(2), until_ms: END + 1 }, END));
    await db.batch(r.activateSlotStatements(claim(1, { flow_id: id(2) }), END));
    await db.batch(r.activateSlotStatements(claim(1, { flow_id: id(2) }), END + DAY));
    assert.equal(row(db, 'SELECT role FROM pull_owner_slots').role, 'active');
    assert.equal(row(db, 'SELECT until_ms FROM pull_owner_slots').until_ms, null);
    // A damaged/over-cap store cannot promote even when its own slot exists.
    for (let n = 2; n <= 4; n++) {
      await db.batch([install(r, n), r.stmt("INSERT INTO pull_owner_slots VALUES(?,?,'active',?,NULL)", HASH, id(n), id(n))]);
    }
    await assert.rejects(db.batch(r.activateSlotStatements(claim(1, { flow_id: id(2) }), END + DAY)), /CHECK constraint failed/);
  });

  test('slot release is scoped to owner/install/role/flow and preserves old-owner revert', async (t) => {
    const { db, registry: r } = fixture(t);
    await db.batch([install(r, 1), ...slotStatements(r, 1),
      ...slotStatements(r, 1, { role: 'revert', until_ms: NOW + 7 * DAY })]);
    await db.batch(r.releaseSlotStatements(claim(1, { role: 'pending', flow_id: id(9) })));
    assert.equal(count(db, 'pull_owner_slots'), 2);
    await db.batch(r.releaseSlotStatements(claim(1, { role: 'pending' })));
    assert.equal(count(db, 'pull_owner_slots'), 1);
    assert.equal(row(db, 'SELECT role FROM pull_owner_slots').role, 'revert');
  });

  test('address reserve/assign/release is deadline-bound and quarantines exactly 90 days', async (t) => {
    const { db, registry: r } = fixture(t); const value = { address_local: address(1), install_id: id(1) };
    await db.batch([install(r, 1), ...reservation(r, 1)]);
    await db.batch(reservation(r, 1));
    await assert.rejects(db.batch(reservation(r, 1, { until_ms: END + 1 }, NOW + 1)), /CHECK constraint failed/);
    await assert.rejects(db.batch(r.assignAddressStatements(value, END)), /CHECK constraint failed/);
    await db.batch(r.assignAddressStatements(value, NOW));
    await db.batch(r.assignAddressStatements(value, END));
    await db.batch([r.stmt("UPDATE pull_installs SET state='active' WHERE install_id=?", id(1))]);
    assert.equal((await r.recipient(address(1))).install_id, id(1));
    assert.equal((await r.installation(id(1))).address_local, address(1));
    await db.batch(r.releaseAddressStatements(value, END));
    const until = END + 90 * DAY;
    assert.equal(row(db, 'SELECT until_ms FROM pull_addresses').until_ms, until);
    assert.equal(await r.recipient(address(1)), null);
    await db.batch(r.releaseAddressStatements(value, END + DAY));
    assert.equal(row(db, 'SELECT until_ms FROM pull_addresses').until_ms, until);
    await assert.rejects(db.batch(reservation(r, 1, { until_ms: until + 10 }, until - 1)), /CHECK constraint failed/);
    await assert.rejects(db.batch(r.assignAddressStatements(value, until)), /CHECK constraint failed/);
    await db.batch(reservation(r, 1, { until_ms: until + 10 }, until));
    assert.equal(row(db, 'SELECT state FROM pull_addresses').state, 'reserved');
  });

  test('expiry never silently reuses an address and foreign installs cannot assign/release it', async (t) => {
    const { db, registry: r } = fixture(t);
    await db.batch([install(r, 1), install(r, 2), ...reservation(r, 1)]);
    const foreign = { address_local: address(1), install_id: id(2) };
    await assert.rejects(db.batch(r.assignAddressStatements(foreign, NOW)), /CHECK constraint failed/);
    await assert.rejects(db.batch(r.releaseAddressStatements(foreign, NOW)), /CHECK constraint failed/);
    await assert.rejects(db.batch(reservation(r, 1, { until_ms: END + 1 }, END)), /CHECK constraint failed/);
    await assert.rejects(db.batch(reservation(r, 2, { address_local: address(1) })), /CHECK constraint failed/);
    assert.equal(row(db, 'SELECT state FROM pull_addresses').state, 'reserved');
  });

  test('a failure after flow/outbox rolls back registry, budgets and all caller writes', async (t) => {
    const { db, registry: r } = fixture(t);
    await assert.rejects(db.batch([...signup(r, 1), r.guard('0')]), /CHECK constraint failed/);
    for (const table of ['pull_installs', 'pull_generations', 'pull_addresses', 'pull_owner_slots',
      'pull_flows', 'pull_control_outbox', 'pull_budgets']) assert.equal(count(db, table), 0, table);
    assert.equal(row(db, 'SELECT version FROM pull_version').version, 1);
    await db.batch(signup(r, 1));
    assert.deepEqual(db.sql.prepare('PRAGMA foreign_key_check').all(), []);
    assert.equal(row(db, 'PRAGMA integrity_check').integrity_check, 'ok');
  });

  test('a null SQL guard aborts instead of admitting an unknown predicate', async (t) => {
    const { db, registry: r } = fixture(t);
    await assert.rejects(db.batch([...signup(r, 1), r.guard('NULL')]), /CHECK constraint failed/);
    assert.equal(count(db, 'pull_installs'), 0);
    assert.equal(count(db, 'pull_budgets'), 0);
  });

  test('caller operation uniqueness rolls back replay/conflict charges and preserves original digest/result', async (t) => {
    const { db, registry: r } = fixture(t); await db.batch([install(r, 1)]);
    const operation = (digest) => r.stmt(`INSERT INTO pull_operations VALUES(?,1,'/v1/pull/management/start',?,?,?,200,?,?)`,
      id(1), id(7), digest, '{"state":"pending"}', NOW, NOW + 30 * DAY);
    await db.batch([...verification(r, 1), operation(HASH)]);
    const before = budgets(db);
    // Later HTTP unit returns the stored exact-digest result before writing;
    // concurrent replay/conflict still cannot commit another budget charge.
    for (const digest of [HASH, OTHER]) {
      await assert.rejects(db.batch([...verification(r, 1), operation(digest)]), /UNIQUE constraint failed/);
      assert.deepEqual(budgets(db), before);
      assert.equal(row(db, 'SELECT input_digest FROM pull_operations').input_digest, HASH);
      assert.equal(row(db, 'SELECT result_json FROM pull_operations').result_json, '{"state":"pending"}');
    }
  });

  test('builders reject invalid identities, deadlines and unsafe timestamp arithmetic before SQL', (t) => {
    const { registry: r } = fixture(t);
    for (const now of [-1, 1.5, NaN, Infinity, '1', Number.MAX_SAFE_INTEGER]) {
      assert.throws(() => verification(r, 1, HASH, now), /invalid_request/);
    }
    for (const over of [{ install_id: 'bad' }, { install_id: [id(1)] }, { owner_hash: 'x'.repeat(64) }, { flow_id: id(1).toUpperCase() + 'A' },
      { role: 'active' }, { until_ms: NOW }, { until_ms: END + 1 }]) {
      assert.throws(() => slotStatements(r, 1, over), /invalid_request/);
    }
    for (const over of [{ address_local: 'a'.repeat(30) }, { install_id: 'bad' }, { until_ms: NOW }, { until_ms: END + 1 }]) {
      assert.throws(() => reservation(r, 1, over), /invalid_request/);
    }
  });

  for (const action of ['slot', 'address', 'verification']) {
    test(`concurrent ${action} claims on independent SQLite writers admit exactly one last claimant`, async (t) => {
      const dir = mkdtempSync(join(tmpdir(), 'pull-owner-')); const path = join(dir, 'registry.sqlite');
      const db = new D1(path); const r = new OwnerRegistry(db);
      t.after(() => { db.sql.close(); rmSync(dir, { recursive: true, force: true }); });
      if (action === 'slot') {
        await db.batch([install(r, 1), ...slotStatements(r, 1), install(r, 2), ...slotStatements(r, 2)]);
      } else if (action === 'verification') {
        await db.batch(verification(r, 1)); await db.batch(verification(r, 2));
      }
      const results = await race(t, path, action, [3, 4]);
      assert.equal(results.filter((v) => v.ok).length, 1, JSON.stringify(results));
      assert.match(results.find((v) => !v.ok).error, action === 'address' ? /UNIQUE constraint failed/ : /CHECK constraint failed/);
      if (action === 'slot') {
        assert.equal(count(db, 'pull_owner_slots'), 3);
        assert.equal(count(db, 'pull_installs'), 3);
      } else if (action === 'address') {
        assert.equal(count(db, 'pull_addresses'), 1); assert.equal(count(db, 'pull_installs'), 1);
        const winning = results.find((v) => v.ok).n;
        assert.equal(row(db, 'SELECT install_id FROM pull_addresses').install_id, id(winning));
      } else {
        assert.equal(row(db, "SELECT count FROM pull_budgets WHERE bucket LIKE 'pull:verify:target:%'").count, 3);
        assert.equal(row(db, "SELECT count FROM pull_budgets WHERE bucket LIKE 'pull:send:global:%'").count, 3);
      }
      assert.deepEqual(db.sql.prepare('PRAGMA foreign_key_check').all(), []);
      assert.equal(row(db, 'PRAGMA integrity_check').integrity_check, 'ok');
    });
  }
}
