import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fixture, sendAndReceive, emailMessage, D1, NOW } from './transport-support.js';
import { replay, provenRoute } from '../replay.js';
import { Ledger, DAY } from '../ledger.js';
import { verify } from '../signing.js';
test('durable local custody clears worker content without claiming a completed turn', async () => {
  const f = await fixture(); await sendAndReceive(f); await f.legacy.scheduled({}, f.env);
  const event = await f.ledger.first('SELECT * FROM events');
  assert.equal(event.state, 'local_received'); assert.equal(event.payload, null); assert.equal(f.delivered.length, 1);
  const delivery = f.delivered[0];
  assert.equal(delivery.redirect, 'manual');
  const request = new Request(delivery.url, { method: 'POST', headers: delivery.headers, body: delivery.body });
  assert.equal(await verify({ request, body: delivery.body, publicKey: f.serviceKeys.publicKey, key_id: 'sk1',
    direction: 'service-to-install', install_id: 'install1', generation: 1, now: NOW, claimNonce: async () => true }), true);
});
for (const mode of ['offline', '500', '302', 'generic200', 'wrong_event', 'wrong_digest', 'wrong_generation', 'oversize']) {
  test(`delivery ${mode} retains D1 custody for retry`, async () => {
    const f = await fixture({ fetcher: async (_u, init) => {
      if (mode === 'offline') throw new Error('offline');
      if (mode === '500' || mode === '302') return new Response('', { status: Number(mode) });
      if (mode === 'generic200') return Response.json({ ok: true });
      if (mode === 'oversize') return new Response('x'.repeat(3000));
      const p = JSON.parse(new TextDecoder().decode(init.body));
      return Response.json({ custody: 'durable', event_id: mode === 'wrong_event' ? 'other' : p.event_id,
        body_digest: mode === 'wrong_digest' ? 'other' : p.body_digest, install_id: p.install_id,
        generation: mode === 'wrong_generation' ? 2 : p.generation });
    } });
    await sendAndReceive(f); await f.legacy.scheduled({}, f.env);
    const event = await f.ledger.first('SELECT * FROM events');
    assert.equal(event.state, 'pending'); assert.ok(event.payload); assert.ok(event.next_attempt > NOW);
  });
}
test('lost ack replay uses same bytes/event but a fresh nonce', async () => {
  const attempts = []; let lose = true;
  const f = await fixture({ fetcher: async (url, init) => {
    attempts.push({ url, ...init }); const p = JSON.parse(new TextDecoder().decode(init.body));
    if (lose) { lose = false; throw new Error('ack lost after local insert'); }
    return Response.json({ custody: 'durable', event_id: p.event_id, body_digest: p.body_digest,
      install_id: p.install_id, generation: p.generation });
  } });
  await sendAndReceive(f); await f.legacy.scheduled({}, f.env);
  f.clock.now += 10_000; await f.legacy.scheduled({}, f.env);
  assert.equal(attempts.length, 2); assert.deepEqual(attempts[0].body, attempts[1].body);
  assert.notEqual(attempts[0].headers.get('x-mail-nonce'), attempts[1].headers.get('x-mail-nonce'));
  assert.equal((await f.ledger.first('SELECT * FROM events')).payload, null);
});
test('concurrent replay claims only once; expired lease cannot settle a replacement', async () => {
  const f = await fixture(); const { result } = await sendAndReceive(f);
  const claims = await Promise.all([f.ledger.claim(result.event_id, NOW), new Ledger(f.db).claim(result.event_id, NOW)]);
  assert.equal(claims.filter(Boolean).length, 1); const old = claims.find(Boolean);
  await f.ledger.reconcile(NOW + 60_000); const next = await f.ledger.claim(result.event_id, NOW + 60_000);
  assert.ok(next); await f.ledger.settle(old, true, NOW + 60_000);
  assert.equal((await f.ledger.first('SELECT * FROM events')).lease_id, next.lease_id);
});
test('one installation preserves receipt order while first event is blocked/offline', async () => {
  const f = await fixture(); const { outId, result } = await sendAndReceive(f);
  const later = await f.legacy.email(emailMessage({ outId, id: 'later@example.com' }), f.env);
  assert.equal(await f.ledger.claim(later.event_id, NOW), null);
  const first = await f.ledger.claim(result.event_id, NOW); await f.ledger.settle(first, true, NOW);
  assert.ok(await f.ledger.claim(later.event_id, NOW));
});
for (const update of ['active=0', 'generation=2']) {
  test(`registration ${update} cancels old queued execution and purges body`, async () => {
    const f = await fixture(); await sendAndReceive(f); await f.ledger.run(`UPDATE registrations SET ${update}`);
    await f.legacy.scheduled({}, f.env);
    const event = await f.ledger.first('SELECT * FROM events');
    assert.equal(event.state, 'revoked'); assert.equal(event.payload, null); assert.equal(f.delivered.length, 0);
    await f.ledger.run('UPDATE registrations SET active=1,generation=3'); await f.legacy.scheduled({}, f.env);
    assert.equal(f.delivered.length, 0);
  });
}
test('queue expiry and terminal metadata deletion have explicit boundaries', async () => {
  const f = await fixture(); await sendAndReceive(f, { id: null });
  await f.ledger.reconcile(NOW + 30 * DAY - 1); assert.equal((await f.ledger.first('SELECT * FROM events')).state, 'held');
  await f.ledger.reconcile(NOW + 30 * DAY); const event = await f.ledger.first('SELECT * FROM events');
  assert.equal(event.state, 'expired'); assert.equal(event.payload, null); assert.equal(event.reason, 'queue_expired');
  await f.ledger.reconcile(NOW + 60 * DAY); assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM events')).n, 0);
});
for (const over of [ { route_verified: 0 }, { route_generation: 2 }, { route_origin: 'http://127.0.0.1' },
  { route_origin: 'https://127.0.0.1' }, { route_origin: 'https://host.local' }, { route_origin: 'https://a.example.net/path' },
  { route_origin: 'https://user:password@a.example.net' }, { route_origin: 'https://a.example.net?x=1' },
  { route_origin: 'https://a.example.net:8443' }, { route_path: '//other.example.net' } ]) {
  test(`route rejects ${JSON.stringify(over)}`, async () => {
    const f = await fixture(); assert.equal(provenRoute({ ...f.reg, ...over }), null);
  });
}
test('pending route never makes a network request', async () => {
  const f = await fixture(); await sendAndReceive(f); await f.ledger.run('UPDATE registrations SET route_verified=0');
  await f.legacy.scheduled({}, f.env); assert.equal(f.delivered.length, 0);
  assert.equal((await f.ledger.first('SELECT * FROM events')).reason, 'route_pending');
});
test('a fresh D1 connection recovers queue and nonce claims after restart', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'agent-mail-test-')); const path = join(dir, 'mail.sqlite3');
  let db;
  try {
    const f = await fixture({ db: new D1(path) }); db = f.db;
    const { result } = await sendAndReceive(f); const request = await f.request();
    const copy = request.clone(); await f.http.fetch(request, f.env); db.sql.close();
    db = new D1(path, false); f.env.MAIL_DB = db;
    assert.equal((await f.http.fetch(copy, f.env)).status, 401);
    const ledger = new Ledger(db); assert.equal((await ledger.first('SELECT * FROM events WHERE event_id=?', result.event_id)).state, 'pending');
    await f.legacy.scheduled({}, f.env);
    assert.equal((await ledger.first('SELECT * FROM events WHERE event_id=?', result.event_id)).state, 'local_received');
  } finally { db?.sql.close(); rmSync(dir, { recursive: true }); }
});
test('storage failures are visible, do not reset or acknowledge mail', async () => {
  const f = await fixture(); f.db.sql.exec('DROP TABLE events');
  await assert.rejects(sendAndReceive(f), /agent_mail_custody_failed/);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM threads')).n, 1);
});
