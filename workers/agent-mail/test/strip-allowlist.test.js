// MIME-tree allowlist + html_only (MC-1166 unit 3 fix round). Ports the nine cases
// Fenn's review suite failed on 8f13eb51 and the controls around them.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { strip } from '../strip.js';
import { mime, multipart, plainPart, htmlPart } from './fixtures.js';

const PAYLOAD = 'INJECTED-CONTENT';
const body = (contentType, text, extra = []) => mime([`Content-Type: ${contentType}`, ...extra], text);
const multipartBody = (parts, boundary = 'inner') => parts
  .map((p) => `--${boundary}\r\n${p.headers.join('\r\n')}\r\n\r\n${p.body}\r\n`).join('') + `--${boundary}--`;

const assertAbsent = (result, ...needles) => {
  const dump = JSON.stringify(result);
  for (const n of needles) assert.ok(!dump.includes(n), `leaked: ${n}`);
};

// ---- html_only: no HTML conversion at all (Dave's call C) -------------------

// Every HTML body Fenn showed leaking hidden text, plus his controls, as HTML-only mail.
const HTML_BODIES = {
  'literal hidden': `<p>Visible</p><div hidden>${PAYLOAD}</div>`,
  'literal display none': `<p>Visible</p><div style="display:none">${PAYLOAD}</div>`,
  'nested hidden': `<p>Visible</p><div hidden><div>${PAYLOAD}</div></div>`,
  'img alt': `<p>Visible</p><img src="https://evil.example/${PAYLOAD}" alt="${PAYLOAD}">`,
  'comment': `<p>Visible</p><!-- ${PAYLOAD} -->`,
  'encoded CSS colon': `<p>Visible</p><span style="display&#58;none">${PAYLOAD}</span>`,
  'style class': `<style>.secret{display:none}</style><p>Visible</p><span class="secret">${PAYLOAD}</span>`,
  'opacity important': `<p>Visible</p><span style="opacity:0!important">${PAYLOAD}</span>`,
  'transparent color': `<p>Visible</p><span style="color:transparent">${PAYLOAD}</span>`,
  'closed details': `<details><summary>Visible</summary>${PAYLOAD}</details>`,
};
for (const [name, html] of Object.entries(HTML_BODIES)) {
  test(`HTML-only mail is dropped as html_only, no text returned: ${name}`, async () => {
    const r = await strip(body('text/html', html));
    assert.equal(r.ok, false);
    assert.equal(r.reason_code, 'html_only');
    assert.equal(r.stripped.html_parts, 1);
    assert.equal('text' in r, false);
    assertAbsent(r, PAYLOAD, 'Visible');
  });
}

test('html_only: charset / transfer-encoded HTML (utf-16 base64, QP soft break) is dropped too', async () => {
  const utf16 = await strip(body('text/html; charset=utf-16le',
    Buffer.from(`<p>Visible</p><div hidden>${PAYLOAD}</div>`, 'utf16le').toString('base64'),
    ['Content-Transfer-Encoding: base64']));
  assert.equal(utf16.reason_code, 'html_only');
  const qp = await strip(body('text/html', `<p>Visible</p><div style=3D"disp=\r\nlay:none">${PAYLOAD}</div>`,
    ['Content-Transfer-Encoding: quoted-printable']));
  assert.equal(qp.reason_code, 'html_only');
  assertAbsent(utf16, PAYLOAD);
  assertAbsent(qp, PAYLOAD);
});

test('html_only: a text/html attachment is an attachment, not html_only', async () => {
  const r = await strip(body('text/html', `<p>${PAYLOAD}</p>`, ['Content-Disposition: attachment']));
  assert.equal(r.ok, true);
  assert.equal(r.has_text, false);
  assert.equal(r.stripped.attachments, 1);
  assertAbsent(r, PAYLOAD);
});

test('html_only: HTML next to a surviving text/plain is ignored and the message is kept', async () => {
  const r = await strip(multipart('alternative', [plainPart('Approved.'), htmlPart(`<p>${PAYLOAD}</p>`)]));
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Approved.');
  assert.equal(r.stripped.html_parts, 1);
  assertAbsent(r, PAYLOAD);
});

test('html_only: a text/plain that did not survive the allowlist does not rescue the HTML', async () => {
  const raw = multipart('mixed', [
    { headers: ['Content-Type: text/plain', 'Content-Disposition: x-private'], body: PAYLOAD },
    htmlPart('<p>html</p>'),
  ]);
  const r = await strip(raw);
  assert.equal(r.reason_code, 'html_only');
  assertAbsent(r, PAYLOAD);
});

// ---- MIME-tree allowlist (Fenn P1: attachment containers, unknown dispositions) ----

