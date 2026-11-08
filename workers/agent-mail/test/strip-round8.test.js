// MC-1166 unit 3 fix round 8: where a part's header block ends.
// postal-mime ends a header block at the first line that is neither a header field
// nor a space/tab fold and splits lines on LF only; Thunderbird ends it only at a
// blank line and also splits on a bare CR. A Content-ID hidden behind a junk line, a
// bare CR, or form-feed/vertical-tab padding was therefore seen by one reader and not
// the other. strip() now judges every header section on the raw bytes and refuses
// (malformed_mime) one that holds a non-header line, a bare CR, or does not end on an
// empty line. Repros are Fenn's round-8 probes (probe-round8.mjs, probe2-round8.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
const bytes = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0)));
const REFUSED = { ok: false, reason_code: 'malformed_mime' };

// html first child whose header lines vary; plain second child carries the same
// strict Content-ID and the injected text.
const fennRepro = (htmlHeaders, nl = '\r\n') => [
  'Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"',
  '',
  '--B',
  ...htmlHeaders,
  '',
  '<p>Visible</p>',
  '--B',
  'Content-Type: text/plain',
  'Content-ID: <root@example.net>',
  '',
  P,
  '--B--',
].join(nl);

const ID = '<root@example.net>';
const REPROS = {
  'colon-less junk line between headers': ['Content-Type: text/html', 'X', `Content-ID: ${ID}`],
  'bare CR between headers': [`Content-Type: text/html\rContent-ID: ${ID}`],
  'form feed before colon': ['Content-Type: text/html', `Content-ID\f: ${ID}`],
  'vertical tab before colon': ['Content-Type: text/html', `Content-ID\v: ${ID}`],
  'form feed then fold': ['Content-Type: text/html', `Content-ID\f\r\n\t: ${ID}`],
  'fold then form feed': ['Content-Type: text/html', `Content-ID\r\n\t\f: ${ID}`],
  'form feed fold line': ['Content-Type: text/html', `Content-ID\r\n\f: ${ID}`],
  'bare CR fold of the name': ['Content-Type: text/html', `Content-ID\r\t: ${ID}`],
  'CR CR LF fold': ['Content-Type: text/html', `Content-ID\r\r\n\t: ${ID}`],
  'CR before colon': ['Content-Type: text/html', `Content-ID\r: ${ID}`],
  'leading form feed on the name': ['Content-Type: text/html', `\fContent-ID: ${ID}`],
  'NBSP before colon': ['Content-Type: text/html', `Content-ID\xa0: ${ID}`],
  'NUL before colon': ['Content-Type: text/html', `Content-ID\0: ${ID}`],
  'junk line, valid-looking header after': ['Content-Type: text/html', 'X: y', 'junk', `Content-ID: ${ID}`],
};

for (const [name, headers] of Object.entries(REPROS)) {
  test(`Fenn R8 repro (${name}) -> malformed_mime, no leak`, async () => {
    const r = await strip(bytes(fennRepro(headers)));
    noLeak(r);
    assert.deepEqual(r, REFUSED);
  });
}

test('Fenn R8 repro with bare-LF line endings is refused as well', async () => {
  const r = await strip(bytes(fennRepro(['Content-Type: text/html', 'X', `Content-ID: ${ID}`], '\n')));
  noLeak(r);
  assert.deepEqual(r, REFUSED);
});

test('control: the same message with a canonical header block -> html_only', async () => {
  const r = await strip(bytes(fennRepro(['Content-Type: text/html', `Content-ID: ${ID}`])));
  noLeak(r);
  assert.equal(r.reason_code, 'html_only');
});

test('Round-7 folded-name repros still refuse (tab, space, ws-only fold line)', async () => {
  for (const f of [`Content-ID\r\n\t: ${ID}`, `Content-ID\r\n : ${ID}`, `Content-ID\r\n \t\r\n\t: ${ID}`]) {
    const r = await strip(bytes(fennRepro(['Content-Type: text/html', f])));
    noLeak(r);
    assert.deepEqual(r, REFUSED);
  }
});

// ---- header sections other than the one a boundary opens ----

test('junk line in the TOP-LEVEL header block is refused', async () => {
  const r = await strip(bytes(`Content-Type: text/plain\r\nX\r\nContent-ID: ${ID}\r\n\r\n${P}`));
  noLeak(r);
  assert.deepEqual(r, REFUSED);
});

