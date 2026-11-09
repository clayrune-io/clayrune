import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, sendValue, emailMessage, NOW } from './transport-support.js';
import { bytes, SKEW_MS, verify } from '../signing.js';
import { createWorker } from '../worker.js';
import { constructMail } from '../outbound.js';

test('control: verified plus address and case remain the exact outbound recipient', async () => {
  const f = await fixture();
  await f.ledger.run('UPDATE registrations SET owner_address=?', 'Owner+tag@EXAMPLE.COM');
  assert.equal((await f.worker.fetch(await f.request(), f.env)).status, 200);
  assert.equal(f.sent[0].to, 'Owner+tag@EXAMPLE.COM');
  assert.match(f.sent[0].raw, /To: Owner\+tag@EXAMPLE.COM\r\n/);
});

test('control: header injection, multiple recipients, and Unicode owner spellings are refused', async () => {
  const f = await fixture();
  for (const owner_address of ['owner@example.com,other@example.com',
    'owner@example.com\r\nBcc: other@example.com', 'owner@ｅxample.com', 'оwner@example.com']) {
    assert.throws(() => constructMail({ ...f.reg, owner_address }, sendValue(),
      'synthetic@mail.example.net', f.config), /invalid_service_address/);
  }
});

for (const path of ['/v1/mail/send', '/v1/mail/reconcile']) {
test(`P1 unsigned requests must not exhaust the authenticated owner quota: ${path}`, async () => {
  const f = await fixture();
  for (let i = 0; i < 200; i++) {
    const request = new Request(`https://service.example.net${path}`, {
      method: 'POST', headers: { 'x-mail-install-id': 'install1' }, body: '{}',
    });
    assert.equal((await f.worker.fetch(request, f.env)).status, 401);
  }
  assert.equal((await f.ledger.first("SELECT COUNT(*) AS n FROM rate_limits WHERE bucket LIKE 'http:install1:%'")).n, 0);
  const response = await f.worker.fetch(await f.request(path, path.endsWith('send') ? sendValue() : {}), f.env);
  console.log('unsigned quota result', response.status, await response.json(), 'sends', f.sent.length);
  assert.equal(response.status, 200, 'valid owner request must still be possible after unsigned traffic');
  assert.equal(f.sent.length, path.endsWith('send') ? 1 : 0);
});
}

test('P2 a transient thread lookup failure must surface as custody failure', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  const prepare = f.db.prepare.bind(f.db);
  let injected = false;
  f.db.prepare = (sql) => {
    if (!injected && sql === 'SELECT * FROM threads WHERE token_hash=?') {
      injected = true;
      throw new Error('synthetic transient D1 read failure');
    }
    return prepare(sql);
  };
  const message = emailMessage({ outId: sent.message_id });
  let failure;
  let result;
  try { result = await f.worker.email(message, f.env); } catch (error) { failure = error; }
  console.log('thread lookup result', result, 'SMTP rejections', message.rejects,
    'event count', (await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n);
  assert.ok(injected);
  assert.match(failure?.message ?? '', /^agent_mail_custody_failed$/);
  assert.deepEqual(message.rejects, []);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 0);
  assert.equal((await f.worker.email(emailMessage({ outId: sent.message_id }), f.env)).status, 'queued');
});

for (const localLength of [31, 32]) {
  test(`P2 full reply mailbox boundary: ${localLength}-character address local part`, async () => {
    const f = await fixture();
    const local = 'a'.repeat(localLength);
    if (localLength === 32) {
      await assert.rejects(f.ledger.run('UPDATE registrations SET address_local=?', local), /CHECK constraint failed/);
      // Model an old-schema or corrupted registration to exercise runtime defense too.
      f.db.sql.exec('PRAGMA ignore_check_constraints=ON');
    }
    await f.ledger.run('UPDATE registrations SET address_local=?', local);
    f.db.sql.exec('PRAGMA ignore_check_constraints=OFF');
    const response = await f.worker.fetch(await f.request(), f.env);
    if (localLength === 32) {
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { code: 'storage_or_configuration_failure' });
      assert.equal(f.sent.length, 0);
      for (const table of ['intents', 'threads']) {
        assert.equal((await f.ledger.first(`SELECT COUNT(*) AS n FROM ${table}`)).n, 0);
      }
      return;
    }
    assert.equal(response.status, 200);
    const sent = await response.json();
    const replyTo = /^Reply-To: (.*)$/m.exec(f.sent[0].raw)[1];
    const message = emailMessage({ outId: sent.message_id });
    message.to = replyTo;
    const result = await f.worker.email(message, f.env);
    console.log('reply address result', { localLength, replyLocalLength: replyTo.split('@')[0].length, result });
    assert.equal(result.status, 'queued', 'successful outbound must have a usable reply address');
  });
}

