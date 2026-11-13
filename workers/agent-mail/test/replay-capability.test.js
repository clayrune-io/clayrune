import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, sendAndReceive, NOW } from './transport-support.js';
import { replay } from '../replay.js';
import { MACHINE_HEADER } from '../edge-policy.js';

const receipt = (p) => Response.json({ custody: 'durable', event_id: p.event_id, body_digest: p.body_digest,
  install_id: p.install_id, generation: p.generation });
async function pending(options = {}) {
  const calls = [];
  const f = await fixture({ fetcher: async (url, init) => { calls.push({ url, ...init });
    return receipt(JSON.parse(new TextDecoder().decode(init.body))); }, ...options });
  await sendAndReceive(f);
  return { f, calls };
}
const run = (f, cp, over = {}) => replay({ ledger: f.ledger, config: f.config, now: NOW, cp, ...over });

test('without cp the delivery carries no capability header (behaviour unchanged)', async () => {
  const { f, calls } = await pending();
  await f.legacy.scheduled({}, f.env);
  assert.equal(calls.length, 1); assert.equal(calls[0].headers.has(MACHINE_HEADER), false);
});

test('with cp the mail-mode capability bound to the signed request is attached', async () => {
  const { f, calls } = await pending();
  const seen = [];
  const cp = { issue: async (v) => { seen.push(v); return { ok: true, body: { capability: 'header.claims.sig' } }; } };
  await run(f, cp, { fetcher: async (url, init) => { calls.push({ url, ...init }); return receipt(JSON.parse(new TextDecoder().decode(init.body))); } });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].headers.get(MACHINE_HEADER), 'header.claims.sig');
  assert.equal(calls[0].headers.get('x-mail-direction'), 'service-to-install');
  assert.equal(seen.length, 1);
  assert.deepEqual({ ...seen[0] }, { install_id: 'install1', generation: 1, mode: 'mail',
    req_digest: calls[0].headers.get('x-mail-digest'), mail_nonce: calls[0].headers.get('x-mail-nonce'), mail_key_id: 'ik1' });
  assert.equal((await f.ledger.first('SELECT * FROM events')).payload, null);
});

for (const [name, issue] of [['a CP refusal', async () => ({ ok: false, code: 'grant_revoked' })],
  ['an empty capability', async () => ({ ok: true, body: { capability: '' } })],
  ['a missing capability', async () => ({ ok: true, body: {} })],
  ['a non-string capability', async () => ({ ok: true, body: { capability: 5 } })],
  ['a thrown error', async () => { throw new Error('boom'); }]]) {
  test(`${name} sends nothing and keeps D1 custody for retry`, async () => {
    const { f, calls } = await pending();
    await run(f, { issue }, { fetcher: async (url, init) => { calls.push({ url, ...init }); return receipt({}); } });
    assert.equal(calls.length, 0);
    const event = await f.ledger.first('SELECT * FROM events');
    assert.equal(event.state, 'pending'); assert.ok(event.payload);
    assert.equal(event.reason, 'capability_unavailable'); assert.ok(event.next_attempt > NOW);
  });
}
