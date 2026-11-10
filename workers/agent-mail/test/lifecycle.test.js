// Unit 7d-2d: Worker lifecycle routes (contract section 5). Real SQLite behind the D1 facade, injected CP
// stub, fixed clock. The fixture drives the REAL enrollment core, so the fence/commit interplay is the
// production SQL, not a model of it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EnrollmentDB, fixture } from './enrollment-support.js';
import { keys } from './transport-support.js';
import { base64url, bytes, sha256, sign, verify, randomId } from '../signing.js';
import { createWorker } from '../worker.js';
import { Ledger } from '../ledger.js';
import { drainCpRevokes, LIFECYCLE_BODY_LIMIT } from '../lifecycle.js';

const TOKEN = 'ab'.repeat(32);
const OTHER = 'cd'.repeat(32);
const RECEIPT = '7e'.repeat(32);
const CHALLENGE = '3c'.repeat(32);
const BASE = 'https://service.example.net';

const cpStub = (over = {}) => ({ leases: [], revokes: [],
  async lease(v) { this.leases.push(v); return this.next ? this.next(v) : { ok: false, code: 'cp_unavailable' }; },
  async revoke(v) { this.revokes.push(v); return this.revokeResult ?? { ok: true, body: {} }; }, ...over });

async function world({ provider, fence = TOKEN, cp = cpStub(), commit = true } = {}) {
  const db = new EnrollmentDB();
  db.sql.exec(readFileSync(new URL('../migrations/0002_delivery_proof.sql', import.meta.url), 'utf8'));
  const hooks = { provider: async () => {} };
  const f = await fixture({ db, ...(fence ? { fenceDigest: await sha256(bytes(fence)) } : {}),
    provider: async (address) => { await hooks.provider(); return provider ? provider(address) : { address, verified: true }; } });
  const service = await keys();
  const env = { MAIL_ENABLED: 'true', MAIL_LIFECYCLE_ENABLED: 'true', MAIL_DB: db, MAIL_DOMAIN: 'mail.example.net',
    MAIL_FROM: 'agent@mail.example.net', MAIL_SERVICE_KEY_ID: 'sk1', MAIL_SERVICE_PRIVATE_KEY: service.privateKey };
  const worker = createWorker({ clock: () => f.time.now, cp, send: async () => {}, fetcher: async () => Response.json({}) });
  const w = { f, db, env, worker, cp, service, hooks, time: f.time, ledger: new Ledger(db),
    mailPriv: base64url(await crypto.subtle.exportKey('pkcs8', f.mail.privateKey)), install: null };
  if (commit) { await f.confirm(); w.install = (await f.complete()).install_id; }
  return w;
}
const path = (name) => `/v1/mail/lifecycle/${name}`;
const j = (v) => bytes(JSON.stringify(v));
async function fenceCall(w, name, value, { id = w.install ?? 'install1', generation = 1, nonce = randomId() } = {}) {
  const req = new Request(BASE + path(name), { method: 'POST', body: j(value), headers: { 'content-type': 'application/json',
    'x-mail-install-id': id, 'x-mail-generation': String(generation), 'x-mail-nonce': nonce } });
  return { res: await w.worker.fetch(req, w.env), nonce, path: path(name), id, generation };
}
async function installCall(w, name, value, over = {}) {
  const body = typeof value === 'string' ? bytes(value) : j(value);
  const headers = await sign({ privateKey: w.mailPriv, key_id: 'mail1', install_id: w.install, generation: 1,
    direction: 'install-to-service-lifecycle', path: path(name), body, now: w.time.now, ...over });
  const res = await w.worker.fetch(new Request(BASE + path(name), { method: 'POST', body, headers }), w.env);
  return { res, nonce: headers.get('x-mail-nonce'), path: path(name), id: w.install, generation: 1 };
}
async function signedOk(w, r) {
  const body = new Uint8Array(await r.res.clone().arrayBuffer());
  const request = new Request(BASE + r.path, { method: 'POST', headers: r.res.headers });
  return r.res.headers.get('x-mail-nonce') === r.nonce && verify({ request, body, publicKey: w.service.publicKey,
    direction: 'service-to-install-response', install_id: r.id, generation: r.generation, key_id: 'sk1',
    claimNonce: async () => true, now: w.time.now });
}
const row = (w, sql, ...a) => w.db.sql.prepare(sql).get(...a);
const reg = (w) => row(w, 'SELECT * FROM registrations WHERE install_id=?', w.install);
async function ok(w, r, status = 200) {
  assert.equal(r.res.status, status);
  if (status === 200) assert.ok(await signedOk(w, r), 'response must verify under the service key');
  return r.res.clone().json();
}
async function armVerified(w, { digest = RECEIPT, expires = w.time.now + 100_000 } = {}) {
  await ok(w, await installCall(w, 'proof-arm', { install_id: w.install, generation: 1, challenge: CHALLENGE,
    expires_ms: w.time.now + 60_000 }));
  w.db.sql.prepare(`UPDATE mail_delivery_proofs SET state='verified',receipt_digest=?,verified_ms=?,expires_ms=?,challenge=NULL
    WHERE install_id=?`).run(digest, w.time.now, expires, w.install);
}

