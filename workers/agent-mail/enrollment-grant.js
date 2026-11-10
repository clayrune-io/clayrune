// No fetching token-selected URLs. Trust is supplied by reviewed service configuration.
import { bytes, unbase64url, sha256 } from './signing.js';
import { canonicalR, fieldsR, origin, ID, HEX } from './enrollment-transcript.js';
export const GRANT_TYPE = 'clayrune-mail-enrollment+jwt';
export const GRANT_AUDIENCE = 'clayrune-mail-enrollment';
export const OWNERSHIP_LEASE_MS = 300_000;
export async function verifyGrant(token, r, trust, clock = Date.now) {
  if (!trust || !origin(trust.issuer) || !origin(trust.serviceOrigin)
      || trust.jwksUrl !== `${trust.issuer}/v1/mail-enrollment/jwks`
      || !Array.isArray(trust.jwks?.keys)) throw new Error('grant_trust_required');
  if (typeof token !== 'string' || token.length > 8192) throw new Error('bad_grant');
  const parts = token.split('.');
  if (parts.length !== 3) throw new Error('bad_grant');
  const decode = (s) => JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(unbase64url(s)));
  const h = decode(parts[0]); const c = decode(parts[1]); const m = fieldsR(r);
  if (!h || Object.keys(h).sort().join(',') !== 'alg,kid,typ' || h.alg !== 'ES256'
      || h.typ !== GRANT_TYPE || !ID.test(h.kid)) throw new Error('bad_grant_header');
  const matches = trust.jwks.keys.filter((k) => k.kid === h.kid);
  if (matches.length !== 1) throw new Error('unknown_grant_key');
  const jwk = matches[0];
  if (jwk.kty !== 'EC' || jwk.crv !== 'P-256' || jwk.alg !== 'ES256' || jwk.use !== 'sig'
      || jwk.d !== undefined || unbase64url(jwk.x).length !== 32 || unbase64url(jwk.y).length !== 32) throw new Error('bad_grant_key');
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  const sig = unbase64url(parts[2]);
  if (sig.length !== 64 || !await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, sig,
    bytes(`${parts[0]}.${parts[1]}`))) throw new Error('bad_grant_signature');
  const now = clock();
  if (!c || c.iss !== trust.issuer || c.aud !== GRANT_AUDIENCE || c.token_use !== GRANT_AUDIENCE
      || !ID.test(c.sub ?? '') || !ID.test(c.jti ?? '') || !HEX.test(c.record_version ?? '')
      || !['iat', 'nbf', 'exp', 'checked_at_ms'].every((k) => Number.isSafeInteger(c[k]))
      || c.iat !== c.nbf || c.exp <= c.iat || c.exp - c.iat > 60
      || c.iat * 1000 > now || c.exp * 1000 <= now || c.nbf * 1000 > now
      || c.checked_at_ms > c.iat * 1000 + 999 || c.checked_at_ms > now
      || c.checked_at_ms < c.iat * 1000 - 999
      || c.checked_at_ms + OWNERSHIP_LEASE_MS <= now
      || m.service_origin !== trust.serviceOrigin || Number(m.expires_ms) <= now
      || c.r_sha256 !== await sha256(canonicalR(r))
      || c.device_id !== m.device_id || c.tunnel_uuid !== m.tunnel_uuid || c.route_origin !== m.route_origin
      || c.install_id !== m.install_id || c.expected_generation !== m.expected_generation) throw new Error('bad_grant_claims');
  return c;
}
