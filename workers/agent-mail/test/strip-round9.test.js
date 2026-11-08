// MC-1166 unit 3 fix round 9: one canonical-form gate on the raw message.
// Round 8 judged only header sections, so a delimiter that Thunderbird reads and
// postal-mime does not (a lone CR ends a line for Thunderbird; FF/VT after the boundary
// is whitespace to it) sat in a BODY, where strip() looked at nothing. postal-mime also
// checks a shared boundary outermost-first, Thunderbird innermost-first. Rather than
// chase each disagreement, strip() refuses (malformed_mime) any message that is not
// canonical:
//   1. a CR not immediately followed by LF, anywhere (bodies included);
//   2. a line that starts with `--` + a declared boundary and continues with anything
//      other than an optional `--` and then space/tab only;
//   3. a nested multipart whose boundary equals, is a prefix of, or has as a prefix,
//      any ancestor's boundary;
//   4. a header section (top level or any part) whose first line is a fold.
// Repros are Fenn's round-9 probes (probe-r9.mjs, probe-r9b.mjs), plus his nine
// ordinary-mail shapes, which must still pass.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
const bytes = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0)));
const REFUSED = { ok: false, reason_code: 'malformed_mime' };
const J = (a) => a.join('\r\n');
const ID = '<root@example.net>';

function refuses(name, msg) {
  test(`R9 ${name} -> malformed_mime, no leak`, async () => {
    const r = await strip(bytes(msg));
    noLeak(r);
    assert.deepEqual(r, REFUSED);
  });
}
function passes(name, msg, expectText) {
  test(`R9 ordinary: ${name} -> ok`, async () => {
    const r = await strip(bytes(msg));
    assert.equal(r.ok, true, JSON.stringify(r));
    if (expectText !== undefined) assert.match(r.text, expectText);
  });
}

// ---- (1) round 7 / round 8 repros stay refused ---------------------------------
const rel = (h) => J(['Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', '--B', ...h, '', '<p>Visible</p>', '--B', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, '--B--']);
const R78 = {
  r7_tab_fold: ['Content-Type: text/html', `Content-ID\r\n\t: ${ID}`],
  r7_space_fold: ['Content-Type: text/html', `Content-ID\r\n : ${ID}`],
  r7_trailing_space: ['Content-Type: text/html', `Content-ID \r\n\t: ${ID}`],
  r7_bare_lf_fold: ['Content-Type: text/html', `Content-ID\n\t: ${ID}`],
  r7_ws_only_fold: ['Content-Type: text/html', `Content-ID\r\n \r\n\t: ${ID}`],
  r8_bare_cr_fold: ['Content-Type: text/html', `Content-ID\r\t: ${ID}`],
  r8_junk_line: ['Content-Type: text/html', 'X', `Content-ID: ${ID}`],
  r8_bare_cr_between: [`Content-Type: text/html\rContent-ID: ${ID}`],
  r8_ff_colon: ['Content-Type: text/html', `Content-ID\f: ${ID}`],
  r8_vt_colon: ['Content-Type: text/html', `Content-ID\v: ${ID}`],
  r8_ff_then_fold: ['Content-Type: text/html', `Content-ID\f\r\n\t: ${ID}`],
  r8_fold_then_ff: ['Content-Type: text/html', `Content-ID\r\n\t\f: ${ID}`],
};
for (const [k, h] of Object.entries(R78)) refuses(`round 7/8 repro ${k}`, rel(h));
// Fenn's r8_junk_after_ct is all valid header lines (`X: y`), so it is not malformed: both
// children carry the root Content-ID, no root is selected, nothing is read.
test('R9 r8_junk_after_ct (valid X: header, duplicate root id) -> refused html_only, no leak', async () => {
  const r = await strip(bytes(rel(['Content-Type: text/html', 'X: y', `Content-ID: ${ID}`])));
  noLeak(r);
  assert.equal(r.ok, false);
  assert.equal(r.reason_code, 'html_only');
});

// ---- (2) delimiters Thunderbird reads and postal-mime does not -------------------
const relTB = (delim) => J(['Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', '--B', 'Content-Type: text/html', '', '<p>Visible</p>',
  `${delim}Content-Type: text/html`, `Content-ID: ${ID}`, '', '<p>Visible</p>',
  '--B', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, '--B--']);
refuses('tb_delim_bare_cr_related (bare CR in a body)', relTB('--B\r'));
refuses('tb_delim_ff_related (--B FF)', relTB('--B\f\r\n'));
refuses('tb_delim_vt_related (--B VT)', relTB('--B\v\r\n'));
const mixTB = (term) => J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', term, '--B', 'Content-Type: text/plain', '', P, '--B--']);
refuses('tb_term_bare_cr_mixed (--B-- CR x)', mixTB('--B--\rx'));
refuses('tb_term_ff_mixed (--B-- FF)', mixTB('--B--\f'));

// ---- (2b) delimiters postal-mime reads and strip did not -------------------------
refuses('pm_delim_cr_cr_lf_junkline', J(['Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', '--B\r', 'Content-Type: text/html', 'X', `Content-ID: ${ID}`, '', '<p>Visible</p>', '--B', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, '--B--']));
refuses('pm_delim_cr_cr_lf_mixed_attach', J(['Content-Type: multipart/mixed; boundary=B', '', '--B\r', 'Content-Type: text/plain', 'X', 'Content-Disposition: attachment; filename=a.txt', '', P, '--B--']));

