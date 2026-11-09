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
//   - any of those four header names has whitespace before its colon:
//     `malformed_mime`. postal-mime ignores the padded name when extracting type,
//     disposition, encoding or ID, while validation and other readers trim it.
//     Refuse rather than let extraction default to an untyped plain body (round 13).
//   - a part's raw Content-Type declares multipart/* but postal-mime parsed it as
//     a leaf (it rewrites a multipart with a missing, empty or non-ASCII boundary
//     to text/plain): `malformed_mime`. Otherwise the declared subtype exclusion
//     below would be applied to the rewritten type and its bytes read as text. The
//     declared type is read through postal-mime's own structured-header parser, so
//     comments carry no meaning. Padded names also count for duplicates, then refuse.
//   - any header section (top level, and each part after a delimiter line), read from
//     the RAW bytes, holds a line that is neither a header field nor a space/tab fold,
//     holds a bare CR, or does not end on an empty line: `malformed_mime`. postal-mime
//     ends a header block at the first such line, Thunderbird only at a blank line, so
//     a Content-ID behind it is read by one reader and not the other. Bodies are not
//     inspected. See headerSectionRefusal. A section whose first line is a fold is
//     refused too.
//   - the raw message is not in canonical form (canonical.js; round 9): a CR not
//     followed by LF anywhere, bodies included; a line that starts `--` + a declared
//     boundary and continues with anything but an optional `--` and space/tab; a nested
//     multipart whose boundary equals, prefixes or extends an ancestor's:
//     `malformed_mime`. postal-mime and Thunderbird disagree on which lines are
//     delimiters in each of those cases, so a part is hidden from one and not the other.
//   - a Content-Type, Content-Disposition or Content-Transfer-Encoding (any part) is not
//     wholly in the strict grammar `token "/" token *( ";" token "=" ( token / quoted ) )`
//     (quoted: no `\`, no CR/LF; Disposition without the `/`; CTE one token); a
//     Content-Type has a `boundary*` parameter, more than one `boundary`, or a boundary
//     that is not a plain token or a quoted string with no edge whitespace; or a
//     multipart/* has no `boundary`: `malformed_mime` (canonical.js; rounds 10-11).
//     postal-mime and Thunderbird read a Content-Type differently (comments, backslashes,
//     RFC 2231, quoting, an unreadable earlier parameter), so the two would split the
//     message on different delimiters; only shapes both read alike are admitted.
//     `filename*` is not affected.
//   - allowlists on top of the grammar (canonical.js; round 12): a C0 control (not HTAB) or
//     DEL in a Content-* header name or value; a Content-Transfer-Encoding other than 7bit,
//     8bit, binary, quoted-printable or base64; a Content-Type or Content-Disposition
//     parameter whose name contains `*` and does not start `name` or `filename`; or an
//     X-Sun-Charset header: `malformed_mime`. Thunderbird truncates a value at NUL, decodes
//     uuencode/yEnc bodies, prefers `start*`/`charset*` to the plain parameter and reads
//     X-Sun-Charset; postal-mime does none of those.
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
import {
  bareCrRefusal, boundaryLineRefusal, boundaryNestingRefusal, boundaryOf,
  headerGrammarRefusal, headerAllowlistRefusal, boundaryParamRefusal, multipartBoundaryRefusal,
} from './canonical.js';

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
// Thunderbird joins the fold and reads the header. Scanned over header sections only
// (headerSectionRefusal), so a body line followed by an indented colon line is ordinary
// text. Any whitespace but a line break counts as padding or fold, form feed and
// vertical tab included: Thunderbird backs up over ASCII isspace before the colon and
// jsmime's rule is unverified, so doubt resolves to refusal. The field-line check
// there already refuses a bare name line (no colon); this stays as a second line.
const FOLDED_NAME = /(?:^|[\r\n])[!-9;-~]+[^\S\r\n]*(?:(?:\r\n|\n|\r)[^\S\r\n]+)+[^\S\r\n]*:/;
// A header line as postal-mime reads it (mime-node.ts FIELD_START): a name of printable
// ASCII bar the colon, optional space or tab, a colon. Anything else is not a header.
const FIELD_LINE = /^(?:From |[!-9;-~]*[ \t]*:)/;
const latin1 = new TextDecoder('latin1');

function rawHeaderKeys(node) {
  return (node.rawHeaderLines || []).map((h) => String(h.key || '').trim());
}

