// The type projection combines profile coverage with route_readiness executors.
// A browser domain grant alone does not make a Desk read route available.
export function readUnavailable(info, kind) {
  const supported = (info?.picker || []).flatMap(t => t.variants || []).some(v =>
    v.route_id && v.transport !== 'mcp' && v.runtime?.purposes?.read_own &&
    (v.coverage || []).some(c => c.purpose === 'read_own' && c.account_kind === kind &&
      ['documented', 'claimed'].includes(c.status)));
  return supported ? '' : `Reading isn't available for ${info?.service?.label || info?.host || 'this service'} yet.`;
}
