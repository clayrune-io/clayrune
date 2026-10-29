// The type projection combines profile coverage with route_readiness executors.
// An existing browser grant must remain visible and revocable even if a route
// is no longer projected. This exception never authorizes a new grant.
export function readUnavailable(info, kind, liveGrant = false) {
  if (liveGrant) return '';
  const supported = (info?.picker || []).flatMap(t => t.variants || []).some(v =>
    v.route_id && v.transport !== 'mcp' && v.runtime?.purposes?.read_own &&
    (v.coverage || []).some(c => c.purpose === 'read_own' && c.account_kind === kind &&
      ['documented', 'claimed'].includes(c.status)));
  return supported ? '' : `Reading isn't available for ${info?.service?.label || info?.host || 'this service'} yet.`;
}
