import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { decideMachineRequest, isMachineRequest, stripForProxy, filterOriginResponse, MACHINE_HEADER,
  MAX_BODY_BYTES, MAX_RESPONSE_BYTES } from '../edge-policy.js';
import { ROUTE_PATH } from '../delivery-capability.js';

// Python-signed vectors (control_plane.tests.delivery_vectors): no key material is generated here.
const V = JSON.parse(readFileSync(new URL('./fixtures/delivery-vectors.json', import.meta.url), 'utf8'));
const trust = { issuer: V.issuer, jwksUrl: V.jwks_url, jwks: V.jwks };
const vector = (name) => V.vectors.find((v) => v.name === name);
const mail = vector('valid mail capability');
const proof = vector('valid proof capability');

function request(v = mail, over = {}) {
  const h = v.request.headers;
  const headers = new Headers({ 'content-length': '100', 'content-type': 'application/json',
    'x-mail-install-id': h.install_id, 'x-mail-generation': h.generation, 'x-mail-nonce': h.nonce,
    'x-mail-digest': h.digest, 'x-mail-key-id': h.key_id, 'x-mail-direction': h.direction,
    [MACHINE_HEADER]: v.token, ...(over.headers ?? {}) });
  for (const name of over.drop ?? []) headers.delete(name);
  return { method: 'POST', host: v.request.host, target: ROUTE_PATH, trust, isDenied: async () => false,
    clock: () => v.now_ms, ...over, headers };
}
const decide = (v, over) => decideMachineRequest(request(v, over));
const refused = (out, status, code) => { assert.deepEqual(out, { allow: false, status, code }); };

test('a valid mail capability is allowed and returns its claims', async () => {
  const out = await decide(mail);
  assert.equal(out.allow, true);
  assert.equal(out.claims.mode, 'mail'); assert.equal(out.claims.install_id, 'install1');
});

test('a valid proof capability is allowed only with the proof direction', async () => {
  assert.equal((await decide(proof)).allow, true);
  refused(await decide(proof, { headers: { 'x-mail-direction': 'service-to-install' } }), 403, 'capability_request_mismatch');
  refused(await decide(mail, { headers: { 'x-mail-direction': 'service-to-install-proof' } }), 403, 'capability_request_mismatch');
});

test('branch selection is header presence alone', () => {
  assert.equal(isMachineRequest(new Headers({ [MACHINE_HEADER]: 'x' })), true);
  assert.equal(isMachineRequest(new Headers({ 'X-Clayrune-Mail-Delivery': '' })), true);
  assert.equal(isMachineRequest(new Headers({ cookie: 'cr_session=1', authorization: 'Bearer x' })), false);
});

