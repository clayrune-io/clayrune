// Strip a raw MIME message down to plain text before custody (Ron's hardening
// rule 2). Pure: bytes in, { text, counts } out. The stripped parts are never
// returned, stored, decoded further or OCR'd -- only counted.
//
//   strip(raw, opts?) -> Promise<
//     { ok: true,  text, has_text, source: 'plain'|'none', stripped } |
//     { ok: false, reason_code, stripped? } >
//
// ALLOWLIST. Text is taken ONLY from text/plain leaves where
//   - every ancestor is multipart/mixed, multipart/alternative or
//     multipart/related (the root is its own ancestor chain, so a bare
//     text/plain message qualifies), and
//   - every node on the path, leaf and containers, has an absent or `inline`
//     Content-Disposition (every such header, duplicates included) and no
//     filename / name parameter on its Content-Disposition or Content-Type.
// Anything else on the path -- attachment, an unrecognised disposition
// (RFC 2183 2.8: treat as attachment), another multipart subtype, a named part --
// drops the whole subtree. A multipart disposition applies to the container.
// text/html is NEVER converted or kept: a message whose only body is HTML is
// dropped with reason_code `html_only`. Deciding what HTML hides from a reader
// is an arms race (entities, stylesheets, !important, transparent colour); every
// mainstream client sends a text/plain alternative (Dave's call, 2026-11-08).
//
// WHY THIS DOES NOT USE postal-mime's `email.text` OR `textMap`: `email.text`
// merges text rendered from any html part that is not a sibling of the plain
// part, and `textMap` is grouped by the nearest multipart/alternative, so
// neither says which container or disposition a leaf sat under. This walks
// `parser.root` (@internal MimeNode tree), the same tree postal-mime's own
// classifier walks, so there is no second MIME parser to disagree with it.
// postal-mime is pinned to an exact version in package.json and
// test/strip.test.js fails if the tree shape or the pin moves.

import PostalMime from 'postal-mime';

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
    html_parts: 0, remote_image_refs: 0,
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

const KEEP_MULTIPART = new Set(['mixed', 'alternative', 'related']);
// parameter names that make a part a named file (RFC 2183 filename, RFC 2045 name,
// RFC 2231 filename*0 / filename*=)
const NAME_PARAM_KEY = /^(?:file)?name/i;
const NAME_PARAM_RAW = /(?:^|;)\s*(?:file)?name\s*\*?\s*\d*\s*\*?\s*=/i;

const lc = (v) => String(v || '').toLowerCase();

function hasNameParam(parsed, raw) {
  const keys = Object.keys((parsed && parsed.params) || {});
  return keys.some((k) => NAME_PARAM_KEY.test(k)) || NAME_PARAM_RAW.test(String(raw || ''));
}

// May body text sit under this node? Judged on the node's own headers only.
function nodeAllowed(node) {
  // every Content-Disposition header, not just the one postal-mime resolves
  for (const h of node.headers || []) {
    if (h.key !== 'content-disposition') continue;
    const token = String(h.value || '').split(';')[0].trim().toLowerCase();
    if (token !== 'inline') return false;
    if (hasNameParam(null, h.value)) return false;
  }
  const disp = node.contentDisposition;
  if (lc(disp.parsed.value) !== '' && lc(disp.parsed.value) !== 'inline') return false;
  if (hasNameParam(disp.parsed, disp.value)) return false;
  if (hasNameParam(node.contentType.parsed, node.contentType.value)) return false;
  if (node.contentType.multipart && !KEEP_MULTIPART.has(lc(node.contentType.multipart))) return false;
  return true;
}

// Walk the MIME tree. `ok` is true while every node on the path so far passed.
// Counts leaves postal-mime classed as inline text that are not kept; leaves it
// classed as attachments are already counted from email.attachments.
function walk(node, ok, counts, kept) {
  const here = ok && nodeAllowed(node);
  if (node.contentType.multipart) {
    for (const child of node.childNodes) walk(child, here, counts, kept);
    return;
  }
  const type = lc(node.contentType.parsed.value);
  const asAttachment = lc(node.contentDisposition.parsed.value) === 'attachment';
  if (type === 'text/html' && !asAttachment) {
    counts.html_parts += 1;
    counts.remote_image_refs += countRemoteRefs(node.getTextContent());
  } else if (type === 'text/plain') {
    if (here) kept.push(node.getTextContent());
    else if (!asAttachment) counts.attachments += 1;
  }
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

  const kept = [];
  walk(parser.root, true, stripped, kept);

  let text = kept.join('\n\n');
  const source = kept.length > 0 ? 'plain' : 'none';
  // HTML present and no surviving text/plain: nothing to read, and no conversion.
  if (kept.length === 0 && stripped.html_parts > 0) {
    return { ok: false, reason_code: 'html_only', stripped };
  }
  text = text.replace(/\r\n?/g, '\n').trim();

  if (encoder.encode(text).byteLength > lim.maxTextBytes) {
    return { ok: false, reason_code: 'text_too_large', stripped };
  }
  return { ok: true, text, has_text: text !== '', source, stripped };
}
