// MC-1166 unit 3 fix round 12: four allowlists on Content-* headers (canonical.js
// headerAllowlistRefusal), each closing a class Fenn's round-12 review found one instance of.
//   1. no C0 control (bar HTAB) or DEL in a Content-* header name or value
//   2. Content-Transfer-Encoding in {7bit, 8bit, binary, quoted-printable, base64}
//   3. a `*` parameter on Content-Type / Content-Disposition only as name* / filename*
//   4. no X-Sun-Charset header
// Repros are Fenn's r12-leaks.mjs; the ordinary shapes are r12-ordinary.mjs plus the
// Apple Mail / Gmail / Thunderbird shapes committed in rounds 10-11, which must still pass.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
const bytes = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0)));
const REFUSED = { ok: false, reason_code: 'malformed_mime' };
const J = (a) => a.join('\r\n');
const N = '\u0000';

function refuses(name, msg) {
  test(`R12 ${name} -> malformed_mime, no leak`, async () => {
    const r = await strip(bytes(msg));
    noLeak(r);
    assert.deepEqual(r, REFUSED);
  });
}
function passes(name, msg, expectText) {
  test(`R12 ordinary: ${name} -> ok`, async () => {
    const r = await strip(bytes(msg));
    assert.equal(r.ok, true, JSON.stringify(r));
    if (expectText !== undefined) assert.match(r.text, expectText);
  });
}

// --- 1. control characters ------------------------------------------------------------------
// Thunderbird copies a value through a C string: a NUL ends it, so an unterminated quote ends
// parameter parsing (no boundary at all) or cuts a boundary short.
refuses('r12_nul_in_earlier_quoted', J([`Content-Type: multipart/mixed; x="${N}"; boundary=B`, '',
  '--B', 'Content-Type: text/plain', '', P, '--B--', '']));
refuses('r12_nul_in_quoted_boundary', J([`Content-Type: multipart/mixed; boundary="B${N}Z"`, '',
  '--B', 'Content-Type: text/plain', '', 'Visible',
  '--B', 'Content-Type: application/octet-stream', 'Content-Disposition: attachment; filename=a.bin', '',
  `--B${N}Z`, 'Content-Type: text/plain', '', P, `--B${N}Z--`, '--B--', '']));
refuses('ctl_soh_in_quoted_param', J(['Content-Type: text/plain; x="\u0001"', '', P, '']));
for (const c of ['\u0000', '\u0001', '\u0008', '\u000b', '\u000c', '\u001b', '\u001f', '\u007f']) {
  const hex = c.charCodeAt(0).toString(16).padStart(2, '0');
  refuses(`control 0x${hex} in a Content-Type value, top level`, J([`Content-Type: text/plain; x="a${c}b"`, '', P, '']));
  refuses(`control 0x${hex} in a Content-Disposition value, nested part`, J(['Content-Type: multipart/mixed; boundary=B', '',
    '--B', 'Content-Type: text/plain', `Content-Disposition: inline; x="a${c}b"`, '', P, '--B--', '']));
  refuses(`control 0x${hex} in a Content-ID value`, J(['Content-Type: text/plain', `Content-ID: <a${c}b@x.example>`, '', P, '']));
}
refuses('NUL after an unquoted token', J([`Content-Type: text/plain; charset=utf-8${N}`, '', P, '']));
refuses('NUL in a Content-Transfer-Encoding value', J(['Content-Type: text/plain', `Content-Transfer-Encoding: 7bit${N}`, '', P, '']));
refuses('NUL in a folded continuation line', J([`Content-Type: text/plain;`, `\tcharset="u${N}tf-8"`, '', P, '']));
// a control in a header that is not Content-* is not this rule's business (and not refused)
passes('HTAB inside a folded Content-Type is fine', J(['Content-Type: text/plain;', '\tcharset="utf-8"', '', 'hello', '']), /hello/);

// --- 2. Content-Transfer-Encoding allowlist ---------------------------------------------------
// Thunderbird decodes these and drops every line before `begin ` / `=ybegin line=`; postal-mime
// passes the body through. A text part is blank to Ron and plain text to postal-mime.
for (const cte of ['x-uuencode', 'uuencode', 'x-uue', 'uue', 'x-yencode', 'x-foo', 'x-gzip64', 'binhex', 'x-binhex', 'bogus']) {
  refuses('r12_cte_' + cte, J(['Content-Type: text/plain', `Content-Transfer-Encoding: ${cte}`, '', P, '']));
  refuses(`r12_cte_${cte} in a mixed`, J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible',
    '--B', 'Content-Type: text/plain', `Content-Transfer-Encoding: ${cte}`, '', P, '--B--', '']));
}
refuses('r12_cte_uue_in_mixed', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B',
  'Content-Type: text/plain', 'Content-Transfer-Encoding: x-uuencode', '', P, '--B--', '']));
