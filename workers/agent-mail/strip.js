// Strip a raw MIME message down to plain text before custody (Ron's hardening
// rule 2). Pure: bytes in, { text, counts } out. The stripped parts are never
// returned, stored, decoded further or OCR'd -- only counted.
//
//   strip(raw, opts?) -> Promise<
//     { ok: true,  text, has_text, source: 'plain'|'html_fallback'|'none', stripped } |
//     { ok: false, reason_code, stripped? } >
//
// Kept:     text/plain parts (inline, not disposition=attachment).
// Fallback: text/html converted to text ONLY when the message has no text/plain
//           part at all (html-text.js; hidden/remote content dropped).
// Dropped:  every attachment, inline/embedded image, calendar part, forwarded
//           message/rfc822, and all HTML when a text/plain part exists.
//
// WHY THIS DOES NOT USE postal-mime's `email.text`: that field merges the plain
// body with text rendered from any html part that is not a sibling of it in one
// multipart/alternative -- a multipart/mixed carrying text/plain + text/html
// yields the HTML payload in `email.text`. So the plain text is read per part
// from the parser's textMap (an @internal field; postal-mime is pinned to an
// exact version in package.json and test/strip.test.js fails if the shape moves).

import PostalMime from 'postal-mime';
import { htmlToText } from './html-text.js';

// Provisional limits from AGENT_MAIL_SPEC D4 (64 KiB inbound MIME, 16 KiB reply).
export const DEFAULT_LIMITS = Object.freeze({
  maxRawBytes: 64 * 1024,
  maxTextBytes: 16 * 1024,
  maxParts: 100,
  maxNestingDepth: 8,
  maxHeadersSize: 32 * 1024,
});

const encoder = new TextEncoder();

function emptyCounts() {
  return {
    attachments: 0, inline_images: 0, calendar_parts: 0, nested_messages: 0,
    html_parts: 0, hidden_dropped: 0, remote_image_refs: 0,
  };
}

function classify(att, counts) {
  const mt = String(att.mimeType || '').toLowerCase();
  if (mt === 'text/calendar' || mt === 'application/ics' || mt === 'text/x-vcalendar'
      || /\.ics$/i.test(att.filename || '')) counts.calendar_parts += 1;
  else if (mt === 'message/rfc822') counts.nested_messages += 1;
  else if (mt.startsWith('image/') && (att.related || att.disposition === 'inline' || att.contentId)) counts.inline_images += 1;
  else counts.attachments += 1;
}

function countRemoteRefs(html) {
  // counts only; the HTML itself is dropped
  return (html.match(/<img\b/gi) || []).length + (html.match(/\burl\s*\(/gi) || []).length
    + (html.match(/<(?:link|video|audio|source|embed|iframe)\b/gi) || []).length;
}

export async function strip(raw, opts = {}) {
  const lim = { ...DEFAULT_LIMITS, ...opts };
  let bytes;
  if (typeof raw === 'string') bytes = encoder.encode(raw);
  else if (raw instanceof Uint8Array) bytes = raw;
  else if (raw instanceof ArrayBuffer) bytes = new Uint8Array(raw);
  else return { ok: false, reason_code: 'bad_input' };
  if (bytes.byteLength > lim.maxRawBytes) return { ok: false, reason_code: 'mime_too_large' };

  const parser = new PostalMime({
    forceRfc822Attachments: true, // a forwarded message is an attachment, never inlined
    maxPartCount: lim.maxParts,
    maxNestingDepth: lim.maxNestingDepth,
    maxHeadersSize: lim.maxHeadersSize,
  });
  let email;
  try {
    email = await parser.parse(bytes);
  } catch (e) {
    return { ok: false, reason_code: /^EMAX/.test(String(e && e.code)) ? 'mime_limits' : 'mime_parse_error' };
  }

  const stripped = emptyCounts();
  for (const att of email.attachments || []) classify(att, stripped);

  // Own entries only: plain from plain, html from html. Never the cross-type
  // fallback rendering postal-mime applies to email.text.
  const plain = [];
  const html = [];
  for (const entry of parser.textMap.values()) {
    for (const item of entry.plain || []) if (item.type === 'text') plain.push(item.value);
    for (const item of entry.html || []) if (item.type === 'text') html.push(item.value);
  }
  stripped.html_parts = html.length;
  for (const h of html) stripped.remote_image_refs += countRemoteRefs(h);

  let text;
  let source;
  if (plain.length > 0) {
    text = plain.join('\n\n');
    source = 'plain';
  } else if (html.length > 0) {
    const parts = html.map(htmlToText);
    text = parts.map((p) => p.text).filter(Boolean).join('\n\n');
    stripped.hidden_dropped = parts.reduce((s, p) => s + p.hidden_dropped, 0);
    source = 'html_fallback';
  } else {
    text = '';
    source = 'none';
  }
  text = text.replace(/\r\n?/g, '\n').trim();

  if (encoder.encode(text).byteLength > lim.maxTextBytes) {
    return { ok: false, reason_code: 'text_too_large', stripped };
  }
  return { ok: true, text, has_text: text !== '', source, stripped };
}
