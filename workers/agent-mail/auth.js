// Inbound authentication decision for agent mail (D6=A). Pure library code: no
// I/O of its own, no Cloudflare bindings, no Worker entry point (unit 2 owns
// email()). Ron's hardening rule 1: a message that fails ANY check here is
// DROPPED -- never forwarded, queued or shown to a model. All that survives is
// rejectionRecord(): time, reason code, auth result words. No body, subject,
// address or token.
//
//   decide(meta, ownerRecord, tokenLookup) -> Promise<
//     { accept: true,  thread, evidence } |
//     { accept: false, reason_code, evidence? } >
//
// meta         { rcptTo: envelope recipient string,
//                headers: ordered header list (see headers.js),
//                now?: epoch ms (tests) }
// ownerRecord  { active: true, install_id, generation, owner_address }  -- the
//              registered, verified owner for the install that rcptTo belongs to
// tokenLookup  (token: string) => thread | null  (sync or async). The caller
//              owns hashing/verifier storage; this module never sees a verifier.
// thread       { thread_id, install_id, owner_generation, expires_at (epoch ms),
//                revoked: boolean, message_ids?: string[] }  -- ignored legacy field
//
// decide never throws; any internal failure is a drop (fail closed).

import { allOf, normalizeHeaders } from './headers.js';
import { parseAddrSpec, parseMailbox, sameMailbox } from './mailbox.js';
import { evaluateIngress } from './authres.js';

export const REASONS = Object.freeze([
  'bad_input', 'owner_inactive', 'owner_invalid', 'token_missing', 'from_invalid',
  'from_not_owner', 'untrusted_ingress', 'ingress_ambiguous', 'dmarc_not_pass',
  'auth_domain_mismatch', 'dmarc_unaligned', 'lookup_error', 'token_unknown',
  'thread_invalid', 'token_revoked', 'token_expired', 'thread_owner_mismatch',
  'owner_generation_mismatch',
  // Historical records may still carry these; decide() no longer emits them.
  'message_id_missing', 'message_id_mismatch',
  'internal_error',
]);

// Reply address shape (the spec leaves syntax to D2; this is our pick):
//   <install>+<token>@<domain>   token = 26-64 chars of [a-z0-9]
// 26 base32 chars = 130 bits. The lookup is the real check; this only bounds work.
const TOKEN_LOCAL_RE = /^[a-z0-9][a-z0-9._-]{0,63}\+([a-z0-9]{26,64})$/;

function reject(reason_code, evidence) {
  return evidence ? { accept: false, reason_code, evidence } : { accept: false, reason_code };
}

function extractToken(rcptTo) {
  if (typeof rcptTo !== 'string') return null;
  const addr = parseAddrSpec(rcptTo.trim());
  if (!addr) return null;
  const m = TOKEN_LOCAL_RE.exec(addr.local.toLowerCase());
  return m ? m[1] : null;
}

async function run(meta, ownerRecord, tokenLookup) {
  if (!meta || typeof meta !== 'object' || typeof tokenLookup !== 'function') return reject('bad_input');
  const headers = normalizeHeaders(meta.headers);
  if (!headers) return reject('bad_input');
  const now = Number.isFinite(meta.now) ? meta.now : Date.now();

  // Owner (active, bound to a generation, with a parseable verified address).
  if (!ownerRecord || typeof ownerRecord !== 'object' || ownerRecord.active !== true) return reject('owner_inactive');
  const owner = parseAddrSpec(ownerRecord.owner_address);
  if (!owner || typeof ownerRecord.install_id !== 'string' || ownerRecord.install_id === ''
      || ownerRecord.generation === undefined || ownerRecord.generation === null) {
    return reject('owner_invalid');
  }

  // Cheap checks first, so a stranger costs no lookup.
  const token = extractToken(meta.rcptTo);
  if (!token) return reject('token_missing');

  const froms = allOf(headers, 'from');
  if (froms.length !== 1) return reject('from_invalid'); // two From fields is a classic spoof
  const from = parseMailbox(froms[0].value);
  if (!from) return reject('from_invalid');
  if (!sameMailbox(from, owner)) return reject('from_not_owner');

  const ingress = evaluateIngress(headers, from.domain);
  if (!ingress.ok) return reject(ingress.reason_code, ingress.evidence);

  // Thread token.
  let thread;
  try {
    thread = await tokenLookup(token);
  } catch {
    return reject('lookup_error', ingress.evidence);
  }
  if (!thread) return reject('token_unknown', ingress.evidence);
  if (typeof thread !== 'object' || typeof thread.install_id !== 'string') {
    return reject('thread_invalid', ingress.evidence);
  }
  if (thread.revoked !== false) return reject('token_revoked', ingress.evidence); // missing flag = revoked
  if (!Number.isFinite(thread.expires_at) || now >= thread.expires_at) return reject('token_expired', ingress.evidence);
  if (thread.install_id !== ownerRecord.install_id) return reject('thread_owner_mismatch', ingress.evidence);
  if (thread.owner_generation !== ownerRecord.generation) return reject('owner_generation_mismatch', ingress.evidence);

  // Verified owner From + trusted aligned DMARC + the secret thread token are
  // the proof. Provider-assigned Message-IDs and reply headers are only hints.

  return { accept: true, thread, evidence: ingress.evidence };
}

export async function decide(meta, ownerRecord, tokenLookup) {
  try {
    return await run(meta, ownerRecord, tokenLookup);
  } catch {
    return reject('internal_error');
  }
}

/**
 * The only thing kept about a dropped message. Whitelisted fields; a decision
 * object that somehow carries more is cut down to this.
 */
export function rejectionRecord(decision, now = Date.now()) {
  const reason = decision && REASONS.includes(decision.reason_code) ? decision.reason_code : 'internal_error';
  const rec = { at: new Date(now).toISOString(), reason_code: reason };
  const ev = decision && decision.evidence;
  if (ev && typeof ev === 'object') {
    const word = /^[a-z+]{1,40}$/;
    rec.evidence = {};
    for (const k of ['ingress', 'spf', 'dkim', 'dmarc', 'aligned']) {
      if (typeof ev[k] === 'string' && word.test(ev[k])) rec.evidence[k] = ev[k];
    }
  }
  return rec;
}
