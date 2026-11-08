// MC-1166 unit 3 fix round 2: multipart/related root selection (RFC 2387),
// duplicate-header refusal (ambiguous_mime), parser-fallback refusal
// (malformed_mime). Every reproduction from Fenn's round-2 review is here, each
// with the exact outcome rather than just "payload absent".
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';
import { multipart, plainPart, htmlPart } from './fixtures.js';

const P = 'INJECTED-CONTENT';
const CRLF = (...lines) => lines.join('\r\n');

const assertAbsent = (result, ...needles) => {
  const dump = JSON.stringify(result);
  for (const n of needles) assert.ok(!dump.includes(n), `leaked: ${n}`);
};
const refused = (r, code) => {
  assert.deepEqual(r, { ok: false, reason_code: code });
  assertAbsent(r, P);
};

const relatedHeader = 'MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=B; type="text/html"; start="<root>"';
const relatedBody = (parts) => parts.map((p) => `--B\r\n${p}\r\n`).join('') + '--B--';
const related = (header, parts) => `${header}\r\n\r\n${relatedBody(parts)}`;

// ---- Fenn round 2, P1: multipart/related resources are not the body --------

test('related: HTML root + plain resource -> html_only, resource text not returned (Fenn P1)', async () => {
  const r = await strip(related(relatedHeader, [
    'Content-Type: text/html\r\nContent-ID: <root>\r\n\r\n<p>VISIBLE</p>',
    `Content-Type: text/plain\r\nContent-ID: <unused>\r\n\r\n${P}`,
  ]));
  assert.equal(r.ok, false);
  assert.equal(r.reason_code, 'html_only');
  assert.equal(r.stripped.html_parts, 1);
  assert.equal(r.stripped.attachments, 1); // the resource is counted, not read
  assertAbsent(r, P, 'VISIBLE');
});

test('related: plain resource BEFORE the HTML root -> html_only (Fenn P1, order swapped)', async () => {
  const r = await strip(related(relatedHeader, [
    `Content-Type: text/plain\r\nContent-ID: <unused>\r\n\r\n${P}`,
    'Content-Type: text/html\r\nContent-ID: <root>\r\n\r\n<p>VISIBLE</p>',
  ]));
  assert.equal(r.ok, false);
  assert.equal(r.reason_code, 'html_only');
  assertAbsent(r, P);
});

test('related: legit reply, text/plain root named by start, image resource -> text kept', async () => {
  const r = await strip(related(relatedHeader.replace('text/html', 'text/plain'), [
    'Content-Type: image/gif\r\nContent-ID: <pic>\r\nContent-Transfer-Encoding: base64\r\n\r\nR0lGODlhAQABAAAAACw=',
    'Content-Type: text/plain\r\nContent-ID: <root>\r\n\r\nApproved.',
  ]));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
  assert.equal(r.source, 'plain');
  assert.equal(r.stripped.inline_images, 1);
});

test('related: legit reply, no start parameter -> first child is the root', async () => {
  const r = await strip(related('Content-Type: multipart/related; boundary=B', [
    'Content-Type: text/plain\r\n\r\nApproved.',
    `Content-Type: text/plain\r\nContent-ID: <res>\r\n\r\n${P}`,
  ]));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
  assert.equal(r.stripped.attachments, 1);
  assertAbsent(r, P);
});

test('related: no start parameter and the first child is HTML -> html_only, later plain resource not read', async () => {
  const r = await strip(related('Content-Type: multipart/related; boundary=B', [
    'Content-Type: text/html\r\n\r\n<p>VISIBLE</p>',
    `Content-Type: text/plain\r\n\r\n${P}`,
  ]));
  assert.equal(r.reason_code, 'html_only');
  assertAbsent(r, P);
});

test('related: start names no child -> no text from any child', async () => {
  const r = await strip(related('Content-Type: multipart/related; boundary=B; start="<nope>"', [
    'Content-Type: text/plain\r\nContent-ID: <a>\r\n\r\nfirst',
    `Content-Type: text/plain\r\nContent-ID: <b>\r\n\r\n${P}`,
  ]));
  assert.equal(r.ok, true);
  assert.equal(r.has_text, false);
  assert.equal(r.source, 'none');
  assert.equal(r.stripped.attachments, 2);
  assertAbsent(r, P, 'first');
});

