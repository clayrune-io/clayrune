// Cloudflare entry/factory. Dependencies are injected only by tests, never HTTP.
import { Ledger } from './ledger.js';
import { verify } from './signing.js';
import { ingress, readBounded } from './ingress.js';
import { outbound } from './outbound.js';
import { replay } from './replay.js';
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
export function createWorker({ send, fetcher = fetch, clock = Date.now } = {}) {
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
      if (request.method !== 'POST' || !['/v1/mail/send', '/v1/mail/reconcile'].includes(url.pathname)
          || url.search) return reply(404, { code: 'not_found' });
      const now = clock(); const ledger = new Ledger(env.MAIL_DB);
      try {
        // No dashboard cookie/bearer token is read by this receiver.
        const id = request.headers.get('x-mail-install-id');
        if (!/^[A-Za-z0-9_-]{1,128}$/.test(id ?? '')) return reply(401, { code: 'unauthorized' });
        const reg = await ledger.registration(id);
        if (!reg?.active || !reg.owner_verified) return reply(401, { code: 'unauthorized' });
        if (!await ledger.limit(`http:${id}`, 200, now)) return reply(429, { code: 'request_limit' });
        let body;
        // JSON escaping can expand a valid 16 KiB text body sixfold.
        try { body = await readBounded(request.body, 131_072); }
        catch { return reply(413, { code: 'body_limit' }); }
        if (!await verify({ request, body, publicKey: reg.public_key, key_id: reg.key_id,
          direction: 'install-to-service', install_id: reg.install_id, generation: reg.generation,
          claimNonce: (m) => ledger.claimNonce(m), now })) return reply(401, { code: 'unauthorized' });
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