test('junk line in a part nested two multiparts deep is refused', async () => {
  const raw = [
    'Content-Type: multipart/mixed; boundary=OUT', '',
    '--OUT', 'Content-Type: multipart/related; boundary=IN; start="<root@example.net>"', '',
    '--IN', 'Content-Type: text/html', 'X', `Content-ID: ${ID}`, '', '<p>v</p>',
    '--IN', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P,
    '--IN--', '--OUT--',
  ].join('\r\n');
  const r = await strip(bytes(raw));
  noLeak(r);
  assert.deepEqual(r, REFUSED);
});

test('a header block that runs into end of input (no empty line) is refused', async () => {
  const r = await strip(bytes('Content-Type: text/plain\r\nSubject: a\r\n'));
  assert.deepEqual(r, REFUSED);
});

test('a part whose header block runs into the next delimiter is refused', async () => {
  const raw = [
    'Content-Type: multipart/mixed; boundary=B', '',
    '--B', 'Content-Type: text/plain',
    '--B', 'Content-Type: text/plain', '', 'hello',
    '--B--',
  ].join('\r\n');
  assert.deepEqual(await strip(bytes(raw)), REFUSED);
});

test('a whitespace-only line ending a header block (not empty) is refused', async () => {
  const r = await strip(bytes(`Content-Type: text/plain\r\n \r\nNote ordinary body\r\n`));
  assert.deepEqual(r, REFUSED);
});

test('CR CR as the blank line is refused (not blank to postal-mime)', async () => {
  const r = await strip(bytes(`Content-Type: text/plain\r\r${P}`));
  noLeak(r);
  assert.deepEqual(r, REFUSED);
});

// ---- what must still pass ----

test('ordinary multipart/alternative reply passes (CRLF)', async () => {
  const raw = [
    'From: ron@example.net', 'Subject: Re: Approve?',
    'Content-Type: multipart/alternative; boundary="b1"', '',
    '--b1', 'Content-Type: text/plain; charset=utf-8', '', 'Approved. Go ahead.',
    '--b1', 'Content-Type: text/html; charset=utf-8', '', '<p>Approved. Go ahead.</p>',
    '--b1--', '',
  ].join('\r\n');
  const r = await strip(bytes(raw));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved. Go ahead.');
});

test('ordinary multipart/alternative reply passes (bare LF)', async () => {
  const raw = [
    'Content-Type: multipart/alternative; boundary=b1', '',
    '--b1', 'Content-Type: text/plain', '', 'Approved.',
    '--b1--', '',
  ].join('\n');
  const r = await strip(bytes(raw));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
});

test('folded header VALUES (including a fold that begins with a colon) pass', async () => {
  const raw = 'Content-Type: text/plain;\r\n\tcharset=utf-8\r\nSubject: a\r\n b\r\nX-Note: first\r\n : second\r\n\r\nApproved.';
  const r = await strip(bytes(raw));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
});

test('a header with spaces before its colon, on one line, is still a header (not refused)', async () => {
  const r = await strip(bytes('Content-Type : text/plain\r\n\r\nApproved.'));
  assert.equal(r.ok, true);
});

test('control (flipped from round 7): body text "Approved" then an indented colon line passes', async () => {
  for (const body of ['Approved\n  : proceed', 'Approved\r\n\t: proceed', 'Approved\r\n :\r\n']) {
    const r = await strip(bytes(`Content-Type: text/plain\r\n\r\n${body}`));
    assert.equal(r.ok, true, JSON.stringify(r));
    assert.ok(r.text.startsWith('Approved'));
  }
});

test('the same body text inside a multipart text/plain part passes', async () => {
  const raw = [
    'Content-Type: multipart/mixed; boundary=B', '',
    '--B', 'Content-Type: text/plain', '', 'Approved', '  : proceed',
    '--B--', '',
  ].join('\r\n');
  const r = await strip(bytes(raw));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved\n  : proceed');
});

// Round 9 superseded the bare-CR half of this control: a CR not followed by LF is refused
// anywhere, bodies included (strip-round9.test.js). Junk lines, FF and VT in a body still pass.
test('junk lines, form feed and vertical tab inside a BODY pass', async () => {
  const r = await strip(bytes('Content-Type: text/plain\r\n\r\nX\r\nOK\nstill\fbody\vtext\r\nContent-ID\f: <x>'));
  assert.equal(r.ok, true);
});

test('a body line that looks like a delimiter of no declared boundary is just text', async () => {
  const r = await strip(bytes('Content-Type: text/plain\r\n\r\n--B\r\nnot a header\r\n'));
  assert.equal(r.ok, true);
});
