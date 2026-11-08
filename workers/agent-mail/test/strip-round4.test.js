// MC-1166 unit 3 fix round 4: a `>` inside a bracketed domain literal on the right of
// `@` (RFC 5322 3.6.4 no-fold-literal; dtext includes `>`) does not close the msg-id,
// so `<root@[a>b]> (comment)` and `<root@[a>b]>` normalise equal. Fenn round-4 repros.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';
import { normalizeMsgId } from '../content-id.js';

const P = 'INJECTED-CONTENT';
const OK = 'Approved.';
const msg = (headers, body = P) => `${headers}\r\n\r\n${body}`;
const multi = (type, b, parts, extra = '') =>
  msg(`Content-Type: multipart/${type}; boundary=${b}${extra}`,
    parts.map((p) => `--${b}\r\n${p}\r\n`).join('') + `--${b}--`);
const leaf = (id, body = OK, type = 'text/plain') =>
  msg(`Content-Type: ${type}\r\nContent-ID: ${id}`, body);
const start = (t) => `; type="${t}"; start="<root@[a>b]>"`;

test('domain-literal IDs that normalise equal count as two hits -> no root (Fenn R4 repro)', async () => {
  const r = await strip(multi('related', 'B', [
    leaf('<root@[a>b]> (comment)', '<p>Visible</p>', 'text/html'),
    leaf('<root@[a>b]>', P),
  ], start('text/html')));
  assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
  assert.equal(r.ok, false, JSON.stringify(r));
  assert.equal(r.reason_code, 'html_only');
});

test('single root with domain literal and trailing comment is kept (Fenn R4 control)', async () => {
  const r = await strip(multi('related', 'B', [leaf('<root@[a>b]> (comment)')], start('text/plain')));
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(r.text, OK);
  assert.equal(r.source, 'plain');
});

test('normalizeMsgId: domain-literal cases', () => {
  const cases = [
    ['<root@[a>b]>', 'root@[a>b]'],
    ['<root@[a>b]> (comment)', 'root@[a>b]'],
    ['(c) <root@[a>b]>', 'root@[a>b]'],
    ['<root@[a>b]> junk', '<root@[a>b]> junk'],
    ['<root@[unclosed>', 'root@[unclosed'],
  ];
  for (const [input, want] of cases) assert.equal(normalizeMsgId(input), want, JSON.stringify(input));
});
