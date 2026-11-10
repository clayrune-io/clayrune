// Worker -> CP signed calls for delivery authority (MC-1166 7d-2b, contract section 2, D1).
// Every call is signed with the mail-service Ed25519 key, direction 'service-to-cp', over the
// exact CP path. The CP origin is fixed reviewed config; redirects are never followed; every
// failure is a closed code and never carries an exception message, response body or key.
import { sign, bytes } from './signing.js';
import { origin, ID, HEX } from './enrollment-transcript.js';
export const CP_PREFIX = '/v1/mail-delivery';
export const MAX_REQUEST_BYTES = 16 * 1024;
export const MAX_RESPONSE_BYTES = 64 * 1024;
const GEN = /^[1-9][0-9]{0,14}$/;
const NONCE = /^[a-f0-9]{32}$/;
const CODE = /^[a-z][a-z_]{0,63}$/;
const NAMES = ['bind', 'issue', 'lease', 'revoke'];
export function createCpClient({ issuer, key_id, privateKey, fetcher = fetch, clock = Date.now, timeoutMs = 10_000 }) {
  if (!origin(issuer) || !ID.test(key_id ?? '') || typeof privateKey !== 'string') throw new Error('cp_client_config_required');
  async function call(name, install_id, generation, value) {
    if (!NAMES.includes(name) || !ID.test(install_id ?? '') || !GEN.test(String(generation))) {
      return { ok: false, code: 'bad_request' };
    }
    const path = `${CP_PREFIX}/${name}`;
    const body = bytes(JSON.stringify(value));
    if (body.length > MAX_REQUEST_BYTES) return { ok: false, code: 'bad_request' };
    let response;
    try {
      const headers = await sign({ privateKey, key_id, install_id, generation: String(generation),
        direction: 'service-to-cp', path, body, now: clock() });
      response = await fetcher(`${issuer}${path}`, { method: 'POST', headers, body, redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs) });
    } catch { return { ok: false, code: 'cp_unavailable' }; }
    // A redirect is never a result: a login page must not look like an authority answer.
    if (response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)) {
      return { ok: false, status: response.status, code: 'cp_redirect' };
    }
    let parsed;
    try {
      const raw = new Uint8Array(await response.arrayBuffer());
      if (raw.length > MAX_RESPONSE_BYTES) return { ok: false, status: response.status, code: 'cp_response_too_large' };
      parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
    } catch { return { ok: false, status: response.status, code: 'cp_bad_response' }; }
    if (response.status === 200 && parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return { ok: true, status: 200, body: parsed };
    }
    const code = typeof parsed?.code === 'string' && CODE.test(parsed.code) ? parsed.code : 'cp_error';
    return { ok: false, status: response.status, code };
  }
  return {
    // `generation` is the registration generation AFTER enrollment commit (expected_generation + 1).
    bind: ({ install_id, generation, grant, r }) => call('bind', install_id, generation, { grant, r }),
    issue: ({ install_id, generation, mode, req_digest, mail_nonce, mail_key_id, worker_active }) => {
      if (!['mail', 'proof'].includes(mode) || !HEX.test(req_digest ?? '') || !NONCE.test(mail_nonce ?? '')
          || !ID.test(mail_key_id ?? '')) return Promise.resolve({ ok: false, code: 'bad_request' });
      const value = { install_id, generation: String(generation), mode, req_digest, mail_nonce, mail_key_id };
      if (worker_active !== undefined) value.worker_active = worker_active === true;
      return call('issue', install_id, generation, value);
    },
    lease: ({ install_id, generation }) => call('lease', install_id, generation,
      { install_id, generation: String(generation) }),
    revoke: ({ install_id, generation }) => call('revoke', install_id, generation,
      { install_id, generation: String(generation) }),
  };
}
