// Retained legacy send/reconcile HTTP boundary. Public routing and feature gates live in worker.js.
import { Ledger } from './ledger.js';
import { verify } from './signing.js';
import { readRequestBody, BODY_TIMEOUT_MS } from './request-body.js';
import { outbound } from './outbound.js';
import { replay } from './replay.js';
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
// Resolve config only after authentication, inside the original failure boundary.
export async function legacyTransportHttp({ request, env, config, send, fetcher = fetch,
  clock = Date.now, bodyTimeoutMs = BODY_TIMEOUT_MS }) {
  const url = new URL(request.url);
  const ledger = new Ledger(env.MAIL_DB);
  try {
    // Invalid traffic shares a bounded bucket, never an install's allowance.
    // Apply it only on refusal so abuse cannot lock out a valid signature.
    const unauthorized = async () => await ledger.limit('http:unauthenticated', 10_000, clock(), 60_000)
      ? reply(401, { code: 'unauthorized' }) : reply(429, { code: 'abuse_limit' });
    // No dashboard cookie/bearer token is read by this receiver.
    const id = request.headers.get('x-mail-install-id');
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(id ?? '')) return await unauthorized();
    const reg = await ledger.registration(id);
    if (!reg?.active || !reg.owner_verified) return await unauthorized();
    let body;
    // JSON escaping can expand a valid 16 KiB text body sixfold.
    try { body = await readRequestBody(request.body, { timeoutMs: bodyTimeoutMs }); }
    catch (error) {
      return error.message === 'body_timeout' ? reply(408, { code: 'body_timeout' })
        : reply(413, { code: 'body_limit' });
    }
    if (!await verify({ request, body, publicKey: reg.public_key, key_id: reg.key_id,
      direction: 'install-to-service', install_id: reg.install_id, generation: reg.generation,
      claimNonce: (m) => ledger.claimNonce(m), clock })) return await unauthorized();
    const now = clock();
    if (!await ledger.limit(`http:${id}`, 200, now)) return reply(429, { code: 'request_limit' });
    let value;
    try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
    catch { return reply(400, { code: 'invalid_json' }); }
    if (url.pathname === '/v1/mail/send') {
      const result = await outbound({ ledger, reg, value, body, config: config(),
        send, now });
      const { status, ...resultBody } = result;
      return reply(status, resultBody);
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).length !== 0) return reply(400, { code: 'invalid_reconcile' });
    return reply(200, await replay({ ledger, config: config(), fetcher, now, clock, install: reg.install_id }));
  } catch {
    // Storage/signing/config failures are observable without leaking owner,
    // token, key or body through exception strings. Never reset a database.
    console.error('[agent-mail] fetch_storage_or_configuration_failure');
    return reply(503, { code: 'storage_or_configuration_failure' });
  }
}
