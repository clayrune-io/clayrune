import test from 'node:test';
import assert from 'node:assert/strict';
import { cloudflareEmailSending } from '../email-sending.js';

const NOW = 1_800_000_000_000;
const FROM = 'agents@mail.example.net', DOMAIN = 'mail.example.net';
const OWNER = 'Owner.Name+tag@example.com', TOKEN = '0123456789abcdef'.repeat(2);
const LOCAL = `m${'a'.repeat(29)}`;
const reg = () => ({ active: 1, owner_verified: 1, address_local: LOCAL, owner_address: OWNER });
const value = () => ({ intent_id: 'intent_1', thread_id: 'thread-1', kind: 'question',
  reply_token: TOKEN, subject: 'Question about the build', body: 'Which revision?\nQuoted text.' });
const outcome = (name, code, provider_id = '') => ({ outcome: name, code, provider_id });
const UNKNOWN = outcome('uncertain', 'uncertain');
const tick = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(send = async () => ({ messageId: 'provider-unique-1' }), options = {}) {
  let now = NOW, sequence = 0;
  const pending = new Map(), calls = [], delays = [];
  const timers = {
    setTimeout(fn, delay) { const id = ++sequence; pending.set(id, { fn, at: now + delay }); delays.push(delay); return id; },
    clearTimeout(id) { pending.delete(id); },
  };
  const binding = { send(message) { assert.equal(this, binding); calls.push(message); return send(message); } };
  const adapter = cloudflareEmailSending({ binding, from: FROM, domain: DOMAIN, clock: () => now, timers, ...options });
  return { adapter, calls, delays, pending,
    send: (r = reg(), v = value()) => adapter.send({ reg: r, value: v }),
    setNow(at) { now = at; },
    advance(ms) { now += ms; for (const [id, task] of pending) if (task.at <= now) { pending.delete(id); task.fn(); } },
  };
}

test('structured send has the exact owner-only field set and fixed headers; provider assigns Message-ID', async () => {
  const f = fixture();
  assert.deepEqual(await f.send(), outcome('accepted', 'ok', 'provider-unique-1'));
  assert.deepEqual(f.calls, [{ from: FROM, to: OWNER, replyTo: `${LOCAL}+${TOKEN}@${DOMAIN}`,
    subject: value().subject, text: value().body,
    headers: { 'Auto-Submitted': 'auto-generated', 'X-Clayrune-Agent-Mail': '1' } }]);
  assert.match(f.calls[0].replyTo, /^m[a-f0-9]{29}\+[a-f0-9]{32}@mail\.example\.net$/);
  assert.equal(f.calls[0].replyTo.split('@')[0].length, 63);
  assert.deepEqual(Object.keys(f.calls[0]).sort(), ['from', 'headers', 'replyTo', 'subject', 'text', 'to']);
  assert.deepEqual(f.delays, [10_000]); assert.equal(f.pending.size, 0);
  f.advance(100_000); await tick(); assert.equal(f.calls.length, 1);
});

test('recipient is the supplied current owner snapshot, preserving local case/dots/plus exactly', async () => {
  const f = fixture();
  const owner = 'Different.Owner+keep@EXAMPLE.org';
  assert.equal((await f.send({ ...reg(), owner_address: owner })).outcome, 'accepted');
  assert.equal(f.calls[0].to, owner); assert.equal(typeof f.calls[0].to, 'string');
});

for (const field of ['to', 'cc', 'bcc', 'from', 'replyTo', 'reply_to', 'headers', 'raw', 'html',
  'attachments', 'message_id', 'messageId', 'callback_url', 'host']) {
  test(`caller ${field} override refuses before binding invocation`, async () => {
    const f = fixture();
    await assert.rejects(f.send(reg(), { ...value(), [field]: 'stranger@example.org' }), /^Error: invalid_send$/);
    assert.equal(f.calls.length, 0); assert.equal(f.pending.size, 0);
  });
}

for (const token of ['', 'a'.repeat(31), 'a'.repeat(33), 'A'.repeat(32), 'g'.repeat(32),
  `${'a'.repeat(31)}\n`, 'a'.repeat(32) + '\n', 123, null]) {
  test(`refuses invalid reply token ${JSON.stringify(token)}`, async () => {
    const f = fixture();
    await assert.rejects(f.send(reg(), { ...value(), reply_token: token }), /^Error: invalid_send$/);
    assert.equal(f.calls.length, 0);
  });
}

for (const overrides of [{ active: 0 }, { active: '1' }, { owner_verified: 0 }, { owner_verified: 'true' },
  { owner_address: 'Display <owner@example.com>' }, { owner_address: 'a@example.com,b@example.com' },
  { owner_address: 'a@example.com\r\nBcc: evil@example.com' }, { owner_address: '' },
  { address_local: 'legacy' }, { address_local: `${LOCAL}+${TOKEN}` }, { address_local: LOCAL + '\n' }]) {
  test(`refuses unverified/inactive/invalid owner snapshot ${JSON.stringify(overrides)}`, async () => {
    const f = fixture();
    await assert.rejects(f.send({ ...reg(), ...overrides }), /^Error: registration_inactive$/);
    assert.equal(f.calls.length, 0);
  });
}

