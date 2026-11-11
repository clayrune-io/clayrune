// Unit 7d-2e-2, decision E2a: the Worker asks the CP preflight before `begin` does anything, and fails closed.
// A fake CP (injected fetcher) stands in for control_plane/app/routes_mail_enrollment.py; no network.
import test from 'node:test';
import assert from 'node:assert/strict';
import { keys } from './transport-support.js';
import { verify, sha256, bytes } from '../signing.js';
import { createPreflightClient, PREFLIGHT_PATH } from '../enrollment-preflight.js';
import { world, row } from './enrollment-http-support.js';

const ISSUER = 'https://authority.example.com';
const NOW = 1_794_249_600_000;
const INPUT = { flow_id: 'flow1', device_id: 'device1', device_public_key: 'k'.repeat(43),
  tunnel_uuid: '12345678-1234-1234-1234-123456789abc', route_origin: 'https://device.example.com' };
const cp = (status, body = { ok: true }, over = {}) => {
  const calls = [];
  const fetcher = async (url, init) => { calls.push({ url, init }); return over.throws ? Promise.reject(new Error('net')) :
    over.response ?? new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: over.headers }); };
  return { fetcher, calls };
};
async function client(fake, over = {}) {
  const k = await keys();
  return { k, run: createPreflightClient({ issuer: ISSUER, key_id: 'sk1', privateKey: k.privateKey, fetcher: fake.fetcher,
    clock: () => NOW, ...over }) };
}

test('request: service-signed POST to the pinned path, no redirects, flow_id as install, generation 1', async () => {
  const fake = cp(200); const { k, run } = await client(fake);
  assert.deepEqual(await run(INPUT), { ok: true });
  const [{ url, init }] = fake.calls;
  assert.equal(url, `${ISSUER}${PREFLIGHT_PATH}`); assert.equal(PREFLIGHT_PATH, '/v1/mail-enrollment/preflight');
  assert.equal(init.method, 'POST'); assert.equal(init.redirect, 'manual');
  // flow_id is the signed envelope's install scope; it is not repeated in the body
  assert.deepEqual(JSON.parse(new TextDecoder().decode(init.body)), { device_id: INPUT.device_id,
    device_public_key: INPUT.device_public_key, tunnel_uuid: INPUT.tunnel_uuid, route_origin: INPUT.route_origin });
  const request = new Request(url, { method: 'POST', headers: init.headers });
  assert.equal(await verify({ request, body: init.body, publicKey: k.publicKey, direction: 'service-to-cp', install_id: 'flow1',
    generation: 1, key_id: 'sk1', claimNonce: async () => true, now: NOW }), true);
  assert.equal(init.headers.get('x-mail-digest'), await sha256(init.body));
});

test('only 200 {"ok":true} is ok', async () => {
  for (const [status, body, expected] of [
    [200, { ok: true }, { ok: true }], [200, { ok: false }, { ok: false, refused: false }],
    [200, { ok: true, extra: 1 }, { ok: false, refused: false }], [200, { ok: 'true' }, { ok: false, refused: false }],
    [200, {}, { ok: false, refused: false }], [200, [], { ok: false, refused: false }], [200, 'not json', { ok: false, refused: false }],
    [200, 'null', { ok: false, refused: false }], [204, '', { ok: false, refused: false }]]) {
    const { run } = await client(cp(status, body)); assert.deepEqual(await run(INPUT), expected, `${status} ${JSON.stringify(body)}`);
  }
});

test('400/403/409 are a CP refusal; 401/404/429/5xx are unavailable; both are ok:false', async () => {
  for (const status of [400, 403, 409]) assert.deepEqual(await (await client(cp(status))).run(INPUT), { ok: false, refused: true }, String(status));
  for (const status of [401, 404, 408, 422, 429, 500, 502, 503, 504]) {
    assert.deepEqual(await (await client(cp(status))).run(INPUT), { ok: false, refused: false }, String(status));
  }
});

test('transport failure, timeout, redirect and opaque redirect all fail closed as unavailable', async () => {
  const unavailable = { ok: false, refused: false };
  assert.deepEqual(await (await client(cp(200, {}, { throws: true }))).run(INPUT), unavailable);
  for (const status of [301, 302, 307, 308]) {
    assert.deepEqual(await (await client(cp(status, '', { headers: { location: 'https://evil.example.com/' } }))).run(INPUT), unavailable);
  }
  const opaque = { type: 'opaqueredirect', status: 0, arrayBuffer: async () => new ArrayBuffer(0) };
  assert.deepEqual(await (await client(cp(0, '', { response: opaque }))).run(INPUT), unavailable);
  const slow = { fetcher: async (_u, init) => new Promise((_res, rej) => init.signal.addEventListener('abort', () => rej(new Error('aborted')))) };
  assert.deepEqual(await (await client(slow, { timeoutMs: 20 })).run(INPUT), unavailable);
});

