import test from 'node:test';
import assert from 'node:assert/strict';
import { sanitizeName, displayLabel, quotedLabel, FALLBACK_NAME } from '../display-name.js';
import { cloudflareEmailSending } from '../email-sending.js';
import { constructMail, validateSend } from '../outbound.js';

const FROM = 'agents@mail.example.net', DOMAIN = 'mail.example.net';
const TOKEN = '0123456789abcdef'.repeat(2), LOCAL = `m${'a'.repeat(29)}`;
const base = () => ({ intent_id: 'intent_1', thread_id: 'thread-1', kind: 'note', reply_token: TOKEN,
  subject: 'Hello', body: 'Body text' });
const reg = () => ({ active: 1, owner_verified: 1, address_local: LOCAL, owner_address: 'owner@example.com' });
async function sent(display_name) {
  const calls = [];
  const adapter = cloudflareEmailSending({ from: FROM, domain: DOMAIN,
    binding: { async send(m) { calls.push(m); return { messageId: 'p1' }; } } });
  const value = display_name === undefined ? base() : { ...base(), display_name };
  assert.equal((await adapter.send({ reg: reg(), value })).outcome, 'accepted');
  return calls[0];
}

test('allowlisted names pass, whitespace collapses, label gets the via suffix', () => {
  for (const [raw, want] of [['Dave', 'Dave'], ['  Posy   Q.  ', 'Posy Q.'], ["O'Neil-Smith_2", "O'Neil-Smith_2"],
    ['a'.repeat(40), 'a'.repeat(40)]]) assert.equal(sanitizeName(raw), want);
  assert.equal(displayLabel('Dave'), 'Dave via Clayrune');
  assert.equal(displayLabel(undefined), FALLBACK_NAME); // fallback is already generic: no suffix
});

test('anything outside the allowlist, over 40 chars, empty or non-string falls back', () => {
  for (const raw of ['a'.repeat(41), '', '   ', 'Dave<evil@x.com>', 'Dave"', 'Dave\\', 'Dave;', 'Dave,x', 'Zoë',
    'Dave X', 'Dave\ud800', 'Dave\r\nBcc: evil@example.com', 'Dave\nX', 'Dave\tX', 'Dave\x00', 'Dave\x7f',
    'Dave X', 'Dave\u0085X', 42, null, undefined, {}, ['Dave']]) {
    assert.equal(sanitizeName(raw), FALLBACK_NAME, JSON.stringify(raw));
  }
});

test('quotedLabel is an RFC 5322 quoted-string and escapes backslash and double-quote', () => {
  assert.equal(quotedLabel("Dave O'Neil."), `"Dave O'Neil. via Clayrune"`);
  // Unreachable through sanitizeName, but the escaper must hold on its own.
  assert.equal(`"${'a"b\\c'.replace(/[\\"]/g, '\\$&')}"`, '"a\\"b\\\\c"');
});

test('structured send carries the display name on From and Reply-To, address unchanged', async () => {
  const m = await sent('Dave');
  assert.deepEqual(m.from, { email: FROM, name: 'Dave via Clayrune' });
  assert.deepEqual(m.replyTo, { email: `${LOCAL}@${DOMAIN}`, name: 'Dave via Clayrune' });
  assert.deepEqual(Object.keys(m).sort(), ['from', 'headers', 'replyTo', 'subject', 'text', 'to']);
});

test('older install without display_name still validates and sends with the fallback', async () => {
  assert.equal(validateSend(base()), true);
  const m = await sent(undefined);
  assert.deepEqual(m.from, { email: FROM, name: FALLBACK_NAME });
  assert.equal(m.replyTo.name, FALLBACK_NAME);
});

test('INJECTION: CRLF + Bcc in display_name sends with the fallback, adds no recipient or header', async () => {
  for (const evil of ['Dave\r\nBcc: evil@example.com', 'Dave\nBcc: evil@example.com', 'Dave" <evil@example.com>, "x',
    'Dave\r\n\r\nInjected body']) {
    assert.equal(validateSend({ ...base(), display_name: evil }), true); // never fails the send
    const m = await sent(evil);
    assert.equal(m.from.name, FALLBACK_NAME); assert.equal(m.replyTo.name, FALLBACK_NAME);
    assert.equal(m.to, 'owner@example.com'); assert.equal(m.bcc, undefined);
    assert.equal(JSON.stringify(m).includes('evil@example.com'), false);
    assert.deepEqual(Object.keys(m.headers).sort(), ['Auto-Submitted', 'References', 'X-Clayrune-Agent-Mail']);
  }
});

test('display_name is only type- and size-bounded by validateSend; other unknown keys still refuse', () => {
  assert.equal(validateSend({ ...base(), display_name: '' }), true);
  assert.equal(validateSend({ ...base(), display_name: 'x'.repeat(256) }), true);
  for (const bad of [{ display_name: 'x'.repeat(257) }, { display_name: 5 }, { display_name: null },
    { display_name: ['Dave'] }, { Display_Name: 'Dave' }, { name: 'Dave' }]) {
    assert.equal(validateSend({ ...base(), ...bad }), false, JSON.stringify(bad));
  }
  assert.equal(validateSend({ ...base(), subject: 'a\r\nBcc: x' }), false); // subject rules unchanged
});

test('legacy raw MIME path: quoted display name on From and Reply-To, fallback on injection', () => {
  const r = { owner_address: 'owner@example.com', address_local: 'opaque1' };
  const config = { from: FROM, domain: DOMAIN };
  const raw = constructMail(r, { ...base(), display_name: "Dave O'Neil." }, 'id@mail.example.net', config).raw;
  assert.match(raw, /^From: "Dave O'Neil\. via Clayrune" <agents@mail\.example\.net>\r$/m);
  assert.match(raw, new RegExp(`^Reply-To: "Dave O'Neil\\. via Clayrune" <opaque1@mail\\.example\\.net>\\r$`, 'm'));
  const bad = constructMail(r, { ...base(), display_name: 'Dave\r\nBcc: evil@example.com' }, 'id@mail.example.net', config).raw;
  assert.doesNotMatch(bad, /evil@example\.com/); assert.doesNotMatch(bad, /^Bcc:/mi);
  assert.match(bad, /^From: "Clayrune agent" </m);
  assert.equal(bad.split('\r\n').filter((l) => l === '').length, 2, 'one header/body break plus the trailing newline');
});
