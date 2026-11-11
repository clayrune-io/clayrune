// Destination provider seam (MC-1166 7d-2e-2, decision E4a). `Enrollment.complete` needs an
// independent, read-only answer to "is this exact address a verified destination?" and only the
// shape of that answer is defined here: { address, verified } with `verified === true` strictly.
//
// SEAM ONLY. The real reader (a Cloudflare Email Routing destination-address read made with a
// read-only token held as the Worker secret MAIL_DESTINATION_READ_TOKEN) is NOT shipped: no token,
// no live call, no fetch of any kind lives in this file. Until a reader is injected,
// `destinationProviderFor` returns null and the enrollment HTTP surface answers
// 503 enrollment_config_required, so nothing can enroll live by accident.
export const DESTINATION_TOKEN_ENV = 'MAIL_DESTINATION_READ_TOKEN';

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

// Production resolution order: an injected provider (tests/dev) wins; otherwise a reader (none ships)
// plus the token secret. Anything else is "not configured" (null), never a permissive default.
export function destinationProviderFor(env, { provider, reader } = {}) {
  if (typeof provider === 'function') return provider;
  try { return createDestinationProvider({ reader, token: env?.[DESTINATION_TOKEN_ENV] }); }
  catch { return null; }
}
