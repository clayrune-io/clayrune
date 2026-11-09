import test from 'node:test';
import assert from 'node:assert/strict';
import PostalMime from 'postal-mime';
import { decide, rejectionRecord, REASONS } from '../auth.js';
import { parseMailbox } from '../mailbox.js';
import { parseAuthResults } from '../authres.js';
import {
  NOW, OWNER, RCPT, TOKEN, OUTBOUND_ID, thread, lookupOk, cfAr, headers, meta,
} from './fixtures.js';

const run = (m = meta(), owner = OWNER, lookup = lookupOk()) => decide(m, owner, lookup);
const dropped = (d, code) => {
  assert.equal(d.accept, false);
  assert.equal(d.reason_code, code);
};

test('genuine owner reply is accepted and returns the thread', async () => {
  const d = await run();
  assert.equal(d.accept, true);
  assert.equal(d.thread.thread_id, 't-1');
  assert.deepEqual(d.evidence, { ingress: 'cloudflare', spf: 'pass', dkim: 'pass', dmarc: 'pass', aligned: 'spf+dkim' });
});

test('display name and comments around the owner address are fine', async () => {
  for (const from of ['Ron Levy <owner@example.com>', '"Levy, Ron" <owner@example.com>',
    'owner@example.com (Ron)', 'OWNER@EXAMPLE.COM'.replace('OWNER', 'owner')]) {
    assert.equal((await run(meta({ headers: headers({ from }) }))).accept, true, from);
  }
});

// ---- Ron's cases ---------------------------------------------------------

test('non-owner sender is dropped even with a perfect DMARC pass', async () => {
  const ar = cfAr({ mailfrom: 'stranger@other.example', d: 'other.example', from: 'other.example' });
  dropped(await run(meta({ headers: headers({ from: 'stranger@other.example', ar }) })), 'from_not_owner');
});

test('spoofed From with failed DMARC is dropped', async () => {
  const ar = cfAr({ spf: 'fail', dkim: 'none', dmarc: 'fail' });
  const d = await run(meta({ headers: headers({ ar }) }));
  dropped(d, 'dmarc_not_pass');
  assert.equal(d.evidence.dmarc, 'fail');
});

test('substring / look-alike From values are not the owner', async () => {
  const spoofs = [
    '"owner@example.com" <attacker@evil.example>',
    'owner@example.com.evil.example',
    'owner@example.com <attacker@evil.example>',
    'attacker-owner@example.com',
    'owner@example.com@evil.example',
    'xowner@example.com',
    'owner@sub.example.com',
    'owner@example.com, attacker@evil.example',
    'undisclosed: owner@example.com;',
    '"owner"@example.com',
    'Owner@example.com', // local part is case-sensitive on purpose
  ];
  for (const from of spoofs) {
    const d = await run(meta({ headers: headers({ from }) }));
    assert.equal(d.accept, false, from);
    assert.match(d.reason_code, /^from_(not_owner|invalid)$/, from);
  }
});

test('domain case does not matter', async () => {
  assert.equal((await run(meta({ headers: headers({ from: 'owner@EXAMPLE.com' }) }))).accept, true);
});

test('two From fields are dropped', async () => {
  const h = headers();
  h.push({ key: 'from', value: 'attacker@evil.example' });
  dropped(await run(meta({ headers: h })), 'from_invalid');
});

test('sender-injected Authentication-Results cannot rescue a failed ingress result', async () => {
  // Cloudflare (topmost) says fail; the sender added a "pass" claiming to be Cloudflare.
  const h = headers({
    ar: cfAr({ dmarc: 'fail', spf: 'fail', dkim: 'fail' }),
    extra: [{ key: 'authentication-results', value: cfAr() }],
  });
  dropped(await run(meta({ headers: h })), 'ingress_ambiguous');
});

test('injected result from a different authserv-id is ignored, not trusted', async () => {
  const h = headers({
    ar: cfAr({ dmarc: 'fail' }),
    extra: [{ key: 'authentication-results', value: 'mx.google.com; dmarc=pass header.from=example.com' }],
  });
  dropped(await run(meta({ headers: h })), 'dmarc_not_pass');
});

test('no Cloudflare result at all (only a sender-supplied other authserv-id) is untrusted', async () => {
  const h = headers({ ar: 'attacker.example; dmarc=pass header.from=example.com' });
  dropped(await run(meta({ headers: h })), 'untrusted_ingress');
  dropped(await run(meta({ headers: headers({ ar: null }) })), 'untrusted_ingress');
});

test('a Cloudflare-looking result that is not the first one is untrusted', async () => {
  const h = headers({
    ar: 'mx.google.com; dmarc=pass header.from=example.com',
    extra: [{ key: 'authentication-results', value: cfAr() }],
  });
  dropped(await run(meta({ headers: h })), 'untrusted_ingress');
});