for (const config of [{ binding: undefined }, { binding: {} }, { from: 'bad' },
  { from: 'Display <agents@mail.example.net>' }, { from: 'agents@other.example' },
  { domain: 'MAIL.example.net' }, { domain: 'mail.example.net\r\nBcc: other@example.com' },
  { clock: 1 }, { timers: {} }]) {
  test(`invalid service configuration fails before send (${Object.keys(config)})`, () => {
    assert.throws(() => fixture(undefined, config), /^Error: provider_config_required$/);
  });
}

test('subject/body use UTF-8 byte bounds, plain text only, without MIME transformation', async () => {
  const f = fixture(); const subject = '\u{1f9f1}'.repeat(50), body = '\u00e9'.repeat(8192);
  assert.equal((await f.send(reg(), { ...value(), subject, body })).outcome, 'accepted');
  assert.equal(f.calls[0].subject, subject); assert.equal(f.calls[0].text, body);
  for (const fields of [{ subject: subject + 'x' }, { body: body + 'x' }, { subject: ' ' }, { body: '\n' },
    { subject: 'hello\r\nBcc: evil@example.com' }, { subject: 'x\x7f' }, { body: 'x\0' }]) {
    await assert.rejects(f.send(reg(), { ...value(), ...fields }), /^Error: invalid_send$/);
  }
  assert.equal(f.calls.length, 1);
});

for (const id of ['p', 'x'.repeat(256), 'opaque / id:<not-an-rfc-message-id>']) {
  test(`valid provider id is returned unchanged, opaque (${id.length} bytes)`, async () => {
    const f = fixture(async () => ({ messageId: id }));
    assert.deepEqual(await f.send(), outcome('accepted', 'ok', id)); assert.equal(f.calls.length, 1);
  });
}

for (const receipt of [undefined, null, {}, { messageId: '' }, { messageId: ' ' }, { messageId: 1 },
  { messageId: 'x'.repeat(257) }, { messageId: 'nonascii\u00e9' }, { messageId: 'x\r\n' },
  { messageId: 'x\0' }, { messageId: 'x\x7f' }, { messageId: 'x\t' }, { provider_id: 'wrong-field' }]) {
  test(`invalid provider receipt is uncertain, never repeated: ${JSON.stringify(receipt)}`, async () => {
    const f = fixture(async () => receipt);
    assert.deepEqual(await f.send(), UNKNOWN); assert.equal(f.pending.size, 0);
    f.advance(60_000); await tick(); assert.equal(f.calls.length, 1);
  });
}

const ERRORS = [
  ['E_RATE_LIMIT_EXCEEDED', 'retryable', 'provider_quota'],
  ['E_DAILY_LIMIT_EXCEEDED', 'retryable', 'provider_quota'],
  ['E_RECIPIENT_SUPPRESSED', 'permanent', 'provider_suppressed'],
  ...['E_VALIDATION_ERROR', 'E_FIELD_MISSING', 'E_TOO_MANY_RECIPIENTS', 'E_TOO_MANY_ATTACHMENTS',
    'E_SENDER_NOT_VERIFIED', 'E_RECIPIENT_NOT_ALLOWED', 'E_ACCOUNT_SUSPENDED',
    'E_SENDER_DOMAIN_NOT_AVAILABLE', 'E_CONTENT_TOO_LARGE', 'E_HEADER_NOT_ALLOWED',
    'E_HEADER_USE_API_FIELD', 'E_HEADER_VALUE_INVALID', 'E_HEADER_VALUE_TOO_LONG',
    'E_HEADER_NAME_INVALID', 'E_HEADERS_TOO_LARGE', 'E_HEADERS_TOO_MANY']
    .map((code) => [code, 'permanent', 'provider_unavailable']),
  ...['E_INTERNAL_SERVER_ERROR', 'E_DELIVERY_FAILED', 'ETIMEDOUT', 'ECONNRESET', 'E_FUTURE_CODE', undefined]
    .map((code) => [code, 'uncertain', 'uncertain']),
];
for (const [code, state, reason] of ERRORS) {
  test(`Error.code ${code} maps to ${state}/${reason}, without exception text or retry`, async () => {
    const privateText = `private body, ${OWNER}, ${TOKEN}`;
    const f = fixture(async () => { throw Object.assign(new Error(privateText), { code }); });
    const got = await f.send();
    assert.deepEqual(got, outcome(state, reason)); assert.equal(JSON.stringify(got).includes(privateText), false);
    assert.equal(f.pending.size, 0); f.advance(100_000); await tick(); assert.equal(f.calls.length, 1);
  });
}

