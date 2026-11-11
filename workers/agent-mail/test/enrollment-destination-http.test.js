import test from 'node:test';
import assert from 'node:assert/strict';
import { world, SECRET, replyVerifies, row, codeOf } from './enrollment-http-support.js';
import { canonicalBegin } from '../enrollment-begin-proof.js';
import { base64url, randomId } from '../signing.js';

async function proof(w, { timestamp = String(w.time.now), nonce = randomId(), service = w.env.MAIL_SERVICE_ORIGIN } = {}) {
  const value = { ...w.scratch.value };
  return { value, flow_secret: SECRET, proof: { timestamp, nonce,
    signature: base64url(await crypto.subtle.sign('Ed25519', w.scratch.device.privateKey,
      await canonicalBegin(value, SECRET, service, timestamp, nonce))) } };
}

test('missing, wrong key, stale, foreign-service and replayed begin proofs cannot provision', async () => {
  let prepares = 0;
  const provider = async (address) => ({ address, verified: true });
  provider.ensure = async ({ address }) => { prepares += 1; return { address, verified: true }; };
  const w = await world({ provider });
  try {
    const request = await proof(w);
    const missing = { ...request }; delete missing.proof;
    for (const input of [missing, { ...request, proof: { ...request.proof, signature: base64url(new Uint8Array(64)) } },
      await proof(w, { timestamp: String(w.time.now - 300001) }), await proof(w, { timestamp: String(w.time.now + 300001) }),
      await proof(w, { service: 'https://foreign.example.com' })]) {
      const r = await w.call('begin', input); assert.equal(r.res.status, 401);
    }
    assert.equal(prepares, 0); assert.equal(w.preflights.length, 0);
    // Refusal accounting can write the shared abuse bucket, never identity/provisioning rows.
    assert.equal(row(w, 'SELECT count(*) AS n FROM mail_destination_attempts').n, 0);
    assert.equal((await w.call('begin', request)).res.status, 200); assert.equal(prepares, 1);
    assert.equal((await w.call('begin', request)).res.status, 401); assert.equal(prepares, 1);
  } finally { w.restore(); }
});

test('signature binds all begin fields, optional fence, secret digest and freshness context', async () => {
  const w = await world();
  try {
    const original = await proof(w);
    for (const key of Object.keys(original.value)) {
      const changed = { ...original, value: { ...original.value, [key]: key === 'owner_address' ? 'other@example.com' : 'changed' } };
      const r = await w.call('begin', changed); assert.ok([400, 401].includes(r.res.status), key);
    }
    for (const input of [
      { ...original, value: { ...original.value, fence_digest: 'f'.repeat(64) } },
      { ...original, flow_secret: 'b'.repeat(64) },
      { ...original, proof: { ...original.proof, timestamp: String(w.time.now + 1) } },
      { ...original, proof: { ...original.proof, nonce: randomId() } },
    ]) assert.equal((await w.call('begin', input)).res.status, 401);
    assert.equal(w.preflights.length, 0); assert.equal(w.sent.length, 0);
  } finally { w.restore(); }
});

test('CP remains required; proof freshness rechecked after slow CP before provisioning', async () => {
  let w;
  w = await world({ preflight: async () => { w.time.now += 300001; return { ok: true }; } });
  try {
    const r = await w.begin(); assert.equal(r.res.status, 401);
    assert.equal(row(w, 'SELECT count(*) AS n FROM mail_begin_nonces').n, 0);
    assert.equal(row(w, 'SELECT count(*) AS n FROM mail_destination_attempts').n, 0);
    assert.equal(w.sent.length, 0);
  } finally { w.restore(); }
});

test('production provider wiring: CF pending before code, verified retry through confirm/grant/complete', async () => {
  const rows = []; const requests = [];
  const w = await world({ noProvider: true,
    envOver: { MAIL_DESTINATION_TOKEN: 'synthetic-token', MAIL_DESTINATION_ACCOUNT_ID: 'a'.repeat(32) },
    fetcher: async (url, init) => {
      requests.push({ url, init });
      if (url.startsWith('https://authority.example.com/')) return Response.json({ ok: true });
      assert.ok(url.startsWith('https://api.cloudflare.com/client/v4/accounts/'));
      if (init.method === 'POST') {
        const email = JSON.parse(init.body).email; const value = { email, verified: null }; rows.push(value);
        return Response.json({ success: true, result: value });
      }
      return Response.json({ success: true, result: rows, result_info: { page: 1, per_page: 50, total_pages: 1, total_count: rows.length } });
    } });
  try {
    for (let i = 0; i < 2; i += 1) {
      const r = await w.begin(); assert.equal(r.res.status, 409);
      assert.deepEqual(r.json, { flow_id: 'flow1', code: 'destination_unverified' });
      assert.equal(await replyVerifies(w, r, 'flow1'), true);
      assert.equal(row(w, 'SELECT count(*) AS n FROM mail_enrollment_flows').n, 0);
      assert.equal(row(w, 'SELECT count(*) AS n FROM mail_identity_reservations').n, 0);
      assert.equal(w.sent.length, 0);
    }
    assert.equal(requests.filter((r) => r.init.method === 'POST' && r.url.includes('api.cloudflare.com')).length, 1);
    rows[0].verified = '2026-11-11T10:00:00Z';
    const begun = await w.begin(); assert.equal(begun.res.status, 200); assert.equal(w.sent.length, 1);
    const confirmed = await w.call('confirm', { flow_id: 'flow1', flow_secret: SECRET, code: codeOf(w.sent[0])[0] });
    assert.equal(confirmed.json.confirmed, true);
    const r = await w.call('complete', { flow_id: 'flow1', flow_secret: SECRET,
      token: await w.scratch.token({}, {}, begun.json.transcript),
      device_signature: await w.scratch.proof(w.scratch.device.privateKey, begun.json.transcript),
      mail_signature: await w.scratch.proof(w.scratch.mail.privateKey, begun.json.transcript) });
    assert.equal(r.res.status, 200); assert.equal(r.json.active, false);
    assert.equal(r.json.state, 'ownership_recorded_delivery_pending');
    assert.equal((await w.begin()).json.code_sent, false);
  } finally { w.restore(); }
});
