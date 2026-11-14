// Reply token in References (reply-token.js) and its inbound read in auth.js
// decide(): plain recipient + token id in References/In-Reply-To. Synthetic
// fixtures only (reserved example domains).
import test from 'node:test';
import assert from 'node:assert/strict';
import { decide, rejectionRecord, REASONS } from '../auth.js';
import { referencesValue, headerTokens, newNonce, MAX_CANDIDATES } from '../reply-token.js';
import { cfAr, headers, thread, OWNER, NOW } from './fixtures.js';

const DOMAIN = 'mail.example.net';
const PLAIN = `inst1a2b@${DOMAIN}`;
const T = 'ab12cd34'.repeat(4); // the valid 32-hex reply token
const NONCE = 'fixednonce0123456789';
const id = (token, n = NONCE, domain = DOMAIN) => `<${token}.${n}@${domain}>`;
const hex = (n) => n.toString(16).padStart(32, '0');
const hdr = (key, value) => ({ key, value });

function spy(valid = T, t = thread()) {
  const calls = [];
  const lookup = async (token) => { calls.push(token); return token === valid ? t : null; };
  return { lookup, calls };
}
const mail = (extra = [], over = {}) => ({
  rcptTo: PLAIN, now: NOW,
  headers: headers({ inReplyTo: '<cf-assigned-id@example.com>', extra, ...over }),
});
const dropped = (d, code) => { assert.equal(d.accept, false); assert.equal(d.reason_code, code); };

// ---- outbound value ------------------------------------------------------

test('referencesValue is <token.nonce@domain>; nonce is fresh 128-bit hex and never the token', () => {
  const a = referencesValue(T, DOMAIN), b = referencesValue(T, DOMAIN);
  assert.match(a, new RegExp(`^<${T}\\.[a-f0-9]{32}@mail\\.example\\.net>$`));
  assert.notEqual(a, b);
  assert.match(newNonce(), /^[a-f0-9]{32}$/);
  assert.equal(referencesValue(T, DOMAIN, NONCE), `<${T}.${NONCE}@${DOMAIN}>`);
  assert.equal(referencesValue(T, DOMAIN, () => NONCE), `<${T}.${NONCE}@${DOMAIN}>`);
});

test('referencesValue refuses a bad token, nonce or domain (header-injection surface)', () => {
  for (const token of ['short', T.toUpperCase(), `${T}\r\nBcc: x@y.z`, '', undefined]) {
    assert.throws(() => referencesValue(token, DOMAIN, NONCE), /invalid_service_address/);
  }
  for (const nonce of ['', 'short', `${NONCE}>\r\nBcc: x@y.z`, 'has space 0123456789', 5]) {
    assert.throws(() => referencesValue(T, DOMAIN, nonce), /invalid_service_address/);
  }
  for (const domain of ['', 'Mail.Example.net', 'a b', `x.net>\r\nBcc: x@y.z`, undefined]) {
    assert.throws(() => referencesValue(T, domain, NONCE), /invalid_service_address/);
  }
});

// ---- headerTokens --------------------------------------------------------

test('headerTokens: nearest (last) occurrence first, deduped, other domains and non-token ids ignored', () => {
  const t1 = hex(1), t2 = hex(2), t3 = hex(3);
  const h = [
    hdr('in-reply-to', `<cf-id@example.com> ${id(t3)}`),
    hdr('references', `${id(t1)} <plain@${DOMAIN}> ${id(t2)} ${id(t1, 'again')} ${id(hex(9), NONCE, 'other.example')} <${'z'.repeat(32)}.n@${DOMAIN}>`),
  ];
  assert.deepEqual(headerTokens(h, DOMAIN), [t3, t1, t2]);
  assert.deepEqual(headerTokens(h, 'MAIL.example.NET'), [t3, t1, t2]); // domain compared case-insensitively
  assert.deepEqual(headerTokens(h, 'other.example'), [hex(9)]);
  assert.deepEqual(headerTokens([], DOMAIN), []);
  assert.deepEqual(headerTokens(null, DOMAIN), []);
  assert.deepEqual(headerTokens(h, undefined), []);
});

test('headerTokens: needs exactly 32 hex then a dot; a longer or dotless local part is not a token id', () => {
  for (const local of [`${T}`, `${T}0.x`, T.slice(1) + '.x', `${T.toUpperCase()}`.slice(0, 31) + '.x', `x${T}.n`]) {
    assert.deepEqual(headerTokens([hdr('references', `<${local}@${DOMAIN}>`)], DOMAIN), [], local);
  }
  assert.deepEqual(headerTokens([hdr('references', `<${T.toUpperCase()}.n@${DOMAIN}>`)], DOMAIN), [T]); // case folded
});

test(`headerTokens caps one message at ${MAX_CANDIDATES} distinct tokens`, () => {
  const refs = Array.from({ length: 50 }, (_, i) => id(hex(i + 1))).join(' ');
  const got = headerTokens([hdr('references', refs)], DOMAIN);
  assert.equal(got.length, MAX_CANDIDATES);
  assert.equal(got[0], hex(50)); // nearest (last) first
});

// ---- decide(): References path ------------------------------------------

test('plain recipient + our token id in References: accepted, thread resolved by that token', async () => {
  const s = spy();
  const d = await decide(mail([hdr('references', id(T))]), OWNER, s.lookup);
  assert.equal(d.accept, true);
  assert.equal(d.thread.thread_id, 't-1');
  assert.deepEqual(s.calls, [T]);
});

