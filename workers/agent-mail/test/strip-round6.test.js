// MC-1166 unit 3 fix round 6: a Content-ID written with whitespace before the colon
// (`Content-ID : <x>`) is still a Content-ID to other readers (Thunderbird's
// MimeHeaders_get backs over it). postal-mime keeps the space in the key and leaves
// contentId unset, so root selection used to skip that child and miss the duplicate.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';

const P = 'INJECTED-CONTENT';
const OK = 'Approved.';
const msg = (headers, body = P) => `${headers}\r\n\r\n${body}`;
const multi = (type, b, parts, extra = '') =>
  msg(`Content-Type: multipart/${type}; boundary=${b}${extra}`,
    parts.map((p) => `--${b}\r\n${p}\r\n`).join('') + `--${b}--`);
const part = (type, idHeader, body) => msg(`Content-Type: ${type}${idHeader ? `\r\n${idHeader}` : ''}`, body);
const noLeak = (r) => assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
const start = (t) => `; type="${t}"; start="<root@example.net>"`;

for (const [name, sep] of [['space', ' '], ['tab', '\t']]) {
  test(`Fenn R6 repro (${name} before colon): spaced html root + plain duplicate -> nothing read`, async () => {
    const r = await strip(multi('related', 'B', [
      part('text/html', `Content-ID${sep}: <root@example.net>`, '<p>Visible</p>'),
      part('text/plain', 'Content-ID: <root@example.net>', P),
    ], start('text/html')));
    noLeak(r);
    assert.equal(r.ok, false, JSON.stringify(r));
    assert.equal(r.reason_code, 'html_only');
  });

  test(`Fenn R6 reversed (${name}): canonical html first, spaced plain duplicate -> nothing read`, async () => {
    const r = await strip(multi('related', 'B', [
      part('text/html', 'Content-ID: <root@example.net>', '<p>Visible</p>'),
      part('text/plain', `Content-ID${sep}: <root@example.net>`, P),
    ], start('text/html')));
    noLeak(r);
    assert.equal(r.ok, false, JSON.stringify(r));
    assert.equal(r.reason_code, 'html_only');
  });

  test(`spaced Content-ID on the only matching root is read consistently (${name})`, async () => {
    const r = await strip(multi('related', 'B', [
      part('text/plain', `Content-ID${sep}: <root@example.net>`, OK),
    ], start('text/plain')));
    assert.equal(r.ok, true, JSON.stringify(r));
    assert.equal(r.text.trim(), OK);
  });
}

test('control: an image resource with no Content-ID does not void the root', async () => {
  const r = await strip(multi('related', 'B', [
    part('text/plain', 'Content-ID: <root@example.net>', OK),
    msg('Content-Type: image/png\r\nContent-Transfer-Encoding: base64', 'iVBORw0KGgo='),
  ], start('text/plain')));
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(r.text.trim(), OK);
});