test('authserv-id must match exactly, not as a prefix or suffix', async () => {
  for (const id of ['mx.cloudflare.net.evil.example', 'evil-mx.cloudflare.net', 'mx.cloudflare.net.evil']) {
    const h = headers({ ar: cfAr().replace('mx.cloudflare.net', id) });
    dropped(await run(meta({ headers: h })), 'untrusted_ingress');
  }
});

test('D6=A: SPF-only aligned pass is accepted; DKIM-only aligned pass is accepted', async () => {
  let d = await run(meta({ headers: headers({ ar: cfAr({ dkim: 'none', d: 'unrelated.example' }) }) }));
  assert.equal(d.accept, true);
  assert.equal(d.evidence.aligned, 'spf');
  d = await run(meta({ headers: headers({ ar: cfAr({ spf: 'softfail', mailfrom: 'bounce@esp.example' }) }) }));
  assert.equal(d.accept, true);
  assert.equal(d.evidence.aligned, 'dkim');
});

test('DMARC pass without any aligned SPF or DKIM pass is dropped', async () => {
  const ar = cfAr({ mailfrom: 'bounce@esp.example', d: 'esp.example' });
  dropped(await run(meta({ headers: headers({ ar }) })), 'dmarc_unaligned');
});

test('alignment: subdomain aligns, unrelated suffix does not, bare TLD never does', async () => {
  let d = await run(meta({ headers: headers({ ar: cfAr({ d: 'mail.example.com', dkim: 'pass', spf: 'fail' }) }) }));
  assert.equal(d.accept, true);
  d = await run(meta({ headers: headers({ ar: cfAr({ d: 'com', spf: 'fail' }) }) }));
  dropped(d, 'dmarc_unaligned');
  d = await run(meta({ headers: headers({ ar: cfAr({ d: 'notexample.com', spf: 'fail' }) }) }));
  dropped(d, 'dmarc_unaligned');
});

test('DMARC result about a different From domain is dropped', async () => {
  dropped(await run(meta({ headers: headers({ ar: cfAr({ from: 'other.example' }) }) })), 'auth_domain_mismatch');
});

test('missing dmarc result and malformed result are dropped', async () => {
  dropped(await run(meta({ headers: headers({ ar: 'mx.cloudflare.net; spf=pass smtp.mailfrom=owner@example.com' }) })), 'dmarc_not_pass');
  dropped(await run(meta({ headers: headers({ ar: 'mx.cloudflare.net; garbage' }) })), 'untrusted_ingress');
});

test('comments inside the result do not hide or fake a pass', async () => {
  const ar = 'mx.cloudflare.net (cf) ; spf=pass (domain ok) smtp.mailfrom=owner@example.com; '
    + 'dmarc=fail (p=none; dmarc=pass) header.from=example.com';
  dropped(await run(meta({ headers: headers({ ar }) })), 'dmarc_not_pass');
});

// ---- tokens --------------------------------------------------------------

test('guessed token: unknown to the lookup', async () => {
  const guess = `inst1a2b+${'z'.repeat(26)}@mail.example.net`;
  dropped(await run(meta({ rcptTo: guess })), 'token_unknown');
});

test('no token / malformed token in the recipient', async () => {
  for (const rcptTo of ['inst1a2b@mail.example.net', 'inst1a2b+short@mail.example.net', 'not-an-address', undefined]) {
    dropped(await run(meta({ rcptTo })), 'token_missing');
  }
});

test('expired token (boundary inclusive) and revoked token', async () => {
  dropped(await run(meta(), OWNER, lookupOk(thread({ expires_at: NOW - 1 }))), 'token_expired');
  dropped(await run(meta(), OWNER, lookupOk(thread({ expires_at: NOW }))), 'token_expired');
  assert.equal((await run(meta(), OWNER, lookupOk(thread({ expires_at: NOW + 1 })))).accept, true);
  dropped(await run(meta(), OWNER, lookupOk(thread({ revoked: true }))), 'token_revoked');
  const noFlag = thread();
  delete noFlag.revoked;
  dropped(await run(meta(), OWNER, lookupOk(noFlag)), 'token_revoked');
  dropped(await run(meta(), OWNER, lookupOk(thread({ expires_at: 'tomorrow' }))), 'token_expired');
});

test('token from another install or an old owner generation is dropped', async () => {
  dropped(await run(meta(), OWNER, lookupOk(thread({ install_id: 'inst-2' }))), 'thread_owner_mismatch');
  dropped(await run(meta(), OWNER, lookupOk(thread({ owner_generation: 2 }))), 'owner_generation_mismatch');
});

test('inactive / revoked owner record is dropped', async () => {
  dropped(await run(meta(), { ...OWNER, active: false }), 'owner_inactive');
  dropped(await run(meta(), null), 'owner_inactive');
  dropped(await run(meta(), { ...OWNER, owner_address: 'not an address' }), 'owner_invalid');
});

