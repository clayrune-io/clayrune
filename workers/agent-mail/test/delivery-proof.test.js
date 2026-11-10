import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EnrollmentDB } from './enrollment-support.js';
import { keys } from './transport-support.js';
import { Ledger } from '../ledger.js';
import { base64url, bytes, sha256, verify } from '../signing.js';
import { MACHINE_HEADER } from '../edge-policy.js';
import { ROUTE_PATH } from '../delivery-capability.js';
import { armProof, invalidateProofs, proofStatus, sendProof, proofBody, receiptArray, receiptBytes,
  PROOF_DIRECTION, PROOF_TTL_MS } from '../delivery-proof.js';

const V = JSON.parse(readFileSync(new URL('./fixtures/proof-vectors.json', import.meta.url), 'utf8'));
const NOW = 1_794_249_600_000;
const CHALLENGE = 'ab'.repeat(32);
const ROUTE = 'https://device.example.test';
const PIN = { manifest_sequence: 1, service_key_id: 'service1', service_public_key_sha256: 'c3'.repeat(32) };
const OWNER = 'owner@example.com';

async function setup({ lease = NOW + 300_000, active = 0, owner_verified = 1 } = {}) {
  const db = new EnrollmentDB();
  db.sql.exec(readFileSync(new URL('../migrations/0002_delivery_proof.sql', import.meta.url), 'utf8'));
  const ledger = new Ledger(db);
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const mailPublic = base64url(await crypto.subtle.exportKey('raw', pair.publicKey));
  const serviceKeys = await keys();
  const reg = { install_id: 'install1', generation: 1, active, owner_verified, owner_address: OWNER,
    address_local: 'a'.repeat(30), key_id: 'mail_flow1', public_key: mailPublic, route_origin: ROUTE,
    route_path: ROUTE_PATH, route_generation: null, route_verified: 0 };
  await ledger.run('INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ...Object.values(reg));
  await ledger.run('INSERT INTO mail_installations(install_id,device_id,address_local,generation,ownership_lease_until) '
    + 'VALUES(?,?,?,?,?)', 'install1', 'device1', 'a'.repeat(30), 1, lease);
  return { db, ledger, reg, mailKey: pair.privateKey, serviceKeys,
    config: { privateKey: serviceKeys.privateKey, keyId: 'service1', servicePin: PIN } };
}

const token = (claims) => `${base64url(bytes('{"alg":"ES256"}'))}.${base64url(bytes(JSON.stringify(claims)))}.sig`;
const goodClaims = (over = {}) => ({ mode: 'proof', install_id: 'install1', generation: '1', route_origin: ROUTE, ...over });
const cpOk = (over) => ({ calls: [], issue(v) { this.calls.push(v); return Promise.resolve({ ok: true, body: { capability: token(goodClaims(over)) } }); } });
const receiptFor = async (ctx, sentBody, over = {}) => {
  const fields = { install_id: 'install1', generation: 1, challenge: sentBody.challenge,
    service_nonce: sentBody.service_nonce, route_origin: ROUTE, manifest_sequence: 1, proof_id: '1f'.repeat(16), ...over };
  const signature = base64url(await crypto.subtle.sign('Ed25519', ctx.mailKey, receiptBytes(fields)));
  return { custody: 'proof', install_id: 'install1', generation: 1, challenge: sentBody.challenge,
    proof_id: fields.proof_id, signature };
};
const edge = (ctx, { status = 200, mutate = (v) => v, calls = [] } = {}) => async (url, init) => {
  calls.push({ url, ...init });
  const sent = JSON.parse(new TextDecoder().decode(init.body));
  return Response.json(mutate(await receiptFor(ctx, sent)), { status });
};
const arm = (ctx, over = {}) => armProof(ctx.ledger, ctx.reg, { challenge: CHALLENGE, expires_ms: NOW + 120_000, ...over }, NOW);
const proofRow = (ctx) => ctx.ledger.first('SELECT * FROM mail_delivery_proofs');
const run = (ctx, over = {}) => sendProof({ ledger: ctx.ledger, config: ctx.config, cp: cpOk(), reg: ctx.reg,
  fetcher: edge(ctx), now: NOW, ...over });

// Cross-language gate: every byte below was produced by Python (tests/test_agent_mail_u7d2_proof.py).
test('golden vectors: body, receipt array, digest and signature match Python', async () => {
  const i = V.body.inputs;
  const reg = { install_id: i.install_id, generation: Number(i.generation), key_id: i.key_id, route_origin: i.route_origin };
  const pin = { manifest_sequence: i.manifest_sequence, service_key_id: i.service_key_id,
    service_public_key_sha256: i.service_public_key_sha256 };
  const body = proofBody({ challenge: i.challenge, reg, pin, owner_address_sha256: i.owner_address_sha256,
    service_nonce: i.service_nonce });
  assert.equal(new TextDecoder().decode(body), V.body.json);
  const f = V.receipt.fields;
  const fields = { install_id: f.install, generation: f.generation, challenge: f.challenge, service_nonce: f.service_nonce,
    route_origin: f.route_origin, manifest_sequence: f.manifest_sequence, proof_id: f.proof_id };
  assert.equal(JSON.stringify(receiptArray(fields)), V.receipt.array_json);
  const signed = receiptBytes(fields);
  assert.equal(await sha256(signed), V.receipt.digest);
  const raw = Uint8Array.from(Buffer.from(V.mail_public_key, 'base64url'));
  const key = await crypto.subtle.importKey('raw', raw, 'Ed25519', false, ['verify']);
  assert.equal(await crypto.subtle.verify('Ed25519', key, Buffer.from(V.receipt.signature, 'base64url'), signed), true);
});

test('armProof requires a verified owner and a bounded future expiry', async () => {
  const unverified = await setup({ owner_verified: 0 });
  assert.deepEqual(await arm(unverified), { ok: false, code: 'owner_not_verified' });
  const ctx = await setup();
  for (const over of [{ challenge: 'zz' }, { challenge: 7 }, { expires_ms: NOW }, { expires_ms: NOW - 1 },
    { expires_ms: NOW + PROOF_TTL_MS + 1 }, { expires_ms: 1.5 }, { expires_ms: String(NOW + 1000) }]) {
    assert.deepEqual(await arm(ctx, over), { ok: false, code: 'bad_proof_arm' }, JSON.stringify(over));
  }
  assert.equal(await proofRow(ctx), null);
  assert.deepEqual(await arm(ctx, { expires_ms: NOW + PROOF_TTL_MS }), { ok: true });
});

test('re-arming replaces an armed or verified row; a consumed row is final', async () => {
  const ctx = await setup();
  await arm(ctx);
  assert.equal((await run(ctx)).ok, true);
  assert.equal((await proofRow(ctx)).state, 'verified');
  assert.deepEqual(await arm(ctx, { challenge: 'cd'.repeat(32) }), { ok: true });
  const again = await proofRow(ctx);
  assert.equal(again.state, 'armed'); assert.equal(again.receipt_digest, null); assert.equal(again.expires_ms, null);
  await ctx.ledger.run("UPDATE mail_delivery_proofs SET state='consumed'");
  assert.deepEqual(await arm(ctx), { ok: false, code: 'proof_consumed' });
  assert.equal((await proofRow(ctx)).state, 'consumed');
});

test('invalidateProofs voids armed and verified rows and drops the challenge, never a consumed one', async () => {
  const ctx = await setup();
  await arm(ctx);
  await invalidateProofs(ctx.ledger, 'install1');
  const row = await proofRow(ctx);
  assert.equal(row.state, 'invalidated'); assert.equal(row.challenge, null);
  await ctx.ledger.run("UPDATE mail_delivery_proofs SET state='consumed'");
  await invalidateProofs(ctx.ledger, 'install1');
  assert.equal((await proofRow(ctx)).state, 'consumed');
  assert.equal((await run(ctx)).code, 'proof_not_armed');
});

test('proofStatus never reads a stale row as current', async () => {
  const ctx = await setup();
  assert.deepEqual(await proofStatus(ctx.ledger, 'install1', 1, NOW), { state: 'none' });
  await arm(ctx);
  assert.equal((await proofStatus(ctx.ledger, 'install1', 1, NOW + 119_999)).state, 'armed');
  assert.equal((await proofStatus(ctx.ledger, 'install1', 1, NOW + 120_000)).state, 'invalidated');
  await run(ctx);
  const live = await proofStatus(ctx.ledger, 'install1', 1, NOW);
  assert.equal(live.state, 'verified'); assert.equal(live.expires_ms, NOW + 300_000);
  assert.equal((await proofStatus(ctx.ledger, 'install1', 1, NOW + 300_000)).state, 'invalidated');
  assert.equal((await proofStatus(ctx.ledger, 'install1', 2, NOW)).state, 'none');
});

test('happy path: signed proof direction, capability attached, receipt verified, row verified', async () => {
  const ctx = await setup(); await arm(ctx);
  const calls = []; const cp = cpOk();
  const out = await run(ctx, { cp, fetcher: edge(ctx, { calls }) });
  assert.equal(out.ok, true);
  assert.equal(out.expires_ms, NOW + 300_000); // the ownership lease is the earlier bound
  assert.equal(calls.length, 1);
  const call = calls[0];
  assert.equal(call.url, ROUTE + ROUTE_PATH); assert.equal(call.redirect, 'manual');
  assert.equal(call.headers.get('x-mail-direction'), PROOF_DIRECTION);
  assert.equal(call.headers.get(MACHINE_HEADER), token(goodClaims()));
  assert.equal(cp.calls[0].mode, 'proof');
  assert.equal(cp.calls[0].req_digest, call.headers.get('x-mail-digest'));
  assert.equal(cp.calls[0].mail_nonce, call.headers.get('x-mail-nonce'));
  const request = new Request(call.url, { method: 'POST', headers: call.headers, body: call.body });
  assert.equal(await verify({ request, body: call.body, publicKey: ctx.serviceKeys.publicKey, key_id: 'service1',
    direction: PROOF_DIRECTION, install_id: 'install1', generation: 1, now: NOW, claimNonce: async () => true }), true);
  const body = JSON.parse(new TextDecoder().decode(call.body));
  assert.deepEqual(Object.keys(body), ['kind', 'version', 'challenge', 'install_id', 'generation', 'manifest_sequence',
    'service_key_id', 'service_public_key_sha256', 'mail_key_id', 'route_origin', 'owner_address_sha256', 'service_nonce']);
  assert.equal(body.owner_address_sha256, await sha256(bytes(OWNER)));
  const row = await proofRow(ctx);
  assert.equal(row.state, 'verified'); assert.equal(row.challenge, null); assert.equal(row.receipt_digest, out.receipt_digest);
});

test('expiry is capped at ten minutes when the lease is longer', async () => {
  const ctx = await setup({ lease: NOW + 3_600_000 }); await arm(ctx);
  assert.equal((await run(ctx)).expires_ms, NOW + PROOF_TTL_MS);
});

test('an expired ownership lease records nothing', async () => {
  const ctx = await setup({ lease: NOW }); await arm(ctx);
  assert.equal((await run(ctx)).code, 'ownership_lease_expired');
  assert.equal((await proofRow(ctx)).state, 'armed');
});

test('a proof creates no event, no registration change and no delivery authority', async () => {
  const ctx = await setup(); await arm(ctx); await run(ctx);
  assert.equal((await ctx.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
  const reg = await ctx.ledger.first('SELECT active,route_verified,route_generation FROM registrations');
  assert.deepEqual({ ...reg }, { active: 0, route_verified: 0, route_generation: null });
});

for (const [name, setupOver, runOver, code] of [
  ['an active registration', { active: 1 }, {}, 'not_pending'],
  ['an unverified owner', { owner_verified: 0 }, {}, 'not_pending'],
  ['no service pin', {}, { configPatch: { servicePin: undefined } }, 'service_pin_required'],
  ['a malformed service pin', {}, { configPatch: { servicePin: { ...PIN, manifest_sequence: 0 } } }, 'service_pin_required'],
  ['a route path that is not the ingress', {}, { regPatch: { route_path: '/other' } }, 'route_invalid'],
  ['a non-https route', {}, { regPatch: { route_origin: 'http://device.example.test' } }, 'route_invalid'],
]) {
  test(`sendProof refuses ${name}`, async () => {
    const ctx = await setup(setupOver); await arm(ctx);
    let fetched = false;
    const out = await sendProof({ ledger: ctx.ledger, config: { ...ctx.config, ...runOver.configPatch },
      cp: cpOk(), reg: { ...ctx.reg, ...runOver.regPatch }, fetcher: async () => { fetched = true; }, now: NOW });
    assert.equal(out.code, code); assert.equal(fetched, false);
  });
}

test('sendProof refuses when nothing is armed or the arm expired', async () => {
  const ctx = await setup();
  assert.equal((await run(ctx)).code, 'proof_not_armed');
  await arm(ctx);
  assert.equal((await run(ctx, { now: NOW + 120_000 })).code, 'proof_not_armed');
});

for (const [name, issue, code] of [
  ['CP refusal', async () => ({ ok: false, code: 'grant_revoked' }), 'grant_revoked'],
  ['a missing capability', async () => ({ ok: true, body: {} }), 'bad_capability'],
  ['an unparseable capability', async () => ({ ok: true, body: { capability: 'a.b.c' } }), 'bad_capability'],
  ['a capability for another route', async () => ({ ok: true, body: { capability: token(goodClaims({ route_origin: 'https://other.example.test' })) } }), 'route_mismatch'],
  ['a mail-mode capability', async () => ({ ok: true, body: { capability: token(goodClaims({ mode: 'mail' })) } }), 'bad_capability'],
  ['another install', async () => ({ ok: true, body: { capability: token(goodClaims({ install_id: 'install2' })) } }), 'bad_capability'],
  ['another generation', async () => ({ ok: true, body: { capability: token(goodClaims({ generation: '2' })) } }), 'bad_capability'],
]) {
  test(`sendProof stops before any request on ${name}`, async () => {
    const ctx = await setup(); await arm(ctx);
    let fetched = false;
    const out = await run(ctx, { cp: { issue }, fetcher: async () => { fetched = true; } });
    assert.equal(out.ok, false); assert.equal(out.code, code); assert.equal(fetched, false);
    assert.equal((await proofRow(ctx)).state, 'armed');
  });
}

const html = () => new Response('<html>Sign in</html>', { status: 200, headers: { 'content-type': 'text/html' } });
for (const [name, fetcher] of [
  ['an unreachable edge', async () => { throw new Error('offline'); }],
  ['a redirect to a login page', async () => new Response('', { status: 302 })],
  ['a 200 HTML login page', async () => html()],
  ['a 500', async () => new Response('', { status: 500 })],
  ['an oversize body', async () => new Response('x'.repeat(5000))],
  ['a JSON array', async () => Response.json([1])],
]) {
  test(`${name} is no custody and leaves the proof armed`, async () => {
    const ctx = await setup(); await arm(ctx);
    const out = await run(ctx, { fetcher });
    assert.equal(out.ok, false); assert.equal(out.code, 'edge_unreachable');
    assert.equal((await proofRow(ctx)).state, 'armed');
  });
}

test('a generic 200 object is a bad receipt and leaves the proof armed', async () => {
  const ctx = await setup(); await arm(ctx);
  const out = await run(ctx, { fetcher: async () => Response.json({ ok: true }) });
  assert.equal(out.code, 'bad_receipt');
  assert.equal((await proofRow(ctx)).state, 'armed');
});

test('a locked vault is a retryable refusal, not custody', async () => {
  const ctx = await setup(); await arm(ctx);
  const out = await run(ctx, { fetcher: async () => Response.json({ error: 'vault_locked' }, { status: 503 }) });
  assert.deepEqual(out, { ok: false, code: 'vault_locked', retry: true });
  assert.equal((await proofRow(ctx)).state, 'armed');
  const other = await run(ctx, { fetcher: async () => Response.json({ error: 'something' }, { status: 503 }) });
  assert.equal(other.code, 'edge_unreachable'); assert.equal(other.retry, undefined);
});

const BAD = {
  'wrong challenge': (v) => ({ ...v, challenge: 'cd'.repeat(32) }),
  'wrong install': (v) => ({ ...v, install_id: 'install2' }),
  'wrong generation': (v) => ({ ...v, generation: 2 }),
  'string generation': (v) => ({ ...v, generation: '1' }),
  'wrong custody': (v) => ({ ...v, custody: 'durable' }),
  'extra key': (v) => ({ ...v, extra: 1 }),
  'missing key': ({ signature, ...v }) => v,
  'short proof id': (v) => ({ ...v, proof_id: 'ab' }),
  'uppercase proof id': (v) => ({ ...v, proof_id: 'AB'.repeat(16) }),
  'non-string signature': (v) => ({ ...v, signature: 5 }),
  'garbage signature': (v) => ({ ...v, signature: 'AAAA' }),
  'signature over another proof id': (v) => ({ ...v, proof_id: '2e'.repeat(16) }),
  'signature bit flipped': (v) => ({ ...v, signature: (v.signature[0] === 'A' ? 'B' : 'A') + v.signature.slice(1) }),
};
for (const [name, mutate] of Object.entries(BAD)) {
  test(`receipt with ${name} is refused and records nothing`, async () => {
    const ctx = await setup(); await arm(ctx);
    const out = await run(ctx, { fetcher: edge(ctx, { mutate }) });
    assert.equal(out.ok, false); assert.equal(out.code, 'bad_receipt');
    const row = await proofRow(ctx);
    assert.equal(row.state, 'armed'); assert.equal(row.receipt_digest, null);
  });
}

test('a receipt signed by a different key is refused', async () => {
  const ctx = await setup(); await arm(ctx);
  const other = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const fetcher = async (_u, init) => {
    const sent = JSON.parse(new TextDecoder().decode(init.body));
    const r = await receiptFor({ mailKey: other.privateKey }, sent);
    return Response.json(r);
  };
  assert.equal((await run(ctx, { fetcher })).code, 'bad_receipt');
});

test('a receipt replayed from an earlier request fails on the fresh service nonce', async () => {
  const ctx = await setup(); await arm(ctx);
  let stale;
  await run(ctx, { fetcher: async (_u, init) => {
    const sent = JSON.parse(new TextDecoder().decode(init.body));
    stale = await receiptFor(ctx, sent); return Response.json(stale);
  } });
  await arm(ctx); // same challenge, new service_nonce on the next send
  const out = await run(ctx, { fetcher: async () => Response.json(stale) });
  assert.equal(out.code, 'bad_receipt');
  assert.equal((await proofRow(ctx)).state, 'armed');
});

test('a proof invalidated while the request was in flight is never recorded', async () => {
  const ctx = await setup(); await arm(ctx);
  const inner = edge(ctx);
  const out = await run(ctx, { fetcher: async (u, i) => { await invalidateProofs(ctx.ledger, 'install1'); return inner(u, i); } });
  assert.deepEqual(out, { ok: false, code: 'proof_raced' });
  assert.equal((await proofRow(ctx)).state, 'invalidated');
});

test('a re-arm during the request wins: the old challenge cannot verify the new row', async () => {
  const ctx = await setup(); await arm(ctx);
  const inner = edge(ctx);
  const out = await run(ctx, { fetcher: async (u, i) => { await arm(ctx, { challenge: 'ee'.repeat(32) }); return inner(u, i); } });
  assert.equal(out.code, 'proof_raced');
  assert.equal((await proofRow(ctx)).state, 'armed');
});

test('a consumed proof can never be proved again', async () => {
  const ctx = await setup(); await arm(ctx); await run(ctx);
  await ctx.ledger.run("UPDATE mail_delivery_proofs SET state='consumed'");
  assert.equal((await run(ctx)).code, 'proof_not_armed');
});

test('the proof module is not a delivery path: no scheduler, route table or flag in its source', () => {
  const source = readFileSync(new URL('../delivery-proof.js', import.meta.url), 'utf8');
  assert.equal(/scheduled|addEventListener|export default|MAIL_ENABLED|env\./.test(source), false);
  assert.equal(/UPDATE registrations|INSERT INTO events|route_verified\s*=/.test(source), false);
});
