// Signed lifecycle responses (MC-1166 7d-2d, contract section 5.1). The service key signs the digest
// of the exact response body under its own direction, bound to the request's path and nonce, so a
// response can neither be replayed for another call nor forged by anything that lacks the key.
// Only successful (200) answers are ever signed: an HTTP error, a timeout or an unsigned body is,
// by contract, never an acknowledgment.
import { bytes, sign } from './signing.js';

export const RESPONSE_DIRECTION = 'service-to-install-response';

export async function signedReply({ config, install_id, generation, path, nonce, value, clock = Date.now }) {
  const body = bytes(JSON.stringify(value));
  const headers = await sign({ privateKey: config.privateKey, key_id: config.keyId, install_id,
    generation: String(generation), direction: RESPONSE_DIRECTION, path, body, now: clock(), nonce });
  headers.set('cache-control', 'no-store');
  return new Response(body, { status: 200, headers });
}