// ---- (2c) finder vs postal-mime splitting: still refused ------------------------
const junk = ['Content-Type: text/html', 'X', `Content-ID: ${ID}`];
const relD = (d, end = '--B--', sep = '\r\n') => ['Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', 'preamble --B text', d, ...junk, '', '<p>Visible</p>', '--B', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, end, 'epilogue'].join(sep);
refuses('delim_trailing_spaces', relD('--B   '));
refuses('delim_trailing_tab', relD('--B\t'));
refuses('lf_only', relD('--B', '--B--', '\n'));
refuses('lf_only_trailing_ws', relD('--B \t', '--B--', '\n'));
const nested = (inner) => J(['Content-Type: multipart/mixed; boundary=O', '', 'pre', '--O', 'Content-Type: multipart/related; boundary=I; type="text/html"; start="<root@example.net>"', '', 'inner pre', '--I', ...inner, '', '<p>Visible</p>', '--I', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, '--I--', 'inner epi', '--O--', 'epi']);
refuses('nested_junk', nested(junk));
refuses('nested_bare_cr', nested([`Content-Type: text/html\rContent-ID: ${ID}`]));
refuses('boundary_param_trailing_space', J(['Content-Type: multipart/related; boundary="B "; type="text/html"; start="<root@example.net>"', '', '--B ', ...junk, '', '<p>Visible</p>', '--B', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, '--B--']));

// ---- (3) interpretation calls ---------------------------------------------------
refuses('3b CR CR blank line', 'Content-Type: text/plain\r\n\r\r\nHello\r\n');
refuses('3c leading fold, Content-ID on the plain part', J([
  'Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', '--B', 'Content-Type: text/html', '', '<p>Visible</p>',
  '--B', `\tContent-ID: ${ID}`, 'Content-Type: text/plain', '', P, '--B--']));
refuses('3c leading fold, Content-ID on the html part', J([
  'Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', '--B', `\tContent-ID: ${ID}`, 'Content-Type: text/html', '', '<p>Visible</p>',
  '--B', 'Content-Type: text/plain', `Content-ID: ${ID}`, '', P, '--B--']));
refuses('3c top-level leading fold', J([' X-Junk: 1', 'Content-Type: text/plain', '', 'Hello']));
refuses('rule 4: part whose first line is a space-only fold', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', ' ', 'Content-Type: text/plain', '', 'x', '--B--']));

// ---- (r9b) same / prefix boundaries across nesting -------------------------------
refuses('nested_same_boundary', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: multipart/related; boundary=B; type="text/html"; start="<root@example.net>"', '', '--B', 'Content-Type: text/html', `Content-ID: ${ID}`, '', '<p>Visible</p>', '--B', 'Content-Type: text/plain', 'Content-ID: <res@example.net>', '', P, '--B--', '']));
refuses('inner_boundary_is_outer_terminator', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: multipart/related; boundary="B--"; type="text/html"', '', '--B--', 'Content-Type: text/html', '', '<p>Visible</p>', '--B--', 'Content-Type: text/plain', '', P, '--B----', '']));
// Not a refusal: after `--B--` postal-mime reads nothing (epilogue), so the reopened part is
// never returned. The agent sees LESS than a reader that opens it, which is the safe side;
// none of the four canonical-form rules fires, and none was asked for.
test('R9 epilogue_reopen -> epilogue ignored, injected text not returned', async () => {
  const r = await strip(bytes(J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B--', '--B', 'Content-Type: text/plain', '', P, ''])));
  noLeak(r);
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Visible');
});
const nestBnd = (outer, inner) => J([`Content-Type: multipart/mixed; boundary="${outer}"`, '', `--${outer}`, `Content-Type: multipart/alternative; boundary="${inner}"`, '', `--${inner}`, 'Content-Type: text/plain', '', 'Visible', `--${inner}--`, `--${outer}--`, '']);
refuses('inner boundary extends outer (B / BB)', nestBnd('B', 'BB'));
refuses('inner boundary is a prefix of outer (BB / B)', nestBnd('BB', 'B'));
refuses('grandchild extends grandparent', J(['Content-Type: multipart/mixed; boundary=AAA', '', '--AAA', 'Content-Type: multipart/mixed; boundary=ZZZ', '', '--ZZZ', 'Content-Type: multipart/alternative; boundary=AAAB', '', '--AAAB', 'Content-Type: text/plain', '', 'x', '--AAAB--', '--ZZZ--', '--AAA--', '']));
passes('unrelated nested boundaries', nestBnd('outer_1', 'inner_2'), /Visible/);

// ---- (rule 1/2 unit cases) -------------------------------------------------------
refuses('rule 1: bare CR in a single-part body', 'Content-Type: text/plain\r\n\r\nHello\rWorld\r\n');
refuses('rule 1: bare CR as the last byte', 'Content-Type: text/plain\r\n\r\nHello\r\n\r');
refuses('rule 1: CR CR LF in a body', 'Content-Type: text/plain\r\n\r\nHello\r\r\nWorld\r\n');
refuses('rule 1: bare CR in a base64 attachment body', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Hi', '--B', 'Content-Type: application/pdf', 'Content-Transfer-Encoding: base64', '', 'JVBERi0x\rLjQK', '--B--', '']));
const mixBody = (line) => J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', line, '--B', 'Content-Type: text/plain', '', 'More', '--B--', '']);
refuses('rule 2: body line --Bx (boundary continues)', mixBody('--Bx'));
refuses('rule 2: body line --B--x', mixBody('--B--x'));
refuses('rule 2: body line --B---', mixBody('--B---'));
refuses('rule 2: body line --B NBSP', mixBody('--B\xa0'));
passes('rule 2: --B--<tab> is a plain terminator shape', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B--\t', '']), /Visible/);
passes('rule 2: signature "-- " and quoted "> --Bx" are not delimiter lines', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'ok', '> --Bx', '-- ', 'Ron', '--B--', '']), /ok/);

// ---- (4) ordinary mail: all must pass -------------------------------------------
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