test('enrollment begin stores the optional fence digest and rejects a malformed one', async () => {
  const w = await world({ commit: false });
  assert.equal(row(w, 'SELECT fence_digest FROM mail_enrollment_flows').fence_digest, await sha256(bytes(TOKEN)));
  const noFence = await world({ commit: false, fence: null });
  assert.equal(row(noFence, 'SELECT fence_digest FROM mail_enrollment_flows').fence_digest, '');
  const bad = new (w.f.enrollment.constructor)({ ...w.f.config });
  await assert.rejects(bad.begin({ ...w.f.value, flow_id: 'flowZ', device_id: 'deviceZ', fence_digest: 'xyz' }, w.f.secret), /bad_enrollment/);
  // A retried begin must carry the same digest: a different one is a conflicting flow, not a re-issue.
  await assert.rejects(w.f.enrollment.begin({ ...w.f.value, fence_digest: await sha256(bytes(OTHER)) }, w.f.secret), /flow_conflict/);
});

test('cancel before begin tombstones the flow; a later begin refuses; replay is identical', async () => {
  const db = new EnrollmentDB();
  db.sql.exec(readFileSync(new URL('../migrations/0002_delivery_proof.sql', import.meta.url), 'utf8'));
  const service = await keys(); const time = { now: 1_794_249_600_000 };
  const w = { db, time, service, env: { MAIL_ENABLED: 'true', MAIL_LIFECYCLE_ENABLED: 'true', MAIL_DB: db,
    MAIL_SERVICE_KEY_ID: 'sk1', MAIL_SERVICE_PRIVATE_KEY: service.privateKey },
  worker: createWorker({ clock: () => time.now, cp: cpStub() }) };
  const first = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN }, { id: 'install1' });
  assert.deepEqual(await ok(w, first), { flow_id: 'flow1', state: 'cancelled', assignment: null });
  const again = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN }, { id: 'install1' });
  assert.deepEqual(await ok(w, again), { flow_id: 'flow1', state: 'cancelled', assignment: null });
  // Somebody else's token on the tombstone is the same refusal as any bad token.
  assert.equal((await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: OTHER }, { id: 'install1' })).res.status, 401);
  assert.equal(db.sql.prepare('SELECT COUNT(*) AS n FROM mail_lifecycle_fences').get().n, 1);
  // The enrollment core refuses to begin a fenced flow (fixture begins flow1 with the same db).
  await assert.rejects(fixture({ db, fenceDigest: await sha256(bytes(TOKEN)) }), /flow_fenced/);
  assert.equal(db.sql.prepare('SELECT COUNT(*) AS n FROM mail_enrollment_flows').get().n, 0);
});

test('the fence-create bucket bounds tombstones for flows that never existed', async () => {
  const w = await world({ commit: false });
  w.db.sql.prepare('INSERT INTO rate_limits VALUES(?,?,?)').run(`lifecycle:fence-create:${Math.floor(w.time.now / 86_400_000)}`, 1000, w.time.now + 2 * 86_400_000);
  const r = await fenceCall(w, 'cancel', { flow_id: 'neverseen', fence_token: TOKEN }, { id: 'install1' });
  assert.equal(r.res.status, 429);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_lifecycle_fences WHERE flow_id=?', 'neverseen').n, 0);
});

