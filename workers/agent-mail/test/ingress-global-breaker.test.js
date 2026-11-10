// Fenn global-breaker.test.mjs: original four probes; only imports relocated.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, emailMessage, sendValue } from './transport-support.js';
import { cfAr } from './fixtures.js';
import { DAY } from '../ledger.js';

async function twoOwners() {
  const f = await fixture();
  const one = await f.worker.fetch(await f.request(), f.env);
  assert.equal(one.status, 200);
  const first = await one.json();
  await f.ledger.run(`INSERT INTO registrations SELECT 'install2',generation,active,owner_verified,
    'second@example.com','opaque2',key_id,public_key,route_origin,route_path,route_generation,route_verified
    FROM registrations WHERE install_id='install1'`);
  f.reg.install_id = 'install2';
  const token2 = 'b'.repeat(32);
  const two = await f.worker.fetch(await f.request('/v1/mail/send', sendValue({ reply_token: token2 })), f.env);
  assert.equal(two.status, 200);
  const second = await two.json();
  return { f, replies: () => {
    const one = emailMessage({ outId: first.message_id });
    const two = emailMessage({ outId: second.message_id, token: token2,
      from: 'second@example.com', envelope: 'second@example.com' });
    two.to = two.to.replace('opaque1+', 'opaque2+');
    return [one, two];
  } };
}

test('control: independent owners both queue before stranger traffic', async () => {
  const { f, replies } = await twoOwners();
  for (const m of replies()) {
    assert.equal((await f.worker.email(m, f.env)).status, 'queued');
    assert.deepEqual(m.rejects, []);
  }
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 2);
});

for (const target of ['known', 'unknown']) {
  test(`P2 shared breaker: 10000 ${target}-recipient strangers must not refuse both owners`, async () => {
    const { f, replies } = await twoOwners();
    for (let i = 0; i < 10000; i++) {
      const m = emailMessage({ from: 'stranger@example.org', envelope: 'stranger@example.org',
        token: '0'.repeat(32), outId: 'unknown@example.org',
        ar: cfAr({ from: 'example.org', mailfrom: 'stranger@example.org', d: 'example.org' }),
        id: `spam${i}@example.org` });
      if (target === 'unknown') m.to = m.to.replace('opaque1+', 'unregistered+');
      assert.equal((await f.worker.email(m, f.env)).reason,
        target === 'known' ? 'from_not_owner' : 'owner_inactive');
    }
    const observations = [];
    for (const m of replies()) observations.push({ result: await f.worker.email(m, f.env), rejects: m.rejects });
    const counts = (await f.ledger.stmt('SELECT bucket,count FROM rate_limits WHERE bucket LIKE ?', 'ingress%').all()).results;
    const events = (await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n;
    console.log(JSON.stringify({ target, observations, counts, events }));
    assert.deepEqual(observations.map(o => o.result.status), ['queued', 'queued']);
  });
}

test('concurrent strangers do not spend protected quota below global limit', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  const results = await Promise.all(Array.from({ length: 250 }, (_, i) => f.worker.email(emailMessage({
    from: 'stranger@example.org', envelope: 'stranger@example.org', token: '0'.repeat(32),
    outId: 'unknown@example.org', id: `race${i}@example.org`,
    ar: cfAr({ from: 'example.org', mailfrom: 'stranger@example.org', d: 'example.org' }),
  }), f.env)));
  assert.ok(results.every(r => r.reason === 'from_not_owner'));
  assert.equal(await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'"), null);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'")).count, 200);
  const message = emailMessage({ outId: sent.message_id });
  assert.equal((await f.worker.email(message, f.env)).status, 'queued');
  assert.deepEqual(message.rejects, []);
});

async function bucket(f, name) {
  return f.ledger.first('SELECT count FROM rate_limits WHERE bucket=?', `${name}:${Math.floor(f.clock.now / DAY)}`);
}

test('authenticated owner above accepted-global allowance is still refused', async () => {
  const { f, replies } = await twoOwners();
  // Pre-fill all but the last reservation, then exercise the boundary through
  // the public handler across two independent owners, below their own limits.
  await f.ledger.run('INSERT INTO rate_limits VALUES(?,?,?)',
    `ingress-accepted:global:${Math.floor(f.clock.now / DAY)}`,
    f.ledger.policy.globalIngressPerDay - 1, f.clock.now + 2 * DAY);
  const [one, two] = replies();
  assert.equal((await f.worker.email(one, f.env)).status, 'queued');
  assert.deepEqual(one.rejects, []);
  assert.deepEqual(await f.worker.email(two, f.env), { status: 'rejected', reason: 'ingress_limit' });
  assert.deepEqual(two.rejects, ['Agent mail refused']);
  assert.equal((await bucket(f, 'ingress-accepted:global')).count, 10000);
  assert.equal(await bucket(f, 'ingress:install2'), null);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 1);
});

