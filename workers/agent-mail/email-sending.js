// Paid structured Email Sending; no HTTP, MIME, storage or retry policy.
import { parseAddrSpec } from './mailbox.js';
import { validateSend } from './outbound.js';
import { bytes } from './signing.js';

const TIMEOUT_MS = 10_000;
const PERMANENT = new Set([
  'E_VALIDATION_ERROR', 'E_FIELD_MISSING', 'E_TOO_MANY_RECIPIENTS', 'E_TOO_MANY_ATTACHMENTS',
  'E_SENDER_NOT_VERIFIED', 'E_RECIPIENT_NOT_ALLOWED', 'E_ACCOUNT_SUSPENDED',
  'E_SENDER_DOMAIN_NOT_AVAILABLE', 'E_CONTENT_TOO_LARGE', 'E_HEADER_NOT_ALLOWED',
  'E_HEADER_USE_API_FIELD', 'E_HEADER_VALUE_INVALID', 'E_HEADER_VALUE_TOO_LONG',
  'E_HEADER_NAME_INVALID', 'E_HEADERS_TOO_LARGE', 'E_HEADERS_TOO_MANY',
]);
const result = (outcome, code, provider_id = '') => ({ outcome, code, provider_id });
const uncertain = () => result('uncertain', 'uncertain');
function refusal(error) {
  const code = error instanceof Error ? error.code : undefined;
  if (code === 'E_RATE_LIMIT_EXCEEDED' || code === 'E_DAILY_LIMIT_EXCEEDED') {
    return result('retryable', 'provider_quota');
  }
  if (code === 'E_RECIPIENT_SUPPRESSED') return result('permanent', 'provider_suppressed');
  if (PERMANENT.has(code)) return result('permanent', 'provider_unavailable');
  // Internal/delivery/transport failures do not prove nonacceptance. In
  // particular, never infer submission state from an exception's message.
  return uncertain();
}

/** Internal adapter for unit12: .send({reg,value}) returns
 * {outcome:accepted|retryable|permanent|uncertain,code,provider_id}.
 * Caller owns the durable current-owner authorization/reservation and must not
 * resubmit uncertain intents. reg is its immutable authorized owner snapshot;
 * this adapter performs no lookup and never chooses a recipient from value.
 * clock returns Unix ms; timers supplies setTimeout/clearTimeout for tests.
 * binding.send has no documented abort argument: the timeout bounds our wait,
 * not provider execution, and late completion cannot authorize another call.
 */
export function cloudflareEmailSending({ binding, from, domain, clock = Date.now, timers = globalThis }) {
  if (typeof binding?.send !== 'function' || typeof clock !== 'function'
      || typeof timers?.setTimeout !== 'function' || typeof timers?.clearTimeout !== 'function'
      || !parseAddrSpec(from) || parseAddrSpec(from).domain !== domain
      || parseAddrSpec(`mail@${domain}`)?.domain !== domain) throw new Error('provider_config_required');
  return {
    async send({ reg, value }) {
      if (!validateSend(value) || [value.subject, value.body].some((s) => new TextDecoder().decode(bytes(s)) !== s)) {
        throw new Error('invalid_send');
      }
      if (![true, 1].includes(reg?.active) || ![true, 1].includes(reg?.owner_verified)
          || !parseAddrSpec(reg?.owner_address)
          || typeof reg?.address_local !== 'string' || !/^m[a-f0-9]{29}$/.test(reg.address_local)) {
        throw new Error('registration_inactive');
      }
      const message = {
        from, to: reg.owner_address, replyTo: `${reg.address_local}+${value.reply_token}@${domain}`,
        subject: value.subject, text: value.body,
        headers: { 'Auto-Submitted': 'auto-generated', 'X-Clayrune-Agent-Mail': '1' },
      };
      if (!parseAddrSpec(message.replyTo)) throw new Error('invalid_service_address');
      const started = clock();
      if (!Number.isSafeInteger(started) || started < 0 || started > Number.MAX_SAFE_INTEGER - TIMEOUT_MS) {
        throw new Error('provider_config_required');
      }
      const deadline = started + TIMEOUT_MS;
      const beforeDeadline = () => {
        try {
          const now = clock();
          return Number.isSafeInteger(now) && now >= started && now < deadline;
        } catch { return false; } // A failed clock cannot establish timely acceptance.
      };
      let timer;
      try {
        const timeout = new Promise((resolve) => { timer = timers.setTimeout(() => resolve(uncertain()), TIMEOUT_MS); });
        const attempt = (async () => {
          try {
            const receipt = await binding.send(message);
            if (!beforeDeadline()) return uncertain();
            const id = receipt?.messageId;
            // Opaque provider identifier, NOT an RFC Message-ID. Do not trim,
            // parse or equate it with the derived internal intent key.
            if (typeof id !== 'string' || !/^[\x20-\x7e]{1,256}$/.test(id) || !id.trim()) return uncertain();
            return result('accepted', 'ok', id);
          } catch (error) {
            return beforeDeadline() ? refusal(error) : uncertain();
          }
        })();
        return await Promise.race([attempt, timeout]);
      } finally { timers.clearTimeout(timer); }
    },
  };
}
