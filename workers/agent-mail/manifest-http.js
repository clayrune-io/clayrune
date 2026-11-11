// Publish side of the root-signed mail-service manifest (MC-1166 unit 7e-2p).
//
//   GET /v1/mail/manifest   (no query string)
//
// The client (mc/agent_mail_manifest_fetch.py) fetches this document and runs the pinned-root verifier
// itself. This Worker only SERVES a document that was signed OFFLINE (tools/agent-mail-manifest-sign.py):
// it never holds the root private key and never signs. The document is the operator var
// MAIL_SERVICE_MANIFEST, a JSON string {"manifest":[11 items],"signature":"<base64url>"}; it is public by
// design (anyone may read it, only the root can make a valid one), so it is a plain var, not a secret.
//
// Served byte-for-byte as configured, after a shape check only. The check is NOT a signature check (this
// Worker has no root public key); it exists so a half-pasted var answers 503 with a closed token instead
// of a document the client will then have to refuse. A missing or malformed var is always 503, never a
// partial or default document. Non-GET, or any query string, is not routed here (worker.js falls through
// to its existing 404).
export const MANIFEST_PATH = '/v1/mail/manifest';
// Same ceiling as the client's RESPONSE_LIMIT (32 KiB); the signed array itself is capped at 16 KiB.
export const MANIFEST_LIMIT = 32 * 1024;
const B64URL = /^[A-Za-z0-9_-]+$/;
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });

// `url.search` is '' for a bare trailing '?', so the raw URL is checked too: no query form is served.
export const isManifestRequest = (request, url) =>
  request.method === 'GET' && url.pathname === MANIFEST_PATH && !url.search && !request.url.includes('?');

// 64 raw bytes -> 86 unpadded base64url characters, last character carries only 4 bits of 6.
const signatureShaped = (s) => typeof s === 'string' && s.length === 86 && B64URL.test(s)
  && 'AQgw'.includes(s[85]);

function wellFormed(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  if (keys.length !== 2 || !keys.includes('manifest') || !keys.includes('signature')) return false;
  const m = value.manifest;
  return Array.isArray(m) && m.length === 11
    && m.slice(0, 10).every((s) => typeof s === 'string' && s.length > 0)
    && Array.isArray(m[10]) && m[10].every((s) => typeof s === 'string')
    && signatureShaped(value.signature);
}

export function manifestHttp({ env }) {
  const raw = env.MAIL_SERVICE_MANIFEST;
  if (typeof raw !== 'string' || raw === '') return reply(503, { code: 'manifest_not_configured' });
  if (new TextEncoder().encode(raw).length > MANIFEST_LIMIT) return reply(503, { code: 'manifest_malformed' });
  let value;
  try { value = JSON.parse(raw); } catch { return reply(503, { code: 'manifest_malformed' }); }
  if (!wellFormed(value)) return reply(503, { code: 'manifest_malformed' });
  return new Response(raw, { status: 200, headers: { 'content-type': 'application/json',
    'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
}