refuses('CTE on the multipart container itself', J(['Content-Type: multipart/mixed; boundary=B', 'Content-Transfer-Encoding: x-uuencode', '',
  '--B', 'Content-Type: text/plain', '', P, '--B--', '']));
for (const cte of ['7bit', '8bit', 'binary', 'quoted-printable', 'base64', '7BIT', 'Quoted-Printable', 'BASE64', ' 7bit ']) {
  const body = cte.trim().toLowerCase() === 'base64' ? 'aGVsbG8=' : 'hello'; // base64 of "hello"
  passes(`CTE ${JSON.stringify(cte)}`, J(['Content-Type: text/plain', `Content-Transfer-Encoding:${cte.startsWith(' ') ? '' : ' '}${cte}`, '', body, '']), /hello/);
}
passes('CTE absent (7bit)', J(['Content-Type: text/plain', '', 'hello', '']), /hello/);

// --- 3. starred parameters --------------------------------------------------------------------
// Thunderbird prefers an RFC 2231 form to a plain parameter; postal-mime keeps the plain one.
refuses('r12_start_star_beside_plain', J(["Content-Type: multipart/related; start=\"<a@x.example>\"; start*=''%3Cb%40x.example%3E; boundary=R", '',
  '--R', 'Content-Type: text/plain', 'Content-ID: <a@x.example>', '', P,
  '--R', 'Content-Type: text/plain', 'Content-ID: <b@x.example>', '', 'Visible', '--R--', '']));
refuses('r12_start_star_alone', J(["Content-Type: multipart/related; start*=''%3Cb%40x.example%3E; boundary=R", '',
  '--R', 'Content-Type: text/plain', 'Content-ID: <b@x.example>', '', 'Visible', '--R--', '']));
refuses('r12_charset_star_beside_plain', J(["Content-Type: text/plain; charset=us-ascii; charset*=''utf-16le", '', P, '']));
for (const p of ["boundary*=''B", 'boundary*0=B', 'boundary*0*=utf-8\'\'B', "type*=''x", "charset*0=utf-", "foo*=''x", 'x*1=a', '*=a', '*x=a']) {
  refuses(`Content-Type parameter ${p}`, J([`Content-Type: multipart/mixed; ${p}; boundary=B`, '', '--B', 'Content-Type: text/plain', '', P, '--B--', '']));
}
refuses('Content-Disposition parameter creation-date* ', J(['Content-Type: text/plain', "Content-Disposition: inline; creation-date*=''x", '', P, '']));
refuses('Content-Disposition parameter size*0', J(['Content-Type: text/plain', 'Content-Disposition: inline; size*0=1', '', P, '']));
refuses('starred parameter in a nested part', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', "Content-Type: text/plain; charset*=''utf-16le", '', P, '--B--', '']));
refuses('uppercase CHARSET*', J(["Content-Type: text/plain; CHARSET*=''utf-16le", '', P, '']));
// name*, filename*, name*0*, filename*0*/filename*1* are the shapes real clients send
passes('Apple name*', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', "Content-Type: application/pdf; name*=utf-8''a%20b.pdf",
  "Content-Disposition: attachment; filename*=utf-8''a%20b.pdf", '', 'JVBERi0=', '--B', 'Content-Type: text/plain', '', 'Visible', '--B--', ''] ), /Visible/);
passes('Gmail filename*0* / filename*1*', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible', '--B',
  'Content-Type: application/pdf; name="x.pdf"', 'Content-Disposition: attachment;', "\tfilename*0*=utf-8''long%20name%20part%20one;", '\tfilename*1*=%20part%20two.pdf',
  '', 'JVBERi0=', '--B--', ''] ), /Visible/);

// --- 4. X-Sun-Charset -------------------------------------------------------------------------
refuses('r12_x_sun_charset', J(['X-Sun-Charset: utf-16le', 'Content-Type: text/plain', '', P, '']));
refuses('X-Sun-Charset in a nested part', J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'X-Sun-Charset: utf-16le', 'Content-Type: text/plain', '', P, '--B--', '']));
refuses('X-Sun-Charset, mixed case', J(['x-sun-CHARSET: us-ascii', 'Content-Type: text/plain; charset=us-ascii', '', P, '']));
refuses('X-Sun-Charset with space before the colon', J(['X-Sun-Charset : utf-16le', 'Content-Type: text/plain', '', P, '']));

