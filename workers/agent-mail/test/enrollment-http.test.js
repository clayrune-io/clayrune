// Unit 7d-2e-2: enrollment ceremony HTTP surface (contract docs/AGENT_MAIL_UNIT7D2E_CONTRACT.md 2.8).
// Real SQLite behind the D1 facade, the REAL Enrollment core, fake CP preflight, fixed clock.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { world, replyVerifies, row, codeOf, mailText, SECRET, NONCE, BASE, path } from './enrollment-http-support.js';
import { enrollmentConfig, ENROLLMENT_BODY_LIMIT, isEnrollmentPath } from '../enrollment-http.js';
import { verifyGrant } from '../enrollment-grant.js';
import { canonicalR, verifyProof } from '../enrollment-transcript.js';
import { reserveIdentity } from '../enrollment-reservations.js';
import { sha256, verify, base64url } from '../signing.js';

const V = JSON.parse(readFileSync(new URL('./fixtures/ceremony-vectors.json', import.meta.url), 'utf8'));
const G = JSON.parse(readFileSync(new URL('./fixtures/grant-vectors.json', import.meta.url), 'utf8'));
const FLOW = 'flow1';
const run = (name, fn) => test(name, async () => { const w = await world(); try { await fn(w); } finally { w.restore(); } });
const runWith = (name, options, fn) => test(name, async () => { const w = await world(options); try { await fn(w); } finally { w.restore(); } });
const untouched = (w) => { assert.equal(w.spy.calls, 0, 'no D1 call'); assert.equal(w.sent.length, 0, 'no send'); };
const complete = async (w, begun, over = {}) => w.call('complete', { flow_id: FLOW, flow_secret: SECRET,
  token: await w.scratch.token({}, {}, begun.json.transcript),
  device_signature: await w.scratch.proof(w.scratch.device.privateKey, begun.json.transcript),
  mail_signature: await w.scratch.proof(w.scratch.mail.privateKey, begun.json.transcript), ...over });
const confirmed = async (w) => {
  const begun = await w.begin(); assert.equal(begun.res.status, 200);
  const ok = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code: codeOf(w.sent[0])[0] });
  assert.equal(ok.json.confirmed, true); return begun;
};

run('flag off (unset, "false", "TRUE") answers 503 before any D1 call, send or preflight', async (w) => {
  for (const flags of [{ MAIL_ENROLLMENT_ENABLED: undefined }, { MAIL_ENROLLMENT_ENABLED: 'false' }, { MAIL_ENROLLMENT_ENABLED: 'TRUE' }]) {
    w.env = { ...w.env, ...flags };
    for (const name of ['begin', 'confirm', 'complete']) {
      const r = await w.call(name, { flow_id: FLOW });
      assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'enrollment_disabled' });
    }
  }
  untouched(w); assert.equal(w.preflights.length, 0);
});

run('MAIL_ENABLED off wins first: the existing mail_disabled answer', async (w) => {
  w.env = { ...w.env, MAIL_ENABLED: 'false' };
  const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'mail_disabled' }); untouched(w);
});

for (const missing of ['MAIL_SERVICE_ORIGIN', 'MAIL_SERVICE_PIN', 'MAIL_GRANT_ISSUER', 'MAIL_GRANT_JWKS', 'MAIL_CODE_KEY',
  'MAIL_FROM', 'MAIL_DOMAIN', 'MAIL_SERVICE_KEY_ID', 'MAIL_SERVICE_PRIVATE_KEY']) {
  run(`config ${missing} absent -> 503 enrollment_config_required, no D1 call, no send`, async (w) => {
    w.env = { ...w.env, [missing]: undefined };
    const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'enrollment_config_required' });
    untouched(w); assert.equal(w.preflights.length, 0);
  });
}

