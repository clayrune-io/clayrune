// MC-1166 unit 3 fix round 11: Content-Type / Content-Disposition / Content-Transfer-Encoding
// must match a strict whole-value grammar (canonical.js headerGrammarRefusal), replacing the
// round-10 scanValue walker.
//
// Fenn's round-11 review showed the walker was a third parameter reader: it agreed with
// neither postal-mime nor Thunderbird about a backslash OUTSIDE quotes, about a parameter
// Thunderbird cannot read (which stops all its parameter parsing), or about a quoted type
// token. Header values then passed rules 5-8 while the two real readers registered
// different boundaries or types. Now only shapes both readers parse identically are
// admitted; everything else is malformed_mime.
//   Content-Type        token "/" token *( OWS ";" OWS token "=" ( token / quoted ) ) OWS [";"]
//   Content-Disposition token           *( OWS ";" OWS token "=" ( token / quoted ) ) OWS [";"]
//   Content-Transfer-Encoding  one token
// Repros are Fenn's r11-leaks.mjs; the ordinary shapes are r11-ordinary.mjs (Apple Mail,
// Outlook related, Thunderbird non-ASCII filename*, Gmail filename*0*/filename*1*), which
// must still pass.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
const bytes = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0)));
const REFUSED = { ok: false, reason_code: 'malformed_mime' };
const J = (a) => a.join('\r\n');

function refuses(name, msg) {
  test(`R11 ${name} -> malformed_mime, no leak`, async () => {
    const r = await strip(bytes(msg));
    noLeak(r);
    assert.deepEqual(r, REFUSED);
  });
}
function passes(name, msg, expectText) {
  test(`R11 ordinary: ${name} -> ok`, async () => {
    const r = await strip(bytes(msg));
    assert.equal(r.ok, true, JSON.stringify(r));
    if (expectText !== undefined) assert.match(r.text, expectText);
  });
}

// --- Fenn's r11-leaks.mjs ------------------------------------------------------------
// P1a: postal-mime drops `(")` as a comment after `\"`, flipping its quote parity: boundary
// C. Thunderbird reads the unquoted value of `a` to `;`: boundary B.
const split = (ct, tb, pm) => J([`Content-Type: ${ct}`, '', `--${tb}`, 'Content-Type: text/plain', '', 'Visible',
  `--${tb}`, 'Content-Type: application/octet-stream', 'Content-Disposition: attachment; filename=a.bin', '',
  `--${pm}`, 'Content-Type: text/plain', '', P, `--${pm}--`, `--${tb}--`, '']);
refuses('r11_escquote_comment_split', split('multipart/mixed; a=\\" (") ; boundary=B ; b=" ; boundary=C; z="', 'B', 'C'));

// P1b: Thunderbird stops ALL parameter parsing at an earlier parameter it cannot read, so it
// finds no boundary at all (every line is preamble); postal-mime reads boundary B.
const noBoundaryForTb = (ct) => J([`Content-Type: ${ct}`, '', '--B', 'Content-Type: text/plain', '', P, '--B--', '']);
refuses('r11_space_in_earlier_name', noBoundaryForTb('multipart/mixed; x y=1; boundary=B'));
refuses('r11_junk_after_quoted_param', noBoundaryForTb('multipart/mixed; x="1"junk; boundary=B'));
refuses('r11_junk_after_quoted_charset', noBoundaryForTb('multipart/mixed; charset="a" b; boundary=B'));

// P2: a quoted type token is text/plain to postal-mime, an unknown type to Thunderbird.
refuses('r11_quoted_type_top', J(['Content-Type: "text/plain"', '', P, '']));
refuses('r11_quoted_subtype_top', J(['Content-Type: text/"plain"', '', P, '']));
refuses('r11_quoted_type_part', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible',
  '--B', 'Content-Type: "text/plain"', '', P, '--B--', '']));

// --- the old forms of two round-5 tests (backslash inside a quoted start=) are now refused ---
refuses('backslash-escaped quote inside start= (round-5 shape)', J(['Content-Type: multipart/related; boundary=B; type="text/html"; start="<\\"root\\"([)@example.net>"', '',
  '--B', 'Content-Type: text/html', 'Content-ID: <"root"([)@example.net>', '', '<p>Visible</p>',
  '--B', 'Content-Type: text/plain', 'Content-ID: <"root"([)@example.net> (outside)', '', P, '--B--', '']));

// --- grammar unit cases: each value alone, in the part that decides how it is read ---------
const ctTop = (v) => J([`Content-Type: ${v}`, '', P, '']);
const ctPart = (v) => J(['Content-Type: multipart/mixed; boundary=B', '', '--B', `Content-Type: ${v}`, '', P, '--B--', '']);
const dispPart = (v) => J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', `Content-Disposition: ${v}`, '', P, '--B--', '']);
const cte = (v) => J(['Content-Type: text/plain', `Content-Transfer-Encoding: ${v}`, '', P, '']);