test('saturated global refusals skip audit and install counts, still reject, and allow both owners', async () => {
  const { f, replies } = await twoOwners();
  await f.ledger.run('INSERT INTO rate_limits VALUES(?,?,?)',
    `ingress-refused:global:${Math.floor(f.clock.now / DAY)}`,
    f.ledger.policy.globalIngressPerDay - 1, f.clock.now + 2 * DAY);
  // The last audited refusal, then multiple concurrent over-budget refusals.
  const last = emailMessage({ from: 'stranger@example.com' });
  assert.equal((await f.worker.email(last, f.env)).reason, 'from_not_owner');
  assert.deepEqual(last.rejects, ['Agent mail refused']);
  const messages = Array.from({ length: 25 }, () => emailMessage({ from: 'stranger@example.com' }));
  const results = await Promise.all(messages.map(m => f.worker.email(m, f.env)));
  assert.ok(results.every(r => r.status === 'rejected' && r.reason === 'from_not_owner'));
  for (const m of messages) assert.deepEqual(m.rejects, ['Agent mail refused']);
  assert.equal((await bucket(f, 'ingress-refused:global')).count, 10000);
  assert.equal((await bucket(f, 'ingress-refused-saturated:global')).count, 1);
  assert.equal((await bucket(f, 'ingress-refused:install1')).count, 1);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 1);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejection_overflow')).n, 0);
  for (const m of replies()) {
    assert.equal((await f.worker.email(m, f.env)).status, 'queued');
    assert.deepEqual(m.rejects, []);
  }
  assert.equal((await bucket(f, 'ingress-accepted:global')).count, 2);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 2);
  f.clock.now += DAY;
  const nextDay = emailMessage({ from: 'stranger@example.com' });
  assert.equal((await f.worker.email(nextDay, f.env)).reason, 'from_not_owner');
  assert.deepEqual(nextDay.rejects, ['Agent mail refused']);
  assert.equal((await bucket(f, 'ingress-refused:global')).count, 1);
  assert.equal(await bucket(f, 'ingress-refused-saturated:global'), null);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 2);
});

test('unverified ingress remains the first gate even when both global buckets are full', async () => {
  const f = await fixture();
  f.env.MAIL_INGRESS_VERIFIED = 'false';
  for (const name of ['ingress-accepted:global', 'ingress-refused:global']) {
    await f.ledger.run('INSERT INTO rate_limits VALUES(?,?,?)',
      `${name}:${Math.floor(f.clock.now / DAY)}`, 10000, f.clock.now + 2 * DAY);
  }
  // Reading either recipient or body would prove the launch gate ran too late.
  const message = { rejects: [], get to() { throw new Error('recipient_read'); },
    get rawSize() { throw new Error('raw_size_read'); },
    setReject(value) { this.rejects.push(value); } };
  assert.deepEqual(await f.worker.email(message, f.env), { status: 'rejected', reason: 'ingress_not_verified' });
  assert.deepEqual(message.rejects, ['Agent mail refused']);
  assert.equal((await bucket(f, 'ingress-refused-saturated:global')).count, 1);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 0);
});