for (const [name, over] of [
  ['pin with an extra key', { MAIL_SERVICE_PIN: JSON.stringify({ manifest_sequence: '1', service_key_id: 'service1', service_public_key_sha256: 'b'.repeat(64), x: 1 }) }],
  ['pin with a numeric sequence', { MAIL_SERVICE_PIN: JSON.stringify({ manifest_sequence: 1, service_key_id: 'service1', service_public_key_sha256: 'b'.repeat(64) }) }],
  ['pin that is not JSON', { MAIL_SERVICE_PIN: 'not json' }],
  ['empty key set', { MAIL_GRANT_JWKS: '{"keys":[]}' }],
  ['code key of 31 bytes', { MAIL_CODE_KEY: base64url(new Uint8Array(31).fill(9)) }],
  ['code key that is not base64url', { MAIL_CODE_KEY: '!!!' }],
  ['plain-http issuer', { MAIL_GRANT_ISSUER: 'http://authority.example.com' }],
  ['From outside the service domain', { MAIL_FROM: 'agent@evil.example.org' }],
]) {
  run(`malformed config (${name}) -> 503 enrollment_config_required, nothing runs`, async (w) => {
    w.env = { ...w.env, ...over };
    const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'enrollment_config_required' });
    untouched(w);
  });
}

runWith('no destination provider configured -> 503 enrollment_config_required (nothing can enroll live by accident)',
  { noProvider: true }, async (w) => {
    const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'enrollment_config_required' });
    untouched(w);
  });

run('unknown path, GET, and a query string are 404 and never reach the core', async (w) => {
  assert.equal(isEnrollmentPath('/v1/mail/enrollment/begin'), true);
  assert.equal(isEnrollmentPath('/v1/mail/enrollment/cancel'), false);
  assert.equal(isEnrollmentPath('/v1/mail/enrollment/begin/'), false);
  const bad = [await w.call('cancel', {}), await w.call('begin', {}, { method: 'GET' }), await w.call('begin', {}, { query: '?x=1' })];
  for (const r of bad) { assert.equal(r.res.status, 404); assert.deepEqual(r.json, { code: 'not_found' }); }
  untouched(w);
});

run('missing or malformed x-mail-nonce, bad JSON and oversize bodies are refused before the core', async (w) => {
  for (const nonce of ['', 'zz'.repeat(16), 'ab'.repeat(15)]) {
    const r = await w.begin({}, SECRET, { nonce }); assert.equal(r.res.status, 400);
  }
  const noHeader = await w.worker.fetch(new Request(BASE + path('begin'), { method: 'POST', body: '{}' }), w.env);
  assert.equal(noHeader.status, 400);
  const bad = await w.call('begin', null, { raw: '{not json' }); assert.deepEqual(bad.json, { code: 'invalid_request' });
  const big = await w.call('begin', null, { raw: JSON.stringify({ pad: 'x'.repeat(ENROLLMENT_BODY_LIMIT) }) });
  assert.equal(big.res.status, 413); assert.deepEqual(big.json, { code: 'body_limit' });
  untouched(w);
});

run('begin: signed 200, code only in the mail, never in the reply, headers or logs', async (w) => {
  const r = await w.begin();
  assert.equal(r.res.status, 200);
  assert.equal(w.sent.length, 1);
  const code = codeOf(w.sent[0]); assert.equal(code.length, 1, 'the mail carries the code exactly once');
  assert.equal(r.json.code_sent, true); assert.equal(r.json.state, 'pending'); assert.equal(r.json.flow_id, FLOW);
  assert.equal(r.json.transcript.length, 20); assert.equal(r.json.expected_generation, 0);
  assert.equal(Object.hasOwn(r.json, 'code'), false);
  const everything = [r.text, [...r.res.headers].join('\n'), w.log.join('\n'), JSON.stringify(row(w, 'SELECT * FROM mail_enrollment_flows'))].join('\n');
  assert.equal(everything.includes(code[0]), false, 'the code is not in the reply, headers, logs or the D1 row');
  assert.equal(await replyVerifies(w, r, FLOW), true);
  assert.equal(r.res.headers.get('x-mail-install-id'), FLOW); assert.equal(r.res.headers.get('x-mail-generation'), '1');
  const flow = row(w, 'SELECT * FROM mail_enrollment_flows');
  assert.equal(flow.state, 'pending'); assert.match(flow.code_digest, /^[A-Za-z0-9_-]{43}$/);
});