for (const v of [
  'text/plain; charset=a\\b', // backslash outside quotes (not a token char)
  'text/plain; name=a\\"b', // escaped quote outside quotes
  'text/plain; name="a\\"b"', // backslash inside quotes
  'text/plain; name="a\\\\"', // escaped backslash inside quotes
  'text/plain (comment)', // comment after the type
  'text/plain; charset=(c)utf-8', // comment as a value
  '(c)text/plain', // comment before the type
  'text/plain; charset=utf-8,', // stray comma
  'text/plain; ; charset=utf-8', // empty parameter
  'text/plain;; charset=utf-8',
  'text/plain; charset', // name with no value
  'text/plain; charset=', // empty value
  'text/plain; =utf-8', // empty name
  'text/plain; charset = utf-8', // space around =
  'text/plain; charset =utf-8',
  'text/plain; charset= utf-8',
  'text/plain; char set=utf-8', // space inside a name
  'text/plain; charset="utf-8" x', // junk after a quoted value
  'text/plain; charset=utf 8', // junk after a token value
  'text/plain; name="unterminated',
  'text/plain; name=un"terminated',
  'text',
  'text/',
  '/plain',
  'text/plain/extra',
  'text /plain',
  'text/ plain',
  '"text"/plain',
  'text/plain; name=a<b>',
  'text/plain; name=a@b',
  'text/plain; name=a:b',
  'text/plain; name=a?b',
]) refuses(`Content-Type ${JSON.stringify(v)} (top level)`, ctTop(v));
refuses('Content-Type with a comment inside a part', ctPart('text/plain; name=a(b)'));
refuses('Content-Type with a quoted subtype inside a part', ctPart('text/"plain"'));

for (const v of [
  'attachment junk',
  'attachment; filename=a b.pdf',
  'attachment; filename="a.pdf" b',
  'attachment; filename=a\\b.pdf',
  'attachment (c)',
  '"attachment"',
  'attachment; ; filename=a.pdf',
  'attachment; filename =a.pdf',
  'attachment/inline',
]) refuses(`Content-Disposition ${JSON.stringify(v)}`, dispPart(v));

for (const v of ['7bit; x=y', '"7bit"', '7bit (c)', '7bit, 8bit', 'base 64', 'base64;']) {
  refuses(`Content-Transfer-Encoding ${JSON.stringify(v)}`, cte(v));
}

// the grammar covers every node, including one inside a dropped subtree
refuses('bad Content-Type deep inside an attachment subtree', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: multipart/mixed; boundary=C',
  'Content-Disposition: attachment', '', '--C', 'Content-Type: text/plain; x y=1', '', P, '--C--', '--B--', '']));

// --- values the grammar admits ------------------------------------------------------------
for (const [name, v] of [
  ['bare type', 'text/plain'],
  ['token parameter', 'text/plain; charset=utf-8'],
  ['quoted parameter', 'text/plain; charset="utf-8"'],
  ['format=flowed', 'text/plain; charset=UTF-8; format=flowed'],
  ['trailing semicolon', 'text/plain; charset=us-ascii;'],
  ['trailing semicolon and space', 'text/plain; charset=us-ascii; '],
  ['space before the semicolon', 'text/plain ; charset=us-ascii'],
  ['quoted value with spaces, commas, parens, equals, semicolon', 'text/plain; charset=utf-8; x="a b, (c); d=e"'],
  ['empty quoted value', 'text/plain; charset=utf-8; x=""'],
  ['upper-case name and type', 'TEXT/PLAIN; CHARSET=UTF-8'],
  ['x- parameter, digits and dots', 'text/plain; x-unix-mode=0644; x.y_z=1'],
]) {
  passes(`Content-Type ${name}`, J([`Content-Type: ${v}`, '', 'Hello', '']), /Hello/);
}
passes('Content-Type folded before a parameter', J(['Content-Type: text/plain;', '\tcharset=utf-8', '', 'Hello', '']), /Hello/);
passes('Content-Type folded after the type', J(['Content-Type: text/plain', ' ; charset=utf-8', '', 'Hello', '']), /Hello/);
passes('Content-Type folded inside a quoted value', J(['Content-Type: text/plain; charset=utf-8; x="a', ' b"', '', 'Hello', '']), /Hello/);
passes('Content-Type with trailing ";" then a fold (Gmail)', J(['Content-Type: text/plain; ', '\tcharset=utf-8', '', 'Hello', '']), /Hello/);
passes('Content-Disposition bare', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', 'Content-Disposition: inline', '', 'Hello', '--B--', '']), /Hello/);
passes('Content-Disposition with quoted and token parameters', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain',
  'Content-Disposition: attachment; filename="a b.txt"; size=5;', '', 'Hello', 'xx', '--B--', '']), undefined);
