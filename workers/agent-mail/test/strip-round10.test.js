// MC-1166 unit 3 fix round 10: refuse a Content-Type grammar the two readers parse
// differently (canonical.js headerGrammarRefusal, boundaryParamRefusal,
// multipartBoundaryRefusal).
//
// postal-mime strips comments from a Content-Type value, prefers a plain boundary= to an
// RFC 2231 boundary*=, trims a quoted boundary and ends an unquoted one at whitespace.
// Thunderbird (DoParameterInternal) does none of that. So the message below is split into
// parts under boundary P by postal-mime and under boundary T by Thunderbird, and the
// bytes of an attachment (Thunderbird) are a text part (postal-mime). Rules, all
// malformed_mime, over the raw headers of every part:
//   5. Content-Type / Content-Disposition / Content-Transfer-Encoding with "(" outside a
//      quoted string;
//   6. any boundary* parameter, or more than one boundary parameter;
//   7. a boundary that is not exactly a plain token or a quoted string with no inner
//      quote or backslash and no edge whitespace;
//   8. multipart/* with no usable boundary.
// Repros are Fenn's round-10 probes (r10-params2.mjs, r10-params.mjs, r10-trail.mjs),
// plus rule unit cases and the ordinary mail shapes, which must still pass.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
const bytes = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0)));
const REFUSED = { ok: false, reason_code: 'malformed_mime' };
const J = (a) => a.join('\r\n');

function refuses(name, msg) {
  test(`R10 ${name} -> malformed_mime, no leak`, async () => {
    const r = await strip(bytes(msg));
    noLeak(r);
    assert.deepEqual(r, REFUSED);
  });
}
function passes(name, msg, expectText) {
  test(`R10 ordinary: ${name} -> ok`, async () => {
    const r = await strip(bytes(msg));
    assert.equal(r.ok, true, JSON.stringify(r));
    if (expectText !== undefined) assert.match(r.text, expectText);
  });
}

// ---- (1) Fenn's round 10 probes: all must refuse ---------------------------------
// TB = the boundary Thunderbird would read, PM = the one postal-mime registers.
const split = (param, TB, PM) => J([`Content-Type: multipart/mixed; ${param}`, '', `--${TB}`, 'Content-Type: text/plain', '', 'Visible',
  `--${TB}`, 'Content-Type: application/octet-stream', 'Content-Disposition: attachment; filename=a.bin', '',
  `--${PM}`, 'Content-Type: text/plain', '', P, `--${PM}--`, `--${TB}--`, '']);
const noBoundary = (param) => J([`Content-Type: multipart/mixed; ${param}`, '', '--B', 'Content-Type: text/plain', '', P, '--B--', '']);

refuses('r10_space_comment_value: boundary= (c)B', split('boundary= (c)B', '(c)B', 'B'));
refuses("r10_rfc2231_ext_wins: boundary=A; boundary*=''B", split("boundary=A; boundary*=''B", 'B', 'A'));
refuses('r10_rfc2231_cont_wins: boundary*0=B; boundary=A', split('boundary*0=B; boundary=A', 'B', 'A'));
refuses('r10_quote_inside_token: boundary=B"C"', split('boundary=B"C"', 'B"C"', 'BC'));
refuses('r10_comment_before_name: (c) boundary=B', noBoundary('(c) boundary=B'));
refuses('r10_comment_before_eq: boundary (c)=B', noBoundary('boundary (c)=B'));
refuses('r10_quoted_trailing_space_boundary: boundary="B "', noBoundary('boundary="B "'));
refuses('r10_type_leading_comment: Content-Type: (c)text/plain', J(['Content-Type: (c)text/plain', '', P, '']));
// r10-params.mjs
refuses('space_comment_before_token', split('boundary= (c)B', '(c)B', 'B'));
refuses('unquoted_token_with_space: boundary=B C', split('boundary=B C', 'B', 'B C'));
refuses('comment_then_space_token: boundary=(c) B', split('boundary=(c) B', '(c)', '(c) B'));
refuses("rfc2231_ext_before_plain", split("boundary*=''B; boundary=A", 'B', 'A'));
refuses('rfc2231_cont_after_plain', split('boundary=A; boundary*0=B', 'B', 'A'));
refuses('rfc2231_cont_two: boundary*0=B; boundary*1=C', split('boundary*0=B; boundary*1=C', 'B', 'BC'));