run('begin mail: From from config, To from the stored row only, owner-only headers', async (w) => {
  const r = await w.begin(); assert.equal(r.res.status, 200);
  const [mail] = w.sent;
  assert.equal(mail.from, 'agent@mail.example.net'); assert.equal(mail.to, 'owner@example.com');
  assert.equal(mail.to, row(w, 'SELECT owner_address a FROM mail_enrollment_flows').a);
  assert.match(mail.raw, /^From: agent@mail\.example\.net\r\nTo: owner@example\.com\r\n/);
  assert.match(mail.raw, /\r\nAuto-Submitted: auto-generated\r\n/); assert.match(mail.raw, /\r\nX-Clayrune-Agent-Mail: 1\r\n/);
  assert.equal(mail.raw.includes(codeOf(mail)[0]), false, 'the code is only inside the base64 body');
  assert.match(mailText(mail), /confirmation code/);
});

run('begin: the CP preflight is asked first, with the five device fields, and nothing else leaks to it', async (w) => {
  const r = await w.begin(); assert.equal(r.res.status, 200);
  assert.deepEqual(w.preflights, [{ flow_id: FLOW, device_id: 'device1', device_public_key: w.scratch.value.device_public_key,
    tunnel_uuid: w.scratch.value.tunnel_uuid, route_origin: w.scratch.value.route_origin }]);
});

for (const [name, preflight, status, code] of [
  ['CP refuses', async () => ({ ok: false, refused: true }), 403, 'preflight_refused'],
  ['CP unreachable', async () => ({ ok: false, refused: false }), 503, 'preflight_unavailable'],
  ['preflight answers nothing', async () => undefined, 503, 'preflight_unavailable'],
  ['ok is truthy but not true', async () => ({ ok: 'yes' }), 503, 'preflight_unavailable'],
]) {
  runWith(`E2a fail closed: ${name} -> ${status} ${code}, no D1 write, no send`, { preflight }, async (w) => {
    const r = await w.begin(); assert.equal(r.res.status, status); assert.deepEqual(r.json, { code });
    assert.equal(r.res.headers.get('x-mail-signature'), null);
    untouched(w);
  });
}

runWith('E2a fail closed: a preflight that throws -> 503, no D1 write, no send', { preflight: async () => { throw new Error('boom'); } }, async (w) => {
  const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'storage_or_configuration_failure' });
  untouched(w); assert.equal(w.log.join('').includes('boom'), false);
});

run('begin grammar errors are 400 and never reach the CP or D1', async (w) => {
  const cases = [{ value: {}, flow_secret: SECRET }, { value: w.scratch.value }, { value: w.scratch.value, flow_secret: 'short' },
    { value: w.scratch.value, flow_secret: SECRET, extra: 1 }, { value: null, flow_secret: SECRET },
    { value: { ...w.scratch.value, flow_id: 'bad id!' }, flow_secret: SECRET },
    { value: { ...w.scratch.value, device_id: 7 }, flow_secret: SECRET }];
  for (const c of cases) { const r = await w.call('begin', c); assert.equal(r.res.status, 400, JSON.stringify(c).slice(0, 80)); }
  assert.equal(w.preflights.length, 0); untouched(w);
});

run('begin: a malformed owner address is refused by the core, no mail', async (w) => {
  const r = await w.begin({ owner_address: 'not an address' }); assert.equal(r.res.status, 400); assert.deepEqual(r.json, { code: 'bad_enrollment' });
  assert.equal(w.sent.length, 0);
});

run('identical re-begin returns the transcript, sends nothing; a differing field is flow_conflict', async (w) => {
  const first = await w.begin(); assert.equal(first.json.code_sent, true);
  const again = await w.begin(); assert.equal(again.res.status, 200); assert.equal(again.json.code_sent, false);
  assert.deepEqual(again.json.transcript, first.json.transcript); assert.equal(w.sent.length, 1);
  const conflict = await w.begin({ route_origin: 'https://other.example.com' });
  assert.equal(conflict.res.status, 409); assert.deepEqual(conflict.json, { code: 'flow_conflict' });
  const wrongSecret = await w.begin({}, 'c'.repeat(64)); assert.equal(wrongSecret.res.status, 401);
});

run('a fenced flow cannot begin: 409 flow_fenced, no send', async (w) => {
  w.db.sql.prepare('INSERT INTO mail_lifecycle_fences(flow_id, fence_digest, created_ms) VALUES (?,?,?)')
    .run(FLOW, 'f'.repeat(64), w.time.now);
  const r = await w.begin(); assert.equal(r.res.status, 409); assert.deepEqual(r.json, { code: 'flow_fenced' }); assert.equal(w.sent.length, 0);
});

