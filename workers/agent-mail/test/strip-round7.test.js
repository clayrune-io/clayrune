// MC-1166 unit 3 fix round 7: a header name folded before its colon
// (`Content-ID` CRLF TAB `: <x>`). postal-mime ends the header block on the bare
// name line, so the ID never reaches headers or rawHeaderLines; Thunderbird joins
// the fold and reads it. strip() refuses any such fold on the raw bytes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);

const fennRepro = (folded) => [
  'Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"',
  '',
  '--B',
  'Content-Type: text/html',
  folded,
  '',
  '<p>Visible</p>',
  '--B',
  'Content-Type: text/plain',
  'Content-ID: <root@example.net>',
  '',
  P,
  '--B--',
].join('\r\n');

const FOLDS = [
  ['tab', 'Content-ID\r\n\t: <root@example.net>'],
  ['space', 'Content-ID\r\n : <root@example.net>'],
  ['trailing space + tab', 'Content-ID \r\n\t: <root@example.net>'],
  ['bare LF', 'Content-ID\n\t: <root@example.net>'],
  ['whitespace-only fold line between', 'Content-ID\r\n \t\r\n\t: <root@example.net>'],
];

for (const [name, folded] of FOLDS) {
  test(`Fenn R7 repro (${name}): folded-before-colon Content-ID -> malformed_mime`, async () => {
    const r = await strip(fennRepro(folded));
    noLeak(r);
    assert.deepEqual(r, { ok: false, reason_code: 'malformed_mime' });
  });
}

test('control: canonical Content-ID on the html root -> html_only, payload not read', async () => {
  const r = await strip(fennRepro('Content-ID: <root@example.net>'));
  noLeak(r);
  assert.equal(r.reason_code, 'html_only');
});

test('a folded header name at the top level is refused too', async () => {
  const r = await strip(`Content-Type\r\n : text/plain\r\n\r\n${P}`);
  noLeak(r);
  assert.deepEqual(r, { ok: false, reason_code: 'malformed_mime' });
});

test('an ordinary folded header VALUE is still accepted', async () => {
  const r = await strip('Content-Type: text/plain;\r\n\tcharset=utf-8\r\nSubject: a\r\n b\r\n\r\nApproved.');
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
});

test('a plain reply body with a colon mid-line is still accepted', async () => {
  const r = await strip('Content-Type: text/plain\r\n\r\nNote: approved\r\n  see: below');
  assert.equal(r.ok, true);
});
