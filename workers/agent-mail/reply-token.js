// Reply token carried in the References header (Dave 2026-11-14, journal a841feef).
//
// Cloudflare Email Sending generates Message-ID and rejects a caller-set one
// (E_HEADER_NOT_ALLOWED) but allows References and In-Reply-To
// (developers.cloudflare.com/email-service/reference/headers/). So outbound mail
// carries `References: <TOKEN.NONCE@domain>`; a mail client's reply copies that
// id into its own References, which is where ingress reads the token back.
// TOKEN is the same 32-hex secret the +address used to carry; NONCE is fresh and
// opaque. The token is a lookup key only: authority still comes from the verified
// owner From + Cloudflare DMARC (auth.js), exactly as on the +token path.

const TOKEN = /^[a-f0-9]{32}$/;
const ID = /<([^<>\s@]+)@([^<>\s@]+)>/g;
const ID_LOCAL = /^([a-f0-9]{32})\./;
/** Bound on distinct token candidates read from one message. */
export const MAX_CANDIDATES = 8;

/** 128 bits, lowercase hex. Never derived from the token or any message field. */
export function newNonce() {
  return Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),
    (b) => b.toString(16).padStart(2, '0')).join('');
}

/** The References value for an outbound message: `<token.nonce@domain>`. */
export function referencesValue(token, domain, nonce = newNonce) {
  const n = typeof nonce === 'function' ? nonce() : nonce;
  if (!TOKEN.test(token) || typeof n !== 'string' || !/^[A-Za-z0-9]{16,64}$/.test(n)
      || typeof domain !== 'string' || !/^[a-z0-9.-]+$/.test(domain)) throw new Error('invalid_service_address');
  return `<${token}.${n}@${domain}>`;
}

/** Distinct tokens named by In-Reply-To / References ids on `domain`, nearest
 * ancestor first: In-Reply-To ids, then References ids last to first (a reply
 * appends the id it answers to the end of References). `headers` is the ordered
 * list from headers.js; ids on any other domain are ignored. Never throws. */
export function headerTokens(headers, domain) {
  if (!Array.isArray(headers) || typeof domain !== 'string') return [];
  const want = domain.toLowerCase();
  const ids = (key) => {
    const found = [];
    for (const h of headers) {
      if (h.key !== key || typeof h.value !== 'string') continue;
      for (const m of h.value.matchAll(ID)) {
        const t = ID_LOCAL.exec(m[1].toLowerCase());
        if (t && m[2].toLowerCase() === want) found.push(t[1]);
      }
    }
    return found;
  };
  const ordered = [...ids('in-reply-to'), ...ids('references').reverse()];
  return [...new Set(ordered)].slice(0, MAX_CANDIDATES);
}