test('send failure invalidates the flow (code wiped) and a fresh flow then works', async () => {
  let fail = true; const attempts = [];
  const w = await world({ send: async (m) => { attempts.push(m); if (fail) throw new Error('smtp exploded'); } });
  try {
    const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'code_mail_failed' });
    assert.equal(r.res.headers.get('x-mail-signature'), null);
    assert.equal(attempts.length, 1);
    const flow = row(w, 'SELECT state, code_digest FROM mail_enrollment_flows WHERE flow_id=?', FLOW);
    assert.equal(flow.state, 'invalidated'); assert.equal(flow.code_digest, '');
    assert.equal(w.log.join('\n').includes('smtp exploded'), false, 'no exception text is logged');
    fail = false;
    const retry = await w.begin({ flow_id: 'flow2' }); assert.equal(retry.res.status, 200); assert.equal(attempts.length, 2);
  } finally { w.restore(); }
});

run('an ambiguous send failure is attempted exactly once and never retried', async (w) => {
  let attempts = 0;
  const w2 = await world({ send: async () => { attempts += 1; throw new Error('accepted_then_threw'); } });
  try {
    const r = await w2.begin(); assert.equal(r.res.status, 503);
    assert.equal(attempts, 1);
    const again = await w2.begin(); // same flow: invalidated, the core never reissues, nothing is sent
    assert.equal(again.json.code_sent, false); assert.equal(attempts, 1);
  } finally { w2.restore(); }
});

run('owner budget: the 4th code mail to one owner in a day is 429 mail_limit and invalidates the flow', async (w) => {
  for (let i = 1; i <= 3; i += 1) {
    const r = await w.begin({ flow_id: `flow${i}`, device_id: `device${i}` }); assert.equal(r.res.status, 200, `begin ${i}`);
  }
  const r = await w.begin({ flow_id: 'flow4', device_id: 'device4' });
  assert.equal(r.res.status, 429); assert.deepEqual(r.json, { code: 'mail_limit' }); assert.equal(w.sent.length, 3);
  assert.equal(row(w, 'SELECT state FROM mail_enrollment_flows WHERE flow_id=?', 'flow4').state, 'invalidated');
  const other = await w.begin({ flow_id: 'flow5', device_id: 'device5', owner_address: 'second@example.com' });
  assert.equal(other.res.status, 200, 'a different owner has their own budget');
});

run('global budget: once 200 code mails have gone out the next is 429 mail_limit', async (w) => {
  for (let i = 0; i < 200; i += 1) assert.equal(await w.ledger.limit('enroll-mail:global', 200, w.time.now), true);
  const r = await w.begin(); assert.equal(r.res.status, 429); assert.deepEqual(r.json, { code: 'mail_limit' });
  assert.equal(w.sent.length, 0);
  assert.equal(row(w, 'SELECT state FROM mail_enrollment_flows WHERE flow_id=?', FLOW).state, 'invalidated');
});

run('capacity: with 200 reservations held, begin is 503 enrollment_capacity and sends nothing', async (w) => {
  for (let i = 0; i < 200; i += 1) await reserveIdentity(w.spy, `held${i}`, w.time.now, 200);
  const r = await w.begin(); assert.equal(r.res.status, 503); assert.deepEqual(r.json, { code: 'enrollment_capacity' });
  assert.equal(w.sent.length, 0);
});

run('confirm: wrong or absent flow secret is 401 on the shared bucket and never charges an install', async (w) => {
  await w.begin();
  const code = codeOf(w.sent[0])[0];
  for (const body of [{ flow_id: FLOW, flow_secret: 'c'.repeat(64), code }, { flow_id: FLOW, code }, { flow_id: 'nosuch', flow_secret: SECRET, code }]) {
    const r = await w.call('confirm', body); assert.equal(r.res.status, 401); assert.deepEqual(r.json, { code: 'unauthorized' });
    assert.equal(r.res.headers.get('x-mail-signature'), null);
  }
  const buckets = w.db.sql.prepare('SELECT bucket, count FROM rate_limits').all().filter((b) => !b.bucket.startsWith('enroll-mail:'));
  assert.equal(buckets.length, 1); assert.match(buckets[0].bucket, /^http:unauthenticated:\d+$/); assert.equal(buckets[0].count, 3);
  assert.equal(row(w, 'SELECT code_failures n FROM mail_enrollment_flows').n, 0, 'a stranger cannot burn the human\'s five strikes');
  const good = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code }); assert.equal(good.json.confirmed, true);
});

