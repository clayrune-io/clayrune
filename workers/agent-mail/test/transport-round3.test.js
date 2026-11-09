import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, emailMessage, NOW } from './transport-support.js';
import { cfAr } from './fixtures.js';

// Fenn's round-2 regression-probes.test.mjs:8 and adjacent positive control.
// Only imports and the now-absent protected bucket's diagnostic are adapted;
// the public-handler attack, 200 messages and genuine-reply assertion are kept.
test('P2 unauthenticated mail cannot spend the owner reply allowance', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  // Attacker knows the opaque install address only. No real token, outbound
  // Message-ID or owner identity is supplied. DMARC authenticates the stranger's
  // own domain, a normal Cloudflare ingress shape, never the registered owner.
  for (let i = 0; i < 200; i++) {
    const spoof = emailMessage({ from: 'stranger@example.org', envelope: 'stranger@example.org',
      token: '0'.repeat(32), outId: 'unknown@example.org',
      ar: cfAr({ from: 'example.org', mailfrom: 'stranger@example.org', d: 'example.org' }),
      id: `spam${i}@example.org` });
    const result = await f.worker.email(spoof, f.env);
    assert.equal(result.status, 'rejected');
    assert.equal(result.reason, 'from_not_owner');
  }
  const bucket = await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'");
  const message = emailMessage({ outId: sent.message_id });
  const result = await f.worker.email(message, f.env);
  const events = (await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n;
  console.log('forged inbound exhausts owner', { count: bucket?.count ?? 0, result, rejects: message.rejects, events });
  assert.equal(result.status, 'queued', '200 unauthenticated strangers must not permanently reject the genuine reply');
  assert.equal(bucket, null);
  assert.deepEqual(message.rejects, []);
  assert.equal(events, 1);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 200);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'")).count, 1);
});

test('control: same valid reply queues on an unexhausted install', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  const message = emailMessage({ outId: sent.message_id });
  assert.equal((await f.worker.email(message, f.env)).status, 'queued');
  assert.deepEqual(message.rejects, []);
});

test('authenticated owner attempts still exhaust their own allowance', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  for (let i = 0; i < 200; i++) {
    // Genuine redeliveries keep the custody queue below its independent cap.
    const message = emailMessage({ outId: sent.message_id });
    assert.equal((await f.worker.email(message, f.env)).status, i === 0 ? 'queued' : 'duplicate');
    assert.deepEqual(message.rejects, []);
  }
  const message = emailMessage({ outId: sent.message_id, id: 'overquota@example.com' });
  assert.deepEqual(await f.worker.email(message, f.env), { status: 'rejected', reason: 'ingress_limit' });
  assert.deepEqual(message.rejects, ['Agent mail refused']);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 1);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'")).count, 200);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 1);
  assert.equal((await f.ledger.first("SELECT COUNT(*) AS n FROM rejections WHERE reason='ingress_limit'")).n, 1);
});

test('saturated refusal accounting neither changes refusal reasons nor gates the owner', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  for (let i = 0; i < 200; i++) assert.equal(await f.ledger.limit('ingress-refused:install1', 200, NOW), true);
  const spoof = emailMessage({ from: 'stranger@example.com', outId: sent.message_id });
  assert.equal((await f.worker.email(spoof, f.env)).reason, 'from_not_owner');
  assert.deepEqual(spoof.rejects, ['Agent mail refused']);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 1);
  const message = emailMessage({ outId: sent.message_id });
  assert.equal((await f.worker.email(message, f.env)).status, 'queued');
  assert.deepEqual(message.rejects, []);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 200);
});

for (const [over, reason, protectedCount] of [
  [{ ar: cfAr({ dmarc: 'fail' }) }, 'dmarc_not_pass', 0],
  [{ token: '0'.repeat(32) }, 'token_unknown', 0],
  [{ outId: 'unknown@example.org' }, 'message_id_mismatch', 0],
  [{ ar: null }, 'ingress_header_mismatch', 0],
  [{ extra: ['Auto-Submitted: auto-replied'] }, 'loop_or_bounce', 0],
  [{ type: 'text/html', text: '<b>hidden</b>' }, 'html_only', 1],
  [{ text: '' }, 'no_plain_text', 1],
]) {
  test(`post-lookup ${reason} refusal has separate accounting`, async () => {
    const f = await fixture();
    const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
    const message = emailMessage({ outId: sent.message_id, ...over });
    assert.deepEqual(await f.worker.email(message, f.env), { status: 'rejected', reason });
    assert.deepEqual(message.rejects, ['Agent mail refused']);
    assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
    assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 1);
    const bucket = await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'");
    assert.equal(bucket?.count ?? 0, protectedCount);
  });
}

test('malformed stream after recipient lookup is counted separately', async () => {
  const f = await fixture();
  const message = emailMessage();
  message.raw = new Blob(['no header delimiter']).stream();
  assert.equal((await f.worker.email(message, f.env)).reason, 'malformed_or_oversize');
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 1);
  assert.equal(await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'"), null);
});

test('inactive known recipient refusals count without creating owner allowance', async () => {
  const f = await fixture();
  await f.ledger.run('UPDATE registrations SET active=0');
  assert.equal((await f.worker.email(emailMessage(), f.env)).reason, 'owner_inactive');
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 1);
  assert.equal(await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'"), null);
});

test('unknown recipients cannot create per-install refusal buckets', async () => {
  const f = await fixture();
  const message = emailMessage(); message.to = message.to.replace('opaque1+', 'unknown+');
  assert.equal((await f.worker.email(message, f.env)).reason, 'owner_inactive');
  assert.equal((await f.ledger.first("SELECT COUNT(*) AS n FROM rate_limits WHERE bucket LIKE 'ingress-refused:%'")).n, 0);
});