// --- ordinary real-client shapes (Fenn's r12-ordinary.mjs, 10 shapes) -------------------------------
passes('outlook_docx_size_dates', J(['MIME-Version: 1.0', 'Content-Type: multipart/mixed;', '\tboundary="_004_DM6PR11MB_"', '', '--_004_DM6PR11MB_',
  'Content-Type: text/plain; charset="us-ascii"', 'Content-Transfer-Encoding: quoted-printable', '', 'Approved.', '',
  '--_004_DM6PR11MB_', 'Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document;', '\tname="Q4 Plan.docx"',
  'Content-Description: Q4 Plan.docx', 'Content-Disposition: attachment;', '\tfilename="Q4 Plan.docx"; size=12345;', '\tcreation-date="Mon, 09 Nov 2026 10:00:00 GMT";',
  '\tmodification-date="Mon, 09 Nov 2026 10:00:00 GMT"', 'Content-Transfer-Encoding: base64', '', 'UEsDBA==', '', '--_004_DM6PR11MB_--', '']), /Approved/);
passes('gmail_mobile_flowed_delsp', J(['Content-Type: text/plain; charset="UTF-8"; format=flowed; delsp=yes', 'Content-Transfer-Encoding: 8bit', '', 'Sounds good ', 'to me.', '']), /Sounds good/);
passes('yahoo_part_boundary', J(['Content-Type: multipart/alternative; ', '\tboundary="----=_Part_1234_5678.1699999999999"', '', '------=_Part_1234_5678.1699999999999',
  'Content-Type: text/plain; charset=UTF-8', 'Content-Transfer-Encoding: 7bit', '', 'ok', '------=_Part_1234_5678.1699999999999',
  'Content-Type: text/html; charset=UTF-8', 'Content-Transfer-Encoding: 7bit', '', '<p>ok</p>', '------=_Part_1234_5678.1699999999999--', '']), /ok/);
passes('ios_mail_qp', J(['Content-Type: text/plain;', '\tcharset=utf-8', 'Content-Transfer-Encoding: quoted-printable', 'Mime-Version: 1.0 (1.0)', '', 'Yes=E2=80=94go.', '']), /go\./);
passes('old_uppercase', J(['Content-Type: TEXT/PLAIN; CHARSET=US-ASCII', 'Content-Transfer-Encoding: 7BIT', '', 'ok', '']), /ok/);
passes('outlook_express_nextpart', J(['Content-Type: multipart/alternative;', '\tboundary="----=_NextPart_000_0012_01DA1234.56789ABC"', '', '------=_NextPart_000_0012_01DA1234.56789ABC',
  'Content-Type: text/plain;', '\tcharset="iso-8859-1"', 'Content-Transfer-Encoding: quoted-printable', '', 'ok', '------=_NextPart_000_0012_01DA1234.56789ABC--', '']), /ok/);
passes('mailman_footer_inline', J(['Content-Type: multipart/mixed; boundary="===============1234567890=="', '', '--===============1234567890==',
  'Content-Type: text/plain; charset="us-ascii"', 'MIME-Version: 1.0', 'Content-Transfer-Encoding: 7bit', 'Content-Disposition: inline', '', 'body',
  '--===============1234567890==', 'Content-Type: text/plain; charset="us-ascii"', 'MIME-Version: 1.0', 'Content-Transfer-Encoding: 7bit', 'Content-Disposition: inline', '', 'footer', '--===============1234567890==--', '']), /body/);
passes('tb_flowed_7bit', J(['Content-Type: text/plain; charset=UTF-8; format=flowed', 'Content-Transfer-Encoding: 7bit', 'Content-Language: en-US', '', 'ok', '']), /ok/);
passes('winlive_reply_type', J(['Content-Type: text/plain; format=flowed; charset="utf-8"; reply-type=original', 'Content-Transfer-Encoding: 8bit', '', 'ok', '']), /ok/);
// the tenth r12 ordinary shape is RFC-invalid (unquoted `type=multipart/alternative`: `/` is a
// tspecial) and is already refused by the round-11 grammar; kept here as the documented cost.
refuses('unquoted_type_param_slash (RFC-invalid, refused by the grammar)', J(['Content-Type: multipart/related; type=multipart/alternative; boundary=R', '',
  '--R', 'Content-Type: text/plain', '', 'ok', '--R--', '']));
passes('related with quoted type= (the RFC-valid form)', J(['Content-Type: multipart/related; type="multipart/alternative"; boundary=R', '',
  '--R', 'Content-Type: text/plain', '', 'ok', '--R--', '']), /ok/);
