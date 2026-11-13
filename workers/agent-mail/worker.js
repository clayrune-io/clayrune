// Public pull entry: dispatch and reviewed operator configuration only.
// The production DO export lives in production.js, keeping this factory Node-importable.
import { Ledger } from './ledger.js';
import { BODY_TIMEOUT_MS } from './request-body.js';
import { isLifecyclePath } from './lifecycle.js';
import { isEnrollmentPath } from './enrollment-http.js';
import { manifestHttp, isManifestRequest } from './manifest-http.js';
import { VerificationMail } from './verification-mail.js';
import { unbase64url } from './signing.js';
import { SignupHTTP, SIGNUP_PATHS } from './signup-http.js';
import { OwnerManagementHTTP, MANAGEMENT_PATHS } from './owner-management.js';
import { OwnerChangeHTTP, OWNER_CHANGE_PATHS } from './owner-change.js';
import { LinkRouter, LINK_PATHS } from './link-router.js';
import { SessionAuth, TICKET_PATH, SOCKET_PATH } from './session-auth.js';
import { PullSocketRoute, socketTrustedSource } from './pull-socket-route.js';
import { pullSocketConfig } from './pull-socket-config.js';
import { PullSendHTTP, SEND_PATH, SEND_STATUS_PATH } from './pull-send-http.js';
import { PullActivateHTTP, ACTIVATE_PATH } from './pull-activate-http.js';
import { PullStatusHTTP, STATUS_PATH } from './pull-status-http.js';
import { PullFenceHTTP, FENCE_PATH } from './pull-fence-http.js';
import { PullKeyRotateHTTP, KEY_ROTATE_PATH } from './pull-key-rotate-http.js';
import { ingress } from './ingress.js';
import { sweep } from './pull-sweep.js';

const HTTP_ROUTES = [
  [[TICKET_PATH], SessionAuth],
  [[SEND_PATH, SEND_STATUS_PATH], PullSendHTTP],
  [[ACTIVATE_PATH], PullActivateHTTP],
  [[STATUS_PATH], PullStatusHTTP],
  [[FENCE_PATH], PullFenceHTTP],
  [[KEY_ROTATE_PATH], PullKeyRotateHTTP],
];
const HOSTED_ROUTES = [
  [SIGNUP_PATHS, SignupHTTP],
  [MANAGEMENT_PATHS, OwnerManagementHTTP],
  [OWNER_CHANGE_PATHS, OwnerChangeHTTP],
];
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });

export function createWorker({ clock = Date.now, bodyTimeoutMs = BODY_TIMEOUT_MS } = {}) {
  const config = (env) => ({ db: env.MAIL_DB, serviceOrigin: env.MAIL_SERVICE_ORIGIN,
    domain: env.MAIL_DOMAIN, serviceKeyId: env.MAIL_SERVICE_KEY_ID,
    servicePrivateKey: env.MAIL_SERVICE_PRIVATE_KEY, clock, bodyTimeoutMs });
  const wake = (env) => (id) => env.MAIL_PULL_MAILBOX.get(env.MAIL_PULL_MAILBOX.idFromName(id)).wake(id);
  return {
    async fetch(request, env) {
      if (env.MAIL_ENABLED !== 'true') return reply(503, { code: 'mail_disabled' });
      const url = new URL(request.url);
      // Keep the pre-signed manifest byte contract and the legacy route grammar.
      if (isManifestRequest(request, url)) return manifestHttp({ env });
      if (request.method === 'POST' && !url.search && (isLifecyclePath(url.pathname)
        || isEnrollmentPath(url.pathname) || ['/v1/mail/send', '/v1/mail/reconcile'].includes(url.pathname))) {
        return reply(410, { code: 'legacy_retired' });
      }
      const hosted = HOSTED_ROUTES.find(([paths]) => paths.includes(url.pathname));
      const http = HTTP_ROUTES.find(([paths]) => paths.includes(url.pathname));
      if (!hosted && !http && url.pathname !== SOCKET_PATH) return reply(404, { code: 'not_found' });
      try {
        if (url.pathname === SOCKET_PATH) {
          return await new PullSocketRoute(pullSocketConfig(env), env.MAIL_PULL_MAILBOX).handle(request);
        }
        const context = { trustedSource: socketTrustedSource(request) };
        const options = config(env);
        if (hosted) {
          options.verificationMail = new VerificationMail({ db: env.MAIL_DB, serviceOrigin: env.MAIL_SERVICE_ORIGIN,
            sealKeyId: env.MAIL_PULL_OUTBOX_KEY_ID, sealKey: unbase64url(env.MAIL_PULL_OUTBOX_KEY) });
          // Shared links must reach only LinkRouter; its read-only purpose lookup
          // selects the original request's sole authoritative HTTP handler.
          if (LINK_PATHS.includes(url.pathname)) {
            return await new LinkRouter({ signup: new SignupHTTP(options), management: new OwnerManagementHTTP(options),
              ownerChange: new OwnerChangeHTTP(options) }).handle(request, context);
          }
          return await new hosted[1](options).handle(request, context);
        }
        return await new http[1]({ ...options, config: { from: env.MAIL_FROM, domain: env.MAIL_DOMAIN },
          binding: env.MAIL_SEND }).handle(request, context);
      } catch {
        console.error('[agent-mail] pull_http_storage_or_configuration_failure');
        return reply(503, { v: 1, code: 'service_unavailable' });
      }
    },
    async email(message, env) {
      if (env.MAIL_ENABLED !== 'true') { message.setReject('Agent mail disabled'); return; }
      try {
        return await ingress({ message, ledger: new Ledger(env.MAIL_DB).pull({ clock }),
          config: { domain: env.MAIL_DOMAIN, ingressVerified: env.MAIL_INGRESS_VERIFIED === 'true' },
          now: clock(), wake: wake(env) });
      } catch {
        console.error('[agent-mail] ingress_storage_or_capacity_failure');
        throw new Error('agent_mail_custody_failed');
      }
    },
    async scheduled(_event, env) {
      if (env.MAIL_ENABLED !== 'true') return;
      try {
        return await sweep({ ledger: new Ledger(env.MAIL_DB).pull({ clock }), wake: wake(env) });
      } catch {
        console.error('[agent-mail] pull_sweep_storage_or_configuration_failure');
        throw new Error('agent_mail_sweep_failed');
      }
    },
  };
}
export default createWorker();