const LEAK_CASES = {
  'plain attachment base64': body('text/plain', Buffer.from(PAYLOAD).toString('base64'), ['Content-Disposition: attachment', 'Content-Transfer-Encoding: base64']),
  'plain attachment QP': body('text/plain', PAYLOAD.replaceAll('-', '=2D'), ['Content-Disposition: attachment', 'Content-Transfer-Encoding: quoted-printable']),
  'folded attachment': body('text/plain', PAYLOAD, ['Content-Disposition:\r\n attachment;\r\n filename="x.txt"']),
  'RFC2231 filename attachment': body('text/plain', PAYLOAD, ["Content-Disposition: attachment; filename*=utf-8''x.txt"]),
  'HTML attachment': body('text/html', `<p>${PAYLOAD}</p>`, ['Content-Disposition: attachment']),
  'calendar QP': body('text/calendar', PAYLOAD, ['Content-Transfer-Encoding: quoted-printable']),
  'calendar base64': body('text/calendar', Buffer.from(PAYLOAD).toString('base64'), ['Content-Transfer-Encoding: base64']),
  'embedded image base64': body('image/png', Buffer.from(PAYLOAD).toString('base64'), ['Content-ID: <pic>', 'Content-Disposition: inline', 'Content-Transfer-Encoding: base64']),
  'forwarded message': body('message/rfc822', body('text/plain', PAYLOAD), ['Content-Disposition: inline']),
  'message global': body('message/global', body('text/plain', PAYLOAD)),
  'mixed html sibling': multipart('mixed', [plainPart('Visible'), htmlPart(`<p>${PAYLOAD}</p>`)]),
  'nested normal attachment': multipart('mixed', [plainPart('Visible'), {
    headers: ['Content-Type: multipart/alternative; boundary=inner'],
    body: multipartBody([{ headers: ['Content-Type: text/plain', 'Content-Disposition: attachment'], body: PAYLOAD }, plainPart('Other visible')]),
  }]),
  // the four cases Fenn's review suite failed on 8f13eb51
  'multipart attachment child plain': multipart('mixed', [plainPart('Visible'), {
    headers: ['Content-Type: multipart/alternative; boundary=inner', 'Content-Disposition: attachment; filename=attached.mime'],
    body: multipartBody([plainPart(PAYLOAD)]),
  }]),
  'root multipart attachment': body('multipart/mixed; boundary=inner', multipartBody([plainPart(PAYLOAD)]), ['Content-Disposition: attachment']),
  'multipart attachment child html': body('multipart/alternative; boundary=inner', multipartBody([htmlPart(`<p>${PAYLOAD}</p>`)]), ['Content-Disposition: attachment']),
  'unknown disposition': body('text/plain', PAYLOAD, ['Content-Disposition: x-private; filename=note.txt']),
  // same class, other ways to reach it
  'unknown disposition without filename': body('text/plain', PAYLOAD, ['Content-Disposition: x-private']),
  'empty disposition header': body('text/plain', PAYLOAD, ['Content-Disposition:']),
  'inline disposition WITH filename': body('text/plain', PAYLOAD, ['Content-Disposition: inline; filename=note.txt']),
  'content-type name parameter': body('text/plain; name=note.txt', PAYLOAD),
  'duplicate disposition, first inline second attachment': body('text/plain', PAYLOAD, ['Content-Disposition: inline', 'Content-Disposition: attachment']),
  'multipart/signed': body('multipart/signed; boundary=inner', multipartBody([plainPart(PAYLOAD)])),
  'multipart/digest': body('multipart/digest; boundary=inner', multipartBody([plainPart(PAYLOAD)])),
  'attached multipart two deep': multipart('mixed', [plainPart('Visible'), {
    headers: ['Content-Type: multipart/mixed; boundary=inner', 'Content-Disposition: attachment'],
    body: multipartBody([{
      headers: ['Content-Type: multipart/alternative; boundary=deep'],
      body: multipartBody([plainPart(PAYLOAD), htmlPart(`<p>${PAYLOAD}</p>`)], 'deep'),
    }]),
  }]),
  'named multipart container': multipart('mixed', [plainPart('Visible'), {
    headers: ['Content-Type: multipart/mixed; boundary=inner; name=x.bin'],
    body: multipartBody([plainPart(PAYLOAD)]),
  }]),
};
for (const [name, raw] of Object.entries(LEAK_CASES)) {
  test(`allowlist: nothing leaks (${name})`, async () => {
    assertAbsent(await strip(raw), PAYLOAD);
  });
}

test('allowlist: attachment-container plain next to Visible keeps only Visible and counts the drop', async () => {
  const r = await strip(LEAK_CASES['multipart attachment child plain']);
  assert.equal(r.ok, true);
  assert.equal(r.text, 'Visible');
  assert.equal(r.stripped.attachments, 1);
});