test('P2 a delayed upload must not replay a nonce after its durable claim expires', async () => {
  const f = await fixture();
  const signed = await f.request('/v1/mail/reconcile', {});
  assert.equal((await f.worker.fetch(signed.clone(), f.env)).status, 200);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 1);
  const body = bytes('{}');
  const stream = new ReadableStream({
    async pull(controller) {
      f.clock.now = NOW + SKEW_MS + 1;
      await f.ledger.reconcile(f.clock.now);
      assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 0);
      controller.enqueue(body);
      controller.close();
    },
  }, { highWaterMark: 0 });
  const request = new Request(signed.url, {
    method: 'POST', headers: signed.headers, body: stream, duplex: 'half',
  });
  const response = await f.worker.fetch(request, f.env);
  console.log('replayed nonce result', response.status, await response.json(),
    'age at verification', f.clock.now - NOW,
    'nonce reused', signed.headers.get('x-mail-nonce') === request.headers.get('x-mail-nonce'));
  assert.equal(response.status, 401, 'signature must be checked at verification time, not request-start time');
});

test('P3 concurrent conflicting incoming ids retain visible conflict metadata', async () => {
  const f = await fixture();
  const sent = await (await f.worker.fetch(await f.request(), f.env)).json();
  // Legal interleaving across Workers: both initial reads finish before either insert.
  const prepare = f.db.prepare.bind(f.db);
  let reads = 0;
  let release;
  const barrier = new Promise((resolve) => { release = resolve; });
  f.db.prepare = (sql) => {
    const statement = prepare(sql);
    if (sql.startsWith('SELECT * FROM events WHERE install_id=?')) {
      const first = statement.first.bind(statement);
      statement.first = async () => {
        const snapshot = await first();
        reads++;
        if (reads === 2) release();
        await barrier;
        return snapshot;
      };
    }
    return statement;
  };
  const results = await Promise.all(['first body', 'conflicting body'].map((text) =>
    f.worker.email(emailMessage({ outId: sent.message_id, id: 'same@example.com', text }), f.env)));
  assert.deepEqual(results.map((result) => result.status).sort(), ['conflict', 'queued']);
  const conflicts = (await f.ledger.first("SELECT COUNT(*) AS n FROM rejections WHERE reason='message_id_conflict'")).n;
  console.log('concurrent ingress results', results, 'durable conflict records', conflicts);
  assert.equal(conflicts, 1, 'racing conflict must be as visible as sequential conflicting redelivery');
  const retained = await f.ledger.first('SELECT * FROM events');
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 1);
  assert.equal(JSON.parse(retained.payload).text, 'first body');
});

for (const path of ['/v1/mail/send', '/v1/mail/reconcile']) {
  test(`exhausted unauthenticated bucket cannot block valid signatures: ${path}`, async () => {
    const f = await fixture();
    await f.ledger.run('INSERT INTO rate_limits VALUES(?,?,?)',
      `http:unauthenticated:${Math.floor(NOW / 60_000)}`, 10_000, NOW + 120_000);
    const bad = await f.request(path, path.endsWith('send') ? sendValue() : {});
    bad.headers.set('x-mail-signature', 'A'.repeat(86));
    const refused = await f.worker.fetch(bad, f.env);
    assert.equal(refused.status, 429);
    assert.deepEqual(await refused.json(), { code: 'abuse_limit' });
    assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 0);
    assert.equal((await f.worker.fetch(await f.request(path,
      path.endsWith('send') ? sendValue() : {}), f.env)).status, 200);
  });
}

