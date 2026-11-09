// Version 2 transports only a hold, never stripped text or other body bytes.
// Authentication/correlation already passed; the ordinary replay signature binds
// every field just as for version 1. Never copy arbitrary content properties.
export function heldEnvelope(p) {
  return { version: 2, event_id: p.event_id, install_id: p.install_id,
    generation: p.generation, thread_id: p.thread_id,
    incoming_message_id: p.incoming_message_id, reason: 'empty_body',
    body_digest: p.body_digest, auth: p.auth,
    received_at: p.received_at, expires_at: p.expires_at };
}