// ---- (2) rule unit cases -------------------------------------------------------
const mixed = (ct, partHeaders = ['Content-Type: text/plain']) => J([`Content-Type: ${ct}`, '', '--B', ...partHeaders, '', 'Visible', '--B--', '']);

// rule 5: "(" outside a quoted string, in each of the three headers, top level and nested
refuses('rule 5: comment after the type', mixed('multipart/mixed (c); boundary=B'));
refuses('rule 5: comment after a parameter', mixed('multipart/mixed; boundary=B (c)'));
refuses('rule 5: comment before the type', mixed('(c) multipart/mixed; boundary=B'));
refuses('rule 5: comment inside the type', mixed('multipart(c)/mixed; boundary=B'));
refuses('rule 5: comment in a Content-Disposition', mixed('multipart/mixed; boundary=B', ['Content-Type: text/plain', 'Content-Disposition: inline (c)']));
refuses('rule 5: comment in a Content-Transfer-Encoding', mixed('multipart/mixed; boundary=B', ['Content-Type: text/plain', 'Content-Transfer-Encoding: 7bit (c)']));
refuses('rule 5: comment in a nested part Content-Type', mixed('multipart/mixed; boundary=B', ['Content-Type: text/plain (c)']));
refuses('rule 5: comment after an escaped quote (escape literal)', mixed('multipart/mixed; boundary=B', ['Content-Type: application/pdf', 'Content-Disposition: attachment; filename="a\\"(b"']));
refuses('rule 5: unterminated quote', mixed('multipart/mixed; boundary=B', ['Content-Type: application/pdf', 'Content-Disposition: attachment; filename="abc']));
passes('rule 5: "(" inside a quoted filename is fine', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B', 'Content-Type: application/pdf; name="a (1).pdf"', 'Content-Disposition: attachment; filename="a (1).pdf"', '', 'JVBERi0xLjQK', '--B--', '']), /Visible/);
passes('rule 5: "(" in an unrelated header is fine', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', 'Content-Description: a (note)', '', 'Visible', '--B--', '']), /Visible/);

// rule 6: boundary* and duplicate boundary
refuses('rule 6: boundary*= alone', mixed("multipart/mixed; boundary*=''B"));
refuses('rule 6: boundary*0= alone', mixed('multipart/mixed; boundary*0=B'));
refuses('rule 6: BOUNDARY*0*= (case, extended continuation)', mixed("multipart/mixed; BOUNDARY*0*=utf-8''B"));
refuses('rule 6: boundary *= (space before the star)', mixed("multipart/mixed; boundary *=''B"));
refuses('rule 6: two identical boundary parameters', mixed('multipart/mixed; boundary=B; boundary=B'));
refuses('rule 6: two different boundary parameters', mixed('multipart/mixed; boundary=B; boundary="C"'));
refuses('rule 6: boundary* inside a nested part', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', "Content-Type: multipart/alternative; boundary=C; boundary*=''D", '', '--C', 'Content-Type: text/plain', '', 'Visible', '--C--', '--B--', '']));
refuses('rule 6: second boundary hidden after an escaped quote', mixed('multipart/mixed; boundary=B; name="x\\"; boundary*=y"'));

// rule 7: exactly a token or a clean quoted string
refuses('rule 7: space inside an unquoted value', mixed('multipart/mixed; boundary=B C'));
refuses('rule 7: space after the equals sign', mixed('multipart/mixed; boundary= B'));
refuses('rule 7: space before the equals sign', mixed('multipart/mixed; boundary =B'));
refuses('rule 7: quote inside a token', mixed('multipart/mixed; boundary=B"C"'));
refuses('rule 7: trailing space inside quotes', mixed('multipart/mixed; boundary="B "'));
refuses('rule 7: leading space inside quotes', mixed('multipart/mixed; boundary=" B"'));
refuses('rule 7: backslash inside quotes', mixed('multipart/mixed; boundary="B\\C"'));
refuses('rule 7: escaped quote inside quotes', mixed('multipart/mixed; boundary="B\\"C"'));
refuses('rule 7: junk after the closing quote', mixed('multipart/mixed; boundary="B"x'));
refuses('rule 7: "=" in an unquoted value (not a token)', mixed('multipart/mixed; boundary=A=B'));
refuses('rule 7: "/" in an unquoted value (not a token)', mixed('multipart/mixed; boundary=A/B'));
refuses('rule 7: empty unquoted value', mixed('multipart/mixed; boundary='));
refuses('rule 7: empty quoted value', mixed('multipart/mixed; boundary=""'));
refuses('rule 7: name with no value', mixed('multipart/mixed; boundary'));
passes('rule 7: plain token', mixed('multipart/mixed; boundary=B'), /Visible/);
passes('rule 7: quoted token', mixed('multipart/mixed; boundary="B"'), /Visible/);
passes('rule 7: quoted value with an inner space and dashes', J(['Content-Type: multipart/mixed; boundary="a b-c"', '', '--a b-c', 'Content-Type: text/plain', '', 'Visible', '--a b-c--', '']), /Visible/);
passes('rule 7: quoted value holding "=" (Apple Mail style)', J(['Content-Type: multipart/mixed; boundary="Apple-Mail=_1A2B"', '', '--Apple-Mail=_1A2B', 'Content-Type: text/plain', '', 'Visible', '--Apple-Mail=_1A2B--', '']), /Visible/);
passes('rule 7: trailing whitespace before the semicolon', mixed('multipart/mixed; boundary=B ; charset=utf-8'), /Visible/);
passes('rule 7: uppercase parameter name', mixed('multipart/mixed; BOUNDARY=B'), /Visible/);
passes('rule 7: boundary folded onto its own line', J(['Content-Type: multipart/mixed;', ' boundary="B"', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B--', '']), /Visible/);
passes('rule 7: boundary parameter first of several', mixed('multipart/mixed; boundary="B"; charset=utf-8'), /Visible/);

// rule 8: multipart/* needs a usable boundary
refuses('rule 8: multipart/mixed, no parameters', J(['Content-Type: multipart/mixed', '', '--B', 'Content-Type: text/plain', '', P, '--B--', '']));
refuses('rule 8: multipart/alternative, other parameters only', J(['Content-Type: multipart/alternative; charset=utf-8', '', '--B', 'Content-Type: text/plain', '', P, '--B--', '']));
refuses('rule 8: nested multipart with no boundary', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: multipart/alternative', '', 'x', '--B--', '']));
refuses('rule 8: multipart/signed, no boundary', J(['Content-Type: multipart/signed; protocol="application/pgp-signature"', '', P, '']));
passes('rule 8: text/plain with a boundary-less type is not multipart', J(['Content-Type: text/plain; charset=utf-8', '', 'Visible', '']), /Visible/);

// filename* keeps working (non-ASCII attachment names)
const tbFilenameStar = J(['From: Ron <r@example.com>', 'MIME-Version: 1.0', 'Content-Type: multipart/mixed; boundary="------------AbC123xyz"', '',
  'This is a multi-part message in MIME format.', '--------------AbC123xyz', 'Content-Type: text/plain; charset=UTF-8; format=flowed', 'Content-Transfer-Encoding: 8bit', '',
  'Approved, see the attached.', '', '--------------AbC123xyz',
  'Content-Type: application/pdf; name="=?UTF-8?B?4oKsLnBkZg==?="', "Content-Disposition: attachment; filename*0*=UTF-8''%E2%82%AC;", ' filename*1*=%2Epdf', 'Content-Transfer-Encoding: base64', '',
  'JVBERi0xLjQKJeLjz9MK', '', '--------------AbC123xyz--', '']);
passes('attachment with a non-ASCII filename* (RFC 2231 continuation)', tbFilenameStar, /Approved/);
passes('attachment with filename*=UTF-8 (single)', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Approved', '--B',
  'Content-Type: application/pdf', "Content-Disposition: attachment; filename*=UTF-8''%E2%82%AC.pdf", '', 'JVBERi0xLjQK', '--B--', '']), /Approved/);

// ---- (3) ordinary mail: all must pass --------------------------------------------
// Fenn's nine shapes (copied from strip-round9.test.js), then his three round-10 shapes.
const plain = J(['From: Ron <r@example.com>', 'To: a@example.net', 'Subject: hi', 'Date: Sun, 8 Nov 2026 10:00:00 -0800', 'Message-ID: <x@example.com>', 'MIME-Version: 1.0', 'Content-Type: text/plain; charset="UTF-8"', 'Content-Transfer-Encoding: 7bit', '', 'Approved', '   : proceed', '', '-- ', 'Ron', '']);
passes('plain CRLF (body "Approved" + indented ": proceed")', plain, /Approved/);
passes('plain LF', plain.replace(/\r\n/g, '\n'), /Approved/);
passes('plain, no trailing newline', plain.trimEnd(), /Approved/);
passes('mbox From line', 'From r@example.com Sun Nov  8 10:00:00 2026\r\n' + plain, /Approved/);
passes('Content-ID : <a@b> (space before colon, same line)', J(['Content-Type: text/plain', 'Content-ID : <a@b>', '', 'Hello']), /Hello/);
passes('gmail alternative + QP + quoted "> --"', J(['DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed;', '\td=gmail.com; s=20230601; h=to:subject:message-id:date:from:mime-version', '         :from:to:cc:subject:date:message-id:reply-to;', '\tbh=abc=;', '        b=xyz', 'Received: by 2002:a05 with SMTP id x;', '        Sun, 08 Nov 2026 10:00:00 -0800 (PST)', 'Subject: =?UTF-8?B?w6l0w6k=?=', 'MIME-Version: 1.0', 'Content-Type: multipart/alternative; boundary="000000000000a1b2c3d4e5f6"', '', '--000000000000a1b2c3d4e5f6', 'Content-Type: text/plain; charset="UTF-8"', 'Content-Transfer-Encoding: quoted-printable', '', 'Yes, go ahead.=20', '', 'On Sat, Nov 7, 2026 Agent <a@example.net> wrote:', '> Shall I proceed?', '> --', '> Agent', '', '--000000000000a1b2c3d4e5f6', 'Content-Type: text/html; charset="UTF-8"', 'Content-Transfer-Encoding: quoted-printable', '', '<div dir=3D"ltr">Yes, go ahead.</div>', '', '--000000000000a1b2c3d4e5f6--', '']), /Yes, go ahead/);
passes('apple mixed(alternative) + pdf', J(['MIME-Version: 1.0', 'Content-Type: multipart/mixed;', '\tboundary="Apple-Mail=_1A2B3C4D-5E6F"', '', 'This is a multi-part message in MIME format.', '', '--Apple-Mail=_1A2B3C4D-5E6F', 'Content-Type: multipart/alternative;', '\tboundary="Apple-Mail=_99887766"', '', '', '--Apple-Mail=_99887766', 'Content-Transfer-Encoding: 7bit', 'Content-Type: text/plain;', '\tcharset=us-ascii', '', 'See attached.', '--Apple-Mail=_99887766', 'Content-Transfer-Encoding: 7bit', 'Content-Type: text/html;', '\tcharset=us-ascii', '', '<html><body>See attached.</body></html>', '--Apple-Mail=_99887766--', '', '--Apple-Mail=_1A2B3C4D-5E6F', 'Content-Disposition: attachment;', '\tfilename=report.pdf', 'Content-Type: application/pdf;', '\tname="report.pdf"', 'Content-Transfer-Encoding: base64', '', 'JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFI+PgpzdHJlYW0K', '--Apple-Mail=_1A2B3C4D-5E6F--', '']), /See attached/);
passes('outlook related(alternative) + inline cid image', J(['Content-Type: multipart/related;', '\tboundary="_004_DM6PR_";', '\ttype="multipart/alternative"', 'MIME-Version: 1.0', '', '--_004_DM6PR_', 'Content-Type: multipart/alternative;', '\tboundary="_000_DM6PR_"', '', '--_000_DM6PR_', 'Content-Type: text/plain; charset="us-ascii"', 'Content-Transfer-Encoding: quoted-printable', '', 'Looks good.', '', '--_000_DM6PR_', 'Content-Type: text/html; charset="us-ascii"', 'Content-Transfer-Encoding: quoted-printable', '', '<html><body><img src=3D"cid:image001.png@01DA"></body></html>', '', '--_000_DM6PR_--', '', '--_004_DM6PR_', 'Content-Type: image/png; name="image001.png"', 'Content-Description: image001.png', 'Content-Disposition: inline; filename="image001.png"; size=4;', '\tcreation-date="Sun, 08 Nov 2026 18:00:00 GMT"', 'Content-ID: <image001.png@01DA>', 'Content-Transfer-Encoding: base64', '', 'iVBORw0K', '', '--_004_DM6PR_--', '']), /Looks good/);
passes('part with no headers', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', '', 'no headers part', '--B--']), /no headers part/);
passes('forwarded message/rfc822', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'fwd below', '--B', 'Content-Type: message/rfc822', '', 'From: x@y', 'Subject: inner', 'Content-Type: multipart/alternative; boundary=C', '', '--C', 'Content-Type: text/plain', '', 'inner text', '--C--', '', '--B--', '']), /fwd below/);
const gmail = J(['MIME-Version: 1.0', 'References: <a1@example.net>', 'In-Reply-To: <a1@example.net>', 'From: Ron <ron@example.com>', 'Date: Sun, 8 Nov 2026 14:10:00 -0800',
 'Message-ID: <CAx1y2z3@mail.gmail.com>', 'Subject: Re: [Clayrune] merge?', 'To: agent@example.net', 'Content-Type: multipart/mixed; boundary="000000000000a6e2b605f1d3e9a1"', '',
 '--000000000000a6e2b605f1d3e9a1', 'Content-Type: multipart/alternative; boundary="000000000000a6e2b405f1d3e99f"', '',
 '--000000000000a6e2b405f1d3e99f', 'Content-Type: text/plain; charset="UTF-8"', 'Content-Transfer-Encoding: quoted-printable', '',
 'Approved, go ahead.', '', 'On Sun, Nov 8, 2026 at 2:00=E2=80=AFPM Agent <agent@example.net> wrote:', '', '> Should I merge?', '> --', '> Agent', '>', '',
 '--000000000000a6e2b405f1d3e99f', 'Content-Type: text/html; charset="UTF-8"', 'Content-Transfer-Encoding: quoted-printable', '',
 '<div dir=3D"ltr">Approved, go ahead.</div><br><div class=3D"gmail_quote"><blockquote>Should I merge?</blockquote></div>', '',
 '--000000000000a6e2b405f1d3e99f--', '--000000000000a6e2b605f1d3e9a1',
 'Content-Type: image/png; name="shot.png"', 'Content-Disposition: attachment; filename="shot.png"', 'Content-Transfer-Encoding: base64', 'Content-ID: <f_mh8abc0>', 'X-Attachment-Id: f_mh8abc0', '',
 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', '--000000000000a6e2b605f1d3e9a1--', '']);
const tbPlain = J(['Message-ID: <3f1e@example.com>', 'Date: Sun, 8 Nov 2026 14:12:00 -0800', 'MIME-Version: 1.0', 'User-Agent: Mozilla Thunderbird', 'Subject: Re: [Clayrune] merge?',
 'To: agent@example.net', 'References: <a1@example.net>', 'In-Reply-To: <a1@example.net>', 'From: Ron <ron@example.com>',
 'Content-Type: multipart/mixed; boundary="------------0aBcDeFgHiJkLmNoPqRsTuVw"', '', 'This is a multi-part message in MIME format.',
 '--------------0aBcDeFgHiJkLmNoPqRsTuVw', 'Content-Type: text/plain; charset=UTF-8; format=flowed', 'Content-Transfer-Encoding: 7bit', '',
 'Approved.', '', '------------------------------', '', 'On 11/8/26 2:00 PM, Agent wrote:', '> Should I merge?', '> -- ', '> Agent', '',
 '--------------0aBcDeFgHiJkLmNoPqRsTuVw', 'Content-Type: application/pdf; name="notes.pdf"', 'Content-Disposition: attachment; filename="notes.pdf"', 'Content-Transfer-Encoding: base64', '',
 'JVBERi0xLjQKJeLjz9MK', '', '--------------0aBcDeFgHiJkLmNoPqRsTuVw--', '']);
const tbHtml = J(['MIME-Version: 1.0', 'User-Agent: Mozilla Thunderbird', 'From: Ron <ron@example.com>', 'Subject: Re: x',
 'Content-Type: multipart/mixed; boundary="------------7xQ2pLmN9vRtYwZ3aBcDeF0g"', '', 'This is a multi-part message in MIME format.',
 '--------------7xQ2pLmN9vRtYwZ3aBcDeF0g', 'Content-Type: multipart/alternative;', ' boundary="------------Hk4sJ8dWq1ErTyUiOp2AsDfG"', '',
 '--------------Hk4sJ8dWq1ErTyUiOp2AsDfG', 'Content-Type: text/plain; charset=UTF-8; format=flowed', 'Content-Transfer-Encoding: 8bit', '',
 'Yes, ship it. ', 'Thanks', '', 'On 11/8/26 2:00 PM, Agent wrote:', '> Should I merge?', '',
 '--------------Hk4sJ8dWq1ErTyUiOp2AsDfG', 'Content-Type: text/html; charset=UTF-8', 'Content-Transfer-Encoding: 8bit', '',
 '<html><body><p>Yes, ship it.</p><blockquote>Should I merge?</blockquote></body></html>', '',
 '--------------Hk4sJ8dWq1ErTyUiOp2AsDfG--', '', '--------------7xQ2pLmN9vRtYwZ3aBcDeF0g',
 'Content-Type: text/plain; charset=UTF-8; name="log.txt"', 'Content-Disposition: attachment; filename="log.txt"', 'Content-Transfer-Encoding: base64', '',
 'aGVsbG8K', '', '--------------7xQ2pLmN9vRtYwZ3aBcDeF0g--', '']);
passes('gmail mixed(alternative QP) + png reply with quoted text', gmail, /Approved, go ahead/);
passes('thunderbird plain reply with quoted "-- " and a 30-dash line + pdf', tbPlain, /Approved/);
passes('thunderbird mixed(alternative) + txt reply with a folded boundary parameter', tbHtml, /Yes, ship it/);

// padded delimiters agree in both readers (Fenn: ok, no leak)
const pad = (d1) => J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', d1, 'Content-Type: text/plain', 'Content-Disposition: attachment; filename=a.txt', '', P, '--B--', '']);
for (const [k, d] of Object.entries({ pad_sp: '--B   ', pad_tab: '--B\t', term_pad: '--B-- ' })) {
  test(`R10 padded delimiter ${k} -> no leak`, async () => { noLeak(await strip(bytes(pad(d)))); });
}
