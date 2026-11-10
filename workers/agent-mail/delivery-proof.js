// Worker side of proof of machine custody (MC-1166 7d-2c, contract section 4). Pure module: no HTTP
// entry, no scheduler hook, no flag. The lifecycle routes (7d-2d) call armProof/invalidateProofs and
// the scheduler calls sendProof; nothing here can activate a registration or deliver mail.
import { bytes, sha256, sign, randomId, unbase64url } from './signing.js';
import { readBounded } from './ingress.js';
import { MACHINE_HEADER } from './edge-policy.js';
import { ROUTE_PATH } from './delivery-capability.js';
import { origin, ID, HEX } from './enrollment-transcript.js';

export const PROOF_DIRECTION = 'service-to-install-proof';
export const RECEIPT_PURPOSE = 'clayrune-agent-mail-proof';
export const PROOF_TTL_MS = 600_000;
const MAX_RECEIPT_BYTES = 2048;
const HEX32 = /^[a-f0-9]{32}$/;
const GEN = /^[1-9][0-9]{0,14}$/;
const RECEIPT_KEYS = ['challenge', 'custody', 'generation', 'install_id', 'proof_id', 'signature'];
const fail = (code, extra = {}) => ({ ok: false, code, ...extra });

// Every element is an ASCII string: JSON.stringify and Python json.dumps(separators=(',', ':'))
// then produce identical bytes (cross-language gate: tests/test_agent_mail_u7d2_proof.py).
export function receiptArray({ install_id, generation, challenge, service_nonce, route_origin, manifest_sequence, proof_id }) {
  return [RECEIPT_PURPOSE, '1', install_id, String(generation), challenge, service_nonce, route_origin,
    String(manifest_sequence), proof_id];
}
export const receiptBytes = (fields) => bytes(JSON.stringify(receiptArray(fields)));

// Exact key order is part of the contract (section 4.3); Flask compares field-by-field.
export function proofBody({ challenge, reg, pin, owner_address_sha256, service_nonce }) {
  return bytes(JSON.stringify({ kind: 'enrollment-proof', version: '1', challenge, install_id: reg.install_id,
    generation: String(reg.generation), manifest_sequence: String(pin.manifest_sequence),
    service_key_id: pin.service_key_id, service_public_key_sha256: pin.service_public_key_sha256,
    mail_key_id: reg.key_id, route_origin: reg.route_origin, owner_address_sha256, service_nonce }));
}

// Called by the lifecycle proof-arm route (7d-2d) after install-auth. Replaces and invalidates any
// earlier armed/verified row for the same install+generation; a consumed row is final.
export async function armProof(ledger, reg, { challenge, expires_ms }, now) {
  if (!reg?.owner_verified) return fail('owner_not_verified');
  if (typeof challenge !== 'string' || !HEX.test(challenge) || !Number.isSafeInteger(expires_ms)
      || expires_ms <= now || expires_ms > now + PROOF_TTL_MS) return fail('bad_proof_arm');
  const r = await ledger.run(`INSERT INTO mail_delivery_proofs(install_id,generation,challenge,challenge_sha256,
    state,armed_ms,armed_expires_ms) VALUES(?,?,?,?,'armed',?,?) ON CONFLICT(install_id,generation) DO UPDATE SET
    challenge=excluded.challenge, challenge_sha256=excluded.challenge_sha256, state='armed',
    armed_ms=excluded.armed_ms, armed_expires_ms=excluded.armed_expires_ms, receipt_digest=NULL,
    verified_ms=NULL, expires_ms=NULL WHERE state != 'consumed'`,
  reg.install_id, reg.generation, challenge, await sha256(bytes(challenge)), now, expires_ms);
  return r.meta.changes === 1 ? { ok: true } : fail('proof_consumed');
}

export async function invalidateProofs(ledger, install_id) {
  await ledger.run(`UPDATE mail_delivery_proofs SET state='invalidated', challenge=NULL
    WHERE install_id=? AND state IN ('armed','verified')`, install_id);
}

export async function proofStatus(ledger, install_id, generation, now) {
  const p = await ledger.first('SELECT state,receipt_digest,expires_ms,armed_expires_ms FROM mail_delivery_proofs '
    + 'WHERE install_id=? AND generation=?', install_id, generation);
  if (!p) return { state: 'none' };
  // Expiry is stored, but a stale 'verified' row must never read as current.
  const live = p.state === 'armed' ? p.armed_expires_ms > now : p.state !== 'verified' || p.expires_ms > now;
  return live ? { state: p.state, receipt_digest: p.receipt_digest, expires_ms: p.expires_ms }
    : { state: 'invalidated', receipt_digest: null, expires_ms: p.expires_ms };
}

function claimsOf(token) {
  try {
    const part = token.split('.')[1];
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(unbase64url(part)));
  } catch { return null; }
}

