// Retained legacy email/replay boundary shared by the entry and compatibility tests.
import { Ledger } from './ledger.js';
import { ingress } from './ingress.js';
import { replay } from './replay.js';
import { drainCpRevokes, cpFor } from './lifecycle.js';
// Config remains lazy, inside the original error boundaries; no test-only failure mapping.
export function legacyTransportHooks({ config, fetcher = fetch, clock = Date.now, cp } = {}) {
  return {
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
