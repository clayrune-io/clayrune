import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { verifyDeliveryCapability, CAPABILITY_TYPE, CAPABILITY_AUDIENCE, MAX_LIFETIME_S } from '../delivery-capability.js';
const V = JSON.parse(readFileSync(new URL('./fixtures/delivery-vectors.json', import.meta.url), 'utf8'));
const trust = { issuer: V.issuer, jwksUrl: V.jwks_url, jwks: V.jwks };
const code = async (fn) => { try { await fn(); return 'ok'; } catch (e) { return e.message; } };

test('constants match the contract', () => {
  assert.equal(CAPABILITY_TYPE, 'clayrune-mail-delivery+jwt');
  assert.equal(CAPABILITY_AUDIENCE, 'clayrune-mail-delivery');
  assert.equal(MAX_LIFETIME_S, 60);
  assert.equal(V.jwks.keys.every((k) => k.d === undefined && /^mail-delivery-/.test(k.kid)), true);
});

// Every vector is Python-signed (control_plane.tests.delivery_vectors): the cross-language gate.
for (const v of V.vectors) {
  test(`vector: ${v.name} -> ${v.expect}`, async () => {
    const out = await code(() => verifyDeliveryCapability(v.token, trust, v.request, () => v.now_ms));
    assert.equal(out, v.expect);
  });
}

test('a valid vector returns the verified claims', async () => {
  const v = V.vectors[0];
  const c = await verifyDeliveryCapability(v.token, trust, v.request, () => v.now_ms);
  assert.equal(c.aud, CAPABILITY_AUDIENCE);
  assert.equal(c.install_id, 'install1');
  assert.equal(c.route_origin, 'https://device.example.com');
});

test('there is no leeway on either side of the window', async () => {
  const v = V.vectors[0];
  const c = JSON.parse(Buffer.from(v.token.split('.')[1], 'base64url'));
  assert.equal(await code(() => verifyDeliveryCapability(v.token, trust, v.request, () => c.exp * 1000 - 1)), 'ok');
  assert.equal(await code(() => verifyDeliveryCapability(v.token, trust, v.request, () => c.exp * 1000)), 'capability_expired');
  assert.equal(await code(() => verifyDeliveryCapability(v.token, trust, v.request, () => c.checked_at_ms - 1)), 'capability_not_yet_valid');
  assert.equal(await code(() => verifyDeliveryCapability(v.token, trust, v.request, () => c.checked_at_ms)), 'ok');
});

test('trust must be reviewed config: issuer, jwks url and key set are all required', async () => {
  const v = V.vectors[0];
  const run = (t) => code(() => verifyDeliveryCapability(v.token, t, v.request, () => v.now_ms));
  assert.equal(await run(undefined), 'capability_trust_required');
  assert.equal(await run({ ...trust, issuer: 'not a url' }), 'capability_trust_required');
  assert.equal(await run({ ...trust, jwksUrl: `${V.issuer}/v1/jwks` }), 'capability_trust_required');
  assert.equal(await run({ ...trust, jwks: {} }), 'capability_trust_required');
  assert.equal(await run({ ...trust, issuer: 'https://other.example.com', jwksUrl: 'https://other.example.com/v1/mail-delivery/jwks' }),
    'bad_capability_claims');
});

test('a key set with a private member or wrong curve is refused', async () => {
  const v = V.vectors[0];
  const k = V.jwks.keys[0];
  for (const bad of [{ ...k, d: 'AAAA' }, { ...k, crv: 'P-384' }, { ...k, use: 'enc' }, { ...k, alg: 'RS256' }, { ...k, x: 'AAAA' }]) {
    const out = await code(() => verifyDeliveryCapability(v.token, { ...trust, jwks: { keys: [bad] } }, v.request, () => v.now_ms));
    assert.equal(out, 'bad_capability_key');
  }
  assert.equal(await code(() => verifyDeliveryCapability(v.token, { ...trust, jwks: { keys: [k, k] } }, v.request, () => v.now_ms)),
    'unknown_capability_key');
});

test('a token cannot supply its own key set or jwks url', async () => {
  const v = V.vectors.find((x) => x.name === 'extra header field');
  assert.equal(await code(() => verifyDeliveryCapability(v.token, trust, v.request, () => v.now_ms)), 'bad_capability_header');
});

test('missing request context is a mismatch, never a pass', async () => {
  const v = V.vectors[0];
  for (const r of [undefined, {}, { ...v.request, headers: undefined }]) {
    assert.equal(await code(() => verifyDeliveryCapability(v.token, trust, r, () => v.now_ms)), 'capability_request_mismatch');
  }
});