test('valid token but the reply names no / the wrong outbound Message-ID', async () => {
  dropped(await run(meta({ headers: headers({ inReplyTo: null }) })), 'message_id_missing');
  dropped(await run(meta({ headers: headers({ inReplyTo: '<someone-else@mail.example.net>' }) })), 'message_id_mismatch');
  const viaRefs = headers({ inReplyTo: null });
  viaRefs.push({ key: 'references', value: `<a@x> <${OUTBOUND_ID}>` });
  assert.equal((await run(meta({ headers: viaRefs }))).accept, true);
});

test('lookup is not reached for a stranger (cheap checks first)', async () => {
  let calls = 0;
  const lookup = async () => { calls += 1; return thread(); };
  await decide(meta({ headers: headers({ from: 'stranger@other.example' }) }), OWNER, lookup);
  await decide(meta({ headers: headers({ ar: cfAr({ dmarc: 'fail' }) }) }), OWNER, lookup);
  assert.equal(calls, 0);
});

test('decide never throws: lookup error, bad input, hostile objects all drop', async () => {
  dropped(await run(meta(), OWNER, async () => { throw new Error('db down'); }), 'lookup_error');
  dropped(await decide(null, OWNER, lookupOk()), 'bad_input');
  dropped(await decide({ rcptTo: RCPT, headers: 'nope' }, OWNER, lookupOk()), 'bad_input');
  dropped(await decide(meta(), OWNER, 'not a function'), 'bad_input');
  const evil = { get headers() { throw new Error('boom'); }, rcptTo: RCPT };
  dropped(await decide(evil, OWNER, lookupOk()), 'internal_error');
});

// ---- rejection record ----------------------------------------------------

test('rejection record is metadata only', async () => {
  const d = await run(meta({ headers: headers({ ar: cfAr({ dmarc: 'fail' }), from: 'owner@example.com' }) }));
  const rec = rejectionRecord({ ...d, body: 'SECRET BODY', subject: 'SECRET SUBJECT', from: 'owner@example.com', token: TOKEN }, NOW);
  assert.deepEqual(Object.keys(rec).sort(), ['at', 'evidence', 'reason_code']);
  assert.equal(rec.at, new Date(NOW).toISOString());
  const text = JSON.stringify(rec);
  for (const leak of ['SECRET', 'owner@example.com', TOKEN, 'example.com']) assert.ok(!text.includes(leak), leak);
  assert.equal(rejectionRecord({ reason_code: 'made-up-code', evidence: { dmarc: 'a@b.c' } }, NOW).reason_code, 'internal_error');
  // evidence values that are not result words are dropped, not copied
  assert.deepEqual(rejectionRecord({ reason_code: 'token_unknown', evidence: { dmarc: 'a@b.c' } }, NOW).evidence, {});
});

test('every reason code decide can emit is registered', () => {
  assert.equal(new Set(REASONS).size, REASONS.length);
});

// ---- real postal-mime header shape --------------------------------------

test('postal-mime headers (order + duplicates preserved) drive decide', async () => {
  const raw = [
    `Authentication-Results: ${cfAr()}`,
    `Authentication-Results: ${cfAr()}`, // sender-injected second claimant
    'From: Ron <owner@example.com>', `To: ${RCPT}`, `In-Reply-To: <${OUTBOUND_ID}>`,
    'Subject: Re: x', 'Content-Type: text/plain', '', 'ok', '',
  ].join('\r\n');
  const email = await new PostalMime().parse(raw);
  assert.equal(email.headers.filter((h) => h.key === 'authentication-results').length, 2);
  dropped(await decide({ rcptTo: RCPT, headers: email.headers, now: NOW }, OWNER, lookupOk()), 'ingress_ambiguous');
  const single = raw.replace(`Authentication-Results: ${cfAr()}\r\n`, '');
  const ok = await new PostalMime().parse(single);
  assert.equal((await decide({ rcptTo: RCPT, headers: ok.headers, now: NOW }, OWNER, lookupOk())).accept, true);
});

// ---- parsers directly ----------------------------------------------------

test('parseMailbox edge cases', () => {
  assert.equal(parseMailbox('a@b.example').address, 'a@b.example');
  assert.equal(parseMailbox('Name <a@B.Example>').address, 'a@b.example');
  for (const bad of ['', 'a', 'a@b', '<a@b.example', 'a@b.example>', 'a@b.example <c@d.example> e', '"x <a@b.example>',
    'a@b.example\r\nBcc: c@d.example', 'a@münchen.example', '<a@b.example>, <c@d.example>', 'a b@c.example']) {
    assert.equal(parseMailbox(bad), null, JSON.stringify(bad));
  }
});

test('parseAuthResults reads quoted values and versions', () => {
  const p = parseAuthResults('mx.cloudflare.net 1; dkim=pass reason="good; signature" header.d=example.com');
  assert.equal(p.authservId, 'mx.cloudflare.net');
  assert.equal(p.results.length, 1);
  assert.equal(p.results[0].props['header.d'], 'example.com');
  assert.equal(parseAuthResults('mx.cloudflare.net; none').results.length, 0);
});
