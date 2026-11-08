// Strip a raw MIME message down to plain text before custody (Ron's hardening
// rule 2). Pure: bytes in, { text, counts } out. The stripped parts are never
// returned, stored, decoded further or OCR'd -- only counted.
//
//   strip(raw, opts?) -> Promise<
//     { ok: true,  text, has_text, source: 'plain'|'none', stripped } |
//     { ok: false, reason_code, stripped? } >
//
// REFUSALS. Before any text is taken, the whole MIME tree is checked and the
// message is refused (no text, no counts) when
//   - any part, the top level included, carries more than one Content-Type,
//     Content-Disposition, Content-Transfer-Encoding or Content-ID header
//     (Content-ID picks the multipart/related root): `ambiguous_mime`.
//     Parsers resolve duplicates differently (postal-mime: first wins), so a
//     second header is a way to show one parser a body and another a name or
//     an attachment. Counted on each part's RAW header lines, not on the parsed
//     first-wins view; a legitimate reply never carries a duplicate.
//   - a part's raw Content-Type declares multipart/* but postal-mime parsed it as
//     a leaf (it rewrites a multipart with a missing, empty or non-ASCII boundary
//     to text/plain): `malformed_mime`. Otherwise the declared subtype exclusion
//     below would be applied to the rewritten type and its bytes read as text. The
//     declared type is read through postal-mime's own structured-header parser, so
//     comments carry no meaning and `Content-Type :` is the same header as in the
//     duplicate check above.
//
// ALLOWLIST. Text is taken ONLY from text/plain leaves where
//   - every ancestor is multipart/mixed, multipart/alternative or
//     multipart/related (the root is its own ancestor chain, so a bare
//     text/plain message qualifies), and
//   - every node on the path, leaf and containers, has an absent or `inline`
//     Content-Disposition (every such header, duplicates included) and no
//     filename / name parameter on its Content-Disposition or Content-Type.
// Under multipart/related only the root part (RFC 2387: the child whose Content-ID
// matches the `start` parameter, else the first child) is read; every other child
// is a resource of the root, never a reply body. If `start` is given and names no
// child (or several), nothing under the container is read.
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
import { strictMsgId } from './content-id.js';

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

// Headers that decide how a part's body is read or whether it is a file; a second
// copy of any of them is a parser differential (see REFUSALS above).
const SINGLE_HEADERS = ['content-type', 'content-disposition', 'content-transfer-encoding', 'content-id'];

// Raw header lines of one node. Trimming the name makes `Content-Type :` count as a
// Content-Type: postal-mime keeps the whitespace in the key so it does not collide
// with the real header, but here the only question is "could any reader take this
// for one", so a false positive (refuse) is the safe side.
//
// A field name folded before its colon (`Content-ID` CRLF TAB `: <x>`): postal-mime
// ends the header block on the bare name line and reads the rest as body, while
// Thunderbird joins the fold and reads the header. No per-header check can see a
// header the parser already dropped, so any line holding one header-name token and
// no colon, followed by a folded line that opens with the colon, refuses the whole
// message. Scanned over the raw bytes, bodies included: a false positive refuses,
// it never leaks.
const FOLDED_NAME = /(?:^|[\r\n])[!-9;-~]+[ \t]*(?:(?:\r\n|\n|\r)[ \t]+)+:/;
const latin1 = new TextDecoder('latin1');

function rawHeaderKeys(node) {
  return (node.rawHeaderLines || []).map((h) => String(h.key || '').trim());
}

// The Content-Type this node DECLARED, as postal-mime reads a Content-Type value
// (comments dropped, quoted strings honoured): its own structured-header parser, run
// on the raw header value, because node.contentType is rewritten to text/plain when a
// multipart has no usable boundary. The header is found by the same trimmed name the
// duplicate check uses, so `Content-Type : multipart/signed` (postal-mime keeps the
// space in the key and ignores the line) is the same header to both policies.
function declaredType(node) {
  const h = (node.headers || []).find((x) => String(x.key || '').trim() === 'content-type');
  if (!h) return '';
  return String(node.parseStructuredHeader(String(h.value || '')).value || '').trim().toLowerCase();
}

// -> 'ambiguous_mime' | 'malformed_mime' | null, over every node in the tree,
// dropped subtrees included.
function treeRefusal(node) {
  const keys = rawHeaderKeys(node);
  if (SINGLE_HEADERS.some((k) => keys.filter((x) => x === k).length > 1)) return 'ambiguous_mime';
  if (!node.contentType.multipart && /^multipart\s*\//.test(declaredType(node))) return 'malformed_mime';
  let ambiguous = false;
  let malformed = false;
  for (const child of node.childNodes || []) {
    const r = treeRefusal(child);
    if (r === 'ambiguous_mime') ambiguous = true;
    else if (r) malformed = true;
  }
  if (ambiguous) return 'ambiguous_mime';
  return malformed ? 'malformed_mime' : null;
}

// A child's Content-ID found by trimmed header name, like the duplicate check and
// declaredType: postal-mime keeps whitespace before the colon in the key, so
// `Content-ID : <x>` leaves node.contentId unset while another reader still sees the ID.
// Reading it here keeps that child inside the strictness and uniqueness checks.
function rawContentId(node) {
  const h = (node.headers || []).find((x) => String(x.key || '').trim() === 'content-id');
  return h ? String(h.value || '') : undefined;
}

// RFC 2387 root of a multipart/related node: the child to read, or null for none.
function relatedRoot(node) {
  const kids = node.childNodes;
  const start = node.contentType.parsed.params.start;
  if (start === undefined) return kids[0] || null;
  const want = strictMsgId(start);
  if (want === null) return null;
  // Every child's Content-ID must read strictly: one we cannot read could be a
  // disguised duplicate of the root, so it voids root selection for the node.
  const ids = [];
  for (const k of kids) {
    const raw = rawContentId(k);
    if (raw === undefined) continue;
    const id = strictMsgId(raw);
    if (id === null) return null;
    ids.push([k, id]);
  }
  // Exactly one exact match, and no other child equal ignoring case (a reader
  // that folds the domain's case would see two roots).
  const exact = ids.filter(([, id]) => id === want);
  const folded = ids.filter(([, id]) => id.toLowerCase() === want.toLowerCase());
  return exact.length === 1 && folded.length === 1 ? exact[0][0] : null;
}

function hasNameParam(parsed, raw) {
  const keys = Object.keys((parsed && parsed.params) || {});
  return keys.some((k) => NAME_PARAM_KEY.test(k)) || NAME_PARAM_RAW.test(String(raw || ''));
}

// May body text sit under this node? Judged on the node's own headers only.
function nodeAllowed(node) {
  // every Content-Disposition header, not just the one postal-mime resolves
  // (duplicates are refused as ambiguous_mime before the walk; this stays as a
  // second line)
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
    const root = lc(node.contentType.multipart) === 'related' ? relatedRoot(node) : undefined;
    // a related child that is not the root is walked as dropped, so it is counted
    for (const child of node.childNodes) walk(child, here && (root === undefined || child === root), counts, kept);
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
  if (FOLDED_NAME.test(latin1.decode(bytes))) return { ok: false, reason_code: 'malformed_mime' };

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

  const refusal = treeRefusal(parser.root);
  if (refusal) return { ok: false, reason_code: refusal };

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
