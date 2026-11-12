// D1 is the deadline authority. Worker and D1 wall clocks need not be ordered.
// Evaluate inside the transaction, including its last guard; milliseconds
// without REAL rounding, never a Worker sample taken before queued SQL runs.
export const SQL_NOW = `(CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER))`;
export const CLOCK_TOLERANCE_MS = 5000;

// Only cross-clock consistency lower bounds use this helper. Expiry, quota
// bucket boundaries and signed-proof freshness must keep their strict checks.
// `lower` is trusted SQL, never request input; its default binds a Worker time.
export function sqlClockAtOrAfter(lower = '?') {
  return `${SQL_NOW}>=(${lower}-${CLOCK_TOLERANCE_MS})`;
}
