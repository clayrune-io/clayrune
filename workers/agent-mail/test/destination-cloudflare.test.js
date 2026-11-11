import test from 'node:test';
import assert from 'node:assert/strict';
import { cloudflareDestinations } from '../destination-cloudflare.js';
const accountId = 'a'.repeat(32);
const pending = (email = 'owner@example.com') => ({ email, verified: null });
const page = (rows = [], extra = {}) => ({ success: true, result: rows,
  result_info: { page: 1, per_page: 50, total_count: rows.length, total_pages: 1, ...extra } });
const client = (fetcher, extra = {}) => cloudflareDestinations({ accountId, token: 'synthetic-token', fetcher, ...extra });

test('lists every account page and reads verified timestamp, never truthy boolean', async () => {
  const seen = [];
  const c = client(async (url, init) => {
    seen.push({ url, init });
    return Response.json(seen.length === 1 ? page([pending()], { total_pages: 2, total_count: 2 })
      : page([{ email: 'verified@example.com', verified: '2026-11-01T10:00:00Z' }], { page: 2, total_pages: 2, total_count: 2 }));
  });
  assert.deepEqual(await c.list(), [{ address: 'owner@example.com', verified: false }, { address: 'verified@example.com', verified: true }]);
  assert.equal(seen.length, 2);
  assert.equal(seen[1].url, `https://api.cloudflare.com/client/v4/accounts/${accountId}/email/routing/addresses?page=2&per_page=50`);
  assert.equal(seen[0].init.redirect, 'manual');
  assert.equal(seen[0].init.headers.Authorization, 'Bearer synthetic-token');
  assert.ok(seen[0].init.signal instanceof AbortSignal);
});

test('create sends only email and accepts the exact pending result', async () => {
  const c = client(async (url, init) => {
    assert.equal(url, `https://api.cloudflare.com/client/v4/accounts/${accountId}/email/routing/addresses`);
    assert.equal(init.method, 'POST'); assert.equal(init.body, '{"email":"owner@example.com"}');
    return Response.json({ success: true, result: pending() });
  });
  assert.deepEqual(await c.create('owner@example.com'), { address: 'owner@example.com', verified: false });
});

test('empty account with zero total pages is complete', async () => {
  assert.deepEqual(await client(async () => Response.json(page([], { total_pages: 0 }))).list(), []);
});

for (const [label, response] of [
  ['redirect', () => new Response('', { status: 302, headers: { Location: 'https://evil.example' } })],
  ['forbidden', () => new Response('synthetic-token', { status: 403 })],
  ['provider failure', () => Response.json({ success: false, errors: ['synthetic-token'] })],
  ['malformed JSON', () => new Response('{bad')],
  ['oversize', () => new Response('x'.repeat(131073))],
  ['missing pagination', () => Response.json({ success: true, result: [] })],
  ['truncated pages', () => Response.json(page([], { total_count: 2 }))],
  ['too many pages', () => Response.json(page([], { total_pages: 11 }))],
  ['boolean verified', () => Response.json(page([{ email: 'owner@example.com', verified: true }]))],
  ['bad timestamp', () => Response.json(page([{ email: 'owner@example.com', verified: 'yes' }]))],
  ['duplicate addresses', () => Response.json(page([pending(), pending()]))],
]) test(`${label}: fail closed with constant error`, async () => {
  await assert.rejects(client(async () => response()).list(), /^Error: destination_unavailable$/);
});

test('stalled response body is bounded and cancelled', async () => {
  let cancelled = false;
  const c = client(async () => new Response(new ReadableStream({ cancel() { cancelled = true; } })), { timeoutMs: 10 });
  await assert.rejects(c.list(), /destination_unavailable/); assert.equal(cancelled, true);
});

test('a mismatched create result and a transport exception disclose no remote text', async () => {
  await assert.rejects(client(async () => Response.json({ success: true, result: pending('other@example.com') })).create('owner@example.com'),
    /^Error: destination_unavailable$/);
  await assert.rejects(client(async () => { throw new Error('synthetic-token'); }).list(), /^Error: destination_unavailable$/);
});

test('account and secret are mandatory; account cannot inject a URL', () => {
  for (const config of [{}, { accountId: '../other', token: 't' }, { accountId, token: '' }]) {
    assert.throws(() => cloudflareDestinations(config), /provider_config_required/);
  }
});