// `cp` is a createCpClient() instance (cp-client.js); `config.servicePin` is the reviewed
// { manifest_sequence, service_key_id, service_public_key_sha256 } (same object the enrollment core pins).
// Never throws secrets: every outcome is a closed code. Retried by the scheduler with backoff.
export async function sendProof({ ledger, config, cp, reg, fetcher = fetch, now, clock = () => now }) {
  if (!reg?.owner_verified || reg.active) return fail('not_pending');
  const pin = config.servicePin;
  if (!pin || !GEN.test(String(pin.manifest_sequence)) || !ID.test(pin.service_key_id ?? '')
      || !HEX.test(pin.service_public_key_sha256 ?? '')) return fail('service_pin_required');
  if (!origin(reg.route_origin) || reg.route_path !== ROUTE_PATH) return fail('route_invalid');
  const armed = await ledger.first(`SELECT * FROM mail_delivery_proofs WHERE install_id=? AND generation=?
    AND state='armed' AND armed_expires_ms>?`, reg.install_id, reg.generation, now);
  if (!armed) return fail('proof_not_armed');
  const body = proofBody({ challenge: armed.challenge, reg, pin, service_nonce: randomId(),
    owner_address_sha256: await sha256(bytes(reg.owner_address)) });
  const sent = JSON.parse(new TextDecoder().decode(body));
  const headers = await sign({ privateKey: config.privateKey, key_id: config.keyId, install_id: reg.install_id,
    generation: reg.generation, direction: PROOF_DIRECTION, path: ROUTE_PATH, body, now });
  const issued = await cp.issue({ install_id: reg.install_id, generation: reg.generation, mode: 'proof',
    req_digest: headers.get('x-mail-digest'), mail_nonce: headers.get('x-mail-nonce'), mail_key_id: reg.key_id });
  if (!issued.ok) return fail(issued.code);
  const token = issued.body?.capability;
  const claims = typeof token === 'string' ? claimsOf(token) : null;
  if (!claims) return fail('bad_capability');
  // Proof may use an unverified route, so the registration row's route must equal the one CP derived
  // from its own device row before anything is sent. The edge re-verifies the token itself.
  if (claims.route_origin !== reg.route_origin) return fail('route_mismatch');
  if (claims.mode !== 'proof' || claims.install_id !== reg.install_id
      || claims.generation !== String(reg.generation)) return fail('bad_capability');
  headers.set(MACHINE_HEADER, token);
  let response;
  try {
    response = await fetcher(reg.route_origin + ROUTE_PATH, { method: 'POST', headers, body, redirect: 'manual',
      signal: AbortSignal.timeout(10_000) });
  } catch { return fail('edge_unreachable'); }
  let value;
  try {
    const raw = response.status === 200 || response.status === 503 ? await readBounded(response.body, MAX_RECEIPT_BYTES) : null;
    value = raw && JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
  } catch { value = null; }
  if (response.status === 503 && value?.error === 'vault_locked') return fail('vault_locked', { retry: true });
  // A redirect, login page, generic 2xx or any non-receipt is no custody (replay.js:34-38 rule).
  if (response.status !== 200 || !value || typeof value !== 'object' || Array.isArray(value)) return fail('edge_unreachable');
  if (Object.keys(value).sort().join(',') !== RECEIPT_KEYS.join(',') || value.custody !== 'proof'
      || value.install_id !== reg.install_id || value.generation !== Number(reg.generation)
      || value.challenge !== armed.challenge || typeof value.proof_id !== 'string' || !HEX32.test(value.proof_id)
      || typeof value.signature !== 'string') return fail('bad_receipt');
  const fields = { install_id: reg.install_id, generation: reg.generation, challenge: armed.challenge,
    service_nonce: sent.service_nonce, route_origin: reg.route_origin, manifest_sequence: pin.manifest_sequence,
    proof_id: value.proof_id };
  const signed = receiptBytes(fields);
  try {
    const raw = unbase64url(reg.public_key);
    const key = await crypto.subtle.importKey('raw', raw, 'Ed25519', false, ['verify']);
    const sig = unbase64url(value.signature);
    if (raw.length !== 32 || sig.length !== 64 || !await crypto.subtle.verify('Ed25519', key, sig, signed)) {
      return fail('bad_receipt');
    }
  } catch { return fail('bad_receipt'); }
  const verified = clock();
  const lease = await ledger.first('SELECT ownership_lease_until AS lease FROM mail_installations WHERE install_id=?',
    reg.install_id);
  const expires = Math.min(verified + PROOF_TTL_MS, lease?.lease ?? Infinity);
  if (expires <= verified) return fail('ownership_lease_expired');
  const digest = await sha256(signed);
  const r = await ledger.run(`UPDATE mail_delivery_proofs SET state='verified', receipt_digest=?, verified_ms=?,
    expires_ms=?, challenge=NULL WHERE install_id=? AND generation=? AND state='armed' AND challenge_sha256=?`,
  digest, verified, expires, reg.install_id, reg.generation, await sha256(bytes(armed.challenge)));
  return r.meta.changes === 1 ? { ok: true, receipt_digest: digest, expires_ms: expires } : fail('proof_raced');
}
