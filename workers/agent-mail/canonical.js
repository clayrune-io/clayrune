// Canonical-form gate for a raw MIME message (MC-1166 unit 3, fix round 9).
//
// postal-mime and Thunderbird (libmime) split lines and find boundaries differently:
// Thunderbird ends a line at a lone CR, strips any trailing whitespace (FF and VT
// included) before matching a delimiter, and asks the INNERMOST multipart first when two
// nested multiparts share a boundary; postal-mime splits on LF only, strips every
// trailing CR, accepts only space/tab after a delimiter and asks the OUTERMOST first.
// Each difference lets a part be a body part to one reader and a hidden resource, or no
// part at all, to the other. Rather than chase each difference, strip() refuses a message
// that is not in canonical form (malformed_mime), so the two readers have nothing to
// disagree about. Real mail from Gmail, Apple Mail, Outlook and Thunderbird is canonical
// CRLF with random boundaries; none of the checks below refuses it.
//
// Pure functions over the latin1-decoded raw bytes (one char per byte) and the postal-mime
// MimeNode tree. Each returns 'malformed_mime' or null.

const REFUSED = 'malformed_mime';

// The boundary a multipart node declared, as postal-mime registers it (mime-node.ts:
// trailing space trimmed, ASCII only), or '' when it has none usable.
export function boundaryOf(node) {
  if (!node.contentType.multipart) return '';
  const b = String((node.contentType.parsed.params || {}).boundary || '').trimEnd();
  return b && !/[^\x00-\x7f]/.test(b) ? b : '';
}

// Rule 1: a CR not immediately followed by LF, anywhere -- bodies included. RFC 5322 2.3
// allows CR only as half of CRLF; base64 and quoted-printable never produce one. A lone CR
// is a line end to Thunderbird and not to postal-mime, and CR CR LF is a blank line to
// both, so any CR that is not the CR of a CRLF is refused.
export function bareCrRefusal(text) {
  return /\r(?!\n)/.test(text) ? REFUSED : null;
}

// Rule 2: a line that starts `--` + a declared boundary must be exactly that boundary,
// optionally followed by `--`, then nothing but space/tab. Anything else after it (FF, VT,
// NBSP, more dashes, more boundary text) is a delimiter to one reader and body text to the
// other. Checked over every line of the message, bodies included. Lines are split on LF;
// after rule 1 the only CR left is the one of a CRLF, which is dropped.
export function boundaryLineRefusal(text, boundaries) {
  if (boundaries.size === 0) return null;
  for (const raw of text.split('\n')) {
    if (!raw.startsWith('--')) continue;
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
    for (const b of boundaries) {
      if (!line.startsWith(b, 2)) continue;
      let rest = line.slice(2 + b.length);
      if (rest.startsWith('--')) rest = rest.slice(2);
      if (!/^[ \t]*$/.test(rest)) return REFUSED;
    }
  }
  return null;
}

// Rule 3: a nested multipart's boundary must not equal, be a prefix of, or have as a
// prefix, the boundary of any of its ancestors. RFC 2046 5.1.1 forbids a delimiter inside
// the parts it encloses; with a shared boundary postal-mime gives a line to the outer
// multipart and Thunderbird to the inner one. That EQUAL half is a real split. The prefix
// halves are not: Thunderbird (mimemult.cpp MimeMultipart_check_boundary) matches a
// delimiter at exactly the declared boundary's length (`blen == length - 2`) and postal-mime
// lets only space/tab follow it, so `--BND` is not a delimiter for `B` in either reader
// (Fenn, round 10). The prefix check is defence in depth: it costs nothing on real mail
// (Gmail, Thunderbird, Apple Mail and Outlook nest unrelated random boundaries) and covers
// a reader that matches by prefix. Siblings are not compared with each other (rule 2
// covers a line that continues another declared boundary).
export function boundaryNestingRefusal(node, ancestors = []) {
  const own = boundaryOf(node);
  if (own && ancestors.some((a) => own.startsWith(a) || a.startsWith(own))) return REFUSED;
  const chain = own ? [...ancestors, own] : ancestors;
  for (const child of node.childNodes || []) {
    if (boundaryNestingRefusal(child, chain)) return REFUSED;
  }
  return null;
}

