import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EnrollmentDB, NOW, fixture } from './enrollment-support.js';
import { destinationProvisioning } from '../destination-provisioning.js';
const DAY = 86400000;

function world() {
  const db = new EnrollmentDB(); const time = { now: NOW }; const rows = []; const creates = [];
  db.clock = () => time.now;
  const client = { list: async () => rows.map((r) => ({ ...r })), create: async (address) => {
    // The durable claim exists BEFORE the external side effect.
    assert.ok(db.sql.prepare('SELECT * FROM mail_destination_creates WHERE address=?').get(address));
    creates.push(address); const row = { address, verified: false }; rows.push(row); return row;
  } };
  const provider = () => destinationProvisioning({ db, client, clock: () => time.now });
  const ensure = (address = 'owner@example.com', device_id = 'device1') => provider().ensure({ address, device_id });
  return { db, time, rows, creates, client, provider, ensure };
}

test('0004 upgrades existing enrollment without losing flow or identity; complete schema order works', async () => {
  const db = new EnrollmentDB(':memory:', false);
  db.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  for (const name of ['0001_enrollment.sql', '0002_delivery_proof.sql', '0003_lifecycle.sql']) {
    db.sql.exec(readFileSync(new URL(`../migrations/${name}`, import.meta.url), 'utf8'));
  }
  const f = await fixture({ db });
  const before = db.sql.prepare('SELECT * FROM mail_enrollment_flows').get();
  db.sql.exec(readFileSync(new URL('../migrations/0004_destinations.sql', import.meta.url), 'utf8'));
  assert.deepEqual(db.sql.prepare('SELECT * FROM mail_enrollment_flows').get(), before);
  assert.equal(db.sql.prepare('SELECT count(*) AS n FROM mail_identity_reservations').get().n, 1);
  await f.confirm(); assert.equal((await f.complete()).generation, 1);
  for (const name of ['mail_begin_nonces', 'mail_destination_attempts', 'mail_destination_creates', 'mail_destination_lock']) {
    assert.equal(db.sql.prepare(`SELECT count(*) AS n FROM ${name}`).get().n, 0);
  }
});

test('missing -> one create -> repeated pending -> verified, across provider restarts', async () => {
  const w = world();
  await assert.rejects(w.ensure(), /destination_unverified/);
  for (let i = 0; i < 3; i += 1) await assert.rejects(w.ensure(), /destination_unverified/);
  assert.equal(w.creates.length, 1);
  w.rows[0].verified = true;
  assert.deepEqual(await w.ensure(), { address: 'owner@example.com', verified: true });
});

test('existing pending or verified address is never created, even at capacity', async () => {
  const w = world(); w.rows.push(...Array.from({ length: 190 }, (_, n) => ({ address: `a${n}@example.com`, verified: false })));
  await assert.rejects(w.ensure('a0@example.com'), /destination_unverified/);
  w.rows[0].verified = true; assert.equal((await w.ensure('a0@example.com')).verified, true);
  assert.equal(w.creates.length, 0);
});

test('attempt survives new flow/reservation lifetime, address replacement only at 24h', async () => {
  const w = world(); await assert.rejects(w.ensure(), /destination_unverified/);
  w.time.now += DAY - 1;
  await assert.rejects(w.ensure('next@example.com'), /destination_pending/);
  w.time.now += 1; await assert.rejects(w.ensure('next@example.com'), /destination_unverified/);
  assert.equal(w.creates.length, 2);
  const row = w.db.sql.prepare('SELECT * FROM mail_destination_attempts').get();
  assert.equal(row.expires_ms, w.time.now + 7 * DAY);
});

test('pending lifetime is seven days, retries do not extend it; verified address can recover', async () => {
  const w = world(); await assert.rejects(w.ensure(), /destination_unverified/);
  w.time.now += 7 * DAY;
  await assert.rejects(w.ensure(), /destination_expired/);
  assert.equal(w.creates.length, 1);
  w.rows[0].verified = true; assert.equal((await w.ensure()).verified, true);
});

