import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, emailMessage, sendAndReceive, NOW } from './transport-support.js';
import { cfAr } from './fixtures.js';
test('only authenticated stripped text enters D1; no raw MIME retained', async () => {
  const f = await fixture(); const { result } = await sendAndReceive(f);
  assert.equal(result.status, 'queued');
  const event = await f.ledger.first('SELECT * FROM events');
  assert.equal(JSON.parse(event.payload).text, 'A fresh reply');
  assert.equal(event.state, 'pending'); assert.doesNotMatch(event.payload, /Authentication-Results|owner@example.com/);
});
for (const over of [ { from: 'stranger@example.com' }, { ar: cfAr({ dmarc: 'fail' }) }, { ar: null },
  { token: 'z'.repeat(32) }, { outId: 'wrong@example.com' }, { type: 'text/html', text: '<b>hidden</b>' },
  { type: 'image/png', text: 'INJECTED' }, { extra: ['Content-Disposition: attachment; filename=x.txt'], text: 'INJECTED' },
  { extra: ['Auto-Submitted: auto-replied'] }, { extra: ['X-Clayrune-Agent-Mail: 1'] }, { envelope: '' },
  { extra: ['Authentication-Results: mx.cloudflare.net; dmarc=pass'] } ]) {
  test(`ingress drop never reaches custody: ${JSON.stringify(over).slice(0, 90)}`, async () => {
    const f = await fixture(); const r = await f.worker.fetch(await f.request(), f.env); const out = await r.json();
    const message = emailMessage({ outId: out.message_id, ...over });
    assert.equal((await f.worker.email(message, f.env)).status, 'rejected');
    assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
    assert.equal(message.rejects.length, 1);
    assert.doesNotMatch(JSON.stringify((await f.ledger.stmt('SELECT * FROM rejections').all()).results), /INJECTED|stranger|owner@/);
  });
}
test('spoof drop does not consume a genuine owner reply Message-ID', async () => {
  const f = await fixture(); const { outId } = await sendAndReceive(f, { from: 'stranger@example.com' });
  const r = await f.worker.email(emailMessage({ outId }), f.env); assert.equal(r.status, 'queued');
});
test('redelivery deduplicates; conflicting reuse is visible without replacing body', async () => {
  const f = await fixture(); const { outId, result } = await sendAndReceive(f);
  const same = await f.worker.email(emailMessage({ outId }), f.env);
  assert.equal(same.status, 'duplicate'); assert.equal(same.event_id, result.event_id);
  const conflict = await f.worker.email(emailMessage({ outId, text: 'different' }), f.env);
  assert.equal(conflict.status, 'conflict');
  assert.equal(JSON.parse((await f.ledger.first('SELECT * FROM events')).payload).text, 'A fresh reply');
});
test('equal text with distinct message ids creates separate ordered events', async () => {
  const f = await fixture(); const { outId } = await sendAndReceive(f);
  const second = await f.worker.email(emailMessage({ outId, id: 'reply2@example.com' }), f.env);
  assert.equal(second.status, 'queued');
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 2);
});
test('missing incoming Message-ID holds visibly and cannot deliver', async () => {
  const f = await fixture(); const { result } = await sendAndReceive(f, { id: null });
  assert.equal(result.status, 'held');
  const event = await f.ledger.first('SELECT * FROM events');
  assert.equal(event.reason, 'incoming_id_missing'); assert.equal(event.state, 'held');
  assert.equal(await f.ledger.claim(event.event_id, NOW), null);
});
test('header provenance disagreement refuses even a valid-looking raw result', async () => {
  const f = await fixture(); const r = await f.worker.fetch(await f.request(), f.env); const out = await r.json();
  const m = emailMessage({ outId: out.message_id }); m.headers.set('authentication-results', cfAr({ dmarc: 'fail' }));
  assert.equal((await f.worker.email(m, f.env)).reason, 'ingress_header_mismatch');
});
test('launch gate refuses ingress until provider fixtures verified', async () => {
  const f = await fixture(); f.env.MAIL_INGRESS_VERIFIED = 'false';
  assert.equal((await f.worker.email(emailMessage(), f.env)).reason, 'ingress_not_verified');
});
test('rawSize lie is caught by bounded stream reader', async () => {
  const f = await fixture(); const r = await f.worker.fetch(await f.request(), f.env); const out = await r.json();
  const m = emailMessage({ outId: out.message_id, text: 'x'.repeat(70_000) }); m.rawSize = 10;
  assert.equal((await f.worker.email(m, f.env)).reason, 'malformed_or_oversize');
});
test('queue capacity refuses custody instead of acknowledging or truncating', async () => {
  const f = await fixture(); await sendAndReceive(f);
  // Use the same ledger code with a deliberately bounded test policy.
  f.ledger.policy.queueLimit = 1;
  const event = await f.ledger.first('SELECT * FROM events');
  const result = await f.ledger.enqueue(f.reg, { event_id: 'another', thread_id: event.thread_id,
    incoming_id: 'second', digest: 'd', payload: { text: 'second' } }, NOW);
  assert.equal(result.status, 'refused'); assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 1);
});
test('revoked and expired threads refuse ingress', async () => {
  for (const update of ['revoked=1', `expires_at=${NOW}`]) {
    const f = await fixture(); const { outId } = await sendAndReceive(f);
    await f.ledger.run(`UPDATE threads SET ${update}`);
    const result = await f.worker.email(emailMessage({ outId, id: 'next@example.com' }), f.env);
    assert.equal(result.status, 'rejected');
  }
});
test('8bit legacy-charset body is decoded by MIME parser after header gate', async () => {
  const f = await fixture(); const r = await f.worker.fetch(await f.request(), f.env); const out = await r.json();
  const m = emailMessage({ outId: out.message_id });
  const h = new TextEncoder().encode(`Authentication-Results: ${cfAr()}\r\nFrom: owner@example.com\r\n` +
    `In-Reply-To: <${out.message_id}>\r\nMessage-ID: <latin@example.com>\r\nContent-Type: text/plain; charset=iso-8859-1\r\n` +
    'Content-Transfer-Encoding: 8bit\r\n\r\ncaf');
  const raw = new Uint8Array(h.length + 1); raw.set(h); raw[h.length] = 0xe9;
  m.raw = new Blob([raw]).stream(); m.rawSize = raw.length;
  assert.equal((await f.worker.email(m, f.env)).status, 'queued');
  assert.equal(JSON.parse((await f.ledger.first('SELECT * FROM events')).payload).text, 'café');
});