test('cancel of a pending flow fences it and invalidates it; confirm and complete refuse', async () => {
  const w = await world({ commit: false });
  const r = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN }, { id: 'install1' });
  assert.deepEqual(await ok(w, r), { flow_id: 'flow1', state: 'cancelled', assignment: null });
  assert.equal(row(w, 'SELECT state FROM mail_enrollment_flows').state, 'invalidated');
  await assert.rejects(w.f.confirm(), /flow_fenced/);
  await assert.rejects(w.f.complete(), /flow_fenced/);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_enrollment_commits').n, 0);
});

test('a wrong fence token, an unknown flow digest and a flow without a digest are all refused', async () => {
  const w = await world({ commit: false });
  assert.equal((await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: OTHER }, { id: 'install1' })).res.status, 401);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_lifecycle_fences').n, 0);
  const none = await world({ commit: false, fence: null });
  assert.equal((await fenceCall(none, 'cancel', { flow_id: 'flow1', fence_token: TOKEN }, { id: 'install1' })).res.status, 401);
  assert.equal((await fenceCall(none, 'cancel', { flow_id: 'flow1', fence_token: 'nothex' }, { id: 'install1' })).res.status, 400);
  assert.equal((await fenceCall(none, 'cancel', { flow_id: 'flow1' }, { id: 'install1' })).res.status, 400);
});

test('commit-vs-cancel race: a cancel landing after every JS check still wins inside the commit statement', async () => {
  const w = await world({ commit: false });
  await w.f.confirm();
  // The provider runs after the JS-side checks and before the single INSERT that commits.
  w.hooks.provider = async () => {
    w.install = row(w, 'SELECT install_id FROM mail_enrollment_flows').install_id;
    assert.equal((await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN })).res.status, 200);
  };
  await assert.rejects(w.f.complete(), /enrollment_cas_refused/);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_enrollment_commits').n, 0);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM registrations').n, 0, 'no registration may exist for a fenced flow');
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_installations').n, 0);
});

test('cancel after commit reports the assignment (expected_generation + 1) and does not touch the registration', async () => {
  const w = await world();
  const r = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN });
  assert.deepEqual(await ok(w, r), { flow_id: 'flow1', state: 'cancelled', assignment: { install_id: w.install, generation: 1 } });
  assert.equal(reg(w).generation, 1);
  assert.equal(reg(w).owner_verified, 1);
});

test('revoke deactivates exactly the named generation, fences, queues the CP revoke; cancel stays identical after it', async () => {
  const w = await world();
  w.db.sql.prepare('UPDATE registrations SET active=1,route_verified=1,route_generation=1 WHERE install_id=?').run(w.install);
  await armVerified(w);
  const r = await fenceCall(w, 'revoke', { install_id: w.install, generation: 1, fence_token: TOKEN });
  assert.deepEqual(await ok(w, r), { install_id: w.install, generation: 1, state: 'revoked' });
  assert.equal(reg(w).active, 0); assert.equal(reg(w).route_verified, 0);
  assert.equal(row(w, 'SELECT state FROM mail_delivery_proofs').state, 'invalidated');
  assert.deepEqual({ ...row(w, 'SELECT done FROM mail_lifecycle_cp_revokes') }, { done: 0 });
  const again = await fenceCall(w, 'revoke', { install_id: w.install, generation: 1, fence_token: TOKEN });
  assert.deepEqual(await ok(w, again), { install_id: w.install, generation: 1, state: 'revoked' });
  const cancelled = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN });
  assert.deepEqual(await ok(w, cancelled), { flow_id: 'flow1', state: 'cancelled', assignment: { install_id: w.install, generation: 1 } });
  const cancelledAgain = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN });
  assert.deepEqual(await ok(w, cancelledAgain), { flow_id: 'flow1', state: 'cancelled', assignment: { install_id: w.install, generation: 1 } });
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_lifecycle_cp_revokes').n, 1);
  // A revoke for a registration that is already revoked never reactivates anything.
  assert.equal(reg(w).active, 0);
});

