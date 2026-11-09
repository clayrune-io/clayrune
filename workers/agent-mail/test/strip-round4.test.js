// MC-1166 unit 3 fix round 4: a `>` inside a bracketed domain literal on the right of
// `@` (RFC 5322 3.6.4 no-fold-literal; dtext includes `>`) does not close the msg-id,
// so `<root@[a>b]> (comment)` and `<root@[a>b]>` normalise equal. Fenn round-4 repros.
import test from 'node:test';
import assert from 'node:assert/strict';
import { strip } from '../strip.js';
import { strictMsgId } from '../content-id.js';

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

// fix round 5: a domain literal holding '>' is not strict syntax -> no root
test('single root with a ">" domain literal selects no root (strict ids only)', async () => {
  const r = await strip(multi('related', 'B', [leaf('<root@[a>b]> (comment)', P)], start('text/plain')));
  assert.ok(!JSON.stringify(r).includes(P), `leaked: ${JSON.stringify(r)}`);
  assert.equal(r.has_text, false, JSON.stringify(r));
});

test('strictMsgId: domain-literal cases', () => {
  const cases = [
    ['<root@[a>b]>', null],
    ['<root@[a>b]> (comment)', null],
    ['(c) <root@[a>b]>', null],
    ['<root@[unclosed>', null],
    ['<root@[10.0.0.1]> (c)', 'root@[10.0.0.1]'],
  ];
  for (const [input, want] of cases) assert.equal(strictMsgId(input), want, JSON.stringify(input));
});
