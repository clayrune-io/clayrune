// Pure verifier for a CP-issued delivery capability (MC-1166 7d-2b, contract 1.2/1.3).
// No fetching: trust (issuer + public keys) is reviewed service configuration, never read
// from the token. The edge branch (7d-2c) wraps this with the KV denylist and the proxy.
import { bytes, unbase64url } from './signing.js';
import { origin, ID, HEX } from './enrollment-transcript.js';
export const CAPABILITY_TYPE = 'clayrune-mail-delivery+jwt';
export const CAPABILITY_AUDIENCE = 'clayrune-mail-delivery';
export const ROUTE_PATH = '/api/agent-mail/ingress';
export const MAX_LIFETIME_S = 60;
export const LEASE_CAP_S = 300;
const KID = /^mail-delivery-[A-Za-z0-9_-]{1,100}$/;
const GEN = /^[1-9][0-9]{0,14}$/;
const HEX32 = /^[a-f0-9]{32}$/;
const MODE_DIRECTION = { mail: 'service-to-install', proof: 'service-to-install-proof' };
const CLAIMS = ['iss', 'aud', 'token_use', 'sub', 'jti', 'iat', 'nbf', 'exp', 'mode', 'install_id', 'generation',
  'device_id', 'route_origin', 'route_path', 'method', 'req_digest', 'mail_nonce', 'mail_key_id',
  'checked_at_ms', 'record_version', 'binding_jti'].sort().join(',');
const fail = (code) => { throw new Error(code); };
const decode = (s) => JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(unbase64url(s)));

// `request` is what the edge saw: { host, method, path, headers: { install_id, generation, nonce,
// digest, key_id, direction } } with `path` the RAW request target. Throws a closed
// `bad_capability_*` / `capability_*` code; returns the verified claims.
export async function verifyDeliveryCapability(token, trust, request, clock = Date.now) {
  if (!trust || !origin(trust.issuer) || trust.jwksUrl !== `${trust.issuer}/v1/mail-delivery/jwks`
      || !Array.isArray(trust.jwks?.keys)) fail('capability_trust_required');
  if (typeof token !== 'string' || token.length > 4096) fail('bad_capability');
  const parts = token.split('.');
  if (parts.length !== 3) fail('bad_capability');
  let h; let c;
  try { h = decode(parts[0]); c = decode(parts[1]); } catch { fail('bad_capability'); }
  if (!h || typeof h !== 'object' || Object.keys(h).sort().join(',') !== 'alg,kid,typ' || h.alg !== 'ES256'
      || h.typ !== CAPABILITY_TYPE || typeof h.kid !== 'string' || !KID.test(h.kid)) fail('bad_capability_header');
  const matches = trust.jwks.keys.filter((k) => k.kid === h.kid);
  if (matches.length !== 1) fail('unknown_capability_key');
  const jwk = matches[0];
  let key; let sig;
  try {
    if (jwk.kty !== 'EC' || jwk.crv !== 'P-256' || jwk.alg !== 'ES256' || jwk.use !== 'sig' || jwk.d !== undefined
        || unbase64url(jwk.x).length !== 32 || unbase64url(jwk.y).length !== 32) fail('bad_capability_key');
    key = await crypto.subtle.importKey('jwk', { kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y },
      { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
    sig = unbase64url(parts[2]);
  } catch (e) { fail(e.message === 'bad_capability_key' ? 'bad_capability_key' : 'bad_capability'); }
  if (sig.length !== 64 || !await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, sig,
    bytes(`${parts[0]}.${parts[1]}`))) fail('bad_capability_signature');
  if (!c || typeof c !== 'object' || Array.isArray(c) || Object.keys(c).sort().join(',') !== CLAIMS) {
    fail('bad_capability_claims');
  }
  const now = clock();
  const times = ['iat', 'nbf', 'exp', 'checked_at_ms'];
  if (c.iss !== trust.issuer || c.aud !== CAPABILITY_AUDIENCE || c.token_use !== CAPABILITY_AUDIENCE
      || !ID.test(c.sub) || !HEX32.test(c.jti) || !times.every((k) => Number.isSafeInteger(c[k]))
      || c.iat !== c.nbf || c.exp <= c.iat || c.exp - c.iat > MAX_LIFETIME_S
      || c.exp > Math.floor(c.checked_at_ms / 1000) + LEASE_CAP_S
      || c.checked_at_ms > c.iat * 1000 + 999 || c.checked_at_ms < c.iat * 1000 - 999
      || !Object.hasOwn(MODE_DIRECTION, c.mode) || !ID.test(c.install_id) || !GEN.test(c.generation)
      || !ID.test(c.device_id) || !origin(c.route_origin) || c.route_path !== ROUTE_PATH || c.method !== 'POST'
      || !HEX.test(c.req_digest) || !HEX32.test(c.mail_nonce) || !ID.test(c.mail_key_id)
      || !HEX.test(c.record_version) || !HEX32.test(c.binding_jti)) fail('bad_capability_claims');
  // No leeway: not from the future, not expired, and CP's read is not from the future either.
  if (c.iat * 1000 > now || c.checked_at_ms > now) fail('capability_not_yet_valid');
  if (c.exp * 1000 <= now) fail('capability_expired');
  const r = request ?? {}; const rh = r.headers ?? {};
  if (r.method !== 'POST' || r.path !== ROUTE_PATH || r.host !== new URL(c.route_origin).host
      || rh.install_id !== c.install_id || rh.generation !== c.generation || rh.nonce !== c.mail_nonce
      || rh.digest !== c.req_digest || rh.key_id !== c.mail_key_id
      || rh.direction !== MODE_DIRECTION[c.mode]) fail('capability_request_mismatch');
  return c;
}
