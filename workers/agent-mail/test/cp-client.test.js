import test from 'node:test';
import assert from 'node:assert/strict';
import { createCpClient, CP_PREFIX, MAX_REQUEST_BYTES } from '../cp-client.js';
import { verify, sha256 } from '../signing.js';
import { keys, NOW } from './transport-support.js';

const ISSUER = 'https://authority.example.com';
const NONCE = 'ab'.repeat(16);
const DIGEST = 'cd'.repeat(32);
async function setup(respond) {
  const k = await keys();
  const calls = [];
  const fetcher = async (url, init) => { calls.push({ url, init }); return respond(url, init); };
  const client = createCpClient({ issuer: ISSUER, key_id: 'service1', privateKey: k.privateKey, fetcher, clock: () => NOW });
  return { client, calls, k };
}
const json = (status, value) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
const issueArgs = (over = {}) => ({ install_id: 'install1', generation: 1, mode: 'mail', req_digest: DIGEST,
  mail_nonce: NONCE, mail_key_id: 'mail1', ...over });

test('issue is signed service-to-cp over the exact CP path, verifiable by the mail-service public key', async () => {
  const { client, calls, k } = await setup(() => json(200, { capability: 'x.y.z', jti: 'j', exp: 1 }));
  const r = await client.issue(issueArgs());
  assert.deepEqual(r, { ok: true, status: 200, body: { capability: 'x.y.z', jti: 'j', exp: 1 } });
  const { url, init } = calls[0];
  assert.equal(url, `${ISSUER}${CP_PREFIX}/issue`);
  assert.equal(init.method, 'POST');
  assert.equal(init.redirect, 'manual');
  assert.equal(init.headers.get('x-mail-direction'), 'service-to-cp');
  const request = new Request(url, { method: 'POST', headers: init.headers, body: init.body });
  const body = new Uint8Array(await request.clone().arrayBuffer());
  assert.equal(await verify({ request, body, publicKey: k.publicKey, key_id: 'service1', install_id: 'install1',
    generation: 1, direction: 'service-to-cp', now: NOW, claimNonce: () => true }), true);
  assert.equal(init.headers.get('x-mail-digest'), await sha256(body));
  assert.deepEqual(JSON.parse(new TextDecoder().decode(body)), { install_id: 'install1', generation: '1', mode: 'mail',
    req_digest: DIGEST, mail_nonce: NONCE, mail_key_id: 'mail1' });
});

test('the signature does not verify for another direction, install, generation or path', async () => {
  const { client, calls, k } = await setup(() => json(200, {}));
  await client.lease({ install_id: 'install1', generation: 2 });
  const { url, init } = calls[0];
  const mk = (u = url) => new Request(u, { method: 'POST', headers: init.headers, body: init.body });
  const body = new Uint8Array(await mk().arrayBuffer());
  const base = { publicKey: k.publicKey, key_id: 'service1', install_id: 'install1', generation: 2,
    direction: 'service-to-cp', now: NOW, claimNonce: () => true, body };
  assert.equal(await verify({ ...base, request: mk() }), true);
  assert.equal(await verify({ ...base, request: mk(), direction: 'service-to-install' }), false);
  assert.equal(await verify({ ...base, request: mk(), install_id: 'other' }), false);
  assert.equal(await verify({ ...base, request: mk(), generation: 3 }), false);
  assert.equal(await verify({ ...base, request: mk(url.replace('/lease', '/issue')) }), false);
});

test('each call posts to its own fixed path with a fresh nonce', async () => {
  const { client, calls } = await setup(() => json(200, {}));
  await client.bind({ install_id: 'install1', generation: 1, grant: 'g', r: ['a'] });
  await client.lease({ install_id: 'install1', generation: 1 });
  await client.revoke({ install_id: 'install1', generation: 1 });
  await client.issue(issueArgs({ worker_active: true }));
  assert.deepEqual(calls.map((c) => c.url.slice(ISSUER.length)),
    [`${CP_PREFIX}/bind`, `${CP_PREFIX}/lease`, `${CP_PREFIX}/revoke`, `${CP_PREFIX}/issue`]);
  assert.equal(new Set(calls.map((c) => c.init.headers.get('x-mail-nonce'))).size, 4);
  assert.equal(JSON.parse(new TextDecoder().decode(calls[3].init.body)).worker_active, true);
});

test('redirects are never followed and never an answer', async () => {
  for (const mk of [() => new Response(null, { status: 302, headers: { location: 'https://login.example.com' } }),
    () => ({ status: 200, type: 'opaqueredirect', arrayBuffer: async () => new ArrayBuffer(0) })]) {
    const { client } = await setup(mk);
    const r = await client.issue(issueArgs());
    assert.equal(r.code, 'cp_redirect');
    assert.equal(r.ok, false);
  }
});

test('CP error codes pass through only as closed lowercase codes', async () => {
  let reply = { code: 'authority_refused', message: 'x', request_id: 'r' };
  const { client } = await setup(() => json(403, reply));
  assert.deepEqual(await client.issue(issueArgs()), { ok: false, status: 403, code: 'authority_refused' });
  reply = { code: '<script>alert(1)</script>' };
  assert.deepEqual(await client.issue(issueArgs()), { ok: false, status: 403, code: 'cp_error' });
  reply = { code: 'X'.repeat(200) };
  assert.equal((await client.issue(issueArgs())).code, 'cp_error');
});

test('network failure, bad body and non-object success are closed codes with no message', async () => {
  let c = await setup(() => { throw new Error('connect ECONNREFUSED secret-host'); });
  assert.deepEqual(await c.client.issue(issueArgs()), { ok: false, code: 'cp_unavailable' });
  c = await setup(() => new Response('<html>login</html>', { status: 200 }));
  assert.deepEqual(await c.client.issue(issueArgs()), { ok: false, status: 200, code: 'cp_bad_response' });
  c = await setup(() => json(200, [1]));
  assert.deepEqual(await c.client.issue(issueArgs()), { ok: false, status: 200, code: 'cp_error' });
  c = await setup(() => new Response('x'.repeat(70 * 1024), { status: 200 }));
  assert.equal((await c.client.issue(issueArgs())).code, 'cp_response_too_large');
});

test('local validation refuses before any network call', async () => {
  const { client, calls } = await setup(() => json(200, {}));
  for (const bad of [issueArgs({ mode: 'admin' }), issueArgs({ req_digest: 'x' }), issueArgs({ mail_nonce: 'x' }),
    issueArgs({ mail_key_id: 'bad key' }), issueArgs({ install_id: 'bad id' }), issueArgs({ generation: 0 })]) {
    assert.equal((await client.issue(bad)).code, 'bad_request');
  }
  assert.equal((await client.bind({ install_id: 'install1', generation: 1, grant: 'g'.repeat(MAX_REQUEST_BYTES), r: [] })).code, 'bad_request');
  assert.equal(calls.length, 0);
});

test('client requires reviewed config', async () => {
  const k = await keys();
  for (const bad of [{ issuer: 'http://x', key_id: 'a', privateKey: k.privateKey }, { issuer: ISSUER, key_id: 'bad id', privateKey: k.privateKey },
    { issuer: ISSUER, key_id: 'a', privateKey: undefined }]) {
    assert.throws(() => createCpClient(bad), /cp_client_config_required/);
  }
});