test('only POST is accepted', async () => {
  for (const method of ['GET', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS', 'CONNECT', 'post', undefined]) {
    refused(await decide(mail, { method }), 400, 'bad_method');
  }
});

// Raw target: byte-identical to the ingress path, evaluated before any decode or normalisation.
const ALIASES = ['/api/agent-mail/ingress/', '/api/agent-mail/ingress?x=1', '/api/agent-mail/ingress?', '/api/agent-mail/ingress#f',
  '/api/agent-mail/ingress;a=b', '/api/agent-mail/ingress\\', '/api/agent-mail\\ingress', '//api/agent-mail/ingress',
  '/api//agent-mail/ingress', '/api/./agent-mail/ingress', '/api/agent-mail/../agent-mail/ingress',
  '/api/agent-mail/ingress/.', '/API/agent-mail/ingress', '/api/agent-mail/Ingress', '/api/agent%2dmail/ingress',
  '/api/agent-mail/ingress%2f', '/api/agent-mail/ingress%00', '/api/agent-mail/ingres%73', '/%61pi/agent-mail/ingress',
  '/api/agent-mail/ingress%3f', '/api/agent-mail/ingress%23', '/api/agent-mail/ingress%5c', '/api/agent-mail%2fingress',
  '/api/agent-mail/ingressé', '/api/agent-mail/ingrеss', '/api/agent-mail/ingress ', ' /api/agent-mail/ingress',
  '/api/agent-mail/ingress\n', 'api/agent-mail/ingress', 'https://device.example.com/api/agent-mail/ingress', '*', '', '/'];
for (const target of ALIASES) {
  test(`raw target alias ${JSON.stringify(target)} is refused`, async () => {
    refused(await decide(mail, { target }), 400, 'bad_target');
  });
}

for (const [name, host] of [['port', 'device.example.com:8443'], ['userinfo', 'user@device.example.com'],
  ['ip literal', '127.0.0.1'], ['ipv6', '[::1]'], ['single label', 'localhost'], ['empty', ''], ['uppercase', 'Device.Example.com'],
  ['trailing dot', 'device.example.com.'], ['underscore', 'dev_ice.example.com'], ['space', 'device.example.com '],
  ['path', 'device.example.com/x'], ['non-string', undefined], ['oversize', `${'a'.repeat(250)}.example.com`]]) {
  test(`host with ${name} is refused`, async () => {
    refused(await decide(mail, { host }), 400, 'bad_host');
  });
}

test('a different, well-formed host is refused by the capability binding', async () => {
  refused(await decide(mail, { host: 'other.example.com' }), 403, 'capability_request_mismatch');
});

for (const [name, headers, drop] of [
  ['missing content-length', {}, ['content-length']],
  ['non-numeric content-length', { 'content-length': 'abc' }],
  ['negative content-length', { 'content-length': '-1' }],
  ['leading-zero content-length', { 'content-length': '0100' }],
  ['signed content-length', { 'content-length': '+5' }],
  ['hex content-length', { 'content-length': '0x10' }],
  ['list content-length', { 'content-length': '5, 5' }],
  ['decimal content-length', { 'content-length': '5.0' }],
  ['empty content-length', { 'content-length': '' }],
  ['eight-digit content-length', { 'content-length': '12345678' }],
]) {
  test(`${name} is refused`, async () => {
    refused(await decide(mail, { headers, drop }), 400, 'bad_content_length');
  });
}

test('the body limit is exactly MAX_BODY_BYTES', async () => {
  assert.equal(MAX_BODY_BYTES, 131072);
  assert.equal((await decide(mail, { headers: { 'content-length': String(MAX_BODY_BYTES) } })).allow, true);
  refused(await decide(mail, { headers: { 'content-length': String(MAX_BODY_BYTES + 1) } }), 400, 'body_limit');
  assert.equal((await decide(mail, { headers: { 'content-length': '0' } })).allow, true);
});

test('chunked, transfer-encoding and expect are refused', async () => {
  refused(await decide(mail, { headers: { 'transfer-encoding': 'chunked' } }), 400, 'bad_transfer_encoding');
  refused(await decide(mail, { headers: { 'transfer-encoding': 'identity' } }), 400, 'bad_transfer_encoding');
  refused(await decide(mail, { headers: { expect: '100-continue' } }), 400, 'bad_expect');
});

for (const [name, value, drop] of [['missing', undefined, ['content-type']], ['charset suffix', 'application/json; charset=utf-8'],
  ['upper case', 'Application/JSON'], ['text', 'text/plain'], ['form', 'application/x-www-form-urlencoded'],
  ['json variant', 'application/json5'], ['empty', ''], ['multipart', 'multipart/form-data; boundary=x']]) {
  test(`content type ${name} is refused`, async () => {
    refused(await decide(mail, { headers: value === undefined ? {} : { 'content-type': value }, drop }), 400, 'bad_content_type');
  });
}

for (const [name, headers] of [['Upgrade header', { upgrade: 'websocket' }],
  ['Connection: upgrade', { connection: 'Upgrade' }], ['Connection list', { connection: 'keep-alive, UPGRADE' }],
  ['websocket key', { 'sec-websocket-key': 'dGhlIHNhbXBsZSBub25jZQ==' }],
  ['websocket version', { 'sec-websocket-version': '13' }], ['websocket protocol', { 'Sec-WebSocket-Protocol': 'chat' }],
  ['h2c upgrade', { upgrade: 'h2c', connection: 'Upgrade, HTTP2-Settings', 'http2-settings': 'AAMAAABk' }]]) {
  test(`${name} is refused`, async () => {
    refused(await decide(mail, { headers }), 400, 'upgrade_refused');
  });
}

test('keep-alive without an upgrade token is allowed', async () => {
  assert.equal((await decide(mail, { headers: { connection: 'keep-alive' } })).allow, true);
});

test('a cookie beside a capability is refused, never a fallback', async () => {
  refused(await decide(mail, { headers: { cookie: 'cr_session=abc' } }), 400, 'two_credentials');
  refused(await decide(mail, { headers: { cookie: '' } }), 400, 'two_credentials');
});

for (const [name, header, value] of [['install', 'x-mail-install-id', 'install2'], ['generation', 'x-mail-generation', '2'],
  ['nonce', 'x-mail-nonce', 'ff'.repeat(16)], ['digest', 'x-mail-digest', 'ee'.repeat(32)], ['key id', 'x-mail-key-id', 'mail2']]) {
  test(`cross-${name}: header differing from the claim is refused`, async () => {
    refused(await decide(mail, { headers: { [header]: value } }), 403, 'capability_request_mismatch');
  });
  test(`missing ${name} header is refused`, async () => {
    refused(await decide(mail, { drop: [header] }), 403, 'capability_request_mismatch');
  });
}

test('an unknown direction is refused', async () => {
  for (const d of ['install-to-service', 'service-to-cp', 'service-to-install-response', 'bogus', '']) {
    refused(await decide(mail, { headers: { 'x-mail-direction': d } }), 403, 'capability_request_mismatch');
  }
});

test('every refusal in the shared vectors maps to a bare 401/403 or 503, never to allow', async () => {
  for (const v of V.vectors) {
    if (v.expect === 'ok') continue;
    const out = await decideMachineRequest({ ...request(v), headers: new Headers({ 'content-length': '5',
      'content-type': 'application/json', [MACHINE_HEADER]: v.token, 'x-mail-install-id': v.request.headers?.install_id ?? '',
      'x-mail-generation': v.request.headers?.generation ?? '', 'x-mail-nonce': v.request.headers?.nonce ?? '',
      'x-mail-digest': v.request.headers?.digest ?? '', 'x-mail-key-id': v.request.headers?.key_id ?? '',
      'x-mail-direction': v.request.headers?.direction ?? '' }), method: v.request.method, host: v.request.host,
    target: v.request.path });
    assert.equal(out.allow, false, v.name);
    assert.deepEqual(Object.keys(out).sort(), ['allow', 'code', 'status'], v.name);
    assert.ok([400, 401, 403].includes(out.status), `${v.name}: ${out.status}`);
  }
});

test('an expired or not-yet-valid capability is a 401', async () => {
  const exp = vector('expired');
  refused(await decideMachineRequest({ ...request(exp), clock: () => exp.now_ms }), 401, 'capability_expired');
  const future = vector('issued in the future');
  refused(await decideMachineRequest({ ...request(future), clock: () => future.now_ms }), 401, 'capability_not_yet_valid');
  assert.equal((await decide(vector('one ms before expiry'), { clock: () => vector('one ms before expiry').now_ms })).allow, true);
});

test('a forged, wrongly keyed or malformed token is a 401', async () => {
  for (const [name, code] of [['signature bit flipped', 'bad_capability_signature'], ['unknown kid', 'unknown_capability_key'],
    ['alg none', 'bad_capability_header'], ['wrong issuer', 'bad_capability_claims'], ['two segments', 'bad_capability']]) {
    const v = vector(name);
    refused(await decideMachineRequest({ ...request(mail), headers: request(mail, { headers: { [MACHINE_HEADER]: v.token } }).headers,
      clock: () => v.now_ms }), 401, code);
  }
});

test('missing edge trust is a 503, not an allow and not a fallback to the cookie flow', async () => {
  refused(await decide(mail, { trust: undefined }), 503, 'capability_trust_required');
  refused(await decide(mail, { trust: { ...trust, jwks: {} } }), 503, 'capability_trust_required');
  refused(await decide(mail, { trust: { ...trust, issuer: 'not a url' } }), 503, 'capability_trust_required');
});

test('the denylist is read for the verified subject and fails closed', async () => {
  const seen = [];
  const out = await decide(mail, { isDenied: async (sub) => { seen.push(sub); return false; } });
  assert.equal(out.allow, true); assert.deepEqual(seen, ['account1']);
  refused(await decide(mail, { isDenied: async () => true }), 403, 'user_denied');
  for (const odd of [undefined, null, 0, '', 'false', 1]) {
    refused(await decide(mail, { isDenied: async () => odd }), 403, 'user_denied');
  }
  refused(await decide(mail, { isDenied: async () => { throw new Error('kv down'); } }), 503, 'denylist_unavailable');
  refused(await decide(mail, { isDenied: () => Promise.reject(new Error('kv down')) }), 503, 'denylist_unavailable');
  refused(await decide(mail, { isDenied: undefined }), 503, 'denylist_unavailable');
  refused(await decide(mail, { isDenied: 'yes' }), 503, 'denylist_unavailable');
});

test('the denylist is not consulted for a request that already failed the allow-list', async () => {
  let reads = 0;
  const isDenied = async () => { reads++; return false; };
  await decide(mail, { isDenied, method: 'GET' });
  await decide(mail, { isDenied, target: '/api/agent-mail/ingress/' });
  await decide(mail, { isDenied, headers: { cookie: 'a=b' } });
  await decide(mail, { isDenied, headers: { 'x-mail-nonce': 'ff'.repeat(16) } });
  assert.equal(reads, 0);
});

test('a refusal never echoes a token, a header or an error message', async () => {
  const outs = [await decide(mail, { headers: { 'x-mail-nonce': 'ff'.repeat(16) } }), await decide(mail, { method: 'GET' }),
    await decide(mail, { isDenied: async () => { throw new Error('secret kv detail'); } })];
  for (const out of outs) assert.equal(/secret|ey[A-Za-z0-9]{10}|account1/.test(JSON.stringify(out)), false);
});

test('stripForProxy removes every credential and identity assertion and keeps the rest', () => {
  const out = stripForProxy(new Headers({ [MACHINE_HEADER]: 't', cookie: 'a=b', Authorization: 'Bearer x', 'x-clayrune-user': 'ron',
    'X-Clayrune-Anything': '1', 'cf-access-jwt-assertion': 'j', 'Cf-Access-Client-Id': 'c', 'content-type': 'application/json',
    'x-mail-signature': 's', 'content-length': '5' }));
  assert.deepEqual([...out.keys()].sort(), ['content-length', 'content-type', 'x-mail-signature']);
  assert.equal([...out.keys()].some((k) => /^(x-clayrune-|cf-access-)/.test(k)), false);
});

test('an allowed request returns headers already stripped and adds no identity header', async () => {
  const out = await decide(mail, { headers: { authorization: 'Bearer x', 'cf-access-client-id': 'c', 'x-clayrune-user': 'ron' } });
  assert.equal(out.allow, true);
  for (const name of [MACHINE_HEADER, 'authorization', 'cf-access-client-id', 'x-clayrune-user', 'cookie']) {
    assert.equal(out.headers.has(name), false, name);
  }
  assert.equal(out.headers.get('x-mail-nonce'), mail.request.headers.nonce);
  assert.equal([...out.headers.keys()].some((k) => /user|account|sub|identity/.test(k)), false);
});

// Origin response filtering: a login page must never look like a receipt.
const body = (text) => new Response(text, { status: 200, headers: { 'content-type': 'application/json' } });
test('a 3xx from the origin becomes a 502', async () => {
  for (const status of [301, 302, 303, 307, 308]) {
    const out = await filterOriginResponse(new Response('', { status, headers: { location: 'https://login.example/' } }));
    assert.equal(out.status, 502); assert.deepEqual(await out.json(), { code: 'origin_redirect' });
    assert.equal(out.headers.get('location'), null);
  }
  const opaque = { type: 'opaqueredirect', status: 0, headers: new Headers(), body: null };
  assert.equal((await filterOriginResponse(opaque)).status, 502);
});

test('a status outside 200-599 is a 502', async () => {
  for (const status of [0, 100, 199, 600, 1.5, NaN, undefined]) {
    const out = await filterOriginResponse({ type: 'basic', status, headers: new Headers(), body: null });
    assert.equal(out.status, 502); assert.deepEqual(await out.json(), { code: 'origin_bad_status' });
  }
});

test('Set-Cookie is dropped and the body, status and content type pass through', async () => {
  const original = new Response(JSON.stringify({ custody: 'durable' }), { status: 200,
    headers: { 'content-type': 'application/json', 'set-cookie': 'cr_session=1; Path=/', 'x-clayrune-user': 'ron', server: 'x' } });
  const out = await filterOriginResponse(original);
  assert.equal(out.status, 200); assert.equal(out.headers.get('set-cookie'), null);
  assert.equal(out.headers.get('x-clayrune-user'), null); assert.equal(out.headers.get('server'), null);
  assert.equal(out.headers.get('cache-control'), 'no-store'); assert.equal(out.headers.get('content-type'), 'application/json');
  assert.deepEqual(await out.json(), { custody: 'durable' });
});

test('a non-200 origin status is preserved with its body, not rewritten', async () => {
  const out = await filterOriginResponse(new Response('{"error":"vault_locked"}', { status: 503,
    headers: { 'content-type': 'application/json' } }));
  assert.equal(out.status, 503); assert.deepEqual(await out.json(), { error: 'vault_locked' });
});

test('an empty body stays empty', async () => {
  const out = await filterOriginResponse(new Response(null, { status: 204 }));
  assert.equal(out.status, 204); assert.equal((await out.arrayBuffer()).byteLength, 0);
});

test('the response is bounded at MAX_RESPONSE_BYTES', async () => {
  assert.equal(MAX_RESPONSE_BYTES, 4096);
  const exact = await filterOriginResponse(body('x'.repeat(MAX_RESPONSE_BYTES)));
  assert.equal(exact.status, 200); assert.equal((await exact.arrayBuffer()).byteLength, MAX_RESPONSE_BYTES);
  const over = await filterOriginResponse(body('x'.repeat(MAX_RESPONSE_BYTES + 1)));
  assert.equal(over.status, 502); assert.deepEqual(await over.json(), { code: 'origin_response_too_large' });
});

test('an oversize streamed body is cancelled, not buffered to the end', async () => {
  let cancelled = false; let pulls = 0;
  const stream = new ReadableStream({ pull(c) { pulls++; c.enqueue(new Uint8Array(1500)); }, cancel() { cancelled = true; } });
  const out = await filterOriginResponse(new Response(stream, { status: 200 }));
  assert.equal(out.status, 502); assert.equal(cancelled, true); assert.ok(pulls <= 4, `pulled ${pulls}`);
});

test('an unreadable origin body is a 502, never a pass-through', async () => {
  const stream = new ReadableStream({ pull(c) { c.error(new Error('reset')); } });
  const out = await filterOriginResponse(new Response(stream, { status: 200 }));
  assert.equal(out.status, 502); assert.deepEqual(await out.json(), { code: 'origin_unreadable' });
});
