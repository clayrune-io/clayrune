import test from 'node:test';
import assert from 'node:assert/strict';
import { canonical, bytes, sha256, sign, verify, SKEW_MS } from '../signing.js';
import { fixture, NOW } from './transport-support.js';
test('canonical signing bytes are a fixed ASCII JSON vector', async () => {
  const m = { version: '1', direction: 'install-to-service', key_id: 'ik1', install_id: 'install1',
    generation: '1', method: 'POST', path: '/v1/mail/send', timestamp: String(NOW),
    nonce: 'a'.repeat(32), digest: await sha256(bytes('abc')) };
  assert.equal(new TextDecoder().decode(canonical(m)),
    '["clayrune-agent-mail","1","install-to-service","ik1","install1","1","POST","/v1/mail/send","1794249600000","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"]');
});
test('valid signed request and durable nonce replay refusal', async () => {
  const f = await fixture(); const r = await f.request(); const body = new Uint8Array(await r.arrayBuffer());
  const args = { request: r, body, publicKey: f.reg.public_key, key_id: 'ik1', install_id: 'install1', generation: 1,
    direction: 'install-to-service', now: NOW, claimNonce: (m) => f.ledger.claimNonce(m) };
  assert.equal(await verify(args), true); assert.equal(await verify(args), false);
});
for (const [header, value] of Object.entries({ version: '2', direction: 'service-to-install',
  'key-id': 'other', 'install-id': 'other', generation: '2', timestamp: String(NOW + SKEW_MS + 1),
  nonce: 'b'.repeat(32), digest: '0'.repeat(64), signature: 'A'.repeat(86) })) {
  test(`signature binds ${header}`, async () => {
    const f = await fixture(); const r = await f.request(); r.headers.set(`x-mail-${header}`, value);
    assert.equal((await f.worker.fetch(r, f.env)).status, 401);
    assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 0);
    assert.equal(f.sent.length, 0);
  });
}
for (const change of ['body', 'method', 'path', 'query', 'public-key']) {
  test(`signature rejects changed ${change}`, async () => {
    const f = await fixture(); const r = await f.request(); let body = new Uint8Array(await r.arrayBuffer());
    const url = new URL(r.url); let method = 'POST';
    if (change === 'body') body = bytes('changed');
    if (change === 'method') method = 'PUT';
    if (change === 'path') url.pathname = '/v1/mail/reconcile';
    if (change === 'query') url.search = '?x=1';
    const changed = new Request(url, { method, headers: r.headers, body });
    assert.equal(await verify({ request: changed, body, publicKey: change === 'public-key' ? 'AA' : f.reg.public_key,
      key_id: 'ik1', install_id: 'install1', generation: 1, direction: 'install-to-service', now: NOW,
      claimNonce: (m) => f.ledger.claimNonce(m) }), false);
  });
}
for (const offset of [-SKEW_MS, SKEW_MS, -SKEW_MS - 1, SKEW_MS + 1]) {
  test(`signature timestamp boundary ${offset}`, async () => {
    const f = await fixture(); const r = await f.request('/v1/mail/reconcile', {}, { now: NOW + offset });
    assert.equal((await f.worker.fetch(r, f.env)).status, Math.abs(offset) <= SKEW_MS ? 200 : 401);
  });
}
test('concurrent identical nonce has one winner across ledger instances', async () => {
  const f = await fixture(); const m = { direction: 'install-to-service', install_id: 'install1', generation: 1,
    key_id: 'ik1', nonce: 'n', expires_at: NOW + SKEW_MS };
  assert.equal((await Promise.all(Array.from({ length: 10 }, () => f.ledger.claimNonce(m)))).filter(Boolean).length, 1);
});
test('service delivery signature verifies only with pinned service key/direction', async () => {
  const f = await fixture(); const body = bytes('{}');
  const headers = await sign({ ...f.serviceKeys, key_id: 'sk1', install_id: 'install1', generation: 1,
    direction: 'service-to-install', path: '/api/agent-mail/ingress', body, now: NOW });
  const request = new Request('https://proven.example.net/api/agent-mail/ingress', { method: 'POST', headers, body });
  assert.equal(await verify({ request, body, publicKey: f.serviceKeys.publicKey, key_id: 'sk1', install_id: 'install1',
    generation: 1, direction: 'service-to-install', now: NOW, claimNonce: async () => true }), true);
});