for (const v of ['7bit', '8bit', 'binary', 'quoted-printable', 'base64', 'BASE64', 'x-uuencode']) {
  passes(`Content-Transfer-Encoding ${v}`, J(['Content-Type: text/plain', `Content-Transfer-Encoding: ${v}`, '', 'SGVsbG8=', '']));
}

// --- boundary rules 6-8 still sit on top of the grammar -------------------------------------
const mp = (ct) => J([`Content-Type: ${ct}`, '', '--B', 'Content-Type: text/plain', '', P, '--B--', '']);
refuses('rule 6: boundary*', mp("multipart/mixed; boundary*=utf-8''B"));
refuses('rule 6: boundary*0', mp('multipart/mixed; boundary*0=B; boundary*1=C'));
refuses('rule 6: two boundary parameters', mp('multipart/mixed; boundary=B; boundary=C'));
refuses('rule 6: plain and quoted boundary', mp('multipart/mixed; boundary="B"; boundary=B'));
refuses('rule 7: quoted boundary with edge whitespace', mp('multipart/mixed; boundary=" B"'));
refuses('rule 7: empty quoted boundary', mp('multipart/mixed; boundary=""'));
refuses('rule 8: multipart without a boundary', mp('multipart/mixed; charset=utf-8'));
refuses('rule 8: multipart with no parameters', mp('multipart/mixed'));
passes('rule 7: BOUNDARY upper case', J(['Content-Type: multipart/mixed; BOUNDARY=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B--', '']), /Visible/);
passes('rule 7: boundary after other parameters', J(['Content-Type: multipart/mixed; charset=utf-8; boundary="B"', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B--', '']), /Visible/);
passes('fold inside a quoted boundary', J(['Content-Type: multipart/mixed; boundary="B', ' C"', '', '--B C', 'Content-Type: text/plain', '', 'Visible', '--B C--', '']), /Visible/);

// --- ordinary mail from real clients (Fenn r11-ordinary.mjs): none may be refused -----------
const AM = 'Apple-Mail=_5E1C7A2B-9D3F-4E6A-8B1C-2D3E4F5A6B7C';
const AMA = 'Apple-Mail=_0A9B8C7D-6E5F-4A3B-2C1D-0E9F8A7B6C5D';
const apple = J([
  'From: Ron <ron@example.com>', 'Content-Type: multipart/mixed;', `\tboundary="${AM}"`,
  'Mime-Version: 1.0 (Mac OS X Mail 16.0 \\(3826.700.81\\))', 'Subject: Re: [Clayrune] merge?',
  'Date: Sun, 8 Nov 2026 14:15:00 -0800', 'In-Reply-To: <a1@example.net>', 'To: agent@example.net',
  'References: <a1@example.net>', 'Message-Id: <8F2A1B3C-4D5E-6F70-8192-A3B4C5D6E7F8@example.com>', 'X-Mailer: Apple Mail (2.3826.700.81)', '',
  '', `--${AM}`, 'Content-Type: multipart/alternative;', `\tboundary="${AMA}"`, '', '',
  `--${AMA}`, 'Content-Transfer-Encoding: quoted-printable', 'Content-Type: text/plain;', '\tcharset=utf-8', '',
  'Yes, merge it.=20', '', '> On Nov 8, 2026, at 2:00=E2=80=AFPM, Agent <agent@example.net> wrote:', '> =', '> Should I merge?', '',
  `--${AMA}`, 'Content-Transfer-Encoding: quoted-printable', 'Content-Type: text/html;', '\tcharset=utf-8', '',
  '<html><body>Yes, merge it.</body></html>=', '', `--${AMA}--`, '', '',
  `--${AM}`, 'Content-Disposition: attachment;', "\tfilename*=utf-8''r%C3%A9sum%C3%A9.pdf", 'Content-Type: application/pdf;',
  '\tx-unix-mode=0644;', "\tname*=utf-8''r%C3%A9sum%C3%A9.pdf", 'Content-Transfer-Encoding: base64', '', 'JVBERi0xLjQK', '', `--${AM}--`, '']);

const OB = '_004_DM6PR11MB4123ABCDEF0123456789DM6PR11MB4123namp_';
const OA = '_000_DM6PR11MB4123ABCDEF0123456789DM6PR11MB4123namp_';
const outlook = J([
  'From: Ron Levy <ron@example.com>', 'To: "agent@example.net" <agent@example.net>', 'Subject: RE: [Clayrune] merge?',
  'Thread-Topic: [Clayrune] merge?', 'Thread-Index: AQHbMf1x2y3z4A==', 'Date: Sun, 8 Nov 2026 22:20:00 +0000',
  'Message-ID: <DM6PR11MB4123ABCDEF0123456789@DM6PR11MB4123.namprd11.prod.outlook.com>',
  'References: <a1@example.net>', 'In-Reply-To: <a1@example.net>', 'Accept-Language: en-US', 'Content-Language: en-US',
  'X-MS-Has-Attach: yes', 'X-MS-TNEF-Correlator:',
  'Content-Type: multipart/related;', `\tboundary="${OB}";`, '\ttype="multipart/alternative"', 'MIME-Version: 1.0', '',
  `--${OB}`, 'Content-Type: multipart/alternative;', `\tboundary="${OA}"`, '',
  `--${OA}`, 'Content-Type: text/plain; charset="us-ascii"', 'Content-Transfer-Encoding: quoted-printable', '',
  'Approved.', '', '[cid:image001.png@01DB3F1A.2B3C4D50]', '', 'From: Agent <agent@example.net>', 'Sent: Sunday, November 8, 2026 2:00 PM', '',
  `--${OA}`, 'Content-Type: text/html; charset="us-ascii"', 'Content-Transfer-Encoding: quoted-printable', '',
  '<html><body><p>Approved.</p><img src=3D"cid:image001.png@01DB3F1A.2B3C4D50"></body></html>', '', `--${OA}--`, '',
  `--${OB}`, 'Content-Type: image/png; name="image001.png"', 'Content-Description: image001.png',
  'Content-Disposition: inline; filename="image001.png"; size=68;', '\tcreation-date="Sun, 08 Nov 2026 22:20:00 GMT";',
  '\tmodification-date="Sun, 08 Nov 2026 22:20:00 GMT"', 'Content-ID: <image001.png@01DB3F1A.2B3C4D50>', 'Content-Transfer-Encoding: base64', '',
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', '', `--${OB}--`, '']);

const TBB = '------------q1W2e3R4t5Y6u7I8o9P0aSdF';
const tbNonAscii = J([
  'Message-ID: <77aa@example.com>', 'MIME-Version: 1.0', 'User-Agent: Mozilla Thunderbird', 'From: Ron <ron@example.com>', 'Subject: Re: x',
  'Content-Language: en-US', `Content-Type: multipart/mixed; boundary="${TBB}"`, '',
  'This is a multi-part message in MIME format.', `--${TBB}`,
  'Content-Type: text/plain; charset=UTF-8; format=flowed', 'Content-Transfer-Encoding: 7bit', '', 'Report attached.', '',
  `--${TBB}`, 'Content-Type: application/pdf; name="=?UTF-8?B?15PXldeXLnBkZg==?="',
  'Content-Disposition: attachment;', " filename*=UTF-8''%D7%93%D7%95%D7%97.pdf", 'Content-Transfer-Encoding: base64', '',
  'JVBERi0xLjQK', '', `--${TBB}--`, '']);

const GB = '0000000000001a2b3c05f1d3e9a1';
const gmailContinuation = J([
  'MIME-Version: 1.0', 'From: Ron <ron@example.com>', 'Subject: Re: x', `Content-Type: multipart/mixed; boundary="${GB}"`, '',
  `--${GB}`, 'Content-Type: text/plain; charset="UTF-8"', '', 'See file.', '',
  `--${GB}`, 'Content-Type: application/pdf; ', '\tname="=?UTF-8?B?0J7RgtGH0LXRgiDQt9CwINC90L7Rj9Cx0YDRjCAyMDI2LnBkZg==?="',
  'Content-Disposition: attachment; ', "\tfilename*0*=UTF-8''%D0%9E%D1%82%D1%87%D0%B5%D1%82%20%D0%B7%D0%B0%20;",
  '\tfilename*1*=%D0%BD%D0%BE%D1%8F%D0%B1%D1%80%D1%8C%202026.pdf', 'Content-Transfer-Encoding: base64', 'X-Attachment-Id: f_mh9x',
  'Content-ID: <f_mh9x>', '', 'JVBERi0xLjQK', `--${GB}--`, '']);

passes('Apple Mail mixed>alternative QP + pdf with filename*=utf-8\'\' / name* / x-unix-mode', apple, /Yes, merge it/);
passes('Outlook multipart/related type="multipart/alternative" + inline png with creation-date and size', outlook, /Approved\./);
passes('Thunderbird RFC 2047 name + filename*=UTF-8\'\'...', tbNonAscii, /Report attached/);
passes('Gmail filename*0*/filename*1* continuation', gmailContinuation, /See file\./);

test('R11 ordinary shapes strip their attachments (the grammar did not change what is kept)', async () => {
  const r = await strip(bytes(apple));
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(r.stripped.attachments, 1);
  const g = await strip(bytes(gmailContinuation));
  assert.equal(g.stripped.attachments, 1);
  const o = await strip(bytes(outlook));
  assert.equal(o.stripped.inline_images, 1);
});