// ---- Header grammar gate (fix rounds 10-11) -----------------------------------------------
//
// Rules 2 and 3 take their boundaries from postal-mime's reading of the Content-Type.
// Thunderbird reads the boundary PARAMETER differently (nsMIMEHeaderParamImpl.cpp
// DoParameterInternal): it strips no comments, an unquoted value runs to `;`, RFC 2231
// `boundary*` forms outrank a plain `boundary=`, and a name followed by anything but `=`
// stops all parameter parsing; it also keeps a quoted type token quoted where postal-mime
// unquotes it. A message can then be split into parts under one boundary by postal-mime and
// another by Thunderbird, and the lines between the two readers' delimiters are visible text
// to one and a hidden attachment to the other.
//
// Round 10 walked the value with a home-grown scanner; Fenn's round 11 showed that scanner is
// a third reader that agrees with neither (backslash outside quotes, an unreadable earlier
// parameter, a quoted type). Eleven rounds of per-shape patches did not converge, so the
// walker is gone (Dave, round 11): the WHOLE value of Content-Type, Content-Disposition and
// Content-Transfer-Encoding must match a strict grammar that only admits shapes both readers
// parse identically, and anything else is refused. There is no scanner left to disagree with.
//
//   Content-Type        token "/" token *( OWS ";" OWS token "=" ( token / quoted ) ) OWS [ ";" OWS ]
//   Content-Disposition token           *( OWS ";" OWS token "=" ( token / quoted ) ) OWS [ ";" OWS ]
//   Content-Transfer-Encoding  token   (OWS allowed after it)
//   token  = RFC 2045 token (ASCII, no space, CTL or tspecials), so no `\`, `(`, `"`, `,`, `=`
//   quoted = DQUOTE *( any char but DQUOTE, "\", CR, LF ) DQUOTE   (no escapes, no comments)
//   OWS    = *( [CRLF] SP / HTAB )   (a fold; postal-mime has already unfolded the value)
//
// That refuses a comment, a backslash, a space inside a name, junk after a quoted value, a
// quoted type or subtype, an empty parameter, a stray `,`. Real Gmail, Thunderbird, Apple Mail
// (`filename*=utf-8''..`, `x-unix-mode=0644`) and Outlook (`creation-date="..."`, `size=68`)
// headers all match. Rules 6-8 then read the parsed parameters. All rules check the raw header
// values of EVERY node of the tree (top level and nested, dropped subtrees included), found
// by trimmed header name like the duplicate check, duplicates included. Each returns
// 'malformed_mime' or null.

const SHAPING_HEADERS = ['content-type', 'content-disposition', 'content-transfer-encoding'];

// RFC 2045 token: any ASCII char but space, CTLs and tspecials ( ) < > @ , ; : \ " / [ ] ? =
const TOKEN_CLASS = "[!#$%&'*+\\-.0-9A-Z^_`a-z{|}~]+";
const TOKEN = new RegExp('^' + TOKEN_CLASS + '$');
// A quoted string with no inner `"` or `\` and no leading or trailing whitespace inside.
// (Rule 7 uses this stricter form for a boundary; the grammar below allows any quoted value.)
const QUOTED = /^"[^"\\\s](?:[^"\\]*[^"\\\s])?"$/;

const OWS = '(?:(?:\\r?\\n)?[ \\t])*';
const GRAMMAR_QUOTED = '"[^"\\\\\\r\\n]*"';
// ` ; name=value`: sticky, so the walk is anchored where the last one ended.
const PARAM_STEP = new RegExp(OWS + ';' + OWS + '(' + TOKEN_CLASS + ')=(' + TOKEN_CLASS + '|' + GRAMMAR_QUOTED + ')', 'y');
const PARAM_TAIL = new RegExp(OWS + '(?:;' + OWS + ')?$', 'y');
const TYPE_HEAD = new RegExp('^(' + TOKEN_CLASS + ')/(' + TOKEN_CLASS + ')');
const TOKEN_HEAD = new RegExp('^(' + TOKEN_CLASS + ')');
const CTE_WHOLE = new RegExp('^' + TOKEN_CLASS + OWS + '$');

// Parse `head *( ";" param )` from the start of `value`. Returns { type, params } or null when
// the value is not wholly in the grammar. Parameter names are lowercased; values are raw
// (a quoted value keeps its quotes).
function parseParameterised(value, headRe) {
  const head = headRe.exec(value);
  if (head === null) return null;
  const params = [];
  let at = head[0].length;
  for (;;) {
    PARAM_STEP.lastIndex = at;
    const m = PARAM_STEP.exec(value);
    if (m === null) break;
    params.push({ name: m[1].toLowerCase(), value: m[2] });
    at = PARAM_STEP.lastIndex;
  }
  PARAM_TAIL.lastIndex = at;
  if (!PARAM_TAIL.test(value)) return null;
  return { type: head.slice(1).join('/'), params };
}

// One header value against its grammar: { type, params } (Content-Transfer-Encoding gets
// no parameters) or null.
function parseShaping(name, value) {
  if (name === 'content-type') return parseParameterised(value, TYPE_HEAD);
  if (name === 'content-disposition') return parseParameterised(value, TOKEN_HEAD);
  return CTE_WHOLE.test(value) ? { type: value.trim(), params: [] } : null;
}

function* nodesOf(node) {
  yield node;
  for (const child of node.childNodes || []) yield* nodesOf(child);
}

// [{ name, parsed }] for one node's headers of the given (trimmed, lowercase) names, unfolded;
// `parsed` is null when the value is outside the grammar.
function parsedHeaders(node, names) {
  return (node.headers || [])
    .map((h) => ({ name: String(h.key || '').trim(), value: String(h.value || '') }))
    .filter((h) => names.includes(h.name))
    .map((h) => ({ name: h.name, parsed: parseShaping(h.name, h.value) }));
}

