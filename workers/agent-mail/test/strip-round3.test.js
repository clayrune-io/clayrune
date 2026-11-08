// MC-1166 unit 3 fix round 3: the declared Content-Type is classified with MIME
// comment / quoted-string semantics (not substring matching), Content-ID and `start`
// are compared as normalised msg-ids (RFC 5322 3.6.4), and `Content-Type :` (space
// before the colon) is the same header to the malformed check as to the duplicate
// check. Every MIME reproduction from Fenn's round-3 review is here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';
import { strictMsgId } from '../content-id.js';

const P = 'INJECTED-CONTENT';
const OK = 'Approved.';
const msg = (headers, body = P) => `${headers}\r\n\r\n${body}`;
const multi = (type, b, parts, extra = '') =>
  msg(`Content-Type: multipart/${type}; boundary=${b}${extra}`,
    parts.map((p) => `--${b}\r\n${p}\r\n`).join('') + `--${b}--`);
const leaf = (id, body = OK, type = 'text/plain') =>
  msg(`Content-Type: ${type}` + (id === null ? '' : `\r\nContent-ID: ${id}`), body);

const refused = async (raw, code) => {
  const r = await strip(raw);
  assert.deepEqual(r, { ok: false, reason_code: code });
  assert.ok(!JSON.stringify(r).includes(P));
};
const retained = async (raw) => {
  const r = await strip(raw);
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(r.text, OK);
  assert.ok(!JSON.stringify(r).includes(P));
  return r;
};
const noText = async (raw) => {
  const r = await strip(raw);
  assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
  assert.ok(!r.ok || r.has_text === false, JSON.stringify(r));
  return r;
};

// ---- P2 #1: declared type judged through comments --------------------------

test('comment with semicolon hides multipart/signed, no boundary -> malformed_mime (Fenn P2-1a)', () =>
  refused(msg('Content-Type: (note; note) multipart/signed'), 'malformed_mime'));

test('comment with semicolon, boundary="" -> malformed_mime, attachment block not read (Fenn P2-1b)', () =>
  refused(msg('Content-Type: (note; note) multipart/signed; boundary=""',
    `--B\r\nContent-Type: text/plain\r\nContent-Disposition: attachment\r\n\r\n${P}\r\n--B--`), 'malformed_mime'));

test('comment between type and slash, multipart(note)/signed -> malformed_mime (Fenn P2-1c)', () =>
  refused(msg('Content-Type: multipart(note)/signed'), 'malformed_mime'));

test('comment between slash and subtype -> malformed_mime', () =>
  refused(msg('Content-Type: multipart/(note)signed'), 'malformed_mime'));

test('nested comment before the type -> malformed_mime', () =>
  refused(msg('Content-Type: (a (b; c) d) multipart/signed'), 'malformed_mime'));

test('plain declaration that merely mentions multipart in a comment is kept (Fenn P2-1d, regression vs 358970eb)', () =>
  retained(msg('Content-Type: text/plain (multipart/signed)', OK)));

test('multipart string in a plain type parameter is kept', () =>
  retained(msg('Content-Type: text/plain; note="multipart/signed"', OK)));

test('same malformed comment form nested under a dropped container refuses', () =>
  refused(multi('mixed', 'OUT', [leaf(null),
    multi('mixed', 'IN', [msg('Content-Type: (n; n) multipart/related')], '\r\nContent-Disposition: attachment')]), 'malformed_mime'));

test('well-formed multipart/signed with a boundary behind a comment is not a parser-fallback refusal', async () => {
  const r = await strip(multi('signed', 'B', [leaf(null), 'Content-Type: application/pgp-signature\r\n\r\nsig'])
    .replace('Content-Type: multipart/signed', 'Content-Type: (n; n) multipart/signed'));
  // signed is outside the allowlist, so nothing is read, but it is not malformed_mime
  assert.notEqual(r.reason_code, 'malformed_mime');
  assert.ok(!JSON.stringify(r).includes(OK));
});

// ---- P2 #2: Content-ID / start compared as normalised msg-ids --------------

const relatedType = (type) => `; type="${type}"; start="<root@example.net>"`;

test('Content-ID with trailing comment still matches start; plain root kept (Fenn P2-2a)', async () => {
  const r = await retained(multi('related', 'B', [
    leaf('<other@example.net>', P),
    leaf('<root@example.net> (comment)'),
  ], relatedType('text/plain')));
  assert.equal(r.source, 'plain');
  assert.equal(r.stripped.attachments, 1); // the resource is counted, not read
});

