// 7e-2p: the Worker serves a PRE-SIGNED manifest from config. It never signs and never holds a root key.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createWorker } from '../worker.js';
import { MANIFEST_PATH, MANIFEST_LIMIT, manifestHttp } from '../manifest-http.js';
import { fixture, keys } from './transport-support.js';
import { base64url, bytes } from '../signing.js';

const URL_ = `https://service.example.net${MANIFEST_PATH}`;
const pad = (n) => String(n).padStart(13, '1').slice(0, 13);
async function signedDocument(over = {}) {
  const root = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const service = await keys();
  const array = ['clayrune-mail-service-manifest', '1', 'https://mail.example.net', '7', pad(1), pad(2), 'mail-service-v1',
    service.publicKey, pad(1), pad(3), []];
  const sig = new Uint8Array(await crypto.subtle.sign('Ed25519', root.privateKey, bytes(JSON.stringify(array))));
  return { manifest: array, signature: base64url(sig), ...over };
}
const envWith = (raw, extra = {}) => ({ MAIL_ENABLED: 'true', MAIL_DB: null, MAIL_SERVICE_MANIFEST: raw, ...extra });
const get = (env, url = URL_, init = {}) => createWorker().fetch(new Request(url, { method: 'GET', ...init }), env);

test('serves the configured document verbatim, no-store, without touching storage', async () => {
  const raw = JSON.stringify(await signedDocument(), null, 1); // odd whitespace on purpose
  const res = await get(envWith(raw));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('cache-control'), 'no-store');
  assert.match(res.headers.get('content-type'), /^application\/json/);
  assert.equal(await res.text(), raw);
});
test('served body parses to exactly {manifest[11], signature[86 chars]} and fits the client limit', async () => {
  const raw = JSON.stringify(await signedDocument());
  const body = JSON.parse(await (await get(envWith(raw))).text());
  assert.deepEqual(Object.keys(body).sort(), ['manifest', 'signature']);
  assert.equal(body.manifest.length, 11);
  assert.equal(body.signature.length, 86);
  assert.ok(raw.length <= MANIFEST_LIMIT);
});
test('missing config -> 503 manifest_not_configured, no half document', async () => {
  for (const raw of [undefined, '', null, 5, {}]) {
    const res = await get(envWith(raw));
    assert.equal(res.status, 503);
    assert.deepEqual(await res.json(), { code: 'manifest_not_configured' });
    assert.equal(res.headers.get('cache-control'), 'no-store');
  }
});
test('malformed config -> 503 manifest_malformed', async () => {
  const good = await signedDocument();
  const bad = [
    '{not json', '[]', 'null', '"x"', '42', JSON.stringify({ manifest: good.manifest }),
    JSON.stringify({ signature: good.signature }),
    JSON.stringify({ ...good, extra: 1 }),
    JSON.stringify({ ...good, manifest: good.manifest.slice(0, 10) }),
    JSON.stringify({ ...good, manifest: [...good.manifest, 'x'] }),
    JSON.stringify({ ...good, manifest: [...good.manifest.slice(0, 3), 7, ...good.manifest.slice(4)] }),
    JSON.stringify({ ...good, manifest: [...good.manifest.slice(0, 3), '', ...good.manifest.slice(4)] }),
    JSON.stringify({ ...good, manifest: [...good.manifest.slice(0, 10), 'notalist'] }),
    JSON.stringify({ ...good, manifest: [...good.manifest.slice(0, 10), [1]] }),
    JSON.stringify({ ...good, signature: good.signature.slice(1) }),
    JSON.stringify({ ...good, signature: `${good.signature.slice(0, 85)}/` }),
    JSON.stringify({ ...good, signature: `${good.signature.slice(0, 85)}B` }), // non-zero trailing bits
    JSON.stringify({ ...good, signature: 5 }),
    JSON.stringify({ ...good, padding: 'x'.repeat(MANIFEST_LIMIT) }),
  ];
  for (const raw of bad) {
    const res = await get(envWith(raw));
    assert.equal(res.status, 503, raw.slice(0, 60));
    assert.deepEqual(await res.json(), { code: 'manifest_malformed' });
  }
});
test('oversize var is refused even if otherwise well formed', async () => {
  const good = await signedDocument();
  const raw = JSON.stringify(good) + ' '.repeat(MANIFEST_LIMIT);
  assert.equal((await get(envWith(raw))).status, 503);
});
test('GET only: every other method on the manifest path keeps the existing 404', async () => {
  const raw = JSON.stringify(await signedDocument());
  for (const method of ['POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']) {
    const res = await createWorker().fetch(new Request(URL_, { method }), envWith(raw));
    assert.equal(res.status, 404, method);
    if (method !== 'HEAD') assert.deepEqual(await res.json(), { code: 'not_found' });
  }
});
test('any query string is refused with the existing 404, never served', async () => {
  const raw = JSON.stringify(await signedDocument());
  for (const q of ['?', '?x=1', '?manifest=1', '?cache=bust']) {
    const res = await get(envWith(raw), URL_ + q);
    assert.equal(res.status, 404, q);
    assert.deepEqual(await res.json(), { code: 'not_found' });
  }
});
test('only the exact path is routed (case, trailing slash, prefix, suffix)', async () => {
  const raw = JSON.stringify(await signedDocument());
  for (const p of ['/v1/mail/manifest/', '/v1/mail/Manifest', '/v1/mail/manifest/x', '/v1/mail/manifests', '/manifest', '/v1/mail']) {
    assert.equal((await get(envWith(raw), `https://service.example.net${p}`)).status, 404, p);
  }
});
test('MAIL_ENABLED off still answers mail_disabled first (feature switch unchanged)', async () => {
  const raw = JSON.stringify(await signedDocument());
  const res = await get(envWith(raw, { MAIL_ENABLED: 'false' }));
  assert.equal(res.status, 503);
  assert.deepEqual(await res.json(), { code: 'mail_disabled' });
});
test('existing routes are unchanged: signed send works, GET send is 404, unsigned send is 401', async () => {
  const f = await fixture();
  f.env.MAIL_SERVICE_MANIFEST = JSON.stringify(await signedDocument());
  assert.equal((await f.worker.fetch(await f.request(), f.env)).status, 200);
  assert.equal((await f.worker.fetch(new Request('https://service.example.net/v1/mail/send'), f.env)).status, 404);
  const unsigned = new Request('https://service.example.net/v1/mail/send', { method: 'POST', body: '{}' });
  assert.equal((await f.worker.fetch(unsigned, f.env)).status, 401);
  assert.equal((await f.worker.fetch(new Request(URL_), f.env)).status, 200);
});
test('the module holds no signing or key material and makes no network call', () => {
  const src = readFileSync(new URL('../manifest-http.js', import.meta.url), 'utf8').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(src, /\b(sign|importKey|generateKey|PRIVATE_KEY)\b|\bfetch\(/);
  assert.equal(typeof manifestHttp, 'function');
});