// The Content-Type this node DECLARED, as postal-mime reads a Content-Type value
// (comments dropped, quoted strings honoured): its own structured-header parser, run
// on the raw header value, because node.contentType is rewritten to text/plain when a
// multipart has no usable boundary. The header is found by the same trimmed name the
// duplicate check uses, so `Content-Type : multipart/signed` (postal-mime keeps the
// space in the key and ignores the line) still counts here, then refuses in round 13.
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
  // Keep duplicate-header priority, including duplicates in a descendant. All four
  // body-shaping names must be exact: trimming here would disagree with extraction.
  let malformed = (node.rawHeaderLines || []).some((h) => {
    const key = String(h.key || '');
    return key !== key.trim() && SINGLE_HEADERS.includes(key.trim());
  });
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
// Reading it here keeps that child inside the strictness and uniqueness checks;
// round 13 now refuses the padded name before root selection can run.
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

// Every boundary a multipart node of the tree declares, as postal-mime registers it
// (mime-node.ts: trailing space trimmed, ASCII only).
function boundariesOf(node, out = new Set()) {
  const b = boundaryOf(node);
  if (b) out.add(b);
  for (const child of node.childNodes || []) boundariesOf(child, out);
  return out;
}

// Is this line `--B` ('open') or `--B--` ('close') of one of `boundaries`, followed by
// nothing but space or tab? Matched the way postal-mime's processLine matches.
function delimiterKind(line, boundaries) {
  if (line.length <= 2 || !line.startsWith('--')) return null;
  for (const b of boundaries) {
    if (!line.startsWith(b, 2)) continue;
    let rest = line.slice(2 + b.length);
    const close = rest.startsWith('--');
    if (close) rest = rest.slice(2);
    if (/^[ \t]*$/.test(rest)) return close ? 'close' : 'open';
  }
  return null;
}

// Where does a part's header block end? postal-mime (mime-node.ts feed) ends it at the
// first line that is neither a header field nor a space/tab fold, and splits lines on
// LF only. Thunderbird (libmime) ends it only at a blank line, skips colon-less lines
// and splits on a bare CR too, so a Content-ID behind a junk line or a bare CR is read
// by one reader and not the other. Judged on the RAW bytes, never postal-mime's parsed
// view (the lines it dropped are not in it): every header section -- the top level and
// each part after a delimiter line -- must hold only header-field lines and folds, no
// bare CR, and end on an empty line. Anything else is malformed_mime. Body text is not
// inspected here (canonical.js covers bodies). A section's first line must not be a fold:
// there is nothing to fold onto, postal-mime reads it as a continuation of nothing and
// Thunderbird keeps the leading whitespace in the name, so the two disagree on whether
// `<TAB>Content-ID: <x>` is a Content-ID. A CR CR LF line is blank to both readers
// (postal-mime drops every trailing CR); it is refused only because a CR not followed by
// LF is non-canonical (bareCrRefusal), and the bare-CR check below stays as a second line.
function headerSectionRefusal(text, boundaries) {
  const lines = text.split('\n');
  let inHeaders = true;
  let section = [];
  for (let i = 0; i < lines.length; i += 1) {
    const hasLf = i < lines.length - 1;
    let line = lines[i];
    if (hasLf && line.endsWith('\r')) line = line.slice(0, -1);
    if (!inHeaders) {
      if (delimiterKind(line, boundaries) === 'open') { inHeaders = true; section = []; }
      continue;
    }
    if (hasLf && line === '') {
      if (FOLDED_NAME.test(section.join('\n'))) return 'malformed_mime';
      inHeaders = false;
      continue;
    }
    if (line.includes('\r')) return 'malformed_mime';
    if (section.length === 0 && /^[ \t]/.test(line)) return 'malformed_mime';
    if (!(/^[ \t]/.test(line) || FIELD_LINE.test(line))) return 'malformed_mime';
    section.push(line);
  }
  // the text ended inside a header section, so it did not end on an empty line
  return inHeaders ? 'malformed_mime' : null;
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

  // canonical-form gate, rule 1 (see canonical.js): needs no tree, so it runs before parsing
  const rawText = latin1.decode(bytes);
  const crRefusal = bareCrRefusal(rawText);
  if (crRefusal) return { ok: false, reason_code: crRefusal };

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

  // rules 2-4 need the tree's declared boundaries; ambiguous_mime keeps priority
  const boundaries = boundariesOf(parser.root);
  const refusal = treeRefusal(parser.root)
    || headerGrammarRefusal(parser.root)
    || headerAllowlistRefusal(parser.root)
    || boundaryParamRefusal(parser.root)
    || multipartBoundaryRefusal(parser.root)
    || boundaryNestingRefusal(parser.root)
    || boundaryLineRefusal(rawText, boundaries)
    || headerSectionRefusal(rawText, boundaries);
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
