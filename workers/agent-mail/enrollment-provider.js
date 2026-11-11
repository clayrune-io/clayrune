// Read verification at complete; provision only at possession-proved, CP-approved begin.
import { cloudflareDestinations } from './destination-cloudflare.js';
import { destinationProvisioning } from './destination-provisioning.js';
export const DESTINATION_TOKEN_ENV = 'MAIL_DESTINATION_TOKEN';

// reader({ address, token }) -> { address, verified } | throws. Wrapped so a reader cannot widen the
// answer: only a strict boolean `true` verifies. A throw propagates to Enrollment.complete, which turns
// any provider throw into the constant `destination_unavailable` (no message is ever relayed).
export function createDestinationProvider({ reader, token }) {
  if (typeof reader !== 'function' || typeof token !== 'string' || !token) throw new Error('provider_config_required');
  return async (address) => {
    const found = await reader({ address, token });
    return { address: found?.address, verified: found?.verified === true };
  };
}

// Test double: a fixed table of destination addresses -> verified flag. Exact string match only.
export function fakeDestinationReader(table) {
  const map = new Map(Object.entries(table));
  return async ({ address }) => (map.has(address) ? { address, verified: map.get(address) === true } : { address: undefined, verified: false });
}

// Tests may inject a provider/reader. Production requires D1, account id and the scoped secret.
export function destinationProviderFor(env, { provider, reader, fetcher, clock } = {}) {
  if (typeof provider === 'function') return provider;
  try {
    if (reader) return createDestinationProvider({ reader, token: env?.[DESTINATION_TOKEN_ENV] });
    if (!env?.MAIL_DB) return null;
    return destinationProvisioning({ db: env.MAIL_DB, clock,
      client: cloudflareDestinations({ accountId: env.MAIL_DESTINATION_ACCOUNT_ID, token: env[DESTINATION_TOKEN_ENV], fetcher }) });
  }
  catch { return null; }
}
