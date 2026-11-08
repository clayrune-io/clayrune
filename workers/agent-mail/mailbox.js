// Strict single-mailbox parsing for the From header.
//
// Identity is decided on the parsed addr-spec, never on a substring of the
// header: `"ron@gmail.com" <evil@x.example>` is evil@x.example, and
// `ron@gmail.com.evil.example` is a different domain. Anything that is not
// exactly ONE plain mailbox (a list, a group, a quoted local part, an
// internationalised domain, control characters) returns null and the caller
// drops the message.
//
// Case rule (RFC 5321 2.3.11 / RFC 5322 addr-spec): the domain is
// case-insensitive, the local part is compared exactly as written.

const ATEXT = "A-Za-z0-9!#$%&'*+/=?^_`{|}~-";
const LOCAL_RE = new RegExp(`^[${ATEXT}]+(?:\\.[${ATEXT}]+)*$`);
const LABEL = '[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?';
const DOMAIN_RE = new RegExp(`^${LABEL}(?:\\.${LABEL})+$`);
const QUOTED = '\u0001'; // stands in for every character inside a quoted string

/** Bare addr-spec -> { local, domain, address } with the domain lowercased, or null. */
export function parseAddrSpec(s) {
  if (typeof s !== 'string' || s.length === 0 || s.length > 254) return null;
  const at = s.indexOf('@');
  if (at < 1 || at !== s.lastIndexOf('@')) return null;
  const local = s.slice(0, at);
  const domain = s.slice(at + 1);
  if (local.length > 64 || !LOCAL_RE.test(local) || !DOMAIN_RE.test(domain)) return null;
  const d = domain.toLowerCase();
  return { local, domain: d, address: `${local}@${d}` };
}

// Replace comments with a space and quoted-string contents with a marker that
// can never be part of an address. Returns null on an unterminated quote/comment
// or any control character.
function skeleton(value) {
  let out = '';
  let inQuote = false;
  let escaped = false;
  let depth = 0;
  for (const ch of value) {
    const code = ch.codePointAt(0);
    if (code < 0x20 || code === 0x7f) {
      if (ch !== '\t') return null;
    }
    if (inQuote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inQuote = false;
      out += QUOTED;
    } else if (depth > 0) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '(') depth += 1;
      else if (ch === ')') {
        depth -= 1;
        if (depth === 0) out += ' ';
      }
    } else if (ch === '"') {
      inQuote = true;
      out += QUOTED;
    } else if (ch === '(') {
      depth = 1;
    } else {
      out += ch;
    }
  }
  return inQuote || depth > 0 ? null : out;
}

/** From header value -> { local, domain, address } or null (not exactly one plain mailbox). */
export function parseMailbox(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 998) return null;
  const sk = skeleton(value);
  if (sk === null) return null;
  if (/[,;:]/.test(sk)) return null; // address list or group
  const lt = sk.indexOf('<');
  if (lt === -1) {
    if (sk.includes('>')) return null;
    return parseAddrSpec(sk.trim());
  }
  if (lt !== sk.lastIndexOf('<')) return null;
  const gt = sk.indexOf('>');
  if (gt < lt || sk.slice(gt + 1).trim() !== '') return null;
  if (sk.slice(0, lt).includes('@') || sk.slice(0, lt).includes('>')) return null;
  return parseAddrSpec(sk.slice(lt + 1, gt));
}

export function sameMailbox(a, b) {
  return !!a && !!b && a.local === b.local && a.domain === b.domain;
}
