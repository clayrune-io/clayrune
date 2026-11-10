import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { verifyGrant, GRANT_TYPE, GRANT_AUDIENCE } from '../enrollment-grant.js';
const V = JSON.parse(readFileSync(new URL('./fixtures/grant-vectors.json', import.meta.url), 'utf8'));
const trust = { issuer: V.issuer, serviceOrigin: V.service_origin, jwksUrl: V.jwks_url, jwks: V.jwks };
const code = async (fn) => { try { await fn(); return 'ok'; } catch (e) { return e.message; } };

test('constants match the contract and the published key set is public-only', () => {
  assert.equal(GRANT_TYPE, 'clayrune-mail-enrollment+jwt');
  assert.equal(GRANT_AUDIENCE, 'clayrune-mail-enrollment');
  assert.equal(V.jwks_url, `${V.issuer}/v1/mail-enrollment/jwks`);
  assert.equal(V.jwks.keys.every((k) => k.d === undefined && /^mail-grant-/.test(k.kid)), true);
});

// Every vector is Python-signed (control_plane.tests.grant_vectors): the cross-language gate.
for (const v of V.vectors) {
  test(`vector: ${v.name} -> ${v.expect}`, async () => {
    assert.equal(await code(() => verifyGrant(v.token, v.r, trust, () => v.now_ms)), v.expect);
  });
}

test('a valid vector returns the verified claims', async () => {
  const v = V.vectors[0];
  const c = await verifyGrant(v.token, v.r, trust, () => v.now_ms);
  assert.equal(c.aud, GRANT_AUDIENCE);
  assert.equal(c.install_id, 'install1');
  assert.equal(c.route_origin, 'https://device.example.com');
});

test('trust must be exactly the reviewed /v1/mail-enrollment/jwks of the issuer', async () => {
  const v = V.vectors[0];
  for (const bad of [{ ...trust, jwksUrl: `${V.issuer}/v1/jwks` }, { ...trust, jwksUrl: `${V.issuer}/v1/mail-delivery/jwks` },
    { ...trust, jwksUrl: 'https://evil.example.com/v1/mail-enrollment/jwks' }, { ...trust, jwks: undefined }, null]) {
    assert.equal(await code(() => verifyGrant(v.token, v.r, bad, () => v.now_ms)), 'grant_trust_required');
  }
});
