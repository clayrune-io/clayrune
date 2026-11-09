import test from 'node:test';
import assert from 'node:assert/strict';
import {strip} from '../strip.js';
// Fenn's six original round-12 probes, with only the import path changed.
const J = a => a.join('\r\n');
const P = 'INJECTED-CONTENT';
for (const pad of [' ', '\t']) {
  for (const [name, headers, body] of [
    ['attachment', ['Content-Type: text/plain', `Content-Disposition${pad}: attachment; filename=x.txt`], P],
    ['HTML', [`Content-Type${pad}: text/html`], `<div hidden>${P}</div>`],
    ['named plain file', [`Content-Type${pad}: text/plain; name=x.txt`], P],
  ]) {
    test(`${name} with ${JSON.stringify(pad)} before colon must not reach custody`, async () => {
      const raw = J(['Content-Type: multipart/mixed; boundary=B', '', '--B', 'Content-Type: text/plain', '', 'Visible',
        '--B', ...headers, '', body, '--B--', '']);
      const r = await strip(raw);
      assert.ok(!r.ok || !r.text.includes(P), JSON.stringify(r));
    });
  }
}

const REFUSED = {ok: false, reason_code: 'malformed_mime'};
const shapes = [
  ['Content-Type', 'text/html'],
  ['Content-Disposition', 'attachment; filename=x.txt'],
  ['Content-Transfer-Encoding', 'base64'],
  ['Content-ID', '<root@example.net>'],
];
const mixed = (headers, body) => J(['Content-Type: multipart/mixed; boundary=B', '',
  '--B', 'Content-Type: text/plain', '', 'Visible', '--B', ...headers, '', body, '--B--', '']);
const scopes = [
  ['top level', (headers) => J([...headers, '', P])],
  ['child', (headers) => mixed(headers, P)],
  ['nested child', (headers) => mixed(['Content-Type: multipart/alternative; boundary=C'],
    J(['--C', ...headers, '', P, '--C--']))],
  ['dropped subtree', (headers) => mixed([
    'Content-Type: multipart/mixed; boundary=C', 'Content-Disposition: attachment',
  ], J(['--C', ...headers, '', P, '--C--']))],
];
for (const [name, value] of shapes) {
  for (const pad of [' ', '\t', ' \t ']) {
    for (const [scope, wrap] of scopes) {
      test(`R13 padded ${name} ${JSON.stringify(pad)} at ${scope} refuses without custody`, async () => {
        const key = pad.length > 1 ? name.toUpperCase() : name;
        const headers = [`${key}${pad}: ${value}`];
        if (name !== 'Content-Type') headers.unshift('Content-Type: text/plain');
        assert.deepEqual(await strip(wrap(headers)), REFUSED);
      });
    }
  }
}

for (const [name, value] of shapes) {
  test(`R13 canonical ${name} retains its ordinary meaning`, async () => {
    const headers = [`${name}: ${value}`];
    if (name !== 'Content-Type') headers.unshift('Content-Type: text/plain');
    const body = name === 'Content-Transfer-Encoding' ? 'SGVsbG8=' : P;
    const r = await strip(mixed(headers, body));
    assert.equal(r.ok, true, JSON.stringify(r));
    if (name === 'Content-Type') {
      assert.equal(r.text, 'Visible');
      assert.equal(r.stripped.html_parts, 1);
    } else if (name === 'Content-Disposition') {
      assert.equal(r.text, 'Visible');
      assert.equal(r.stripped.attachments, 1);
    } else {
      assert.equal(r.text, `Visible\n\n${name === 'Content-ID' ? P : 'Hello'}`);
    }
  });
}

test('R13 padded Content-ID on a related root refuses', async () => {
  const r = await strip(J(['Content-Type: multipart/related; boundary=B; start="<root@example.net>"', '',
    '--B', 'Content-Type: text/plain', 'Content-ID : <root@example.net>', '', P, '--B--', '']));
  assert.deepEqual(r, REFUSED);
});

test('R13 duplicate shaping header still takes priority over padding', async () => {
  const r = await strip(mixed(['Content-Type: text/plain', 'Content-Type : text/html'], P));
  assert.deepEqual(r, {ok: false, reason_code: 'ambiguous_mime'});
});

test('R13 descendant duplicate still takes priority over ancestor padding', async () => {
  const r = await strip(J(['Content-Type: multipart/mixed; boundary=B', 'Content-Disposition : inline', '',
    '--B', 'Content-Type: text/plain', 'Content-ID: <a@x>', 'Content-ID: <b@x>', '', P, '--B--', '']));
  assert.deepEqual(r, {ok: false, reason_code: 'ambiguous_mime'});
});

test('R13 padding in an unrelated header and shaping-looking body text remains plain text', async () => {
  const body = 'Content-Disposition : attachment\nContent-Type\t: text/html';
  const r = await strip(J(['Content-Type: text/plain;', '\tcharset=utf-8', 'X-Note : hello', '', body]));
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(r.text, body);
});

