// MC-1166 unit 3 fix round 5: root selection reads Content-ID/start strictly and
// fails closed. Fenn round-5 repro: '[' inside an obsolete local-part comment
// flipped the old lexer into domain-literal mode, so two parts with the same id
// compared as different and the injected one became the root.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const OK = 'Approved.';
const msg = (headers, body = P) => `${headers}\r\n\r\n${body}`;
const multi = (type, b, parts, extra = '') =>
  msg(`Content-Type: multipart/${type}; boundary=${b}${extra}`,
    parts.map((p) => `--${b}\r\n${p}\r\n`).join('') + `--${b}--`);
const leaf = (id, body = OK, type = 'text/plain') =>
  msg(`Content-Type: ${type}\r\nContent-ID: ${id}`, body);
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);

const fennStart = `; type="text/html"; start="<root([)@example.net>"`;

test('Fenn R5 repro: comment-in-local-part duplicate pair -> nothing read', async () => {
  const r = await strip(multi('related', 'B', [
    leaf('<root([)@example.net>', '<p>Visible</p>', 'text/html'),
    leaf('<root([)@example.net> (outside)', P),
  ], fennStart));
  noLeak(r);
  assert.equal(r.ok, false, JSON.stringify(r));
  assert.equal(r.reason_code, 'html_only');
});

test('Fenn R5 control: single non-strict root fails closed', async () => {
  const r = await strip(multi('related', 'B', [leaf('<root([)@example.net>', P)],
    `; type="text/plain"; start="<root([)@example.net>"`));
  noLeak(r);
  assert.equal(r.has_text, false, JSON.stringify(r));
});

test('a non-strict sibling id voids root selection even when start matches strictly', async () => {
  const r = await strip(multi('related', 'B', [
    leaf('<root@example.net>', P),
    leaf('<"root"@example.net>', P),
  ], '; type="text/plain"; start="<root@example.net>"'));
  noLeak(r);
  assert.equal(r.has_text, false, JSON.stringify(r));
});

test('case-folded duplicate of the root -> no root', async () => {
  const r = await strip(multi('related', 'B', [
    leaf('<root@example.net>', P),
    leaf('<root@EXAMPLE.net>', P),
  ], '; type="text/plain"; start="<root@example.net>"'));
  noLeak(r);
  assert.equal(r.has_text, false, JSON.stringify(r));
});

test('plain strict ids still select the root', async () => {
  const r = await strip(multi('related', 'B', [
    leaf('<pic@example.net>', P, 'text/x-other'),
    leaf('<root@example.net> (c)'),
  ], '; type="text/plain"; start="<root@example.net>"'));
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(r.text, OK);
  noLeak(r);
});