run('confirm: five wrong codes invalidate and a correct code after that still loses', async (w) => {
  await w.begin(); const code = codeOf(w.sent[0])[0];
  for (let i = 1; i <= 4; i += 1) {
    const r = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code: String(i).repeat(32) });
    assert.equal(r.res.status, 200); assert.equal(r.json.confirmed, false); assert.equal(r.json.state, 'pending');
  }
  const fifth = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code: '5'.repeat(32) });
  assert.deepEqual([fifth.json.confirmed, fifth.json.state], [false, 'invalidated']);
  const late = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code });
  assert.equal(late.res.status, 409); assert.deepEqual(late.json, { code: 'challenge_unavailable' });
});

run('confirm: a malformed code is 400 and does not count as a strike', async (w) => {
  await w.begin();
  for (const code of ['xyz', '', 5, 'A'.repeat(32), 'a'.repeat(31)]) {
    const r = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code }); assert.equal(r.res.status, 400);
  }
  assert.equal(row(w, 'SELECT code_failures n FROM mail_enrollment_flows').n, 0);
});

run('confirm: a second confirm on a confirmed flow answers true and changes no counter', async (w) => {
  await w.begin(); const code = codeOf(w.sent[0])[0];
  const first = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code });
  const before = row(w, 'SELECT code_confirmed, code_failures, state, code_digest FROM mail_enrollment_flows');
  assert.equal(first.json.confirmed, true);
  for (const c of [code, '1'.repeat(32)]) {
    const r = await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code: c });
    assert.equal(r.res.status, 200); assert.deepEqual([r.json.confirmed, r.json.state], [true, 'pending']);
  }
  assert.deepEqual(row(w, 'SELECT code_confirmed, code_failures, state, code_digest FROM mail_enrollment_flows'), before);
  assert.equal(await replyVerifies(w, first, FLOW), true);
});

run('complete: full ceremony records ownership, ownership_expires_ms == committed lease_until, reply signed', async (w) => {
  const begun = await confirmed(w);
  const r = await complete(w, begun);
  assert.equal(r.res.status, 200);
  assert.equal(r.json.state, 'ownership_recorded_delivery_pending'); assert.equal(r.json.generation, 1);
  assert.equal(r.json.active, false); assert.equal(r.json.route_verified, false); assert.equal(r.json.install_id, begun.json.install_id);
  const lease = row(w, 'SELECT lease_until l FROM mail_enrollment_commits WHERE flow_id=?', FLOW).l;
  assert.equal(r.json.ownership_expires_ms, lease); assert.equal(lease, w.time.now + 300_000);
  assert.equal(await replyVerifies(w, r, FLOW), true);
});

run('E3: after a commit, an exact retry without a token returns the original result; with the right token too', async (w) => {
  const begun = await confirmed(w);
  const token = await w.scratch.token({}, {}, begun.json.transcript);
  const first = await complete(w, begun, { token }); assert.equal(first.res.status, 200);
  const sigs = { device_signature: await w.scratch.proof(w.scratch.device.privateKey, begun.json.transcript),
    mail_signature: await w.scratch.proof(w.scratch.mail.privateKey, begun.json.transcript) };
  const noToken = await w.call('complete', { flow_id: FLOW, flow_secret: SECRET, ...sigs });
  assert.equal(noToken.res.status, 200); assert.deepEqual(noToken.json, first.json);
  const sameToken = await complete(w, begun, { token }); assert.deepEqual(sameToken.json, first.json);
  // a token that IS supplied must still be the committed one
  const otherToken = await complete(w, begun, { token: await w.scratch.token({ jti: 'other' }, {}, begun.json.transcript) });
  assert.equal(otherToken.res.status, 409); assert.deepEqual(otherToken.json, { code: 'grant_conflict' });
});

