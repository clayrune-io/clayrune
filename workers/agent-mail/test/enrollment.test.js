import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fixture, EnrollmentDB, NOW } from './enrollment-support.js';
import { Enrollment } from '../enrollment.js';
import { verifyGrant } from '../enrollment-grant.js';
import { canonicalR } from '../enrollment-transcript.js';

test('disabled default makes no DB or provider call', async () => {
  const e = new Enrollment({});
  await assert.rejects(e.begin({}, ''), /enrollment_disabled/);
  await assert.rejects(e.confirm('', '', ''), /enrollment_disabled/);
  await assert.rejects(e.complete({}), /enrollment_disabled/);
});
test('32-byte challenge, keyed code digest, no plaintext persisted, resumable identity', async () => {
  const f = await fixture();
  assert.match(f.m.challenge_nonce, /^[a-f0-9]{64}$/);
  assert.match(f.started.code, /^[a-f0-9]{32}$/);
  assert.equal(Number(f.m.expires_ms), NOW + 900_000);
  const row = await f.enrollment.flow('flow1', f.secret);
  assert.ok(!JSON.stringify(row).includes(f.started.code));
  assert.notEqual(row.code_digest, f.started.code);
  assert.deepEqual(await f.enrollment.begin(f.value, f.secret), { transcript: f.r, state: 'pending' });
  await assert.rejects(f.enrollment.begin(f.value, 'b'.repeat(64)), /human_flow_required/);
  await assert.rejects(f.enrollment.begin({ ...f.value, owner_address: 'other@example.com' }, f.secret), /flow_conflict/);
});
test('mailbox code is bound to challenge and cannot confirm another flow', async () => {
  const f = await fixture();
  const second = await f.enrollment.begin({ ...f.value, flow_id: 'flow2', device_id: 'device2' }, f.secret);
  assert.equal(await f.enrollment.confirm('flow2', f.secret, f.started.code), false);
  assert.equal(await f.enrollment.confirm('flow2', f.secret, second.code), true);
});
test('five failures invalidate atomically and erase digest; correct late code fails', async () => {
  const f = await fixture();
  for (let n = 0; n < 5; n++) assert.equal(await f.enrollment.confirm('flow1', f.secret, '0'.repeat(32)), false);
  const row = await f.enrollment.flow('flow1', f.secret);
  assert.equal(row.code_failures, 5); assert.equal(row.state, 'invalidated'); assert.equal(row.code_digest, '');
  await assert.rejects(f.confirm(), /challenge_unavailable/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM registrations').get().n, 0);
});
test('mailbox scanner/provider readiness alone cannot consume challenge', async () => {
  const f = await fixture();
  await assert.rejects(f.complete(), /mailbox_confirmation_required/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 0);
});
test('valid grant records ownership only, consumes once, exact retry stable after expiry', async () => {
  const f = await fixture(); assert.equal(await f.confirm(), true);
  const token = await f.token(); const result = await f.complete({ token });
  assert.deepEqual(result, { install_id: f.m.install_id, generation: 1,
    state: 'ownership_recorded_delivery_pending', active: false, route_verified: false });
  const reg = f.db.sql.prepare('SELECT * FROM registrations').get();
  assert.equal(reg.active, 0); assert.equal(reg.route_verified, 0); assert.equal(reg.owner_verified, 1);
  const i = f.db.sql.prepare('SELECT * FROM mail_installations').get();
  assert.equal(i.ownership_lease_until, NOW + 300_000); assert.equal(i.generation, 1);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 0);
  assert.equal((await f.enrollment.flow('flow1', f.secret)).state, 'consumed');
  f.time.now += 900_001;
  assert.deepEqual(await f.complete({ token }), result);
  await assert.rejects(f.complete(), /grant_conflict/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 1);
});
test('concurrent identical completion converges on one generation', async () => {
  const f = await fixture(); await f.confirm(); const token = await f.token();
  const results = await Promise.all([f.complete({ token }), f.complete({ token })]);
  assert.deepEqual(results[0], results[1]);
  assert.equal(f.db.sql.prepare('SELECT generation FROM mail_installations').get().generation, 1);
});
for (const [name, patch] of Object.entries({
  audience: { aud: 'clayrune-dashboard' }, purpose: { token_use: 'dashboard' }, issuer: { iss: 'https://evil.example.com' },
  expired: { exp: Math.floor(NOW / 1000) }, future: { nbf: Math.floor(NOW / 1000) + 1 },
  lifetime: { exp: Math.floor(NOW / 1000) + 61 }, check_time: { checked_at_ms: NOW + 1 },
  old_check: { checked_at_ms: NOW - 300_000 }, digest: { r_sha256: 'f'.repeat(64) },
  device: { device_id: 'other' }, tunnel: { tunnel_uuid: 'other' }, origin: { route_origin: 'https://evil.example.com' },
  install: { install_id: 'other' }, generation: { expected_generation: '1' }, version: { record_version: 'unknown' },
})) test(`grant rejects ${name}`, async () => {
  const f = await fixture(); await f.confirm(); await assert.rejects(f.complete({ token: await f.token(patch) }));
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 0);
});
for (const [name, header] of Object.entries({ alg: { alg: 'HS256' }, type: { typ: 'JWT' },
  jku: { jku: 'https://evil.example.com/jwks' }, crit: { crit: [] }, kid: { kid: 'dashboard-key' }, jwk: { jwk: {} } })) {
  test(`grant header refuses ${name} without fetching`, async () => {
    const f = await fixture(); await assert.rejects(verifyGrant(await f.token({}, header), f.r, f.trust, () => NOW));
  });
}
test('trust URL cannot follow client issuer or foreign JWKS config', async () => {
  const f = await fixture(); const token = await f.token();
  await assert.rejects(verifyGrant(token, f.r, { ...f.trust, jwksUrl: 'https://evil.example.com/jwks' }, () => NOW));
  await assert.rejects(verifyGrant(token, f.r, { ...f.trust, jwks: { keys: [] } }, () => NOW));
});
test('altered signature, dual proof and transcript fail before consumption', async () => {
  const f = await fixture(); await f.confirm();
  await assert.rejects(f.complete({ mailSignature: await f.proof(f.device.privateKey) }), /key_proof_required/);
  const token = await f.token();
  await assert.rejects(f.complete({ token: token.slice(0, -4) + 'AAAA' }));
  const r = [...f.r]; r[11] = 'other';
  await assert.rejects(verifyGrant(token, r, f.trust, () => NOW));
});
for (const provider of [async (address) => ({ address, verified: false }),
  async () => ({ address: 'other@example.com', verified: true }), async () => { throw new Error('offline'); }]) {
  test('provider unavailable/unverified/wrong exact address stays pending', async () => {
    const f = await fixture({ provider }); await f.confirm(); await assert.rejects(f.complete());
    assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM registrations').get().n, 0);
    assert.equal((await f.enrollment.flow('flow1', f.secret)).state, 'pending');
  });
}
test('provider latency cannot extend grant or ownership lease', async () => {
  const f = await fixture(); await f.confirm();
  f.enrollment.provider = async (address) => { f.time.now += 60_000; return { address, verified: true }; };
  await assert.rejects(f.complete(), /bad_grant_claims/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
});
test('SQL admission time rejects expiry after final JS verification', async () => {
  const f = await fixture(); await f.confirm();
  const prepare = f.db.prepare.bind(f.db);
  f.db.prepare = (sql) => {
    const statement = prepare(sql);
    if (sql.startsWith('INSERT INTO mail_enrollment_commits')) {
      const run = statement.run.bind(statement);
      statement.run = async () => { f.time.now += 60_000; return run(); };
    }
    return statement;
  };
  await assert.rejects(f.complete(), /enrollment_cas_refused/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
});
test('lease is anchored to authority check, not delayed grant receipt', async () => {
  const f = await fixture(); await f.confirm(); const token = await f.token(); f.time.now += 45_000;
  await f.complete({ token });
  assert.equal(f.db.sql.prepare('SELECT ownership_lease_until t FROM mail_installations').get().t, NOW + 300_000);
});
test('active existing generation cannot be silently replaced', async () => {
  const f = await fixture(); await f.confirm(); await f.complete();
  f.db.sql.exec('UPDATE registrations SET active=1,route_verified=1');
  const next = await f.enrollment.begin({ ...f.value, flow_id: 'flow2' }, f.secret);
  assert.equal(await f.enrollment.confirm('flow2', f.secret, next.code), true);
  const token = await f.token({ jti: 'grant2' }, {}, next.transcript);
  await assert.rejects(f.enrollment.complete({ flowId: 'flow2', secret: f.secret, token,
    deviceSignature: await f.proof(f.device.privateKey, next.transcript),
    mailSignature: await f.proof(f.mail.privateKey, next.transcript) }), /enrollment_cas_refused/);
  assert.equal(f.db.sql.prepare('SELECT generation FROM mail_installations').get().generation, 1);
  assert.equal(f.db.sql.prepare('SELECT active FROM registrations').get().active, 1);
});
test('grant jti is single-use across different flows and installs', async () => {
  const f = await fixture(); await f.confirm(); await f.complete();
  const next = await f.enrollment.begin({ ...f.value, flow_id: 'flow2', device_id: 'device2' }, f.secret);
  await f.enrollment.confirm('flow2', f.secret, next.code);
  await assert.rejects(f.enrollment.complete({ flowId: 'flow2', secret: f.secret,
    token: await f.token({}, {}, next.transcript),
    deviceSignature: await f.proof(f.device.privateKey, next.transcript),
    mailSignature: await f.proof(f.mail.privateKey, next.transcript) }), /UNIQUE/);
  assert.equal((await f.enrollment.flow('flow2', f.secret)).state, 'pending');
});
test('generation race and active transport row refuse ownership commit', async () => {
  const f = await fixture(); await f.confirm(); await f.complete();
  const next = await f.enrollment.begin({ ...f.value, flow_id: 'flow2' }, f.secret);
  await f.enrollment.confirm('flow2', f.secret, next.code);
  f.db.sql.exec('UPDATE mail_installations SET generation=2');
  await assert.rejects(f.enrollment.complete({ flowId: 'flow2', secret: f.secret,
    token: await f.token({ jti: 'grant2' }, {}, next.transcript),
    deviceSignature: await f.proof(f.device.privateKey, next.transcript),
    mailSignature: await f.proof(f.mail.privateKey, next.transcript) }), /enrollment_cas_refused/);
  assert.equal((await f.enrollment.flow('flow2', f.secret)).state, 'pending');
});
test('failure in registration write rolls back grant, generation, and challenge', async () => {
  const f = await fixture(); await f.confirm();
  f.db.sql.exec("CREATE TRIGGER fail_registration BEFORE INSERT ON registrations BEGIN SELECT RAISE(ABORT,'fault'); END");
  await assert.rejects(f.complete(), /fault/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_commits').get().n, 0);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 1);
  assert.equal((await f.enrollment.flow('flow1', f.secret)).state, 'pending');
  f.db.sql.exec('DROP TRIGGER fail_registration');
  assert.equal((await f.complete()).generation, 1);
});
test('restart retains pending challenge and successful consumption', async () => {
  const folder = mkdtempSync(join(tmpdir(), 'mail-enrollment-')); const path = join(folder, 'state.sqlite');
  let db = new EnrollmentDB(path);
  try {
    const f = await fixture({ db }); await f.confirm(); db.sql.close();
    db = new EnrollmentDB(path, false); db.clock = () => f.time.now; f.enrollment.db = db;
    const token = await f.token(); const result = await f.complete({ token }); db.sql.close();
    db = new EnrollmentDB(path, false); db.clock = () => f.time.now; f.enrollment.db = db;
    assert.deepEqual(await f.complete({ token }), result);
  } finally { db.sql.close(); rmSync(folder, { recursive: true }); }
});
test('expired unproved flow releases its provisional identity and cannot be revived', async () => {
  const f = await fixture(); f.time.now += 900_000;
  const again = await f.enrollment.begin({ ...f.value, flow_id: 'flow2' }, f.secret);
  assert.notEqual(again.transcript[7], f.m.install_id); assert.notEqual(again.transcript[5], f.m.challenge_nonce);
  assert.equal((await f.enrollment.flow('flow1', f.secret)).state, 'invalidated');
  await assert.rejects(f.confirm(), /challenge_unavailable/);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_installations').get().n, 0);
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_identity_reservations').get().n, 1);
});
test('bounded reservations and strict transcript/owner/service pin before state mutation', async () => {
  const f = await fixture({ capacity: 1 });
  await assert.rejects(f.enrollment.begin({ ...f.value, flow_id: 'flow2', device_id: 'device2' }, f.secret), /capacity/);
  for (const patch of [{ owner_address: 'Other <owner@example.com>' }, { service_key_id: 'unknown' },
    { device_public_key: 'bad' }, { route_origin: 'https://evil.example.com/path' }, { callback: 'evil' }]) {
    await assert.rejects(f.enrollment.begin({ ...f.value, ...patch, flow_id: 'invalid' }, f.secret));
  }
  for (const index of [0, 1, 2, 4, 5, 6, 8, 10, 14, 16, 17]) {
    const r = [...f.r]; r[index] = '\u2603'; assert.throws(() => canonicalR(r));
  }
  assert.equal(f.db.sql.prepare('SELECT COUNT(*) n FROM mail_enrollment_flows').get().n, 1);
});
