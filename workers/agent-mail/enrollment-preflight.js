// Worker -> CP preflight for enrollment `begin` (MC-1166 7d-2e-2, decision E2a). Before `begin` inserts a
// flow, reserves an identity or sends a code, the Worker asks the CP whether the named device is a real,
// active, non-suspended device on a live tunnel (POST /v1/mail-enrollment/preflight, built in 7d-2e-1).
//
// Signed like cp-client.js (Ed25519, direction 'service-to-cp', exact path, no redirects, bounded) but a
// separate module: it has a different path prefix and no install exists yet, so the signed envelope
// carries install_id := flow_id and generation := "1" (the same convention as the ceremony replies).
//
// FAIL CLOSED. Only `200 {"ok": true}` is `{ ok: true }`. Everything else is `{ ok: false }`:
//   refused: true  -> the CP answered and said no (400/403/409), so the caller reports a refusal;
//   refused: false -> transport failure, redirect, 401/404/429/5xx, oversize or malformed body.
// No exception text, response body or key is ever returned.
import { sign, bytes } from './signing.js';
import { origin, ID } from './enrollment-transcript.js';

export const PREFLIGHT_PATH = '/v1/mail-enrollment/preflight';
const MAX_RESPONSE_BYTES = 4096;
const REFUSED = new Set([400, 403, 409]);

export function createPreflightClient({ issuer, key_id, privateKey, fetcher = fetch, clock = Date.now, timeoutMs = 10_000 }) {
  if (!origin(issuer) || !ID.test(key_id ?? '') || typeof privateKey !== 'string') throw new Error('preflight_config_required');
  return async function preflight({ flow_id, device_id, device_public_key, tunnel_uuid, route_origin }) {
    let response;
    try {
      if (!ID.test(flow_id ?? '')) return { ok: false, refused: false };
      const body = bytes(JSON.stringify({ device_id, device_public_key, tunnel_uuid, route_origin }));
      const headers = await sign({ privateKey, key_id, install_id: flow_id, generation: '1',
        direction: 'service-to-cp', path: PREFLIGHT_PATH, body, now: clock() });
      response = await fetcher(`${issuer}${PREFLIGHT_PATH}`, { method: 'POST', headers, body, redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs) });
    } catch { return { ok: false, refused: false }; }
    if (response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)) {
      return { ok: false, refused: false };
    }
    if (response.status !== 200) return { ok: false, refused: REFUSED.has(response.status) };
    try {
      const raw = new Uint8Array(await response.arrayBuffer());
      if (raw.length > MAX_RESPONSE_BYTES) return { ok: false, refused: false };
      const parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) && Object.keys(parsed).join() === 'ok'
        && parsed.ok === true ? { ok: true } : { ok: false, refused: false };
    } catch { return { ok: false, refused: false }; }
  };
}
