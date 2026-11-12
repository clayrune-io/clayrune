import test from 'node:test';
import assert from 'node:assert/strict';
import { verifySignedV2 } from '../signed-v2-verifier.js';
import { bytes, sha256, base64url } from '../signing.js';

const ORIGIN = 'https://mail.example.com';
const PATH = '/v1/pull/ticket';
const NOW = 1_794_249_600_000;
const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const publicKey = base64url(await crypto.subtle.exportKey('raw', pair.publicKey));
async function fixture(fields = {}, changes = {}) {
  const raw = bytes('{"op_id":"' + 'a'.repeat(32) + '"}');
  const body = JSON.parse(new TextDecoder().decode(raw));
  const m = { version: '2', direction: 'install-to-pull', key_id: 'current', install_id: 'b'.repeat(32),
    generation: '7', owner_generation: '3', timestamp: String(NOW), nonce: 'c'.repeat(32), digest: await sha256(raw), ...fields };
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction, ORIGIN,
    'POST', PATH, m.key_id, m.install_id, m.generation, m.owner_generation, m.timestamp, m.nonce, m.digest]));
  const signature = base64url(await crypto.subtle.sign('Ed25519', pair.privateKey, input));
  const headers = new Headers();
  for (const [k, v] of Object.entries({ ...m, signature })) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
  return { request: new Request(ORIGIN + PATH, { method: 'POST', headers, body: raw }),
    raw, body, path: PATH, serviceOrigin: ORIGIN, clock: () => NOW,
    resolveKey: async (auth, path, value) => {
      assert.equal(path, PATH); assert.equal(value, body);
      assert.equal(auth.install_id, m.install_id); assert.equal(auth.key_id, m.key_id);
      assert.equal(auth.generation, m.generation); assert.equal(auth.owner_generation, m.owner_generation);
      return publicKey;
    }, ...changes };
}
test('independent signed vector resolves exact arbitrary-generation scope', async () => {
  const f = await fixture();
  const auth = await verifySignedV2(f);
  assert.equal(auth.generation, '7'); assert.equal(auth.owner_generation, '3');
  assert.equal(auth.nonce, 'c'.repeat(32)); assert.equal(auth.digest, await sha256(f.raw));
});
for (const generation of ['1', '999999999999999']) test(`generation boundary ${generation}`, async () => {
  assert.equal((await verifySignedV2(await fixture({ generation, owner_generation: generation }))).generation, generation);
});
for (const [field, values] of Object.entries({ version: ['1', '02'], direction: ['pull-to-install', 'install-to-service'],
  key_id: ['', 'x y', 'a'.repeat(129)], install_id: ['a'.repeat(31), 'B'.repeat(32)],
  generation: ['0', '-1', '01', '1.0', '1000000000000000'], owner_generation: ['0', '01', '1000000000000000'],
  timestamp: ['-1', '01', '1e12', '9007199254740992'], nonce: ['x'.repeat(32), 'a'.repeat(31)],
  digest: ['A'.repeat(64), 'a'.repeat(63)] })) for (const value of values) {
  test(`reject invalid ${field} ${value.slice(0, 36)}`, async () => {
    await assert.rejects(verifySignedV2(await fixture({ [field]: value })), /unauthorized/);
  });
}
for (const field of ['version', 'direction', 'key-id', 'install-id', 'generation', 'owner-generation', 'timestamp', 'nonce', 'digest', 'signature']) {
  test(`missing header ${field}`, async () => {
    const f = await fixture(); f.request.headers.delete(`x-mail-${field}`);
    await assert.rejects(verifySignedV2(f), /unauthorized/);
  });
}
for (const delta of [-300000, 300000]) test(`skew boundary ${delta}`, async () => {
  await verifySignedV2(await fixture({ timestamp: String(NOW + delta) }));
});
for (const delta of [-300001, 300001]) test(`outside skew ${delta}`, async () => {
  await assert.rejects(verifySignedV2(await fixture({ timestamp: String(NOW + delta) })), /unauthorized/);
});
test('expiry during resolver work fails closed', async () => {
  let now = NOW;
  const f = await fixture({}, { clock: () => now, resolveKey: async () => { now += 300001; return publicKey; } });
  await assert.rejects(verifySignedV2(f), /unauthorized/);
});
test('resolver refusal/outage propagates; never accepts possession as authority', async () => {
  for (const reason of ['unauthorized', 'database_down']) {
    await assert.rejects(verifySignedV2(await fixture({}, { resolveKey: async () => { throw new Error(reason); } })), new RegExp(reason));
  }
});
test('raw digest, path, origin, method and signature are bound', async () => {
  for (const changes of [{ raw: bytes('{}') }, { path: '/v1/pull/send' }, { serviceOrigin: 'https://other.example.com' }]) {
    await assert.rejects(verifySignedV2(await fixture({}, changes)), /unauthorized/);
  }
  const f = await fixture();
  for (const url of [ORIGIN + PATH + '?', ORIGIN + PATH + '?x=1', ORIGIN + PATH + '#x']) {
    await assert.rejects(verifySignedV2({ ...f, request: new Request(url, { method: 'POST', headers: f.request.headers, body: f.raw }) }), /unauthorized/);
  }
  await assert.rejects(verifySignedV2({ ...f, request: new Request(ORIGIN + PATH, { headers: f.request.headers }) }), /unauthorized/);
  f.request.headers.set('x-mail-signature', 'a'.repeat(86));
  await assert.rejects(verifySignedV2(f), /unauthorized/);
});
test('wrong authorized key refuses a valid signature', async () => {
  const other = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const key = base64url(await crypto.subtle.exportKey('raw', other.publicKey));
  await assert.rejects(verifySignedV2(await fixture({}, { resolveKey: async () => key })), /unauthorized/);
});
