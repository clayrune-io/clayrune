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
// multipart and Thunderbird to the inner one. Siblings are not compared with each other
// (rule 2 covers a line that continues another declared boundary).
export function boundaryNestingRefusal(node, ancestors = []) {
  const own = boundaryOf(node);
  if (own && ancestors.some((a) => own.startsWith(a) || a.startsWith(own))) return REFUSED;
  const chain = own ? [...ancestors, own] : ancestors;
  for (const child of node.childNodes || []) {
    if (boundaryNestingRefusal(child, chain)) return REFUSED;
  }
  return null;
}