run('E3: a tokenless retry still needs the flow secret and both valid proofs', async (w) => {
  const begun = await confirmed(w); await complete(w, begun);
  const good = { device_signature: await w.scratch.proof(w.scratch.device.privateKey, begun.json.transcript),
    mail_signature: await w.scratch.proof(w.scratch.mail.privateKey, begun.json.transcript) };
  const wrongSecret = await w.call('complete', { flow_id: FLOW, flow_secret: 'c'.repeat(64), ...good });
  assert.equal(wrongSecret.res.status, 401); assert.deepEqual(wrongSecret.json, { code: 'unauthorized' });
  const noSecret = await w.call('complete', { flow_id: FLOW, ...good }); assert.equal(noSecret.res.status, 401);
  const badDevice = await w.call('complete', { flow_id: FLOW, flow_secret: SECRET, ...good, device_signature: good.mail_signature });
  assert.equal(badDevice.res.status, 403); assert.deepEqual(badDevice.json, { code: 'key_proof_required' });
  const badMail = await w.call('complete', { flow_id: FLOW, flow_secret: SECRET, ...good, mail_signature: good.device_signature });
  assert.equal(badMail.res.status, 403);
});

run('E3: with NO commit, a missing token is still refused (400 bad_grant)', async (w) => {
  const begun = await confirmed(w);
  const r = await w.call('complete', { flow_id: FLOW, flow_secret: SECRET,
    device_signature: await w.scratch.proof(w.scratch.device.privateKey, begun.json.transcript),
    mail_signature: await w.scratch.proof(w.scratch.mail.privateKey, begun.json.transcript) });
  assert.equal(r.res.status, 400); assert.deepEqual(r.json, { code: 'bad_grant' });
  assert.equal(row(w, 'SELECT COUNT(*) n FROM mail_enrollment_commits').n, 0);
});

run('complete: wrong flow secret is 401 and burns no install budget; unconfirmed mailbox is 409', async (w) => {
  const begun = await w.begin();
  const unconfirmed = await complete(w, begun); assert.equal(unconfirmed.res.status, 409);
  assert.deepEqual(unconfirmed.json, { code: 'mailbox_confirmation_required' });
  const r = await complete(w, begun, { flow_secret: 'c'.repeat(64) }); assert.equal(r.res.status, 401);
  const buckets = w.db.sql.prepare('SELECT bucket FROM rate_limits').all().map((b) => b.bucket).filter((b) => !b.startsWith('enroll-mail:'));
  assert.equal(buckets.length, 1); assert.match(buckets[0], /^http:unauthenticated:\d+$/);
});

run('complete: grammar errors are 400', async (w) => {
  const begun = await confirmed(w);
  for (const over of [{ device_signature: 5 }, { mail_signature: 'x'.repeat(200) }, { flow_id: 'bad id' }, { extra: 1 }]) {
    const r = await complete(w, begun, over); assert.equal(r.res.status, 400, JSON.stringify(over).slice(0, 60));
  }
});

run('complete: grant failures map to the closed table, never an exception string', async (w) => {
  const begun = await confirmed(w);
  const r = await complete(w, begun, { token: 'not.a.jwt' });
  assert.equal(r.res.status, 400); assert.deepEqual(r.json, { code: 'bad_grant' });
  for (const token of ['', 'a.b', 5, 'a.b.c.d', `${'e30'}.${'e30'}.`, 'x'.repeat(3000)]) {
    const bad = await complete(w, begun, { token }); assert.equal(bad.res.status, 400, String(token).slice(0, 20));
    assert.deepEqual(bad.json, { code: 'bad_grant' });
  }
  assert.equal(r.text.includes('Error'), false);
  const expired = await complete(w, begun, { token: await w.scratch.token({ exp: 1, iat: 0, nbf: 0 }, {}, begun.json.transcript) });
  assert.equal(expired.res.status, 403);
});

runWith('complete: a provider that does not verify the exact address is 409 destination_unverified', {
  provider: async () => ({ address: 'someone@else.example', verified: true }) }, async (w) => {
  const begun = await confirmed(w); const r = await complete(w, begun);
  assert.equal(r.res.status, 409); assert.deepEqual(r.json, { code: 'destination_unverified' });
});

runWith('complete: a provider that throws is 409 destination_unavailable with no message relayed', {
  provider: async () => { throw new Error('secret-token-123'); } }, async (w) => {
  const begun = await confirmed(w); const r = await complete(w, begun);
  assert.equal(r.res.status, 409); assert.deepEqual(r.json, { code: 'destination_unavailable' });
  assert.equal(r.text.includes('secret-token-123'), false); assert.equal(w.log.join('').includes('secret-token-123'), false);
});