test('authenticated send and reconcile share the protected daily allowance', async () => {
  const f = await fixture();
  await f.ledger.run('INSERT INTO rate_limits VALUES(?,?,?)',
    `http:install1:${Math.floor(NOW / 86_400_000)}`, 199, NOW + 172_800_000);
  assert.equal((await f.worker.fetch(await f.request('/v1/mail/reconcile', {}), f.env)).status, 200);
  const refused = await f.worker.fetch(await f.request(), f.env);
  assert.equal(refused.status, 429);
  assert.deepEqual(await refused.json(), { code: 'request_limit' });
  assert.equal(f.sent.length, 0);
});

for (const phase of ['crypto', 'nonce']) {
  test(`signature expiry during asynchronous ${phase} cannot authorize a request`, async () => {
    const f = await fixture();
    const request = await f.request('/v1/mail/reconcile', {});
    let reads = 0; let claims = 0;
    const valid = await verify({ request, body: bytes('{}'), publicKey: f.reg.public_key,
      key_id: 'ik1', install_id: 'install1', generation: 1, direction: 'install-to-service',
      clock: () => phase === 'crypto' && ++reads > 1 ? NOW + SKEW_MS + 1 : f.clock.now,
      claimNonce: async (m) => {
        claims++;
        const claimed = await f.ledger.claimNonce(m);
        f.clock.now = NOW + SKEW_MS + 1;
        await f.ledger.reconcile(f.clock.now);
        return claimed;
      } });
    assert.equal(valid, false);
    assert.equal(claims, phase === 'crypto' ? 0 : 1);
    assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 0);
  });
}

test('stalled upload times out and cancels without waiting on the producer', async () => {
  const f = await fixture(); const signed = await f.request('/v1/mail/reconcile', {});
  let cancelled = false;
  const stream = new ReadableStream({
    pull() { return new Promise(() => {}); },
    cancel() { cancelled = true; return new Promise(() => {}); },
  }, { highWaterMark: 0 });
  const worker = createWorker({ clock: () => f.clock.now, bodyTimeoutMs: 10,
    send: async () => assert.fail('no send before authentication') });
  const response = await worker.fetch(new Request(signed.url, {
    method: 'POST', headers: signed.headers, body: stream, duplex: 'half',
  }), f.env);
  assert.equal(response.status, 408);
  assert.deepEqual(await response.json(), { code: 'body_timeout' });
  assert.equal(cancelled, true);
  assert.equal(stream.locked, false);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 0);
  assert.equal((await f.ledger.first("SELECT COUNT(*) AS n FROM rate_limits WHERE bucket LIKE 'http:install1:%'")).n, 0);
});

test('oversized upload is cancelled before authentication or reservation', async () => {
  const f = await fixture(); const signed = await f.request(); let cancelled = false;
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(131_073)); },
    cancel() { cancelled = true; },
  });
  const response = await f.worker.fetch(new Request(signed.url, {
    method: 'POST', headers: signed.headers, body: stream, duplex: 'half',
  }), f.env);
  assert.equal(response.status, 413);
  assert.equal(cancelled, true);
  assert.equal(f.sent.length, 0);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM nonces')).n, 0);
});

test('full Reply-To validation refuses dot syntax and total mailbox overflow before sending', async () => {
  const f = await fixture();
  for (const address_local of ['bad..dots', 'bad..']) {
    assert.throws(() => constructMail({ ...f.reg, address_local }, sendValue(),
      'synthetic@mail.example.net', f.config), /invalid_service_address/);
  }
  const domain = `${'a'.repeat(63)}.${'b'.repeat(63)}.${'c'.repeat(63)}.net`;
  assert.throws(() => constructMail({ ...f.reg, address_local: 'a'.repeat(31) }, sendValue(),
    `synthetic@${domain}`, { domain, from: `a@${domain}` }), /invalid_service_address/);
});
