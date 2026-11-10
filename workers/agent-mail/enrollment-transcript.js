// Purpose-separated, ASCII-only cross-language enrollment transcript R.
import { bytes, unbase64url } from './signing.js';
export const FIELDS = ['purpose', 'version', 'service_origin', 'flow_id', 'challenge_id',
  'challenge_nonce', 'expires_ms', 'install_id', 'expected_generation', 'device_id',
  'device_public_key', 'mail_key_id', 'mail_public_key', 'owner_address_sha256',
  'tunnel_uuid', 'route_origin', 'route_path', 'manifest_sequence', 'service_key_id',
  'service_public_key_sha256'];
export const ID = /^[A-Za-z0-9_-]{1,128}$/;
export const HEX = /^[a-f0-9]{64}$/;
export function origin(s) {
  return typeof s === 'string' && /^https:\/\/(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(s);
}
export function canonicalR(r) {
  if (!Array.isArray(r) || r.length !== FIELDS.length
      || r.some((v) => typeof v !== 'string' || !/^[\x20-\x7e]{1,512}$/.test(v))) throw new Error('bad_transcript');
  const m = Object.fromEntries(FIELDS.map((k, i) => [k, r[i]]));
  if (m.purpose !== 'clayrune-mail-enrollment' || m.version !== '1'
      || !origin(m.service_origin) || !origin(m.route_origin)
      || !['flow_id', 'challenge_id', 'install_id', 'device_id', 'mail_key_id', 'service_key_id'].every((k) => ID.test(m[k]))
      || !/^[a-f0-9]{64}$/.test(m.challenge_nonce) || !/^[1-9][0-9]{12}$/.test(m.expires_ms)
      || !/^(0|[1-9][0-9]{0,14})$/.test(m.expected_generation)
      || !/^[1-9][0-9]{0,14}$/.test(m.manifest_sequence)
      || !HEX.test(m.owner_address_sha256) || !HEX.test(m.service_public_key_sha256)
      || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(m.tunnel_uuid)
      || m.route_path !== '/api/agent-mail/ingress'
      || unbase64url(m.device_public_key).length !== 32 || unbase64url(m.mail_public_key).length !== 32) {
    throw new Error('bad_transcript');
  }
  return bytes(JSON.stringify(r));
}
export function fieldsR(r) { canonicalR(r); return Object.fromEntries(FIELDS.map((k, i) => [k, r[i]])); }
export async function verifyProof(publicKey, signature, input) {
  try {
    const sig = unbase64url(signature);
    if (sig.length !== 64) return false;
    const key = await crypto.subtle.importKey('raw', unbase64url(publicKey), 'Ed25519', false, ['verify']);
    return await crypto.subtle.verify('Ed25519', key, sig, input);
  } catch { return false; } // Untrusted key/signature grammar, no I/O.
}
