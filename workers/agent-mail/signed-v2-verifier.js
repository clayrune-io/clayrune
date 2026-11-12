// Shared pull HTTP v2 proof boundary. Resolvers authorize the exact request
// scope; possession alone never grants registry/flow authority. This function
// validates nonce grammar but does NOT claim it: the caller must atomically
// insert pull_nonces with its guarded mutation, retaining timestamp+300001ms.
import { bytes, sha256 } from './signing.js';
import { verifyProof } from './enrollment-transcript.js';

export const PULL_SKEW_MS = 300_000;
const FIELDS = ['version', 'direction', 'key_id', 'install_id', 'generation', 'owner_generation',
  'timestamp', 'nonce', 'digest', 'signature'];
const valid = (ok) => { if (!ok) throw new Error('unauthorized'); };
const matches = (re, s) => typeof s === 'string' && re.test(s);
const safe = (s) => matches(/^(0|[1-9][0-9]*)$/, s) && Number.isSafeInteger(Number(s));
const generation = (s) => matches(/^[1-9][0-9]{0,14}$/, s);

/** resolveKey(auth,path,body) returns the authorized public key, or refuses.
 * It may attach private adapter context to auth; that context is not signed.
 * Resolver/storage errors propagate so callers never disguise an outage as a
 * successful proof or fall back to a different key. clock is checked twice.
 */
export async function verifySignedV2({ request, raw, body, path, serviceOrigin, clock, resolveKey }) {
  const url = new URL(request.url);
  valid(request.method === 'POST' && url.origin === serviceOrigin && url.pathname === path
    && !request.url.includes('?') && !request.url.includes('#'));
  const m = Object.fromEntries(FIELDS.map((k) => [k, request.headers.get(`x-mail-${k.replaceAll('_', '-')}`)]));
  valid(m.version === '2' && m.direction === 'install-to-pull'
    && matches(/^[A-Za-z0-9_-]{1,128}$/, m.key_id) && matches(/^[a-f0-9]{32}$/, m.install_id)
    && generation(m.generation) && generation(m.owner_generation)
    && safe(m.timestamp) && matches(/^[a-f0-9]{32}$/, m.nonce)
    && matches(/^[a-f0-9]{64}$/, m.digest) && typeof m.signature === 'string');
  valid(Math.abs(clock() - Number(m.timestamp)) <= PULL_SKEW_MS && m.digest === await sha256(raw));
  const publicKey = await resolveKey(m, path, body);
  const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction,
    serviceOrigin, 'POST', path, m.key_id, m.install_id, m.generation, m.owner_generation,
    m.timestamp, m.nonce, m.digest]));
  valid(await verifyProof(publicKey, m.signature, transcript));
  valid(Math.abs(clock() - Number(m.timestamp)) <= PULL_SKEW_MS);
  return m;
}