test('revoke never touches a newer generation', async () => {
  const w = await world();
  w.db.sql.prepare('UPDATE registrations SET generation=2,active=1,route_verified=1,route_generation=2 WHERE install_id=?').run(w.install);
  w.db.sql.prepare('UPDATE mail_installations SET generation=2 WHERE install_id=?').run(w.install);
  const r = await fenceCall(w, 'revoke', { install_id: w.install, generation: 1, fence_token: TOKEN }, { generation: 1 });
  assert.deepEqual(await ok(w, r), { install_id: w.install, generation: 1, state: 'already_superseded' });
  assert.deepEqual({ ...reg(w) }.generation, 2); assert.equal(reg(w).active, 1); assert.equal(reg(w).route_verified, 1);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_lifecycle_cp_revokes').n, 0);
  // Naming generation 2 with a token minted for the flow that produced generation 1 matches no flow.
  const wrong = await fenceCall(w, 'revoke', { install_id: w.install, generation: 2, fence_token: TOKEN }, { generation: 2 });
  assert.equal(wrong.res.status, 401);
  assert.equal(reg(w).active, 1);
});

test('revoke below the committed generation fences the flow so a later commit refuses', async () => {
  const w = await world({ commit: false });
  await w.f.confirm();
  const install = row(w, 'SELECT install_id FROM mail_enrollment_flows').install_id;
  const r = await fenceCall(w, 'revoke', { install_id: install, generation: 1, fence_token: TOKEN }, { id: install });
  assert.deepEqual(await ok(w, r), { install_id: install, generation: 1, state: 'fenced' });
  assert.equal(row(w, 'SELECT install_id,generation FROM mail_lifecycle_fences').install_id, null);
  await assert.rejects(w.f.complete(), /flow_fenced/);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM registrations').n, 0);
});

test('revoke scope: body must equal the headers, token must match a flow of exactly that generation', async () => {
  const w = await world();
  const bad = await fenceCall(w, 'revoke', { install_id: w.install, generation: 2, fence_token: TOKEN }, { generation: 1 });
  assert.equal(bad.res.status, 400);
  const other = await fenceCall(w, 'revoke', { install_id: w.install, generation: 1, fence_token: OTHER });
  assert.equal(other.res.status, 401);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_lifecycle_fences').n, 0);
});

test('drainCpRevokes calls the CP once per queued revoke, keeps failures pending, never throws', async () => {
  const cp = cpStub(); const w = await world({ cp });
  w.db.sql.prepare('UPDATE registrations SET active=1,route_verified=1,route_generation=1 WHERE install_id=?').run(w.install);
  await ok(w, await fenceCall(w, 'revoke', { install_id: w.install, generation: 1, fence_token: TOKEN }));
  cp.revokeResult = { ok: false, code: 'cp_unavailable' };
  assert.deepEqual(await drainCpRevokes({ ledger: w.ledger, cp }), { drained: 0, pending: 1 });
  const status = await ok(w, await installCall(w, 'status', { install_id: w.install, generation: 1 }));
  assert.equal(status.cp_revoke_pending, true);
  cp.revokeResult = { ok: true, body: {} };
  assert.deepEqual(await drainCpRevokes({ ledger: w.ledger, cp }), { drained: 1, pending: 0 });
  assert.deepEqual(await drainCpRevokes({ ledger: w.ledger, cp }), { drained: 0, pending: 0 });
  assert.deepEqual(cp.revokes, [{ install_id: w.install, generation: 1 }, { install_id: w.install, generation: 1 }]);
  assert.deepEqual(await drainCpRevokes({ ledger: w.ledger, cp: null }), { drained: 0, pending: 0 });
});

test('status is a signed read-only snapshot and works while not yet active', async () => {
  const w = await world();
  const before = await w.db.sql.prepare('SELECT * FROM registrations').all();
  const s = await ok(w, await installCall(w, 'status', { install_id: w.install, generation: 1 }));
  assert.deepEqual(s, { install_id: w.install, generation: 1,
    registration: { active: false, owner_verified: true, route_verified: false },
    lease_until_ms: w.time.now + 300_000, proof: { state: 'none' }, fences: [], cp_revoke_pending: false });
  assert.deepEqual(await w.db.sql.prepare('SELECT * FROM registrations').all(), before);
});

