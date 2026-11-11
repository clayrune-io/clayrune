// Unit 7d-2e-2: enrollment code mail (contract 2.4). Real Ledger over the SQLite D1 facade, no network.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture } from './enrollment-support.js';
import { Ledger } from '../ledger.js';
import { constructCodeMail, sendEnrollmentCode, OWNER_MAILS_PER_DAY, GLOBAL_MAILS_PER_DAY } from '../code-mail.js';
import { bytes, sha256 } from '../signing.js';
import { codeOf, mailText } from './enrollment-http-support.js';

const config = { domain: 'mail.example.net', from: 'agent@mail.example.net' };
const CODE = 'ab'.repeat(16);

test('constants: 3 per owner per day, 200 global', () => {
  assert.equal(OWNER_MAILS_PER_DAY, 3); assert.equal(GLOBAL_MAILS_PER_DAY, 200);
});

test('mail shape: From from config, To from the owner argument, owner-only headers, code exactly once in the body', async () => {
  const mail = await constructCodeMail({ owner: 'owner@example.com', code: CODE, flowId: 'flow1', config });
  assert.equal(mail.from, 'agent@mail.example.net'); assert.equal(mail.to, 'owner@example.com');
  const [head, body] = mail.raw.split('\r\n\r\n');
  assert.match(head, /^From: agent@mail\.example\.net\r\nTo: owner@example\.com\r\nMessage-ID: <[a-f0-9]{64}@mail\.example\.net>\r\n/);
  for (const h of ['Auto-Submitted: auto-generated', 'X-Clayrune-Agent-Mail: 1', 'Content-Transfer-Encoding: base64',
    'Content-Type: text/plain; charset=utf-8', 'MIME-Version: 1.0']) assert.ok(head.includes(h), h);
  assert.equal(head.includes(CODE), false); assert.equal(body.includes(CODE), false, 'base64 hides it from the raw text');
  assert.deepEqual(codeOf(mail), [CODE]);
  assert.ok(body.split('\r\n').every((l) => l.length <= 76));
  assert.match(mailText(mail), /expires in 15 minutes/);
});

test('mail has no Cc, Bcc or Reply-To, and the Message-ID is stable per flow and differs between flows', async () => {
  const a = await constructCodeMail({ owner: 'owner@example.com', code: CODE, flowId: 'flow1', config });
  const b = await constructCodeMail({ owner: 'owner@example.com', code: CODE, flowId: 'flow1', config });
  assert.equal(a.raw, b.raw);
  assert.doesNotMatch(a.raw, /^(Cc|Bcc|Reply-To):/mi);
  const c = await constructCodeMail({ owner: 'owner@example.com', code: CODE, flowId: 'flow2', config });
  assert.notEqual(a.raw.match(/Message-ID: <(.*?)@/)[1], c.raw.match(/Message-ID: <(.*?)@/)[1]);
});

test('constructCodeMail refuses a bad owner, a From outside the domain, or a code that is not 32 lowercase hex', async () => {
  for (const over of [{ owner: 'a b@example.com' }, { owner: 'owner@example.com\r\nBcc: x@y.z' }, { code: 'short' },
    { code: CODE.toUpperCase() }, { config: { ...config, from: 'agent@other.example.org' } },
    { config: { domain: 'bad domain', from: 'a@bad domain' } }]) {
    await assert.rejects(constructCodeMail({ owner: 'owner@example.com', code: CODE, flowId: 'flow1', config, ...over }),
      /invalid_service_address/);
  }
});

async function pending() {
  const f = await fixture(); const sent = [];
  return { f, sent, ledger: new Ledger(f.db), now: f.time.now,
    args: { flowId: 'flow1', owner: 'owner@example.com', code: f.started.code, config } };
}
const flowState = (f) => f.db.sql.prepare('SELECT state, code_digest FROM mail_enrollment_flows').get();

test('sendEnrollmentCode sends once and leaves the flow pending', async () => {
  const { f, sent, ledger, now, args } = await pending();
  const r = await sendEnrollmentCode({ ledger, ...args, send: async (m) => { sent.push(m); }, now });
  assert.deepEqual(r, { ok: true }); assert.equal(sent.length, 1);
  assert.equal(flowState(f).state, 'pending');
});

test('a throwing send invalidates the flow, wipes the code digest, is attempted once, and logs no message', async () => {
  const { f, ledger, now, args } = await pending(); let attempts = 0; const logged = [];
  const real = console.error; console.error = (...a) => logged.push(a.join(' '));
  try {
    const r = await sendEnrollmentCode({ ledger, ...args, now, send: async () => { attempts += 1; throw new Error(`boom ${args.code}`); } });
    assert.deepEqual(r, { ok: false, status: 503, code: 'code_mail_failed' });
  } finally { console.error = real; }
  assert.equal(attempts, 1);
  assert.deepEqual({ ...flowState(f) }, { state: 'invalidated', code_digest: '' });
  assert.equal(logged.join('').includes(args.code), false); assert.equal(logged.join('').includes('boom'), false);
});

test('owner budget: the 4th mail to one owner is mail_limit, flow invalidated, nothing sent', async () => {
  const { f, sent, ledger, now, args } = await pending();
  const bucket = `enroll-mail:owner:${await sha256(bytes('owner@example.com'))}`;
  for (let i = 0; i < 3; i += 1) assert.equal(await ledger.limit(bucket, 3, now), true);
  const r = await sendEnrollmentCode({ ledger, ...args, send: async (m) => { sent.push(m); }, now });
  assert.deepEqual(r, { ok: false, status: 429, code: 'mail_limit' }); assert.equal(sent.length, 0);
  assert.equal(flowState(f).state, 'invalidated');
});

test('global budget exhausted -> mail_limit, nothing sent', async () => {
  const { f, sent, ledger, now, args } = await pending();
  for (let i = 0; i < GLOBAL_MAILS_PER_DAY; i += 1) await ledger.limit('enroll-mail:global', GLOBAL_MAILS_PER_DAY, now);
  const r = await sendEnrollmentCode({ ledger, ...args, send: async (m) => { sent.push(m); }, now });
  assert.deepEqual(r, { ok: false, status: 429, code: 'mail_limit' }); assert.equal(sent.length, 0);
  assert.equal(flowState(f).state, 'invalidated');
});

test('an unusable owner invalidates the flow and sends nothing (code_mail_failed)', async () => {
  const { f, sent, ledger, now, args } = await pending();
  const r = await sendEnrollmentCode({ ledger, ...args, owner: 'not an address', send: async (m) => { sent.push(m); }, now });
  assert.deepEqual(r, { ok: false, status: 503, code: 'code_mail_failed' }); assert.equal(sent.length, 0);
  assert.equal(flowState(f).state, 'invalidated');
});

test('invalidation only touches a pending flow: a flow in any other state is left alone', async () => {
  const { f, ledger, now, args } = await pending();
  f.db.sql.prepare("UPDATE mail_enrollment_flows SET state='consumed'").run();
  await sendEnrollmentCode({ ledger, ...args, now, send: async () => { throw new Error('x'); } });
  assert.equal(flowState(f).state, 'consumed');
});