test('related: empty start, and a start matching two children, yield no text', async () => {
  const dupIds = [
    'Content-Type: text/plain\r\nContent-ID: <root>\r\n\r\nFIRST-ROOT',
    `Content-Type: text/plain\r\nContent-ID: <root>\r\n\r\n${P}`,
  ];
  const twice = await strip(related(relatedHeader.replace('text/html', 'text/plain'), dupIds));
  assert.equal(twice.has_text, false);
  const empty = await strip(related('Content-Type: multipart/related; boundary=B; start=""', dupIds));
  assert.equal(empty.has_text, false);
  assertAbsent(twice, P, 'FIRST-ROOT');
  assertAbsent(empty, P, 'FIRST-ROOT');
});

test('related: start without angle brackets still matches the bracketed Content-ID', async () => {
  const r = await strip(related('Content-Type: multipart/related; boundary=B; start=root', [
    `Content-Type: text/plain\r\nContent-ID: <other>\r\n\r\n${P}`,
    'Content-Type: text/plain\r\nContent-ID: <root>\r\n\r\nApproved.',
  ]));
  assert.equal(r.text, 'Approved.');
  assertAbsent(r, P);
});

test('related: the usual related > alternative > plain+html shape keeps the plain text', async () => {
  const alt = `Content-Type: multipart/alternative; boundary=inner\r\nContent-ID: <root>\r\n\r\n`
    + '--inner\r\nContent-Type: text/plain\r\n\r\nApproved.\r\n--inner\r\nContent-Type: text/html\r\n\r\n<img src="cid:pic">\r\n--inner--';
  const withStart = await strip(related(relatedHeader, [
    alt,
    'Content-Type: image/gif\r\nContent-ID: <pic>\r\nContent-Transfer-Encoding: base64\r\n\r\nR0lGODlhAQABAAAAACw=',
  ]));
  assert.equal(withStart.text, 'Approved.');
  const noStart = await strip(related('Content-Type: multipart/related; boundary=B', [alt, `Content-Type: text/plain\r\nContent-ID: <res>\r\n\r\n${P}`]));
  assert.equal(noStart.text, 'Approved.');
  assertAbsent(noStart, P);
});

test('related: nested under mixed, a non-root plain resource is still not read', async () => {
  const inner = related('Content-Type: multipart/related; boundary=B', [
    'Content-Type: text/html\r\n\r\n<p>VISIBLE</p>',
    `Content-Type: text/plain\r\n\r\n${P}`,
  ]);
  // outer boundary must not equal or share a prefix with the inner `B` (round 9, rule 3)
  const r = await strip(multipart('mixed', [{ headers: [inner.split('\r\n\r\n')[0]], body: inner.slice(inner.indexOf('\r\n\r\n') + 4) }], 'OUTER'));
  assert.equal(r.reason_code, 'html_only');
  assertAbsent(r, P);
});

test('related: ordinary mixed/alternative are unaffected (all plain children still joined)', async () => {
  const r = await strip(multipart('alternative', [plainPart('one'), plainPart('two'), htmlPart('<p>h</p>')]));
  assert.equal(r.text, 'one\n\ntwo');
});

// ---- Fenn round 2, P1: duplicate MIME headers -> ambiguous_mime -------------

test('duplicate Content-ID on a related child refuses (root selection must not be first-wins)', async () => {
  refused(await strip(related(relatedHeader.replace('text/html', 'text/plain'), [
    CRLF('Content-Type: text/plain', 'Content-ID: <other>', 'Content-ID: <root>', '', 'FIRST-ROOT'),
    CRLF('Content-Type: text/plain', 'Content-ID: <root>', '', P),
  ])), 'ambiguous_mime');
});

test('duplicate Content-Disposition with a commented filename on the second (Fenn P1 A)', async () => {
  refused(await strip(CRLF('Content-Type: text/plain', 'Content-Disposition: inline',
    'Content-Disposition: inline; (note) filename=x.txt', '', P)), 'ambiguous_mime');
});

test('duplicate Content-Type with a name on the second (Fenn P1 B)', async () => {
  refused(await strip(CRLF('Content-Type: text/plain', 'Content-Type: text/plain; name=x.txt', '', P)), 'ambiguous_mime');
});

test('duplicate Content-Disposition with RFC 2231 filename* and a comment', async () => {
  refused(await strip(CRLF('Content-Type: text/plain', 'Content-Disposition: inline',
    "Content-Disposition: inline; (note) filename*=utf-8''x.txt", '', P)), 'ambiguous_mime');
});

test('duplicate Content-Transfer-Encoding refuses', async () => {
  refused(await strip(CRLF('Content-Type: text/plain', 'Content-Transfer-Encoding: 7bit',
    'Content-Transfer-Encoding: base64', '', P)), 'ambiguous_mime');
});