test('token in In-Reply-To alone is enough', async () => {
  const s = spy();
  const d = await decide(mail([], { inReplyTo: id(T) }), OWNER, s.lookup);
  assert.equal(d.accept, true);
  assert.deepEqual(s.calls, [T]);
});

test('a Gmail-style References chain with several ids and one valid token is accepted', async () => {
  const s = spy();
  const refs = `<old@example.com> ${id(hex(7))} ${id(T)} <CAF=cf-assigned@mail.example.net>`;
  const d = await decide(mail([hdr('references', refs)]), OWNER, s.lookup);
  assert.equal(d.accept, true);
  // nearest ancestor first: the valid token is the 2nd id from the end, so it is tried
  // before hex(7) and the loop stops at the first thread that resolves.
  assert.deepEqual(s.calls, [T]);
});

test('an unknown nearer token is skipped, the valid older one still resolves', async () => {
  const s = spy();
  const d = await decide(mail([hdr('references', `${id(T)} ${id(hex(7))}`)]), OWNER, s.lookup);
  assert.equal(d.accept, true);
  assert.deepEqual(s.calls, [hex(7), T]);
});

test('only unknown tokens: token_unknown, and lookups are bounded at 4', async () => {
  const s = spy('f'.repeat(32));
  const refs = Array.from({ length: 8 }, (_, i) => id(hex(i + 1))).join(' ');
  dropped(await decide(mail([hdr('references', refs)]), OWNER, s.lookup), 'token_unknown');
  assert.equal(s.calls.length, 4);
});

test('lookup failure on a header token fails closed as lookup_error', async () => {
  const boom = async () => { throw new Error('d1 down'); };
  dropped(await decide(mail([hdr('references', id(T))]), OWNER, boom), 'lookup_error');
});

test('plain recipient with no token id anywhere is dropped (never a new chat)', async () => {
  const s = spy();
  dropped(await decide(mail(), OWNER, s.lookup), 'token_missing');
  dropped(await decide(mail([hdr('references', '<a@example.com> <b@example.com>')]), OWNER, s.lookup), 'token_missing');
  assert.equal(s.calls.length, 0);
});

test('a token id on a foreign domain is not ours: dropped as token_missing', async () => {
  const s = spy();
  dropped(await decide(mail([hdr('references', id(T, NONCE, 'evil.example'))]), OWNER, s.lookup), 'token_missing');
  assert.equal(s.calls.length, 0);
});

test('forged valid token in References from a non-owner is rejected before any lookup', async () => {
  const s = spy();
  const ar = cfAr({ mailfrom: 'stranger@other.example', d: 'other.example', from: 'other.example' });
  const m = mail([hdr('references', id(T))], { from: 'stranger@other.example', ar });
  dropped(await decide(m, OWNER, s.lookup), 'from_not_owner');
  assert.equal(s.calls.length, 0);
});

test('owner From but failed DMARC with a valid token in References is rejected before any lookup', async () => {
  const s = spy();
  const m = mail([hdr('references', id(T))], { ar: cfAr({ spf: 'fail', dkim: 'none', dmarc: 'fail' }) });
  dropped(await decide(m, OWNER, s.lookup), 'dmarc_not_pass');
  assert.equal(s.calls.length, 0);
});

test('header token resolving to another install/generation/revoked/expired thread is rejected', async () => {
  for (const [over, code] of [
    [{ install_id: 'inst-other' }, 'thread_owner_mismatch'],
    [{ owner_generation: 99 }, 'owner_generation_mismatch'],
    [{ revoked: true }, 'token_revoked'],
    [{ expires_at: NOW - 1 }, 'token_expired'],
  ]) {
    const s = spy(T, thread(over));
    dropped(await decide(mail([hdr('references', id(T))]), OWNER, s.lookup), code);
  }
});

// ---- decide(): legacy +token stays readable, and the two carriers --------

test('legacy +token recipient alone still resolves (read-only path for mail in flight)', async () => {
  const s = spy(T);
  const d = await decide({ ...mail(), rcptTo: `inst1a2b+${T}@${DOMAIN}` }, OWNER, s.lookup);
  assert.equal(d.accept, true);
  assert.deepEqual(s.calls, [T]);
});

test('+token and References agree: accepted, a single lookup', async () => {
  const s = spy();
  const d = await decide({ ...mail([hdr('references', `${id(hex(5))} ${id(T)}`)]), rcptTo: `inst1a2b+${T}@${DOMAIN}` },
    OWNER, s.lookup);
  assert.equal(d.accept, true);
  assert.deepEqual(s.calls, [T]);
});

test('+token and References disagree: token_conflict, no lookup', async () => {
  const s = spy();
  const d = await decide({ ...mail([hdr('references', id(hex(5)))]), rcptTo: `inst1a2b+${T}@${DOMAIN}` },
    OWNER, s.lookup);
  dropped(d, 'token_conflict');
  assert.deepEqual(d.evidence, { ingress: 'cloudflare', spf: 'pass', dkim: 'pass', dmarc: 'pass', aligned: 'spf+dkim' });
  assert.equal(s.calls.length, 0);
});

test('+token with unrelated (non-token) References ids is not a conflict', async () => {
  const s = spy();
  const d = await decide({ ...mail([hdr('references', '<a@example.com>')]), rcptTo: `inst1a2b+${T}@${DOMAIN}` },
    OWNER, s.lookup);
  assert.equal(d.accept, true);
});

test('token_conflict is a known reason and survives rejectionRecord', () => {
  assert.ok(REASONS.includes('token_conflict'));
  assert.equal(rejectionRecord({ accept: false, reason_code: 'token_conflict' }).reason_code, 'token_conflict');
});
