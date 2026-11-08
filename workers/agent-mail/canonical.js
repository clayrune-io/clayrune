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

// ---- Content-Type grammar gate (fix round 10) ------------------------------------------
//
// Rules 2 and 3 take their boundaries from postal-mime's reading of the Content-Type.
// Thunderbird reads the boundary PARAMETER differently (nsMIMEHeaderParamImpl.cpp
// DoParameterInternal): it strips no comments, an unquoted value runs to `;`, RFC 2231
// `boundary*` forms outrank a plain `boundary=`, and a name followed by anything but `=`
// stops all parameter parsing. A message can then be split into parts under one boundary by
// postal-mime and another by Thunderbird, and the lines between the two readers' delimiters
// are visible text to one and a hidden attachment to the other. Rather than port a second
// parser, the headers that decide how a part is read must be in a plain grammar that every
// reader reads the same way (Dave, round 10). Rules 5-8 below check the raw header values
// of EVERY node of the tree (top level and nested, dropped subtrees included), found by
// trimmed header name like the duplicate check, duplicates included. Each returns
// 'malformed_mime' or null.

const SHAPING_HEADERS = ['content-type', 'content-disposition', 'content-transfer-encoding'];

// RFC 2045 token: any ASCII char but space, CTLs and tspecials ( ) < > @ , ; : \ " / [ ] ? =
const TOKEN = /^[!#$%&'*+\-.0-9A-Z^_`a-z{|}~]+$/;
// A quoted string with no inner `"` or `\` and no leading or trailing whitespace inside.
const QUOTED = /^"[^"\\\s](?:[^"\\]*[^"\\\s])?"$/;
// `boundary=` + token or quoted string, optionally preceded (a fold) and followed (before
// the `;`) by whitespace. No whitespace around the `=`: Thunderbird stops parameter
// parsing at a name not followed directly by it.
const PLAIN_BOUNDARY = /^\s*boundary=(\S.*?)\s*$/is;

function plainBoundarySegment(seg) {
  const m = PLAIN_BOUNDARY.exec(seg);
  return m !== null && (TOKEN.test(m[1]) || QUOTED.test(m[1]));
}

function* nodesOf(node) {
  yield node;
  for (const child of node.childNodes || []) yield* nodesOf(child);
}

// Values of one node's headers of the given (trimmed, lowercase) names, unfolded.
function headerValues(node, names) {
  return (node.headers || [])
    .filter((h) => names.includes(String(h.key || '').trim()))
    .map((h) => String(h.value || ''));
}

// Walk a header value tracking quoted strings. `honorEscape` says whether a backslash
// inside quotes escapes the next char (quoted-pair) or is literal; readers differ, so each
// check runs both and refuses if either refuses. Returns the `;`-separated segments
// (outside quotes), whether `(` occurred outside quotes, and whether a quote was left open.
function scanValue(value, honorEscape) {
  const segs = [];
  let cur = '';
  let inQuote = false;
  let paren = false;
  for (let i = 0; i < value.length; i += 1) {
    const c = value[i];
    if (inQuote) {
      cur += c;
      if (c === '\\' && honorEscape && i + 1 < value.length) { i += 1; cur += value[i]; } else if (c === '"') inQuote = false;
    } else if (c === '"') { inQuote = true; cur += c; } else if (c === ';') { segs.push(cur); cur = ''; } else {
      if (c === '(') paren = true;
      cur += c;
    }
  }
  segs.push(cur);
  return { segs, paren, open: inQuote };
}

// Rule 5: Content-Type, Content-Disposition or Content-Transfer-Encoding containing `(`
// outside a quoted string. postal-mime drops a comment; Thunderbird keeps it as part of the
// token (`boundary= (c)B`, `Content-Type: (c)text/plain`). A quote left open when escapes
// are ignored (an odd number of `"`) is refused as well: which `(` sits inside it is then a
// matter of opinion.
export function headerCommentRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const value of headerValues(node, SHAPING_HEADERS)) {
      const literal = scanValue(value, false);
      if (literal.paren || literal.open || scanValue(value, true).paren) return REFUSED;
    }
  }
  return null;
}

// Rule 6: no `boundary*` parameter (RFC 2231 `boundary*=`, `boundary*0=`; Thunderbird
// prefers them to a plain `boundary=`, postal-mime does not) and at most one `boundary`
// parameter. Rule 7: that parameter is exactly `boundary=` + a plain token or a quoted
// string (TOKEN / QUOTED above). Content-Type only. A parameter is a `boundary` one when
// its name (text before the first `=`, trimmed) is `boundary`.
export function boundaryParamRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const value of headerValues(node, ['content-type'])) {
      for (const honor of [false, true]) {
        const params = scanValue(value, honor).segs.slice(1).filter((s) => s.trim() !== '');
        let count = 0;
        for (const seg of params) {
          const name = seg.split('=')[0].trim().toLowerCase();
          if (/^boundary\s*\*/.test(name)) return REFUSED;
          if (name !== 'boundary') continue;
          count += 1;
          if (!plainBoundarySegment(seg)) return REFUSED;
        }
        if (count > 1) return REFUSED;
      }
    }
  }
  return null;
}

// Rule 8: a multipart/* Content-Type must carry a usable boundary parameter. postal-mime
// reads a multipart with none as text/plain; Thunderbird reads a multipart with no
// delimiters. The type is the first segment, as the header declares it.
export function multipartBoundaryRefusal(root) {
  for (const node of nodesOf(root)) {
    for (const value of headerValues(node, ['content-type'])) {
      const { segs } = scanValue(value, false);
      if (/^multipart/i.test(segs[0].trim()) && !segs.slice(1).some(plainBoundarySegment)) return REFUSED;
    }
  }
  return null;
}