test('allowlist: root attachment multipart yields no text and is counted', async () => {
  const r = await strip(LEAK_CASES['root multipart attachment']);
  assert.equal(r.ok, true);
  assert.equal(r.has_text, false);
  assert.equal(r.source, 'none');
  assert.equal(r.stripped.attachments, 1);
});

test('allowlist: an unknown-disposition leaf is counted as an attachment', async () => {
  const r = await strip(LEAK_CASES['unknown disposition']);
  assert.equal(r.has_text, false);
  assert.equal(r.stripped.attachments, 1);
});

test('allowlist: normal shapes still pass', async () => {
  const cases = {
    'explicit inline': body('text/plain', 'ok', ['Content-Disposition: inline']),
    'mixed > alternative > plain + html': multipart('mixed', [{
      headers: ['Content-Type: multipart/alternative; boundary=inner'],
      body: multipartBody([plainPart('ok'), htmlPart('<p>ok</p>')]),
    }]),
    'related > alternative > plain + html, inline image': multipart('related', [{
      headers: ['Content-Type: multipart/alternative; boundary=inner'],
      body: multipartBody([plainPart('ok'), htmlPart('<img src="cid:i1">')]),
    }, { headers: ['Content-Type: image/gif', 'Content-ID: <i1>', 'Content-Transfer-Encoding: base64'], body: 'R0lGODlhAQABAAAAACw=' }]),
    'mixed > plain + pdf attachment': multipart('mixed', [plainPart('ok'),
      { headers: ['Content-Type: application/pdf', 'Content-Disposition: attachment; filename=a.pdf'], body: 'x' }]),
  };
  for (const [name, raw] of Object.entries(cases)) {
    const r = await strip(raw);
    assert.equal(r.ok, true, name);
    assert.equal(r.text, 'ok', name);
    assert.equal(r.source, 'plain', name);
  }
});

// ---- charset / encoding controls from Fenn's suite --------------------------

test('plain text charsets and transfer encodings decode', async () => {
  const utf16 = await strip(body('text/plain; charset=utf-16le', Buffer.from('Visible', 'utf16le').toString('base64'), ['Content-Transfer-Encoding: base64']));
  assert.equal(utf16.text, 'Visible');
  const folded = await strip(body('text/plain; charset=utf-8', Buffer.from('Visible').toString('base64').replace(/(.{4})/g, '$1\r\n'), ['Content-Transfer-Encoding: base64']));
  assert.equal(folded.text, 'Visible');
});

test('public email.text merges non-sibling HTML; the tree walk avoids it', async () => {
  const { default: PostalMime } = await import('postal-mime');
  const raw = multipart('mixed', [plainPart('Visible'), htmlPart(`<p>${PAYLOAD}</p>`)]);
  const email = await new PostalMime({ forceRfc822Attachments: true }).parse(raw);
  assert.ok(email.text.includes(PAYLOAD)); // why email.text is not used
  assert.equal((await strip(raw)).text, 'Visible');
});

// ---- postal-mime pin and internal tree shape --------------------------------

test('postal-mime is pinned exactly and the installed copy is that version', () => {
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  assert.equal(pkg.dependencies['postal-mime'], '4.2.0');
  const installed = JSON.parse(readFileSync(new URL('../node_modules/postal-mime/package.json', import.meta.url), 'utf8'));
  assert.equal(installed.version, '4.2.0');
});

test('postal-mime internals this module reads still have the expected shape', async () => {
  // strip.js walks parser.root (@internal MimeNode tree). If a postal-mime bump
  // changes it this fails before anything ships -- and the whole allowlist must be
  // re-reviewed against the new tree, not just this assertion updated.
  const { default: PostalMime } = await import('postal-mime');
  const p = new PostalMime({ forceRfc822Attachments: true });
  await p.parse(multipart('mixed', [
    plainPart('shape'),
    { headers: ['Content-Type: application/pdf', 'Content-Disposition: attachment; filename=a.pdf'], body: 'x' },
  ]));
  const root = p.root;
  assert.ok(root && Array.isArray(root.childNodes) && Array.isArray(root.headers));
  assert.equal(root.contentType.multipart, 'mixed');
  assert.equal(root.contentType.parsed.value, 'multipart/mixed');
  assert.equal(root.childNodes.length, 2);
  const [text, att] = root.childNodes;
  assert.equal(text.contentType.multipart, false);
  assert.equal(text.contentType.parsed.value, 'text/plain');
  assert.equal(typeof text.contentType.value, 'string');
  assert.equal(typeof text.contentType.parsed.params, 'object');
  assert.equal(text.getTextContent().trim(), 'shape');
  assert.equal(att.contentDisposition.parsed.value, 'attachment');
  assert.equal(att.contentDisposition.parsed.params.filename, 'a.pdf');
  assert.equal(typeof att.contentDisposition.value, 'string');
  assert.ok(att.headers.some((h) => h.key === 'content-disposition' && typeof h.value === 'string'));
});