test('install-auth refuses unsigned, wrong-direction, replayed, wrong-generation and foreign-key requests', async () => {
  const w = await world();
  const value = { install_id: w.install, generation: 1 };
  const unsigned = await w.worker.fetch(new Request(BASE + path('status'), { method: 'POST', body: j(value),
    headers: { 'x-mail-install-id': w.install, 'x-mail-generation': '1', 'x-mail-nonce': randomId() } }), w.env);
  assert.equal(unsigned.status, 401);
  assert.equal((await installCall(w, 'status', value, { direction: 'install-to-service' })).res.status, 401);
  const foreign = await keys();
  assert.equal((await installCall(w, 'status', value, { privateKey: foreign.privateKey })).res.status, 401);
  const nonce = randomId();
  assert.equal((await installCall(w, 'status', value, { nonce })).res.status, 200);
  assert.equal((await installCall(w, 'status', value, { nonce })).res.status, 401, 'a replayed nonce is refused');
  assert.equal((await installCall(w, 'status', { install_id: w.install, generation: 2 })).res.status, 409);
  assert.equal((await installCall(w, 'status', { install_id: 'other', generation: 1 })).res.status, 409);
  assert.equal((await installCall(w, 'status', { ...value, extra: 1 })).res.status, 400);
  assert.equal((await installCall(w, 'status', '[1]')).res.status, 409);
  assert.equal((await installCall(w, 'status', 'not json')).res.status, 400);
  // owner_verified=0 is not an install.
  w.db.sql.prepare('UPDATE registrations SET owner_verified=0').run();
  assert.equal((await installCall(w, 'status', value)).res.status, 401);
});

test('error responses are never signed; a flag-off Worker answers 503; bounds and path table hold', async () => {
  const w = await world();
  const bad = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: OTHER });
  assert.equal(bad.res.status, 401);
  assert.equal(bad.res.headers.get('x-mail-signature'), null);
  const big = await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN, pad: 'x'.repeat(LIFECYCLE_BODY_LIMIT) });
  assert.equal(big.res.status, 413);
  const off = await w.worker.fetch(new Request(BASE + path('status'), { method: 'POST', body: j({}) }), { ...w.env, MAIL_LIFECYCLE_ENABLED: 'false' });
  assert.equal(off.status, 503); assert.equal((await off.json()).code, 'lifecycle_disabled');
  const mailOff = await w.worker.fetch(new Request(BASE + path('status'), { method: 'POST', body: j({}) }), { ...w.env, MAIL_ENABLED: 'false' });
  assert.equal(mailOff.status, 503);
  for (const [method, p] of [['GET', path('status')], ['POST', `${path('status')}?x=1`], ['POST', '/v1/mail/lifecycle/other'],
    ['POST', '/v1/mail/lifecycle/status/'], ['POST', '/v1/mail/lifecycle']]) {
    const r = await w.worker.fetch(new Request(BASE + p, { method, ...(method === 'POST' ? { body: j({}) } : {}) }), w.env);
    assert.equal(r.status, 404, `${method} ${p}`);
  }
});

test('the response signature binds body, path, nonce and key; any change fails verification', async () => {
  const w = await world();
  const r = await installCall(w, 'status', { install_id: w.install, generation: 1 });
  assert.ok(await signedOk(w, r));
  const body = new Uint8Array(await r.res.clone().arrayBuffer());
  const forged = bytes(new TextDecoder().decode(body).replace('"active":false', '"active":true'));
  const v = (over) => verify({ request: new Request(BASE + (over.path ?? r.path), { method: 'POST', headers: r.res.headers }),
    body: over.body ?? body, publicKey: over.publicKey ?? w.service.publicKey, direction: 'service-to-install-response',
    install_id: w.install, generation: 1, key_id: 'sk1', claimNonce: async () => true, now: w.time.now });
  assert.equal(await v({ body: forged }), false);
  assert.equal(await v({ path: path('renew') }), false);
  assert.equal(await v({ publicKey: (await keys()).publicKey }), false);
  assert.equal(await v({}), true);
  // The same signature is not valid as the request direction or the proof direction.
  for (const direction of ['install-to-service-lifecycle', 'service-to-install-proof', 'service-to-install']) {
    assert.equal(await verify({ request: new Request(BASE + r.path, { method: 'POST', headers: r.res.headers }), body,
      publicKey: w.service.publicKey, direction, install_id: w.install, generation: 1, key_id: 'sk1',
      claimNonce: async () => true, now: w.time.now }), false);
  }
});

