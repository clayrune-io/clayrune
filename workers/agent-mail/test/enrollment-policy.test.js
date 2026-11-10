// Review regressions: current trust policy and provisional/permanent capacity.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture } from './enrollment-support.js';
import { Enrollment } from '../enrollment.js';

async function completion(f, transcript = f.r, flowId = 'flow1', claims = {}) {
  return { flowId, secret: f.secret, token: await f.token(claims, {}, transcript),
    deviceSignature: await f.proof(f.device.privateKey, transcript),
    mailSignature: await f.proof(f.mail.privateKey, transcript) };
}

for (const patch of [{ manifest_sequence: '2' }, { service_key_id: 'replacement' },
  { service_public_key_sha256: 'd'.repeat(64) }, { manifest_sequence: '2',
    service_key_id: 'replacement', service_public_key_sha256: 'd'.repeat(64) }]) {
  test(`fresh instance refuses pending completion after pin replacement: ${Object.keys(patch).join(',')}`, async () => {
    const f = await fixture(); await f.confirm(); const request = await completion(f);
    const e = new Enrollment({ ...f.config, servicePin: { ...f.config.servicePin, ...patch } });
    await assert.rejects(e.begin(f.value, f.secret), /service_pin_mismatch/);
    await assert.rejects(e.complete(request), /service_pin_mismatch/);
    assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 0);
    assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
    assert.equal((await e.flow('flow1', f.secret)).state, 'pending');
  });
}

test('historical exact committed retry survives pin replacement without renewing ownership', async () => {
  const f = await fixture(); await f.confirm(); const request = await completion(f);
  const result = await f.enrollment.complete(request);
  const before = f.db.sql.prepare('SELECT * FROM mail_installations').get();
  f.time.now += 900_001;
  const e = new Enrollment({ ...f.config, servicePin: { manifest_sequence: '2',
    service_key_id: 'replacement', service_public_key_sha256: 'd'.repeat(64) } });
  assert.deepEqual(await e.complete(request), result);
  assert.deepEqual(f.db.sql.prepare('SELECT * FROM mail_installations').get(), before);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 1);
});

test('pin replaced during asynchronous evidence work refuses the new commit', async () => {
  const f = await fixture(); await f.confirm();
  f.enrollment.provider = async (address) => {
    f.enrollment.servicePin = { ...f.config.servicePin, manifest_sequence: '2' };
    return { address, verified: true };
  };
  await assert.rejects(f.complete(), /service_pin_mismatch/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 0);
});

test('capacity two: invented unproved device expires and a legitimate device can enroll', async () => {
  const f = await fixture({ capacity: 2 });
  await f.enrollment.begin({ ...f.value, flow_id: 'invented', device_id: 'invented-device' }, f.secret);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 2);
  f.time.now += 900_001;
  const e = new Enrollment(f.config); // Fresh instance, same persisted state.
  const value = { ...f.value, flow_id: 'legit', device_id: 'legit-device' };
  const next = await e.begin(value, f.secret);
  assert.equal(await e.confirm('legit', f.secret, next.code), true);
  assert.equal((await e.complete(await completion(f, next.transcript, 'legit'))).generation, 1);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 0);
  assert.equal(f.db.sql.prepare('SELECT device_id FROM mail_installations').get().device_id, 'legit-device');
  for (const flowId of ['flow1', 'invented']) {
    const row = await e.flow(flowId, f.secret);
    assert.equal(row.state, 'invalidated'); assert.equal(row.code_digest, '');
  }
});

test('proven identity and address survive expiry and revoked transport, still consuming capacity', async () => {
  const f = await fixture({ capacity: 1 }); await f.confirm(); await f.complete();
  const before = f.db.sql.prepare('SELECT * FROM mail_installations').get();
  // Transport revocation/removal must not make this established address reusable.
  f.db.sql.exec('DELETE FROM registrations');
  f.time.now += 900_001;
  await assert.rejects(f.enrollment.begin({ ...f.value, flow_id: 'stranger', device_id: 'stranger' }, f.secret), /enrollment_capacity/);
  assert.deepEqual(f.db.sql.prepare('SELECT * FROM mail_installations').get(), before);
  const next = await f.enrollment.begin({ ...f.value, flow_id: 'renew' }, f.secret);
  assert.equal(next.transcript[7], before.install_id); assert.equal(next.transcript[8], '1');
  await f.enrollment.confirm('renew', f.secret, next.code);
  await f.enrollment.complete(await completion(f, next.transcript, 'renew', { jti: 'grant2' }));
  const after = f.db.sql.prepare('SELECT * FROM mail_installations').get();
  assert.equal(after.address_local, before.address_local); assert.equal(after.generation, 2);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 0);
});

test('parallel provisional admissions cannot exceed the last capacity slot', async () => {
  const f = await fixture({ capacity: 2 });
  const results = await Promise.allSettled(['second', 'third'].map((id) =>
    f.enrollment.begin({ ...f.value, flow_id: id, device_id: id }, f.secret)));
  assert.equal(results.filter((r) => r.status === 'fulfilled').length, 1);
  assert.match(results.find((r) => r.status === 'rejected').reason.message, /enrollment_capacity/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 2);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
});

test('invalidated unproved reservations expire without promotion or late consumption', async () => {
  const f = await fixture({ capacity: 1 });
  for (let n = 0; n < 5; n++) await f.enrollment.confirm('flow1', f.secret, '0'.repeat(32));
  f.time.now += 900_001;
  const next = await f.enrollment.begin({ ...f.value, flow_id: 'next', device_id: 'next-device' }, f.secret);
  await assert.rejects(f.complete(), /mailbox_confirmation_required/);
  assert.notEqual(next.transcript[7], f.m.install_id);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
});

test('a fresh flow for an invalidated unproved device cannot extend its provisional lifetime', async () => {
  const f = await fixture({ capacity: 1 });
  const before = f.db.sql.prepare('SELECT * FROM mail_identity_reservations').get();
  for (let n = 0; n < 5; n++) await f.enrollment.confirm('flow1', f.secret, '0'.repeat(32));
  f.time.now += 600_000;
  const next = await f.enrollment.begin({ ...f.value, flow_id: 'retry' }, f.secret);
  assert.equal(next.transcript[7], before.install_id);
  assert.equal(Number(next.transcript[6]), before.expires_at);
  assert.deepEqual(f.db.sql.prepare('SELECT * FROM mail_identity_reservations').get(), before);
  f.time.now = before.expires_at;
  await assert.rejects(f.enrollment.confirm('retry', f.secret, next.code), /challenge_unavailable/);
  await f.enrollment.begin({ ...f.value, flow_id: 'other', device_id: 'other' }, f.secret);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 1);
});

test('database admission refuses expired provisional reservation even with an unexpired flow', async () => {
  const f = await fixture(); await f.confirm();
  f.db.sql.prepare('UPDATE mail_identity_reservations SET expires_at=?').run(f.time.now);
  await assert.rejects(f.complete(), /enrollment_cas_refused/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 0);
});
