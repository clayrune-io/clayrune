import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, sendValue, NOW } from './transport-support.js';
import PostalMime from 'postal-mime';
test('send constructs owner-only MIME after thread correlation is durable', async () => {
  let f;
  f = await fixture({ send: async (mail) => {
    assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM threads')).n, 1);
    assert.equal(mail.to, f.reg.owner_address); assert.equal(mail.from, f.env.MAIL_FROM);
    assert.match(mail.raw, /Reply-To: opaque1\+[a-f0-9]{32}@mail.example.net/);
    assert.doesNotMatch(mail.raw, /^(?:Cc|Bcc):/mi);
  } });
  const r = await f.http.fetch(await f.request(), f.env);
  assert.equal(r.status, 200); assert.equal((await r.json()).code, 'accepted');
  assert.equal((await f.ledger.first('SELECT * FROM intents')).state, 'accepted');
  const thread = await f.ledger.first('SELECT * FROM threads');
  assert.equal(thread.token_hash.length, 64); assert.equal(JSON.stringify(thread).includes(sendValue().reply_token), false);
});
for (const over of [ { to: 'stranger@example.com' }, { cc: 'stranger@example.com' }, { bcc: 'stranger@example.com' },
  { from: 'other@example.com' }, { callback_url: 'http://127.0.0.1/' }, { raw: 'MIME' }, { generation: 2 },
  { subject: 'x\r\nBcc: other@example.com' }, { subject: 'x'.repeat(201) }, { body: 'x'.repeat(16385) },
  { reply_token: 'short' }, { thread_id: '' }, { intent_id: '' }, { kind: 'approval' } ]) {
  test(`refuse outbound client override ${JSON.stringify(over).slice(0, 70)}`, async () => {
    const f = await fixture(); const res = await f.http.fetch(await f.request('/v1/mail/send', sendValue(over)), f.env);
    assert.equal(res.status, 400); assert.equal(f.sent.length, 0);
  });
}
test('freshly signed retry returns prior acceptance without sending again', async () => {
  const f = await fixture();
  for (let i = 0; i < 3; i++) assert.equal((await f.http.fetch(await f.request(), f.env)).status, 200);
  assert.equal(f.sent.length, 1);
  assert.equal((await f.http.fetch(await f.request('/v1/mail/send', sendValue({ body: 'changed' })), f.env)).status, 409);
});
test('possible provider acceptance then timeout is uncertain, never auto-resent', async () => {
  let calls = 0;
  const f = await fixture({ send: async () => { calls++; throw new Error('secret provider failure'); } });
  for (let i = 0; i < 2; i++) {
    const r = await f.http.fetch(await f.request(), f.env);
    assert.equal(r.status, 409); assert.equal((await r.json()).code, 'uncertain');
  }
  assert.equal(calls, 1); assert.equal((await f.ledger.first('SELECT * FROM intents')).state, 'uncertain');
});
test('racing send requests with same intent perform at most one provider write', async () => {
  const f = await fixture();
  const requests = await Promise.all(Array.from({ length: 6 }, () => f.request()));
  await Promise.all(requests.map((r) => f.http.fetch(r, f.env)));
  assert.equal(f.sent.length, 1);
});
test('token/thread collision rolls back intent, no second provider write', async () => {
  const f = await fixture(); await f.http.fetch(await f.request(), f.env);
  const r = await f.http.fetch(await f.request('/v1/mail/send', sendValue({ intent_id: 'intent2' })), f.env);
  assert.equal(r.status, 503); assert.equal(f.sent.length, 1);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM intents')).n, 1);
});
test('stale sending attempt becomes uncertain after crash window', async () => {
  const f = await fixture(); await f.http.fetch(await f.request(), f.env);
  await f.ledger.run("UPDATE intents SET state='sending',finished_at=NULL");
  await f.ledger.reconcile(NOW + 60_000);
  assert.equal((await f.ledger.first('SELECT * FROM intents')).state, 'uncertain');
  assert.equal((await f.http.fetch(await f.request(), f.env)).status, 409); assert.equal(f.sent.length, 1);
});
test('daily install send quota permits 20 and refuses 21st', async () => {
  const f = await fixture();
  for (let i = 0; i < 21; i++) {
    const v = sendValue({ intent_id: `i${i}`, thread_id: `t${i}`, reply_token: i.toString(16).padStart(32, '0') });
    const r = await f.http.fetch(await f.request('/v1/mail/send', v), f.env);
    assert.equal(r.status, i < 20 ? 200 : 429);
  }
  assert.equal(f.sent.length, 20);
});
test('maximum UTF-8 subject round-trips with each encoded word within 75 chars', async () => {
  const f = await fixture(); const subject = '🧱'.repeat(50);
  assert.equal((await f.http.fetch(await f.request('/v1/mail/send', sendValue({ subject })), f.env)).status, 200);
  const mail = f.sent[0].raw;
  for (const word of mail.match(/=\?UTF-8\?B\?[^?]+\?=/g)) assert.ok(word.length <= 75);
  assert.equal((await PostalMime.parse(mail)).subject, subject);
});
test('JSON-escaped valid 16 KiB body is accepted without a smaller HTTP limit', async () => {
  const f = await fixture(); const body = '\u0001'.repeat(16_384);
  assert.equal((await f.http.fetch(await f.request('/v1/mail/send', sendValue({ body })), f.env)).status, 200);
  assert.equal((await PostalMime.parse(f.sent[0].raw)).text.trimEnd(), body);
});
test('positive provider receipt can resolve a concurrently expired sending attempt', async () => {
  let f;
  f = await fixture({ send: async () => { await f.ledger.reconcile(NOW + 60_000); } });
  const response = await f.http.fetch(await f.request(), f.env);
  assert.equal((await response.json()).code, 'accepted');
  assert.equal((await f.ledger.first('SELECT * FROM intents')).state, 'accepted');
});
