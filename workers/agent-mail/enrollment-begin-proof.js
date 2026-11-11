// Possession proof precedes CP preflight and every destination/code side effect.
import { bytes, sha256, SKEW_MS } from './signing.js';
import { canonicalR, FIELDS, HEX, ID, verifyProof } from './enrollment-transcript.js';
import { parseMailbox } from './mailbox.js';

export const BEGIN_FIELDS = ['flow_id', 'owner_address', 'device_id', 'device_public_key', 'mail_key_id',
  'mail_public_key', 'tunnel_uuid', 'route_origin', 'manifest_sequence', 'service_key_id', 'service_public_key_sha256'];

export async function canonicalBegin(value, secret, service, timestamp, nonce) {
  const hasFence = value && Object.hasOwn(value, 'fence_digest');
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join() !== [...BEGIN_FIELDS, ...(hasFence ? ['fence_digest'] : [])].sort().join()
      || BEGIN_FIELDS.some((k) => typeof value[k] !== 'string')
      || (hasFence && (typeof value.fence_digest !== 'string' || !HEX.test(value.fence_digest)))
      || !ID.test(value.flow_id) || !ID.test(value.device_id)
      || parseMailbox(value.owner_address)?.address !== value.owner_address || value.owner_address.length > 254
      || typeof secret !== 'string' || !HEX.test(secret)) throw new Error('bad_enrollment');
  const m = { ...value, purpose: 'clayrune-mail-enrollment', version: '1', service_origin: service,
    challenge_id: 'preflight', challenge_nonce: '0'.repeat(64), expires_ms: timestamp,
    install_id: 'preflight', expected_generation: '0', owner_address_sha256: await sha256(bytes(value.owner_address)),
    route_path: '/api/agent-mail/ingress' };
  try { canonicalR(FIELDS.map((k) => m[k])); }
  catch { throw new Error('bad_enrollment'); }
  return bytes(JSON.stringify(['clayrune-mail-enrollment-begin', '1', service, timestamp, nonce,
    await sha256(bytes(secret)), ...BEGIN_FIELDS.map((k) => value[k]), value.fence_digest ?? '']));
}

export async function verifyBegin({ value, flow_secret, proof }, service, now) {
  if (!proof || typeof proof !== 'object' || Array.isArray(proof)
      || Object.keys(proof).sort().join() !== 'nonce,signature,timestamp'
      || typeof proof.timestamp !== 'string' || !/^[1-9][0-9]{12}$/.test(proof.timestamp)
      || typeof proof.nonce !== 'string' || !/^[a-f0-9]{32}$/.test(proof.nonce)
      || typeof proof.signature !== 'string' || proof.signature.length > 128) return false;
  const input = await canonicalBegin(value, flow_secret, service, proof.timestamp, proof.nonce);
  return Math.abs(now - Number(proof.timestamp)) <= SKEW_MS
    && await verifyProof(value.device_public_key, proof.signature, input);
}

export async function claimBegin(db, device, proof, now) {
  if (Math.abs(now - Number(proof.timestamp)) > SKEW_MS) return false;
  const result = await db.batch([
    db.prepare('DELETE FROM mail_begin_nonces WHERE expires_ms<?').bind(now),
    db.prepare('INSERT OR IGNORE INTO mail_begin_nonces VALUES(?,?,?)')
      .bind(device, proof.nonce, Number(proof.timestamp) + SKEW_MS),
  ]);
  return result[1].meta.changes === 1;
}
