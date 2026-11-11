// Cloudflare entry/factory. Dependencies are injected only by tests, never HTTP.
import { Ledger } from './ledger.js';
import { verify } from './signing.js';
import { ingress } from './ingress.js';
import { readRequestBody, BODY_TIMEOUT_MS } from './request-body.js';
import { outbound } from './outbound.js';
import { replay } from './replay.js';
import { lifecycle, isLifecyclePath, drainCpRevokes, cpFor } from './lifecycle.js';
import { enrollmentHttp, isEnrollmentPath } from './enrollment-http.js';
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
export function createWorker({ send, fetcher = fetch, clock = Date.now, bodyTimeoutMs = BODY_TIMEOUT_MS, cp, enrollment } = {}) {
  const config = (env) => ({ domain: env.MAIL_DOMAIN, from: env.MAIL_FROM,
    ingressVerified: env.MAIL_INGRESS_VERIFIED === 'true', privateKey: env.MAIL_SERVICE_PRIVATE_KEY,
    keyId: env.MAIL_SERVICE_KEY_ID });
  async function sender(env, mail) {
    if (send) return send(mail);
    // Preserve the raw owner-only MIME contract. Cloudflare's EmailMessage import
    // is isolated from Node tests; production uses its send_email binding.
    const { EmailMessage } = await import('cloudflare:email');
    return env.MAIL_SEND.send(new EmailMessage(mail.from, mail.to, mail.raw));
  }
  return {
    async fetch(request, env) {
      if (env.MAIL_ENABLED !== 'true') return reply(503, { code: 'mail_disabled' });
      const url = new URL(request.url);
      // Lifecycle path table lives in lifecycle.js; it has its own flag (MAIL_LIFECYCLE_ENABLED).
      if (request.method === 'POST' && !url.search && isLifecyclePath(url.pathname)) {
        try {
          return await lifecycle({ request, env, ledger: new Ledger(env.MAIL_DB), config: config(env),
            cp: cpFor(env, { cp, fetcher, clock }), clock, bodyTimeoutMs });
        } catch {
          console.error('[agent-mail] lifecycle_storage_or_configuration_failure');
          return reply(503, { code: 'storage_or_configuration_failure' });
        }
      }
      // Enrollment ceremony (7d-2e-2): own module, own flag (MAIL_ENROLLMENT_ENABLED), default off.
      // `enrollment` carries test doubles only (provider/reader/preflight); production passes none.
      if (request.method === 'POST' && !url.search && isEnrollmentPath(url.pathname)) {
        try {
          return await enrollmentHttp({ request, env, ledger: new Ledger(env.MAIL_DB), config: config(env),
            send: (mail) => sender(env, mail), fetcher, clock, bodyTimeoutMs, inject: enrollment });
        } catch {
          console.error('[agent-mail] enrollment_storage_or_configuration_failure');
          return reply(503, { code: 'storage_or_configuration_failure' });
        }
      }
      if (request.method !== 'POST' || !['/v1/mail/send', '/v1/mail/reconcile'].includes(url.pathname)
          || url.search) return reply(404, { code: 'not_found' });
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
          const result = await outbound({ ledger, reg, value, body, config: config(env),
            send: (mail) => sender(env, mail), now });
          const { status, ...resultBody } = result;
          return reply(status, resultBody);
        }
        if (!value || typeof value !== 'object' || Array.isArray(value)
            || Object.keys(value).length !== 0) return reply(400, { code: 'invalid_reconcile' });
        return reply(200, await replay({ ledger, config: config(env), fetcher, now, clock, install: reg.install_id }));
      } catch {
        // Storage/signing/config failures are observable without leaking owner,
        // token, key or body through exception strings. Never reset a database.
        console.error('[agent-mail] fetch_storage_or_configuration_failure');
        return reply(503, { code: 'storage_or_configuration_failure' });
      }
    },
    async email(message, env) {
      if (env.MAIL_ENABLED !== 'true') { message.setReject('Agent mail disabled'); return; }
      const now = clock(); const ledger = new Ledger(env.MAIL_DB);
      // Await persistence before returning. A D1 failure propagates to the email
      // runtime instead of falsely acknowledging successful durable custody.
      try {
        const result = await ingress({ message, ledger, config: config(env), now });
        if (result.status === 'refused') throw new Error('queue_unavailable');
        // Scheduled replay owns network delivery: email lifetime never owns it.
        return result;
      } catch {
        console.error('[agent-mail] ingress_storage_or_capacity_failure');
        throw new Error('agent_mail_custody_failed');
      }
    },
    async scheduled(_event, env) {
      if (env.MAIL_ENABLED !== 'true') return;
      if (env.MAIL_LIFECYCLE_ENABLED === 'true') {
        // Queued Worker -> CP revokes; failure is retried on the next tick and never blocks replay.
        try { await drainCpRevokes({ ledger: new Ledger(env.MAIL_DB), cp: cpFor(env, { cp, fetcher, clock }) }); }
        catch { console.error('[agent-mail] lifecycle_cp_revoke_drain_failure'); }
      }
      try {
        return await replay({ ledger: new Ledger(env.MAIL_DB), config: config(env), fetcher, now: clock(), clock });
      } catch {
        console.error('[agent-mail] replay_storage_or_configuration_failure');
        throw new Error('agent_mail_replay_failed');
      }
    },
  };
}
export default createWorker();
