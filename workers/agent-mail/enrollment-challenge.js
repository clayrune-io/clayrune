// Secrets are returned once to the human-flow adapter, never persisted as plaintext.
import { base64url, bytes, sha256 } from './signing.js';
export const CHALLENGE_TTL = 900_000;
export function randomHex(size) {
  return [...crypto.getRandomValues(new Uint8Array(size))].map((b) => b.toString(16).padStart(2, '0')).join('');
}
export async function codeDigest(pepper, flowId, challengeId, code) {
  if (!(pepper instanceof Uint8Array) || pepper.length !== 32) throw new Error('mail_code_key_required');
  const key = await crypto.subtle.importKey('raw', pepper, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return base64url(await crypto.subtle.sign('HMAC', key,
    bytes(JSON.stringify(['clayrune-mail-code', '1', flowId, challengeId, code]))));
}
export async function flowDigest(secret) {
  if (typeof secret !== 'string' || !/^[a-f0-9]{64}$/.test(secret)) throw new Error('human_flow_required');
  return sha256(bytes(secret));
}