// Rule 5: Content-Type, Content-Disposition and Content-Transfer-Encoding must match the
// grammar above in full. Replaces the round-10 comment/open-quote scan.
export function headerGrammarRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of parsedHeaders(node, SHAPING_HEADERS)) {
      if (h.parsed === null) return REFUSED;
    }
  }
  return null;
}

// ---- Header allowlists (fix round 12) ---------------------------------------------------------
//
// Rule 5 pins the SHAPE of a Content-* value; it does not pin what the characters or the words
// mean to each reader. Fenn's round 12 found three more splits inside the grammar, and Dave
// (round 12) closed them as classes rather than instances. Each rule below is an allowlist:
//
//   9  a C0 control (0x00-0x1F except HTAB) or DEL (0x7F) anywhere in the unfolded name or value
//      of a Content-* header, in any part. Thunderbird copies a value through a C string, so a
//      NUL ends it early (an unterminated quote then ends parameter parsing, or cuts a boundary
//      short); postal-mime keeps the NUL. Real clients never emit one. postal-mime has already
//      removed the CRLF of a fold, so CR and LF cannot appear in a value that got this far; they
//      are in the class anyway.
//  10  Content-Transfer-Encoding is one of 7bit, 8bit, binary, quoted-printable, base64
//      (case-insensitive); absent means 7bit. Thunderbird also decodes x-uuencode, uuencode,
//      x-uue, uue and x-yencode, discarding every line before `begin `, so a text part with
//      one of those is blank to Ron and plain text to postal-mime.
//  11  on Content-Type and Content-Disposition, a parameter whose name contains `*` is refused
//      unless the part before the first `*` is `name` or `filename` (Apple `name*=`, Gmail
//      `filename*0*=`). Thunderbird prefers an RFC 2231 form to a plain parameter of the same
//      name (`start*`, `charset*`, `boundary*`, `type*`); postal-mime keeps the plain one.
//  12  no X-Sun-Charset header in any part. Thunderbird uses it as the charset when the
//      Content-Type has none; postal-mime ignores it.

const ALLOWED_CTE = new Set(['7bit', '8bit', 'binary', 'quoted-printable', 'base64']);
const STARRED_OK = new Set(['name', 'filename']);
const C0_OR_DEL = /[\x00-\x08\x0a-\x1f\x7f]/;

function controlRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of node.headers || []) {
      const key = String(h.key || '').trim();
      if (!key.startsWith('content-')) continue;
      if (C0_OR_DEL.test(key) || C0_OR_DEL.test(String(h.value || ''))) return REFUSED;
    }
  }
  return null;
}

function transferEncodingRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of parsedHeaders(node, ['content-transfer-encoding'])) {
      if (h.parsed === null || !ALLOWED_CTE.has(h.parsed.type.toLowerCase())) return REFUSED;
    }
  }
  return null;
}

function starredParamRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of parsedHeaders(node, ['content-type', 'content-disposition'])) {
      if (h.parsed === null) continue;
      for (const p of h.parsed.params) {
        const star = p.name.indexOf('*');
        if (star !== -1 && !STARRED_OK.has(p.name.slice(0, star))) return REFUSED;
      }
    }
  }
  return null;
}

function sunCharsetRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of node.headers || []) {
      if (String(h.key || '').trim() === 'x-sun-charset') return REFUSED;
    }
  }
  return null;
}

// Rules 9-12 in one call; each is independent and returns 'malformed_mime' or null.
export function headerAllowlistRefusal(root) {
  return controlRefusal(root) || transferEncodingRefusal(root)
    || starredParamRefusal(root) || sunCharsetRefusal(root);
}

// Rule 6: no `boundary*` parameter (RFC 2231 `boundary*=`, `boundary*0=`; Thunderbird
// prefers them to a plain `boundary=`, postal-mime does not) and at most one `boundary`
// parameter. Rule 7: that parameter's value is a plain token or a quoted string with no
// whitespace just inside the quotes (TOKEN / QUOTED above; the grammar already forbids
// whitespace around the `=`). Content-Type only. A header outside the grammar is rule 5's.
export function boundaryParamRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of parsedHeaders(node, ['content-type'])) {
      if (h.parsed === null) continue;
      let count = 0;
      for (const p of h.parsed.params) {
        if (/^boundary\*/.test(p.name)) return REFUSED;
        if (p.name !== 'boundary') continue;
        count += 1;
        if (!TOKEN.test(p.value) && !QUOTED.test(p.value)) return REFUSED;
      }
      if (count > 1) return REFUSED;
    }
  }
  return null;
}

// Rule 8: a multipart/* Content-Type must carry a usable boundary parameter. postal-mime
// reads a multipart with none as text/plain; Thunderbird reads a multipart with no
// delimiters.
export function multipartBoundaryRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const h of parsedHeaders(node, ['content-type'])) {
      if (h.parsed === null) continue;
      if (/^multipart/i.test(h.parsed.type) && !h.parsed.params.some((p) => p.name === 'boundary')) return REFUSED;
    }
  }
  return null;
}