test('proof-arm stores one challenge per generation, replaces it, and is idempotent for a retry', async () => {
  const w = await world();
  const value = { install_id: w.install, generation: 1, challenge: CHALLENGE, expires_ms: w.time.now + 60_000 };
  const a = await ok(w, await installCall(w, 'proof-arm', value));
  assert.deepEqual(a, { install_id: w.install, generation: 1, state: 'armed', expires_ms: value.expires_ms });
  assert.deepEqual(await ok(w, await installCall(w, 'proof-arm', value)), a);
  const second = { ...value, challenge: '9d'.repeat(32) };
  await ok(w, await installCall(w, 'proof-arm', second));
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM mail_delivery_proofs').n, 1);
  assert.equal(row(w, 'SELECT challenge FROM mail_delivery_proofs').challenge, second.challenge);
  assert.equal((await installCall(w, 'proof-arm', { ...value, challenge: 'short' })).res.status, 400);
  assert.equal((await installCall(w, 'proof-arm', { ...value, expires_ms: w.time.now + 10_000_000 })).res.status, 400);
  // The route never activates anything.
  assert.equal(reg(w).active, 0);
});

test('activate needs the verified proof of exactly this generation and digest; a lost ack replays identically', async () => {
  const w = await world();
  const value = { install_id: w.install, generation: 1, receipt_digest: RECEIPT };
  assert.equal((await installCall(w, 'activate', value)).res.status, 409, 'no proof at all');
  await armVerified(w);
  assert.equal((await installCall(w, 'activate', { ...value, receipt_digest: 'f1'.repeat(32) })).res.status, 409);
  assert.equal(reg(w).active, 0);
  const a = await ok(w, await installCall(w, 'activate', value));
  assert.deepEqual(a, { install_id: w.install, generation: 1, state: 'activated', receipt_digest: RECEIPT });
  assert.equal(reg(w).active, 1); assert.equal(reg(w).route_verified, 1); assert.equal(reg(w).route_generation, 1);
  assert.equal(row(w, 'SELECT state FROM mail_delivery_proofs').state, 'consumed');
  // Lost ack: the retry returns the same signed answer and applies nothing twice.
  assert.deepEqual(await ok(w, await installCall(w, 'activate', value)), a);
  assert.equal((await installCall(w, 'proof-arm', { install_id: w.install, generation: 1, challenge: CHALLENGE,
    expires_ms: w.time.now + 1000 })).res.status, 409, 'a consumed proof is final');
});

test('activate refuses an expired proof and a fenced generation, and never revives a revoked one', async () => {
  const w = await world();
  const value = { install_id: w.install, generation: 1, receipt_digest: RECEIPT };
  await armVerified(w, { expires: w.time.now + 10 });
  w.time.now += 1000;
  assert.equal((await installCall(w, 'activate', value)).res.status, 409);
  w.time.now -= 1000;
  await ok(w, await installCall(w, 'activate', value));
  await ok(w, await fenceCall(w, 'revoke', { install_id: w.install, generation: 1, fence_token: TOKEN }));
  assert.equal(reg(w).active, 0);
  assert.equal((await installCall(w, 'activate', value)).res.status, 409, 'fenced: activation after a revoke is refused');
  assert.equal(reg(w).active, 0);
  assert.equal((await installCall(w, 'renew', { install_id: w.install, generation: 1 })).res.status, 409);
  assert.equal((await installCall(w, 'proof-arm', { install_id: w.install, generation: 1, challenge: CHALLENGE,
    expires_ms: w.time.now + 1000 })).res.status, 409);
});