test('duplicates on the top level multipart refuse the whole message', async () => {
  const raw = CRLF('Content-Type: multipart/mixed; boundary=B', 'Content-Disposition: inline',
    'Content-Disposition: inline; (note) filename=x.txt', '', '--B', 'Content-Type: text/plain', '', P, '--B--');
  refused(await strip(raw), 'ambiguous_mime');
});

test('duplicates on a CHILD part, even inside an attachment subtree, refuse the whole message', async () => {
  const raw = multipart('mixed', [plainPart('Visible'), {
    headers: ['Content-Type: application/pdf', 'Content-Type: text/plain', 'Content-Disposition: attachment; filename=a.pdf'],
    body: 'x',
  }]);
  refused(await strip(raw), 'ambiguous_mime');
  refused(await strip(multipart('mixed', [plainPart(P), {
    headers: ['Content-Type: multipart/alternative; boundary=inner', 'Content-Disposition: attachment', 'Content-Disposition: inline'],
    body: `--inner\r\nContent-Type: text/plain\r\n\r\n${P}\r\n--inner--`,
  }])), 'ambiguous_mime');
});

test('duplicate detection reads raw header names: case-insensitive, folded, whitespace before the colon', async () => {
  refused(await strip(CRLF('Content-Type: text/plain', 'CONTENT-TYPE: text/plain; name=x.txt', '', P)), 'ambiguous_mime');
  refused(await strip(CRLF('content-type: text/plain;', ' charset=utf-8', 'Content-Type: text/plain', '', P)), 'ambiguous_mime');
  refused(await strip(CRLF('Content-Type: text/plain', 'Content-Type : text/plain; name=x.txt', '', P)), 'ambiguous_mime');
  refused(await strip(CRLF('Content-Type: text/plain', 'Content-Disposition: inline', 'content-disposition:', ' inline; filename=x', '', P)), 'ambiguous_mime');
});

test('a header name merely appearing inside a folded value is not a duplicate', async () => {
  const r = await strip(CRLF('Content-Type: text/plain', 'X-Note: see', ' Content-Type: text/html', '', 'Approved.'));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
});

test('one Content-Type + one disposition + one encoding is fine; other duplicated headers are not refused', async () => {
  const r = await strip(CRLF('Content-Type: text/plain; charset=utf-8', 'Content-Disposition: inline',
    'Content-Transfer-Encoding: 7bit', 'Received: a', 'Received: b', 'X-Tag: 1', 'X-Tag: 2', '', 'Approved.'));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
});

// ---- Fenn round 2, P2: parser fallback -> malformed_mime --------------------

test('multipart/signed with no boundary is refused (Fenn P2)', async () => {
  refused(await strip(CRLF('Content-Type: multipart/signed', '', P)), 'malformed_mime');
});

test('multipart/signed with an empty boundary and a nested attachment block is refused (Fenn P2)', async () => {
  refused(await strip(`Content-Type: multipart/signed; boundary=""\r\n\r\n--B\r\nContent-Type: text/plain\r\nContent-Disposition: attachment\r\n\r\n${P}\r\n--B--`),
    'malformed_mime');
});

test('every multipart subtype with a missing, empty or non-ASCII boundary is refused', async () => {
  for (const sub of ['mixed', 'alternative', 'related', 'signed', 'digest', 'report', 'encrypted', 'parallel', 'x-unknown']) {
    refused(await strip(CRLF(`Content-Type: multipart/${sub}`, '', P)), 'malformed_mime');
    refused(await strip(CRLF(`Content-Type: multipart/${sub}; boundary=""`, '', P)), 'malformed_mime');
    refused(await strip(CRLF(`Content-Type: Multipart/${sub.toUpperCase()}; boundary="béc"`, '', P)), 'malformed_mime');
  }
});

test('a malformed multipart child refuses the whole message, not just its subtree', async () => {
  const raw = multipart('mixed', [plainPart('Visible'), { headers: ['Content-Type: multipart/signed'], body: P }]);
  refused(await strip(raw), 'malformed_mime');
});

test('a leaf that only mentions multipart in a parameter is not malformed', async () => {
  const r = await strip(CRLF('Content-Type: text/plain; format=flowed; x-note="multipart/signed"', '', 'Approved.'));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
});

test('a well-formed multipart/signed is still an ordinary drop, not a refusal', async () => {
  const r = await strip(multipart('signed', [plainPart(P), { headers: ['Content-Type: application/pgp-signature'], body: 'sig' }]));
  assert.equal(r.ok, true);
  assert.equal(r.has_text, false);
  assertAbsent(r, P);
});