test('expired attempt cannot start a delayed create after an earlier provider outage', async () => {
  const w = world(); const list = w.client.list;
  w.client.list = async () => { throw new Error('destination_unavailable'); };
  await assert.rejects(w.ensure(), /destination_unavailable/);
  w.time.now += 7 * DAY; w.client.list = list;
  await assert.rejects(w.ensure(), /destination_expired/);
  assert.equal(w.creates.length, 0);
});

test('capacity counts unrelated account addresses, admits 189 then refuses 190', async () => {
  const w = world(); w.rows.push(...Array.from({ length: 189 }, (_, n) => ({ address: `a${n}@example.com`, verified: true })));
  await assert.rejects(w.ensure(), /destination_unverified/);
  await assert.rejects(w.ensure('other@example.com', 'device2'), /destination_capacity/);
  assert.equal(w.creates.length, 1);
});

test('global budget 20/rolling 24h survives restarts; resets at the boundary', async () => {
  const w = world();
  for (let n = 0; n < 20; n += 1) await assert.rejects(w.ensure(`a${n}@example.com`, `device${n}`), /destination_unverified/);
  await assert.rejects(w.ensure('over@example.com', 'other'), /destination_rate_limited/);
  w.time.now += DAY;
  await assert.rejects(w.ensure('over@example.com', 'other'), /destination_unverified/);
  assert.equal(w.creates.length, 21);
});

test('per-device budget 3/24h enforced independently of the global budget', async () => {
  const w = world();
  // Historic claims can outlive an attempt/address replacement (including imported operator state).
  for (let n = 0; n < 3; n += 1) w.db.sql.prepare('INSERT INTO mail_destination_creates VALUES(?,?,?,1)')
    .run(`old${n}@example.com`, 'device1', NOW - 1);
  await assert.rejects(w.ensure(), /destination_rate_limited/);
  await assert.rejects(w.ensure('other@example.com', 'device2'), /destination_unverified/);
  w.time.now += DAY; await assert.rejects(w.ensure(), /destination_unverified/);
});

test('parallel claims serialize before list/create and cannot cross capacity', async () => {
  const w = world(); w.rows.push(...Array.from({ length: 189 }, (_, n) => ({ address: `a${n}@example.com`, verified: true })));
  const results = await Promise.allSettled([w.ensure(), w.ensure('other@example.com', 'device2')]);
  assert.equal(w.creates.length, 1);
  assert.ok(results.every((r) => r.status === 'rejected'));
  await assert.rejects(w.ensure('other@example.com', 'device2'), /destination_capacity/);
});

test('crash after claim/ambiguous create never resends, and reserves unobserved capacity', async () => {
  const w = world();
  w.client.create = async () => { throw new Error('destination_unavailable'); };
  await assert.rejects(w.ensure(), /destination_unavailable/);
  let retried = 0; w.client.create = async () => { retried += 1; };
  await assert.rejects(w.ensure(), /destination_unavailable/); assert.equal(retried, 0);
  w.rows.push(...Array.from({ length: 189 }, (_, n) => ({ address: `a${n}@example.com`, verified: true })));
  await assert.rejects(w.ensure('other@example.com', 'device2'), /destination_capacity/);
  w.rows.push({ address: 'owner@example.com', verified: true });
  assert.equal((await w.ensure()).verified, true);
});

test('stale lock recovers; a suspended invocation loses admission before its create', async () => {
  const w = world();
  w.db.sql.prepare('INSERT INTO mail_destination_lock VALUES(1,?,?)').run('dead', NOW + 30000);
  await assert.rejects(w.ensure(), /destination_unavailable/);
  w.time.now += 30000;
  w.client.list = async () => { w.time.now += 30000; return []; };
  await assert.rejects(w.ensure(), /destination_unavailable/);
  assert.equal(w.creates.length, 0);
  w.client.list = async () => []; await assert.rejects(w.ensure(), /destination_unverified/);
});

test('database clock fences an expired claim even when invocation time is stale', async () => {
  const w = world();
  w.client.list = async () => { w.time.now += 30000; return []; };
  const p = destinationProvisioning({ db: w.db, client: w.client, clock: () => NOW });
  await assert.rejects(p.ensure({ address: 'owner@example.com', device_id: 'device1' }), /destination_unavailable/);
  assert.equal(w.creates.length, 0);
});
