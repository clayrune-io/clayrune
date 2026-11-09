import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, sendAndReceive, emailMessage, sendValue, NOW, keys } from './transport-support.js';
import { bytes, sign, verify, sha256 } from '../signing.js';
import { replay } from '../replay.js';
import { multipart, plainPart, htmlPart, cfAr } from './fixtures.js';
test('dashboard cookies and bearer credentials cannot authenticate send', async () => {
  const f = await fixture();
  for (const headers of [ { cookie: 'session=dashboard' }, { authorization: 'Bearer dashboard' }, {} ]) {
    const r = new Request('https://service.example.net/v1/mail/send', { method: 'POST', headers,
      body: JSON.stringify(sendValue()) });
    assert.equal((await f.worker.fetch(r, f.env)).status, 401);
  }
  assert.equal(f.sent.length, 0);
});
test('disabled feature does no storage or send work', async () => {
  const f = await fixture(); f.env.MAIL_ENABLED = 'false'; f.env.MAIL_DB = null;
  assert.equal((await f.worker.fetch(await f.request(), f.env)).status, 503);
  const m = emailMessage(); await f.worker.email(m, f.env); assert.equal(m.rejects.length, 1);
  assert.equal(await f.worker.scheduled({}, f.env), undefined); assert.equal(f.sent.length, 0);
});
test('old generation and wrong install key cannot sign as a different install', async () => {
  const f = await fixture();
  await f.ledger.run(`INSERT INTO registrations SELECT 'install2',1,1,1,'other@example.com','opaque2',
    'ik2',public_key,NULL,NULL,NULL,0 FROM registrations WHERE install_id='install1'`);
  const body = bytes(JSON.stringify(sendValue()));
  const forged = await sign({ ...f.installKeys, key_id: 'ik1', install_id: 'install2', generation: 1,
    direction: 'install-to-service', path: '/v1/mail/send', body, now: NOW });
  assert.equal((await f.worker.fetch(new Request('https://service.example.net/v1/mail/send',
    { method: 'POST', headers: forged, body }), f.env)).status, 401);
  await f.ledger.run('UPDATE registrations SET generation=2 WHERE install_id=?', 'install1');
  assert.equal((await f.worker.fetch(await f.request(), f.env)).status, 401); assert.equal(f.sent.length, 0);
});
test('nonce claim store failure propagates instead of becoming an authentication refusal', async () => {
  const f = await fixture(); const request = await f.request(); const body = new Uint8Array(await request.arrayBuffer());
  await assert.rejects(verify({ request, body, publicKey: f.reg.public_key, key_id: 'ik1',
    install_id: 'install1', generation: 1, direction: 'install-to-service', now: NOW,
    claimNonce: async () => { throw new Error('database unavailable'); } }), /database unavailable/);
});
test('service key pin and key id rotation are both required', async () => {
  const f = await fixture(); const rotated = await keys(); const body = bytes('{}');
  const headers = await sign({ ...rotated, key_id: 'sk2', install_id: 'install1', generation: 1,
    direction: 'service-to-install', path: '/api/agent-mail/ingress', body, now: NOW });
  const request = new Request('https://proven.example.net/api/agent-mail/ingress', { method: 'POST', headers, body });
  const args = { request, body, install_id: 'install1', generation: 1, direction: 'service-to-install',
    now: NOW, claimNonce: async () => true };
  assert.equal(await verify({ ...args, key_id: 'sk1', publicKey: f.serviceKeys.publicKey }), false);
  assert.equal(await verify({ ...args, key_id: 'sk2', publicKey: f.serviceKeys.publicKey }), false);
  assert.equal(await verify({ ...args, key_id: 'sk2', publicKey: rotated.publicKey }), true);
});
test('outbound failure after provider write but before D1 receipt remains unreplayable', async () => {
  const f = await fixture(); const original = f.db.prepare.bind(f.db); let fail = true;
  f.db.prepare = (sql) => {
    if (sql.startsWith('UPDATE intents SET state=') && fail) {
      fail = false; throw new Error('receipt storage unavailable');
    }
    return original(sql);
  };
  const first = await f.worker.fetch(await f.request(), f.env); assert.equal(first.status, 503);
  assert.equal(f.sent.length, 1);
  const second = await f.worker.fetch(await f.request(), f.env); assert.equal((await second.json()).code, 'sending');
  assert.equal(f.sent.length, 1); await f.ledger.reconcile(NOW + 60_000);
  assert.equal((await f.ledger.first('SELECT * FROM intents')).state, 'uncertain');
});
test('immutable thread/token collision cannot attach another outbound intent', async () => {
  const f = await fixture(); await f.worker.fetch(await f.request(), f.env);
  const second = sendValue({ intent_id: 'i2', thread_id: 't2' });
  assert.equal((await f.worker.fetch(await f.request('/v1/mail/send', second), f.env)).status, 503);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM threads')).n, 1); assert.equal(f.sent.length, 1);
});
test('D1 generation check closes revocation between authentication and reservation', async () => {
  const f = await fixture(); await f.ledger.run('UPDATE registrations SET active=0');
  const value = { ...sendValue(), digest: await sha256(bytes('x')), token_hash: 'h', message_id: 'out@example.net' };
  assert.equal(await f.ledger.reserveSend(f.reg, value, NOW), null);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM threads')).n, 0);
});
test('global send quota is enforced even below the install quota', async () => {
  const f = await fixture();
  await f.ledger.run('INSERT INTO rate_limits VALUES(?,?,?)', `send:global:${Math.floor(NOW / 86400000)}`, 1000, NOW + 86400000);
  const res = await f.worker.fetch(await f.request(), f.env); assert.equal(res.status, 429); assert.equal(f.sent.length, 0);
});
test('held queue head cannot starve a different installation replay', async () => {
  const f = await fixture(); await sendAndReceive(f, { id: null });
  const first = await f.ledger.first('SELECT * FROM events');
  await f.ledger.run(`INSERT INTO registrations SELECT 'install2',generation,active,owner_verified,
    owner_address,'opaque2',key_id,public_key,route_origin,route_path,route_generation,route_verified FROM registrations`);
  for (let i = 0; i < 55; i++) {
    await f.ledger.run(`INSERT INTO events(event_id,install_id,generation,thread_id,incoming_id,digest,payload,
      state,received_at,expires_at,next_attempt) VALUES(?,'install1',1,'thread1',?,'d','{}','pending',?,?,?)`,
      `blocked${i}`, `id${i}`, NOW, NOW + 86400000, NOW);
  }
  await f.ledger.run(`INSERT INTO threads SELECT 'install2',1,'thread2','otherhash','out2',expires_at,0 FROM threads LIMIT 1`);
  const p = { ...JSON.parse(first.payload), event_id: 'other', install_id: 'install2', thread_id: 'thread2' };
  await f.ledger.run(`INSERT INTO events(event_id,install_id,generation,thread_id,incoming_id,digest,payload,
    state,received_at,expires_at,next_attempt) VALUES('other','install2',1,'thread2','otherid',?,?,'pending',?,?,?)`,
    first.digest, JSON.stringify(p), NOW, NOW + 86400000, NOW);
  const due = await f.ledger.due(NOW); assert.deepEqual(due.map((e) => e.event_id), ['other']);
  await f.worker.scheduled({}, f.env); assert.equal(f.delivered.length, 1);
  assert.equal(f.delivered[0].headers.get('x-mail-install-id'), 'install2');
});
test('MIME attachments and hidden HTML never survive worker custody', async () => {
  const f = await fixture(); const res = await f.worker.fetch(await f.request(), f.env); const out = await res.json();
  const m = emailMessage({ outId: out.message_id });
  const mime = multipart('mixed', [ plainPart('Visible text'),
    { headers: ['Content-Type: text/plain', 'Content-Disposition: attachment; filename=x.txt'], body: 'INJECTED' },
    htmlPart('<div hidden>HIDDEN-INJECTED</div>') ]);
  const raw = bytes(`Authentication-Results: ${cfAr()}\r\nMessage-ID: <reply1@example.com>\r\nIn-Reply-To: <${out.message_id}>\r\n${mime}`);
  m.raw = new Blob([raw]).stream(); m.rawSize = raw.length;
  const result = await f.worker.email(m, f.env); assert.equal(result.status, 'queued');
  const payload = (await f.ledger.first('SELECT * FROM events')).payload;
  assert.match(payload, /Visible text/); assert.doesNotMatch(payload, /INJECTED/);
});
test('thread revocation purges a queued event before replay', async () => {
  const f = await fixture(); await sendAndReceive(f); await f.ledger.run('UPDATE threads SET revoked=1');
  await f.worker.scheduled({}, f.env); const event = await f.ledger.first('SELECT * FROM events');
  assert.equal(event.state, 'revoked'); assert.equal(event.payload, null); assert.equal(f.delivered.length, 0);
});
test('bounded rejection ledger exposes overflow as a durable count', async () => {
  const f = await fixture();
  f.db.sql.exec(`WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000)
    INSERT INTO rejections(at,reason) SELECT ${NOW},'test_refusal' FROM n`);
  await f.ledger.reject('overflowed', NOW); await f.ledger.reject('overflowed', NOW);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 10000);
  assert.equal((await f.ledger.first('SELECT * FROM rejection_overflow')).count, 2);
});
for (const value of [null, true, 1, [], { install_id: 'other' }]) {
  test(`reconciliation accepts an empty object only: ${JSON.stringify(value)}`, async () => {
    const f = await fixture(); const r = await f.worker.fetch(await f.request('/v1/mail/reconcile', value), f.env);
    assert.equal(r.status, 400); assert.equal((await r.json()).code, 'invalid_reconcile');
  });
}
test('a late receipt after remote revocation cannot count as settled custody', async () => {
  let f;
  f = await fixture({ fetcher: async (_url, init) => {
    const p = JSON.parse(new TextDecoder().decode(init.body));
    await f.ledger.run('UPDATE registrations SET active=0'); await f.ledger.reconcile(NOW);
    return Response.json({ custody: 'durable', event_id: p.event_id, body_digest: p.body_digest,
      install_id: p.install_id, generation: p.generation });
  } });
  await sendAndReceive(f); const result = await f.worker.scheduled({}, f.env);
  assert.equal(result.delivered, 0); assert.equal((await f.ledger.first('SELECT * FROM events')).state, 'revoked');
});
