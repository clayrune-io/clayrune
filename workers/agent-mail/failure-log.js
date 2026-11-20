// What a top-level catch may log about the exception it swallows. Never the
// exception text: a storage error may include tokens or SQL inputs. The name
// (TypeError, Error, ...) is set by code, not by input, and separates a code
// bug or a missing binding from a classified storage fault. The message is kept
// only when it is exactly one of the closed codes these modules throw on purpose.
const CODES = new Set(['storage_unavailable', 'service_unavailable', 'invalid_request', 'thread_lookup_failed',
  'ingress_limit', 'queue_full', 'message_id_conflict', 'stale_generation', 'connection_superseded',
  'registration_frozen', 'registration_inactive', 'fenced', 'unknown_thread', 'event_expired']);

/** failureDetail(error) -> 'name=<Name>' or 'name=<Name> code=<closed code>'. Never throws. */
export function failureDetail(error) {
  try {
    const name = typeof error?.name === 'string' && /^[A-Z][A-Za-z0-9]{0,39}$/.test(error.name) ? error.name : 'unknown';
    return error instanceof Error && CODES.has(error.message) ? `name=${name} code=${error.message}` : `name=${name}`;
  } catch { return 'name=unknown'; }
}
