// Ingress authentication: read Cloudflare's own Authentication-Results and decide
// whether the From domain is DMARC-authenticated (D6=A: DMARC pass with an
// aligned SPF OR DKIM result).
//
// Trust rule. Any sender can put an Authentication-Results header in the body of
// a message, including one that names mx.cloudflare.net. Cloudflare's own
// result is PREPENDED (RFC 8601), so the only header we believe is the one that
//   1. names authserv-id mx.cloudflare.net,
//   2. is the ONLY header that does (a second claimant means somebody added one),
//   3. is the first Authentication-Results header in the message.
// Anything else -> no trusted result -> the caller drops the message.
//
// UNVERIFIED against real Cloudflare output (spec: "must be verified with real
// provider fixtures before launch"); the shape parsed here is the RFC 8601 one.

import { allOf } from './headers.js';

export const TRUSTED_AUTHSERV_ID = 'mx.cloudflare.net';

const KNOWN_RESULTS = new Set([
  'pass', 'fail', 'none', 'neutral', 'softfail', 'temperror', 'permerror', 'policy',
]);

// Remove (comments) outside quotes. Unterminated comment/quote -> null.
function stripComments(s) {
  let out = '';
  let inQuote = false;
  let escaped = false;
  let depth = 0;
  for (const ch of s) {
    if (inQuote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inQuote = false;
      out += ch;
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
      out += ch;
    } else if (ch === '(') {
      depth = 1;
    } else {
      out += ch;
    }
  }
  return inQuote || depth > 0 ? null : out;
}

// Split on `sep` outside quotes.
function splitOutsideQuotes(s, isSep) {
  const parts = [];
  let cur = '';
  let inQuote = false;
  let escaped = false;
  for (const ch of s) {
    if (inQuote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inQuote = false;
      cur += ch;
    } else if (ch === '"') {
      inQuote = true;
      cur += ch;
    } else if (isSep(ch)) {
      parts.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  parts.push(cur);
  return parts;
}

function unquote(v) {
  if (v.length >= 2 && v.startsWith('"') && v.endsWith('"')) {
    return v.slice(1, -1).replace(/\\(.)/g, '$1');
  }
  return v;
}

/**
 * Authentication-Results value -> { authservId, results: [{ method, result, props }] }
 * or null if it is not parseable. Property names/values are as written; method,
 * result and property names are lowercased.
 */
export function parseAuthResults(value) {
  if (typeof value !== 'string') return null;
  const clean = stripComments(value);
  if (clean === null) return null;
  const segments = splitOutsideQuotes(clean, (c) => c === ';');
  const head = segments[0].trim().split(/\s+/);
  if (head.length === 0 || head.length > 2 || head[0] === '') return null;
  if (head.length === 2 && !/^\d+$/.test(head[1])) return null;
  const authservId = head[0].toLowerCase();
  const results = [];
  for (const seg of segments.slice(1)) {
    const text = seg.trim();
    if (text === '' || text.toLowerCase() === 'none') continue;
    const tokens = splitOutsideQuotes(text, (c) => /\s/.test(c)).filter((t) => t !== '');
    const m = /^([A-Za-z0-9_-]+)(?:\/\d+)?=([A-Za-z0-9_-]+)$/.exec(tokens[0]);
    if (!m) return null; // malformed resinfo: treat the whole header as untrusted
    const props = {};
    for (const tok of tokens.slice(1)) {
      const eq = tok.indexOf('=');
      if (eq < 1) continue;
      const k = tok.slice(0, eq).toLowerCase();
      if (!(k in props)) props[k] = unquote(tok.slice(eq + 1));
    }
    results.push({ method: m[1].toLowerCase(), result: m[2].toLowerCase(), props });
  }
  return { authservId, results };
}

// Relaxed alignment without a public-suffix list: equal, or one is a subdomain
// of the other. Stricter than RFC 7489 relaxed mode (sibling subdomains do NOT
// align) -- fail closed. The shorter name must contain a dot so a bare TLD can
// never "align".
function aligned(a, b) {
  if (!a || !b) return false;
  a = a.toLowerCase();
  b = b.toLowerCase();
  if (a === b) return true;
  const [short, long] = a.length < b.length ? [a, b] : [b, a];
  return short.includes('.') && long.endsWith(`.${short}`);
}

function domainOf(v) {
  if (typeof v !== 'string') return '';
  const at = v.lastIndexOf('@');
  return (at === -1 ? v : v.slice(at + 1)).toLowerCase();
}

function summarize(results) {
  if (results.length === 0) return 'missing';
  return results.map((r) => (KNOWN_RESULTS.has(r.result) ? r.result : 'other')).join('+');
}

/**
 * headers: normalized list (see headers.js). fromDomain: lowercased domain of the
 * single parsed From mailbox.
 * -> { ok: true, evidence } | { ok: false, reason_code, evidence }
 * evidence holds result words and which method aligned -- no addresses, no content.
 */
export function evaluateIngress(headers, fromDomain) {
  const ars = allOf(headers, 'authentication-results');
  const parsed = ars.map((h) => parseAuthResults(h.value));
  const trusted = parsed
    .map((p, i) => (p && p.authservId === TRUSTED_AUTHSERV_ID ? i : -1))
    .filter((i) => i !== -1);
  const evidence = { ingress: 'untrusted', spf: 'missing', dkim: 'missing', dmarc: 'missing', aligned: 'none' };
  if (trusted.length === 0) return { ok: false, reason_code: 'untrusted_ingress', evidence };
  if (trusted.length > 1) return { ok: false, reason_code: 'ingress_ambiguous', evidence };
  if (trusted[0] !== 0) return { ok: false, reason_code: 'untrusted_ingress', evidence };
  evidence.ingress = 'cloudflare';

  const results = parsed[0].results;
  const by = (m) => results.filter((r) => r.method === m);
  const spf = by('spf');
  const dkim = by('dkim');
  const dmarc = by('dmarc');
  evidence.spf = summarize(spf);
  evidence.dkim = summarize(dkim);
  evidence.dmarc = summarize(dmarc);

  if (dmarc.length === 0 || dmarc.some((r) => r.result !== 'pass')) {
    return { ok: false, reason_code: 'dmarc_not_pass', evidence };
  }
  // The result must be about THIS From domain, not another From header's.
  if (dmarc.some((r) => r.props['header.from'] !== undefined && r.props['header.from'].toLowerCase() !== fromDomain)) {
    return { ok: false, reason_code: 'auth_domain_mismatch', evidence };
  }
  const spfAligned = spf.some((r) => r.result === 'pass' && aligned(domainOf(r.props['smtp.mailfrom']), fromDomain));
  const dkimAligned = dkim.some((r) => r.result === 'pass' && aligned(domainOf(r.props['header.d']), fromDomain));
  evidence.aligned = spfAligned && dkimAligned ? 'spf+dkim' : spfAligned ? 'spf' : dkimAligned ? 'dkim' : 'none';
  if (!spfAligned && !dkimAligned) return { ok: false, reason_code: 'dmarc_unaligned', evidence };
  return { ok: true, evidence };
}
