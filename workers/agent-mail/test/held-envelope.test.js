import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, sendAndReceive } from './transport-support.js';
import { bytes, sha256, verify } from '../signing.js';

for (const options of [
  { text: '' },
  { type: 'multipart/signed; boundary=s; protocol="application/pgp-signature"',
    text: '--s\r\nContent-Type: text/plain\r\n\r\n\r\n--s\r\nContent-Type: application/pgp-signature\r\n\r\nDO-NOT-KEEP\r\n--s--' },
  { type: 'image/png', text: 'DO-NOT-KEEP' },
  { extra: ['Content-Disposition: attachment; filename=x.txt'], text: 'DO-NOT-KEEP' },
]) {
  test(`body-free hold is durable, replayed and signed: ${options.type ?? 'plain'}/${!!options.extra}`, async () => {
    const f = await fixture();
    assert.equal((await sendAndReceive(f, options)).result.status, 'queued');
    const e = await f.ledger.first('SELECT * FROM events');
    const p = JSON.parse(e.payload);
    assert.equal(p.version, 2); assert.equal(p.reason, 'empty_body');
    assert.equal(p.body_digest, await sha256(bytes('')));
    assert.equal(Object.hasOwn(p, 'text'), false);
    assert.doesNotMatch(e.payload, /DO-NOT-KEEP|pgp-signature|filename/);
    assert.equal(e.state, 'pending');
    assert.equal((await f.worker.scheduled({}, f.env)).delivered, 1);
    const d = f.delivered[0];
    assert.equal(new TextDecoder().decode(d.body), e.payload);
    const request = new Request(d.url, { method: d.method, headers: d.headers, body: d.body });
    const args = { request, body: d.body, publicKey: f.serviceKeys.publicKey, key_id: 'sk1',
      direction: 'service-to-install', install_id: 'install1', generation: 1,
      claimNonce: async () => true, clock: () => f.clock.now };
    assert.equal(await verify(args), true);
    assert.equal(await verify({ ...args, body: bytes(e.payload.replace('empty_body', 'other_hold')) }), false);
    const settled = await f.ledger.first('SELECT * FROM events');
    assert.equal(settled.state, 'local_received'); assert.equal(settled.payload, null);
  });
}

test('body-free holds retry without payload loss until matching custody', async () => {
  const f = await fixture({ fetcher: async () => Response.json({ custody: 'durable' }) });
  await sendAndReceive(f, { text: '' });
  const e = await f.ledger.first('SELECT * FROM events');
  assert.equal((await f.worker.scheduled({}, f.env)).delivered, 0);
  const pending = await f.ledger.first('SELECT * FROM events');
  assert.equal(pending.state, 'pending'); assert.equal(pending.payload, e.payload);
});

test('empty authenticated mail spends owner allowance without recording an SMTP refusal', async () => {
  const f = await fixture();
  const { message, result } = await sendAndReceive(f, { text: '' });
  assert.equal(result.status, 'queued'); assert.deepEqual(message.rejects, []);
  assert.equal((await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress:install1:%'")).count, 1);
  assert.equal(await f.ledger.first("SELECT count FROM rate_limits WHERE bucket LIKE 'ingress-refused:install1:%'"), null);
  assert.equal((await f.ledger.first('SELECT COUNT(*) AS n FROM rejections')).n, 0);
});
