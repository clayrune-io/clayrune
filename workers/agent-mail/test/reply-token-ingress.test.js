// End to end through the Worker: outbound writes a plain Reply-To plus the token
// in References; a reply to that mail is queued, anything else is dropped.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, emailMessage } from './transport-support.js';
import { cfAr } from './fixtures.js';

async function sendOne() {
  const f = await fixture();
  const sent = await (await f.http.fetch(await f.request(), f.env)).json();
  const raw = f.sent[0].raw;
  return { f, sent, raw,
    replyTo: /^Reply-To: .*<(.*)>\r$/m.exec(raw)[1],
    references: /^References: (.*)\r$/m.exec(raw)[1] };
}

test('outbound mail has a plain Reply-To and a token id in References', async () => {
  const { raw, replyTo, references } = await sendOne();
  assert.equal(replyTo, 'opaque1@mail.example.net');
  assert.doesNotMatch(raw, /^Reply-To: .*\+/m);
  assert.match(references, /^<abcdef0123456789abcdef0123456789\.[a-f0-9]{32}@mail\.example\.net>$/);
});

test('a reply carrying our References id (plain recipient) is queued', async () => {
  const { f, sent, replyTo, references } = await sendOne();
  const message = emailMessage({ outId: sent.message_id, plain: true, references });
  message.to = replyTo;
  const result = await f.legacy.email(message, f.env);
  assert.equal(result.status, 'queued');
});

test('a mail-client reply chain (our id plus Cloudflare-assigned ids) is queued', async () => {
  const { f, sent, replyTo, references } = await sendOne();
  const chain = `<earlier@example.com> ${references} <assigned-by-cloudflare@mail.example.net>`;
  const message = emailMessage({ outId: sent.message_id, plain: true, references: chain });
  message.to = replyTo;
  assert.equal((await f.legacy.email(message, f.env)).status, 'queued');
});

test('mail to the plain address with no token id is dropped, never a new chat', async () => {
  const { f, sent, replyTo } = await sendOne();
  const message = emailMessage({ outId: sent.message_id, plain: true });
  message.to = replyTo;
  assert.deepEqual(await f.legacy.email(message, f.env), { status: 'rejected', reason: 'token_missing' });
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
});

test('a stranger replaying the token from our References is rejected as not the owner', async () => {
  const { f, sent, replyTo, references } = await sendOne();
  const ar = cfAr({ mailfrom: 'stranger@example.org', d: 'example.org', from: 'example.org' });
  const message = emailMessage({ outId: sent.message_id, plain: true, references,
    from: 'stranger@example.org', envelope: 'stranger@example.org', ar });
  message.to = replyTo;
  assert.deepEqual(await f.legacy.email(message, f.env), { status: 'rejected', reason: 'from_not_owner' });
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
});

test('a forged token id in References from the owner address with failed DMARC is rejected', async () => {
  const { f, sent, replyTo, references } = await sendOne();
  const ar = cfAr({ spf: 'fail', dkim: 'none', dmarc: 'fail' });
  const message = emailMessage({ outId: sent.message_id, plain: true, references, ar });
  message.to = replyTo;
  assert.deepEqual(await f.legacy.email(message, f.env), { status: 'rejected', reason: 'dmarc_not_pass' });
});

test('an unknown token id in References is dropped as token_unknown', async () => {
  const { f, sent, replyTo } = await sendOne();
  const message = emailMessage({ outId: sent.message_id, plain: true,
    references: `<${'0'.repeat(32)}.${'1'.repeat(32)}@mail.example.net>` });
  message.to = replyTo;
  assert.deepEqual(await f.legacy.email(message, f.env), { status: 'rejected', reason: 'token_unknown' });
});

test('legacy +token Reply-To mail already in flight is still read', async () => {
  const { f, sent } = await sendOne();
  const result = await f.legacy.email(emailMessage({ outId: sent.message_id }), f.env); // opaque1+<token>@
  assert.equal(result.status, 'queued');
});

test('+token and a different token in References disagree: dropped as token_conflict', async () => {
  const { f, sent } = await sendOne();
  const message = emailMessage({ outId: sent.message_id,
    references: `<${'0'.repeat(32)}.${'1'.repeat(32)}@mail.example.net>` });
  assert.deepEqual(await f.legacy.email(message, f.env), { status: 'rejected', reason: 'token_conflict' });
});
