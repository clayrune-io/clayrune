// Cloudflare entry/factory. Dependencies are injected only by tests, never HTTP.
import { Ledger } from './ledger.js';
import { ingress } from './ingress.js';
import { BODY_TIMEOUT_MS } from './request-body.js';
import { legacyTransportHttp } from './legacy-transport-http.js';
import { replay } from './replay.js';
import { lifecycle, isLifecyclePath, drainCpRevokes, cpFor } from './lifecycle.js';
import { enrollmentHttp, isEnrollmentPath } from './enrollment-http.js';
import { manifestHttp, isManifestRequest } from './manifest-http.js';
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
      // Pre-signed service manifest (7e-2p): own module; serves operator config only, never signs.
      if (isManifestRequest(request, url)) return manifestHttp({ env });
      if (request.method !== 'POST' || !['/v1/mail/send', '/v1/mail/reconcile'].includes(url.pathname)
          || url.search) return reply(404, { code: 'not_found' });
      return legacyTransportHttp({ request, env, config: () => config(env),
        send: (mail) => sender(env, mail), fetcher, clock, bodyTimeoutMs });
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
