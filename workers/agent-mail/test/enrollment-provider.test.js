// Unit 7d-2e-2, decision E4a: the destination provider is a SEAM. No token, no live call, no fetch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createDestinationProvider, fakeDestinationReader, destinationProviderFor, DESTINATION_TOKEN_ENV } from '../enrollment-provider.js';

test('the seam names a read-only token secret and nothing else', () => {
  assert.equal(DESTINATION_TOKEN_ENV, 'MAIL_DESTINATION_READ_TOKEN');
});

test('no network, no fetch and no credential live in the provider module', () => {
  const src = readFileSync(new URL('../enrollment-provider.js', import.meta.url), 'utf8');
  const code = src.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
  assert.doesNotMatch(code, /\bfetch\s*\(|https?:\/\/|api\.cloudflare|Bearer|XMLHttpRequest|import\s*\(/);
  assert.doesNotMatch(code, /[A-Za-z0-9_-]{40,}/, 'no token-shaped literal');
});

test('only a strict boolean true verifies, and the answer carries the address the reader returned', async () => {
  const table = { 'ok@example.com': true, 'no@example.com': false };
  const p = createDestinationProvider({ reader: fakeDestinationReader(table), token: 't' });
  assert.deepEqual(await p('ok@example.com'), { address: 'ok@example.com', verified: true });
  assert.deepEqual(await p('no@example.com'), { address: 'no@example.com', verified: false });
  assert.deepEqual(await p('unknown@example.com'), { address: undefined, verified: false });
  for (const verified of ['true', 1, 'yes', {}, [], null, undefined]) {
    const widened = createDestinationProvider({ reader: async ({ address }) => ({ address, verified }), token: 't' });
    assert.equal((await widened('a@example.com')).verified, false, String(verified));
  }
  const nothing = createDestinationProvider({ reader: async () => null, token: 't' });
  assert.deepEqual(await nothing('a@example.com'), { address: undefined, verified: false });
});

test('the fake reader is an exact string match: case or whitespace variants do not verify', async () => {
  const r = fakeDestinationReader({ 'ok@example.com': true });
  for (const a of ['OK@example.com', ' ok@example.com', 'ok@example.com ', 'ok+x@example.com']) {
    assert.equal((await r({ address: a })).verified, false, a);
  }
});

test('the reader receives the address and the token, and nothing else', async () => {
  let seen;
  const p = createDestinationProvider({ reader: async (args) => { seen = args; return { address: args.address, verified: true }; }, token: 'tok' });
  await p('a@example.com'); assert.deepEqual(seen, { address: 'a@example.com', token: 'tok' });
});

test('a provider needs a reader function and a non-empty token', () => {
  for (const bad of [{}, { reader: async () => ({}) }, { reader: async () => ({}), token: '' }, { reader: async () => ({}), token: 5 },
    { reader: 'x', token: 't' }]) assert.throws(() => createDestinationProvider(bad), /provider_config_required/);
});

test('destinationProviderFor: injected provider wins; reader+token builds one; anything else is null, never a default', () => {
  const injected = async () => ({});
  assert.equal(destinationProviderFor({}, { provider: injected }), injected);
  assert.equal(typeof destinationProviderFor({ [DESTINATION_TOKEN_ENV]: 't' }, { reader: fakeDestinationReader({}) }), 'function');
  assert.equal(destinationProviderFor({ [DESTINATION_TOKEN_ENV]: 't' }), null, 'token without a reader (what production has today)');
  assert.equal(destinationProviderFor({}, { reader: fakeDestinationReader({}) }), null, 'reader without a token');
  assert.equal(destinationProviderFor({}), null); assert.equal(destinationProviderFor(undefined), null);
});