test('synchronous binding errors are classified once; transport error text cannot spoof refusal', async () => {
  for (const error of [new Error('E_RECIPIENT_SUPPRESSED'), 'E_RATE_LIMIT_EXCEEDED',
    { code: 'E_RATE_LIMIT_EXCEEDED' }, null]) {
    const f = fixture(() => { throw error; });
    assert.deepEqual(await f.send(), UNKNOWN); assert.equal(f.calls.length, 1); assert.equal(f.pending.size, 0);
  }
  const f = fixture(() => { throw Object.assign(new Error('private'), { code: 'E_RECIPIENT_SUPPRESSED' }); });
  assert.deepEqual(await f.send(), outcome('permanent', 'provider_suppressed'));
});

test('unsettled submission times out exactly at 10s; late acceptance cannot change outcome or resend', async () => {
  const wait = deferred(), f = fixture(() => wait.promise);
  const sending = f.send(); let settled = false; sending.then(() => { settled = true; });
  assert.equal(f.calls.length, 1); assert.deepEqual(f.delays, [10_000]);
  f.advance(9999); await tick(); assert.equal(settled, false);
  f.advance(1); assert.deepEqual(await sending, UNKNOWN); assert.equal(f.pending.size, 0);
  wait.resolve({ messageId: 'late-accepted' }); await tick(); f.advance(100_000);
  assert.deepEqual(await sending, UNKNOWN); assert.equal(f.calls.length, 1);
});

test('late rejection after timeout is handled, with no unhandled rejection or second call', async () => {
  const wait = deferred(), f = fixture(() => wait.promise);
  const sending = f.send(); f.advance(10_000); assert.deepEqual(await sending, UNKNOWN);
  wait.reject(Object.assign(new Error('private'), { code: 'E_RATE_LIMIT_EXCEEDED' }));
  await tick(); assert.equal(f.calls.length, 1); assert.equal(f.pending.size, 0);
});

test('receipt settled before deadline accepts and cancels timeout', async () => {
  const wait = deferred(), f = fixture(() => wait.promise);
  const sending = f.send(); f.advance(9999); wait.resolve({ messageId: 'just-in-time' });
  assert.deepEqual(await sending, outcome('accepted', 'ok', 'just-in-time'));
  assert.equal(f.pending.size, 0); f.advance(1); assert.equal(f.calls.length, 1);
});

test('clock deadline fences receipt/refusal even if timer callback has not run yet', async () => {
  for (const reject of [false, true]) {
    const wait = deferred(), f = fixture(() => wait.promise), sending = f.send();
    f.setNow(NOW + 10_000);
    if (reject) wait.reject(Object.assign(new Error('private'), { code: 'E_DAILY_LIMIT_EXCEEDED' }));
    else wait.resolve({ messageId: 'too-late' });
    assert.deepEqual(await sending, UNKNOWN); assert.equal(f.pending.size, 0); assert.equal(f.calls.length, 1);
  }
});

test('injected invalid initial clock refuses without invoking provider', async () => {
  for (const now of [NaN, Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER]) {
    const f = fixture(); f.setNow(now);
    await assert.rejects(f.send(), /^Error: provider_config_required$/); assert.equal(f.calls.length, 0);
  }
});

test('invalid or regressed completion clock cannot establish timely acceptance/refusal', async () => {
  for (const at of [NaN, Infinity, NOW - 1]) {
    for (const reject of [false, true]) {
      const wait = deferred(), f = fixture(() => wait.promise), sending = f.send();
      f.setNow(at);
      if (reject) wait.reject(Object.assign(new Error('private'), { code: 'E_RATE_LIMIT_EXCEEDED' }));
      else wait.resolve({ messageId: 'opaque' });
      assert.deepEqual(await sending, UNKNOWN); assert.equal(f.calls.length, 1); assert.equal(f.pending.size, 0);
    }
  }
});

test('unpaired Unicode surrogates refuse without provider-side content replacement', async () => {
  const f = fixture();
  for (const fields of [{ subject: 'hello\ud800' }, { subject: '\udc00' }, { body: 'hi\ud800' }, { body: '\udc00' }]) {
    await assert.rejects(f.send(reg(), { ...value(), ...fields }), /^Error: invalid_send$/);
  }
  assert.equal(f.calls.length, 0);
});

test('default real clock and timers send once and clean their timeout without network', async () => {
  let calls = 0;
  const adapter = cloudflareEmailSending({ from: FROM, domain: DOMAIN,
    binding: { async send() { calls++; return { messageId: 'default-timers' }; } } });
  assert.deepEqual(await adapter.send({ reg: reg(), value: value() }), outcome('accepted', 'ok', 'default-timers'));
  assert.equal(calls, 1);
});