test('an oversize response is refused and a bad flow_id never reaches the network', async () => {
  const big = cp(200, JSON.stringify({ ok: true, pad: 'x'.repeat(5000) }));
  assert.deepEqual(await (await client(big)).run(INPUT), { ok: false, refused: false });
  const fake = cp(200);
  for (const flow_id of ['bad id!', undefined, '', 'x'.repeat(200)]) {
    assert.deepEqual(await (await client(fake)).run({ ...INPUT, flow_id }), { ok: false, refused: false });
  }
  assert.equal(fake.calls.length, 0);
});

test('a failing signature setup (bad private key) fails closed without calling the CP', async () => {
  const fake = cp(200); const run = createPreflightClient({ issuer: ISSUER, key_id: 'sk1', privateKey: 'AAAA', fetcher: fake.fetcher, clock: () => NOW });
  assert.deepEqual(await run(INPUT), { ok: false, refused: false }); assert.equal(fake.calls.length, 0);
});

test('construction needs an https issuer, a key id and a key', () => {
  for (const bad of [{ issuer: 'http://x.example.com' }, { issuer: ISSUER, key_id: 'bad id' }, { issuer: ISSUER, key_id: 'sk1' }, {}]) {
    assert.throws(() => createPreflightClient({ privateKey: undefined, ...bad }), /preflight_config_required/);
  }
});

test('through begin: the real client against a fake CP: ok -> flow + one mail; refusal/outage -> nothing written, nothing sent', async () => {
  const seen = []; let answer = () => new Response('{"ok":true}', { status: 200 });
  const w = await world({ fetcher: async (url, init) => { seen.push({ url, init }); return answer(); } });
  try {
    answer = () => new Response('{}', { status: 403 });
    let r = await w.begin(); assert.equal(r.res.status, 403); assert.deepEqual(r.json, { code: 'preflight_refused' });
    answer = () => { throw new Error('connect ECONNREFUSED'); };
    r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'preflight_unavailable' });
    answer = () => new Response('<html>', { status: 200 });
    r = await w.begin(); assert.equal(r.res.status, 503);
    assert.equal(w.sent.length, 0); assert.equal(w.spy.calls, 0, 'not one D1 call on any refused begin');
    assert.equal(w.log.join('').includes('ECONNREFUSED'), false);
    answer = () => new Response('{"ok":true}', { status: 200 });
    r = await w.begin(); assert.equal(r.res.status, 200); assert.equal(w.sent.length, 1);
    assert.equal(row(w, 'SELECT COUNT(*) n FROM mail_enrollment_flows').n, 1);
    assert.equal(seen.length, 4); assert.ok(seen.every((c) => c.url === `${w.env.MAIL_CP_ISSUER}${PREFLIGHT_PATH}`));
    // the CP signature uses the Worker's own service key, with the flow id as install scope
    const { init, url } = seen[3];
    assert.equal(init.headers.get('x-mail-install-id'), 'flow1'); assert.equal(init.headers.get('x-mail-direction'), 'service-to-cp');
    assert.equal(init.headers.get('x-mail-key-id'), 'sk1');
    assert.equal(await verify({ request: new Request(url, { method: 'POST', headers: init.headers }), body: init.body,
      publicKey: w.service.publicKey, direction: 'service-to-cp', install_id: 'flow1', generation: 1, key_id: 'sk1',
      claimNonce: async () => true, now: w.time.now }), true);
    assert.equal(await sha256(bytes(new TextDecoder().decode(init.body))), init.headers.get('x-mail-digest'));
  } finally { w.restore(); }
});

test('with MAIL_CP_ISSUER absent the real client cannot be built: begin is 503 enrollment_config_required, no CP call', async () => {
  const seen = []; const w = await world({ fetcher: async () => { seen.push(1); return new Response('{"ok":true}'); },
    envOver: { MAIL_CP_ISSUER: undefined } });
  try {
    const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'enrollment_config_required' });
    assert.equal(seen.length, 0); assert.equal(w.spy.calls, 0); assert.equal(w.sent.length, 0);
  } finally { w.restore(); }
});