test('flow cancelled between proof and activate: the fence blocks the Worker side too', async () => {
  const w = await world();
  await armVerified(w);
  await ok(w, await fenceCall(w, 'cancel', { flow_id: 'flow1', fence_token: TOKEN }));
  // cancel after commit fenced (install, 1): the activation is refused even though no revoke ran.
  assert.equal((await installCall(w, 'activate', { install_id: w.install, generation: 1, receipt_digest: RECEIPT })).res.status, 409);
  assert.equal(reg(w).active, 0);
});

test('renew is forward-only, signed, bounded by the CP answer, and refuses nonsense', async () => {
  const cp = cpStub(); const w = await world({ cp });
  const value = { install_id: w.install, generation: 1 };
  const RV = 'e9'.repeat(32);
  assert.equal((await installCall(w, 'renew', value)).res.status, 503, 'CP unavailable: nothing extended');
  assert.equal(row(w, 'SELECT ownership_lease_until AS l FROM mail_installations').l, w.time.now + 300_000);
  const start = w.time.now;
  w.time.now += 200_000;
  cp.next = async () => ({ ok: true, body: { checked_at_ms: w.time.now, record_version: RV, lease_until_ms: w.time.now + 300_000 } });
  const a = await ok(w, await installCall(w, 'renew', value));
  assert.deepEqual(a, { install_id: w.install, generation: 1, expires_at_ms: w.time.now + 300_000 });
  assert.equal(row(w, 'SELECT record_version AS v FROM mail_installations').v, RV);
  // Lost ack: a repeat of the same CP answer changes nothing and answers the same.
  assert.deepEqual(await ok(w, await installCall(w, 'renew', value)), a);
  // A stale CP answer can never lower the lease.
  cp.next = async () => ({ ok: true, body: { checked_at_ms: start + 1000, record_version: 'a1'.repeat(32), lease_until_ms: start + 301_000 } });
  assert.deepEqual(await ok(w, await installCall(w, 'renew', value)), a);
  assert.equal(row(w, 'SELECT record_version AS v FROM mail_installations').v, RV);
  for (const body of [{ checked_at_ms: w.time.now, record_version: RV, lease_until_ms: w.time.now + 300_001 },
    { checked_at_ms: w.time.now + 1, record_version: RV, lease_until_ms: w.time.now + 300_001 },
    { checked_at_ms: w.time.now, record_version: 'not-hex', lease_until_ms: w.time.now + 300_000 },
    { checked_at_ms: w.time.now, record_version: RV, lease_until_ms: w.time.now + 300_000, extra: 1 }]) {
    cp.next = async () => ({ ok: true, body });
    assert.equal((await installCall(w, 'renew', value)).res.status, 502);
  }
  cp.next = async () => ({ ok: false, code: 'binding_missing' });
  const refused = await installCall(w, 'renew', value);
  assert.equal(refused.res.status, 503); assert.equal((await refused.res.json()).code, 'binding_missing');
  assert.equal(row(w, 'SELECT ownership_lease_until AS l FROM mail_installations').l, w.time.now + 300_000);
});

test('renew refuses when the lease the CP offers has already expired', async () => {
  const cp = cpStub(); const w = await world({ cp });
  w.time.now += 400_000;
  cp.next = async () => ({ ok: true, body: { checked_at_ms: w.time.now - 350_000, record_version: 'b2'.repeat(32), lease_until_ms: w.time.now - 50_000 } });
  const r = await installCall(w, 'renew', { install_id: w.install, generation: 1 });
  assert.equal(r.res.status, 409);
});

test('lifecycle traffic uses its own bucket: refusal never spends the send budget and the cap holds', async () => {
  const w = await world();
  const day = Math.floor(w.time.now / 86_400_000);
  w.db.sql.prepare('INSERT INTO rate_limits VALUES(?,?,?)').run(`lifecycle:${w.install}:${day}`, 500, w.time.now + 2 * 86_400_000);
  const r = await installCall(w, 'status', { install_id: w.install, generation: 1 });
  assert.equal(r.res.status, 429);
  assert.equal(row(w, 'SELECT COUNT(*) AS n FROM rate_limits WHERE bucket LIKE ?', `http:%`).n, 0, 'send/reconcile bucket untouched');
});