test('two Content-IDs that normalise equal count as two hits -> no root (Fenn P2-2b)', async () => {
  const r = await noText(multi('related', 'B', [
    leaf('<root@example.net> (comment)', '<p>Visible</p>', 'text/html'),
    leaf('<root@example.net>', P),
  ], relatedType('text/html')));
  assert.equal(r.reason_code, 'html_only'); // html seen, no root selected, nothing read
});

test('same pair with plain declared first: still no root', () =>
  noText(multi('related', 'B', [
    leaf('<root@example.net>', P),
    leaf('(c) <root@example.net>', P),
  ], relatedType('text/plain'))));

test('comment before the bracket, and both sides, match', () =>
  retained(multi('related', 'B', [leaf('(c1) <root@example.net> (c2)')], relatedType('text/plain'))));

test('nested comment outside the brackets is CFWS', () =>
  retained(multi('related', 'B', [leaf('<root@example.net> (a (b) c)')], relatedType('text/plain'))));

test('start parameter with a comment outside its brackets matches a bare Content-ID', () =>
  retained(multi('related', 'B', [leaf('<root@example.net>')], '; type="text/plain"; start="<root@example.net> (c)"')));

test('parentheses inside a quoted local part are not stripped', () =>
  noText(multi('related', 'B', [leaf('<"a(b)"@example.net>', P)], '; type="text/plain"; start="<a@example.net>"')));

// fix round 5: a quoted local part is not strict syntax -> no root, even matching itself
test('quoted local part with parentheses selects no root (strict ids only)', () =>
  noText(multi('related', 'B', [leaf('<"a(b)"@example.net> (c)', P)], '; type="text/plain"; start="<\\"a(b)\\"@example.net>"')));

test('different IDs stay different (case, domain)', async () => {
  await noText(multi('related', 'B', [leaf('<ROOT@example.net> (c)', P)], relatedType('text/plain')));
  await noText(multi('related', 'B', [leaf('<root@example.org> (c)', P)], relatedType('text/plain')));
});

test('junk after the closing bracket is not CFWS: no match', () =>
  noText(multi('related', 'B', [leaf('<root@example.net> junk', P)], relatedType('text/plain'))));

test('strictMsgId: unit cases', () => {
  const cases = [
    ['<a@b>', 'a@b'],
    ['  <a@b>  ', 'a@b'],
    ['<a@b> (c)', 'a@b'],
    ['(c) <a@b>', 'a@b'],
    ['(c (d) e)<a@b>(f)', 'a@b'],
    ['(esc \\) still) <a@b>', 'a@b'],
    ['a@b', 'a@b'],
    ['<root>', 'root'],
    ['<root@[192.0.2.1]>', 'root@[192.0.2.1]'],
    ['<"a(b)"@c> (d)', null],
    ['<"a>b"@c>', null],
    ['<a@b> junk', null],
    ['<a@b> (unterminated', null],
    ['<a b@c>', null],
    ['<a@b', null],
    ['', null],
    [undefined, null],
  ];
  for (const [input, want] of cases) assert.equal(strictMsgId(input), want, JSON.stringify(input));
});


// ---- Dave's call: `Content-Type :` is Content-Type to both policies --------

test('Content-Type : multipart/signed (space before colon) -> malformed_mime', () =>
  refused(msg('Content-Type : multipart/signed'), 'malformed_mime'));

test('Content-Type<TAB>: multipart/signed with a boundary (tab before colon) -> malformed_mime', () =>
  refused(msg('Content-Type\t: multipart/signed; boundary=B',
    `--B\r\nContent-Type: text/plain\r\n\r\n${P}\r\n--B--`), 'malformed_mime'));

test('Content-Type : (n; n) multipart/signed combines both rules -> malformed_mime', () =>
  refused(msg('Content-Type : (n; n) multipart/signed'), 'malformed_mime'));

test('Content-Type : text/plain (spacing, non-multipart) is kept', () =>
  retained(msg('Content-Type : text/plain', OK)));

test('spaced and real Content-Type together stay ambiguous_mime (duplicate check unchanged)', () =>
  refused(msg('Content-Type: text/plain\r\nContent-Type : multipart/signed'), 'ambiguous_mime'));