run('only a 200 is ever signed: every refusal carries no signature header', async (w) => {
  const refusals = [await w.call('begin', {}), await w.call('confirm', { flow_id: FLOW, flow_secret: SECRET, code: 'a'.repeat(32) }),
    await w.call('complete', { flow_id: 'nosuch', flow_secret: SECRET, device_signature: 'a', mail_signature: 'b' }),
    await w.begin({ flow_id: 'bad id' })];
  for (const r of refusals) {
    assert.notEqual(r.res.status, 200); assert.equal(r.res.headers.get('x-mail-signature'), null);
    assert.equal(r.res.headers.get('x-mail-digest'), null);
  }
});

run('the unauthenticated bucket is bounded: past its ceiling the answer is 429 abuse_limit', async (w) => {
  await w.begin();
  for (let i = 0; i < 10_000; i += 1) await w.ledger.limit('http:unauthenticated', 10_000, w.time.now, 60_000);
  const r = await w.call('confirm', { flow_id: FLOW, flow_secret: 'c'.repeat(64), code: 'a'.repeat(32) });
  assert.equal(r.res.status, 429); assert.deepEqual(r.json, { code: 'abuse_limit' });
});

// ---- cross-language (Python-built) vectors -------------------------------------------------------

test('Python-built device and mail proofs verify over the Worker canonical transcript', async () => {
  const raw = canonicalR(V.r);
  assert.equal(await sha256(raw), V.canonical_sha256);
  assert.equal(await verifyProof(V.device_public_key, V.device_signature, raw), true);
  assert.equal(await verifyProof(V.mail_public_key, V.mail_signature, raw), true);
  assert.equal(await verifyProof(V.device_public_key, V.mail_signature, raw), false);
});

for (const name of ['begin', 'confirm', 'complete']) {
  test(`Python-signed ${name} reply verifies under the Worker verify() with install=flow_id, generation=1`, async () => {
    const v = V.replies[name]; const body = new TextEncoder().encode(v.body);
    const headers = new Headers({ 'x-mail-version': '1', 'x-mail-direction': v.direction, 'x-mail-key-id': v.key_id,
      'x-mail-install-id': V.flow_id, 'x-mail-generation': '1', 'x-mail-timestamp': v.timestamp, 'x-mail-nonce': v.nonce,
      'x-mail-digest': v.digest, 'x-mail-signature': v.signature });
    const request = new Request(BASE + v.path, { method: 'POST', headers });
    const check = (over = {}) => verify({ request, body, publicKey: V.service_public_key, direction: v.direction,
      install_id: V.flow_id, generation: 1, key_id: v.key_id, claimNonce: async () => true, now: V.now_ms, ...over });
    assert.equal(await check(), true);
    assert.equal(await check({ install_id: 'install1' }), false);
    assert.equal(await check({ generation: 2 }), false);
    // the Worker serializes with JSON.stringify: re-serializing the parsed body is byte-identical
    assert.equal(JSON.stringify(JSON.parse(v.body)), v.body);
  });
}

test('the Python-built grant (7d-2e-1 vector) passes verifyGrant under trust derived from Worker config', async () => {
  const v = G.vectors[0];
  const cfg = enrollmentConfig({ MAIL_SERVICE_ORIGIN: G.service_origin, MAIL_GRANT_ISSUER: G.issuer,
    MAIL_SERVICE_PIN: JSON.stringify({ manifest_sequence: '1', service_key_id: 'service1', service_public_key_sha256: 'b'.repeat(64) }),
    MAIL_GRANT_JWKS: JSON.stringify(G.jwks), MAIL_CODE_KEY: base64url(new Uint8Array(32).fill(9)) },
  { config: { domain: 'mail.example.net', from: 'agent@mail.example.net', keyId: 'sk1', privateKey: 'x' },
    provider: async () => ({}), preflight: async () => ({ ok: true }) });
  assert.ok(cfg, 'config resolves');
  assert.equal(cfg.trust.jwksUrl, G.jwks_url);
  const claims = await verifyGrant(v.token, v.r, cfg.trust, () => v.now_ms);
  assert.equal(claims.install_id, 'install1');
});
