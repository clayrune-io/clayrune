// Pure edge decision for the machine-delivery branch (MC-1166 7d-2c, contract section 3).
// No fetching, no KV, no cookies: trust (issuer + keys) and the denylist reader are injected by
// the edge glue. This file is vendored byte-for-byte into the sibling edge Worker (decision D3);
// its import closure is listed in edge-vendor.manifest.json and checked by edge-vendor.js.
import { verifyDeliveryCapability, ROUTE_PATH } from './delivery-capability.js';

export const MACHINE_HEADER = 'x-clayrune-mail-delivery';
export const MAX_BODY_BYTES = 131072; // mc/agent_mail_envelope.py MAX_REQUEST_BYTES
export const MAX_RESPONSE_BYTES = 4096;
const HOST = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const LENGTH = /^(?:0|[1-9][0-9]{0,6})$/;
// Closed vocabulary: a verifier message outside this set is reported as bad_capability.
const CAPABILITY_CODES = new Set(['capability_trust_required', 'bad_capability', 'bad_capability_header',
  'unknown_capability_key', 'bad_capability_key', 'bad_capability_signature', 'bad_capability_claims',
  'capability_not_yet_valid', 'capability_expired', 'capability_request_mismatch']);
const deny = (status, code) => ({ allow: false, status, code });

// The machine branch is selected by header presence alone; once it is, the cookie flow is never read.
export const isMachineRequest = (headers) => headers.has(MACHINE_HEADER);

const hasUpgrade = (headers) => {
  if (headers.has('upgrade')) return true;
  if ((headers.get('connection') ?? '').toLowerCase().split(',').some((t) => t.trim() === 'upgrade')) return true;
  for (const name of headers.keys()) if (name.toLowerCase().startsWith('sec-websocket-')) return true;
  return false;
};

// `target` is the RAW request target exactly as received (never a parsed/normalised URL): the
// exact-equality check below is what rejects every encoded, dotted, trailing-slash or case alias.
// `headers` is a Headers-like (get/has/keys). `isDenied(sub)` is the KV denylist read (`u:{sub}`).
// Returns { allow: true, claims, headers } (headers already stripped for the proxy) or
// { allow: false, status, code }: a bare 400/401/403 (503 only when edge trust/denylist is unavailable).
export async function decideMachineRequest({ method, host, target, headers, trust, isDenied, clock = Date.now }) {
  if (method !== 'POST') return deny(400, 'bad_method');
  if (target !== ROUTE_PATH) return deny(400, 'bad_target');
  if (typeof host !== 'string' || !HOST.test(host)) return deny(400, 'bad_host');
  const length = headers.get('content-length');
  if (length === null || !LENGTH.test(length)) return deny(400, 'bad_content_length');
  if (Number(length) > MAX_BODY_BYTES) return deny(400, 'body_limit');
  if (headers.has('transfer-encoding')) return deny(400, 'bad_transfer_encoding');
  if (headers.has('expect')) return deny(400, 'bad_expect');
  if (headers.get('content-type') !== 'application/json') return deny(400, 'bad_content_type');
  if (hasUpgrade(headers)) return deny(400, 'upgrade_refused');
  // One credential: a cookie beside a capability is a confused caller, never a fallback.
  if (headers.has('cookie')) return deny(400, 'two_credentials');
  const token = headers.get(MACHINE_HEADER);
  const wire = { install_id: headers.get('x-mail-install-id'), generation: headers.get('x-mail-generation'),
    nonce: headers.get('x-mail-nonce'), digest: headers.get('x-mail-digest'),
    key_id: headers.get('x-mail-key-id'), direction: headers.get('x-mail-direction') };
  let claims;
  try {
    claims = await verifyDeliveryCapability(token, trust, { host, method, path: target, headers: wire }, clock);
  } catch (e) {
    const code = CAPABILITY_CODES.has(e?.message) ? e.message : 'bad_capability';
    if (code === 'capability_trust_required') return deny(503, code);
    return deny(code === 'capability_request_mismatch' ? 403 : 401, code);
  }
  if (typeof isDenied !== 'function') return deny(503, 'denylist_unavailable');
  let denied;
  try { denied = await isDenied(claims.sub); } catch { return deny(503, 'denylist_unavailable'); }
  if (denied !== false) return deny(403, 'user_denied'); // anything but an explicit false fails closed
  return { allow: true, claims, headers: stripForProxy(headers) };
}

// Credentials never reach the origin and no identity header is added: Flask's own mail signature,
// not the edge, is what authorises the request body.
export function stripForProxy(headers) {
  const out = new Headers();
  for (const [name, value] of headers) {
    const n = name.toLowerCase();
    if (n === MACHINE_HEADER || n === 'cookie' || n === 'authorization'
        || n.startsWith('x-clayrune-') || n.startsWith('cf-access-')) continue;
    out.append(name, value);
  }
  return out;
}

// A 3xx from the origin becomes 502 (a login page must never look like a receipt); Set-Cookie is
// dropped; the body is bounded. Anything unreadable is a 502, never a pass-through.
export async function filterOriginResponse(response) {
  const bare = (status, code) => Response.json({ code }, { status, headers: { 'cache-control': 'no-store' } });
  if (response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)) {
    return bare(502, 'origin_redirect');
  }
  if (!Number.isInteger(response.status) || response.status < 200 || response.status > 599) {
    return bare(502, 'origin_bad_status');
  }
  let raw;
  try {
    const reader = response.body ? response.body.getReader() : null;
    const chunks = []; let size = 0;
    if (reader) {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > MAX_RESPONSE_BYTES) { await reader.cancel(); return bare(502, 'origin_response_too_large'); }
          chunks.push(value);
        }
      } finally { reader.releaseLock(); }
    }
    raw = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.byteLength; }
  } catch { return bare(502, 'origin_unreadable'); }
  const headers = new Headers({ 'cache-control': 'no-store' });
  const type = response.headers.get('content-type');
  if (type) headers.set('content-type', type);
  return new Response(raw.length ? raw : null, { status: response.status, headers });
}
