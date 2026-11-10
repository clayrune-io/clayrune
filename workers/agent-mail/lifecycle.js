// Remote lifecycle routes (MC-1166 7d-2d, contract section 5). One module, one path table:
// worker.js dispatches here and nowhere else. Two authority classes:
//   install-auth: Ed25519 by the install MAIL key, direction install-to-service-lifecycle;
//                 needs owner_verified, does NOT need active (status, renew, proof-arm, activate).
//   fence-auth:   a 32-byte fence token (64 lowercase hex chars) whose SHA-256 the flow stored at
//                 begin (decision D2); works with a locked or lost vault (cancel, revoke).
// Only a signed 200 is an acknowledgment (response-signing.js). Every failure is a closed code.
import { bytes, sha256, verify } from './signing.js';
import { readRequestBody } from './request-body.js';
import { armProof, invalidateProofs, proofStatus } from './delivery-proof.js';
import { signedReply } from './response-signing.js';
import { createCpClient } from './cp-client.js';
import { ID, HEX } from './enrollment-transcript.js';
import { OWNERSHIP_LEASE_MS } from './enrollment-grant.js';

export const LIFECYCLE_PREFIX = '/v1/mail/lifecycle';
export const LIFECYCLE_ROUTES = Object.freeze({ cancel: 'fence', revoke: 'fence', status: 'install',
  renew: 'install', 'proof-arm': 'install', activate: 'install' });
export const LIFECYCLE_BODY_LIMIT = 2048;
// Initial values: recovery must never starve the 200/day send/reconcile budget (worker.js, ledger.js).
export const LIFECYCLE_DAILY_LIMIT = 500;
export const FENCE_CREATE_DAILY_LIMIT = 1000;
const GEN = /^[1-9][0-9]{0,14}$/;
const NONCE = /^[a-f0-9]{32}$/;
const TOKEN = /^[a-f0-9]{64}$/;
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).sort().join(',') === [...keys].sort().join(',');
const gen = (g) => Number.isSafeInteger(g) && g > 0;

export const isLifecyclePath = (pathname) => pathname.startsWith(`${LIFECYCLE_PREFIX}/`)
  && Object.hasOwn(LIFECYCLE_ROUTES, pathname.slice(LIFECYCLE_PREFIX.length + 1));

// The CP client only exists when its fixed issuer is configured; without it renew answers
// cp_unavailable and nothing else changes. Tests inject `cp` directly.
export function cpFor(env, { cp, fetcher, clock }) {
  if (cp) return cp;
  if (!env.MAIL_CP_ISSUER) return null;
  try {
    return createCpClient({ issuer: env.MAIL_CP_ISSUER, key_id: env.MAIL_SERVICE_KEY_ID,
      privateKey: env.MAIL_SERVICE_PRIVATE_KEY, fetcher, clock });
  } catch { return null; }
}

async function fenced(ledger, install_id, generation) {
  return !!await ledger.first('SELECT 1 AS x FROM mail_lifecycle_fences WHERE install_id=? AND generation=?',
    install_id, generation);
}

// -- fence-auth ---------------------------------------------------------------------------------
async function cancel(c) {
  const v = c.value;
  if (!exact(v, ['flow_id', 'fence_token']) || !ID.test(v.flow_id) || !TOKEN.test(v.fence_token)) {
    return reply(400, { code: 'invalid_request' });
  }
  const digest = await sha256(bytes(v.fence_token));
  const flow = await c.ledger.first('SELECT fence_digest FROM mail_enrollment_flows WHERE flow_id=?', v.flow_id);
  if (flow) {
    if (!flow.fence_digest || flow.fence_digest !== digest) return c.refuse();
    if (!await c.charge()) return reply(429, { code: 'request_limit' });
    // One statement computes the assignment and writes the fence, so a commit cannot slip between
    // "no commit yet" and the tombstone: the commit trigger refuses once this row exists.
    await c.ledger.run(`INSERT OR IGNORE INTO mail_lifecycle_fences(flow_id,fence_digest,install_id,generation,created_ms)
      SELECT f.flow_id, f.fence_digest,
        CASE WHEN EXISTS (SELECT 1 FROM mail_enrollment_commits k WHERE k.flow_id=f.flow_id) THEN f.install_id END,
        CASE WHEN EXISTS (SELECT 1 FROM mail_enrollment_commits k WHERE k.flow_id=f.flow_id) THEN f.expected_generation+1 END, ?
      FROM mail_enrollment_flows f WHERE f.flow_id=? AND f.fence_digest=?`, c.now, v.flow_id, digest);
    await c.ledger.run(`UPDATE mail_enrollment_flows SET state='invalidated', code_digest='' WHERE flow_id=? AND state='pending'`,
      v.flow_id);
  } else {
    // Cancel before begin: first writer tombstones the flow id. Bounded so a stranger cannot grow it.
    const prior = await c.ledger.first('SELECT 1 AS x FROM mail_lifecycle_fences WHERE flow_id=?', v.flow_id);
    if (!prior && !await c.ledger.limit('lifecycle:fence-create', FENCE_CREATE_DAILY_LIMIT, c.now)) {
      return reply(429, { code: 'fence_limit' });
    }
    await c.ledger.run(`INSERT OR IGNORE INTO mail_lifecycle_fences(flow_id,fence_digest,install_id,generation,created_ms)
      VALUES(?,?,NULL,NULL,?)`, v.flow_id, digest, c.now);
  }
  const fence = await c.ledger.first('SELECT * FROM mail_lifecycle_fences WHERE flow_id=?', v.flow_id);
  // A different token on an existing tombstone is somebody else's flow: same answer as a bad token.
  if (!fence || fence.fence_digest !== digest) return c.refuse();
  return c.ok({ flow_id: v.flow_id, state: 'cancelled',
    assignment: fence.install_id ? { install_id: fence.install_id, generation: fence.generation } : null });
}

async function revoke(c) {
  const v = c.value;
  if (!exact(v, ['install_id', 'generation', 'fence_token']) || !ID.test(v.install_id) || !gen(v.generation)
      || !TOKEN.test(v.fence_token)) return reply(400, { code: 'invalid_request' });
  if (v.install_id !== c.id || String(v.generation) !== c.gen) return reply(400, { code: 'scope_mismatch' });
  const G = v.generation;
  const digest = await sha256(bytes(v.fence_token));
  // The flow that produced generation G had expected_generation G-1 and stored this digest.
  const flow = await c.ledger.first(`SELECT flow_id FROM mail_enrollment_flows
    WHERE install_id=? AND expected_generation=? AND fence_digest=?`, v.install_id, G - 1, digest);
  if (!flow) return c.refuse();
  if (!await c.charge()) return reply(429, { code: 'request_limit' });
  const reg = await c.ledger.registration(v.install_id);
  const fence = (assigned) => c.ledger.stmt(`INSERT OR IGNORE INTO mail_lifecycle_fences(flow_id,fence_digest,install_id,generation,created_ms)
    VALUES(?,?,?,?,?)`, flow.flow_id, digest, assigned ? v.install_id : null, assigned ? G : null, c.now);
  const settle = c.ledger.stmt(`UPDATE mail_enrollment_flows SET state='invalidated', code_digest=''
    WHERE flow_id=? AND state='pending'`, flow.flow_id);
  let state;
  if (reg && reg.generation === G) {
    // Worker deactivation is what stops delivery, so it is committed together with the fence and the
    // queued CP revoke; the CP call itself is retried from cron (drainCpRevokes).
    await c.ledger.db.batch([
      c.ledger.stmt('UPDATE registrations SET active=0, route_verified=0 WHERE install_id=? AND generation=?', v.install_id, G),
      c.ledger.stmt(`UPDATE mail_delivery_proofs SET state='invalidated', challenge=NULL
        WHERE install_id=? AND state IN ('armed','verified')`, v.install_id),
      fence(true), settle,
      c.ledger.stmt(`INSERT OR IGNORE INTO mail_lifecycle_cp_revokes(install_id,generation,created_ms) VALUES(?,?,?)`,
        v.install_id, G, c.now),
    ]);
    state = 'revoked';
  } else if (reg && reg.generation > G) {
    await c.ledger.db.batch([fence(true), settle]); // never touches the newer generation
    state = 'already_superseded';
  } else {
    await c.ledger.db.batch([fence(false), settle]); // a later commit for this flow refuses
    state = 'fenced';
  }
  return c.ok({ install_id: v.install_id, generation: G, state });
}

// -- install-auth -------------------------------------------------------------------------------
async function status(c) {
  if (!exact(c.value, ['install_id', 'generation'])) return reply(400, { code: 'invalid_request' });
  const { reg } = c;
  const inst = await c.ledger.first('SELECT ownership_lease_until AS lease FROM mail_installations WHERE install_id=?', reg.install_id);
  const fences = (await c.ledger.stmt(`SELECT flow_id, generation FROM mail_lifecycle_fences WHERE install_id=?
    ORDER BY created_ms DESC LIMIT 20`, reg.install_id).all()).results;
  const pending = await c.ledger.first('SELECT 1 AS x FROM mail_lifecycle_cp_revokes WHERE install_id=? AND done=0', reg.install_id);
  return c.ok({ install_id: reg.install_id, generation: reg.generation,
    registration: { active: reg.active === 1, owner_verified: reg.owner_verified === 1, route_verified: reg.route_verified === 1 },
    lease_until_ms: inst?.lease ?? null, proof: await proofStatus(c.ledger, reg.install_id, reg.generation, c.now),
    fences, cp_revoke_pending: !!pending });
}

async function renew(c) {
  if (!exact(c.value, ['install_id', 'generation'])) return reply(400, { code: 'invalid_request' });
  const { reg } = c;
  if (await fenced(c.ledger, reg.install_id, reg.generation)) return reply(409, { code: 'fenced' });
  const inst = await c.ledger.first('SELECT generation FROM mail_installations WHERE install_id=?', reg.install_id);
  if (!inst || inst.generation !== reg.generation) return reply(409, { code: 'generation_mismatch' });
  if (!c.cp) return reply(503, { code: 'cp_unavailable' });
  const r = await c.cp.lease({ install_id: reg.install_id, generation: reg.generation });
  if (!r.ok) return reply(503, { code: r.code });
  const b = r.body;
  if (!exact(b, ['checked_at_ms', 'record_version', 'lease_until_ms']) || !Number.isSafeInteger(b.checked_at_ms)
      || b.lease_until_ms !== b.checked_at_ms + OWNERSHIP_LEASE_MS || b.checked_at_ms > c.now
      || typeof b.record_version !== 'string' || !HEX.test(b.record_version)) return reply(502, { code: 'bad_cp_response' });
  // Forward-only: a stale or replayed CP answer can never lower the lease.
  await c.ledger.run(`UPDATE mail_installations SET ownership_checked_at=?, ownership_lease_until=?, record_version=?
    WHERE install_id=? AND generation=? AND COALESCE(ownership_checked_at,0) < ?`,
  b.checked_at_ms, b.lease_until_ms, b.record_version, reg.install_id, reg.generation, b.checked_at_ms);
  const after = await c.ledger.first('SELECT ownership_lease_until AS lease FROM mail_installations WHERE install_id=?', reg.install_id);
  if (!after || after.lease <= c.now) return reply(409, { code: 'lease_expired' });
  return c.ok({ install_id: reg.install_id, generation: reg.generation, expires_at_ms: after.lease });
}

async function proofArm(c) {
  if (!exact(c.value, ['install_id', 'generation', 'challenge', 'expires_ms'])) return reply(400, { code: 'invalid_request' });
  const { reg } = c;
  if (await fenced(c.ledger, reg.install_id, reg.generation)) return reply(409, { code: 'fenced' });
  const r = await armProof(c.ledger, reg, { challenge: c.value.challenge, expires_ms: c.value.expires_ms }, c.now);
  if (!r.ok) return reply(r.code === 'proof_consumed' ? 409 : 400, { code: r.code });
  return c.ok({ install_id: reg.install_id, generation: reg.generation, state: 'armed', expires_ms: c.value.expires_ms });
}

async function activate(c) {
  const v = c.value; const { reg } = c;
  if (!exact(v, ['install_id', 'generation', 'receipt_digest']) || typeof v.receipt_digest !== 'string'
      || !HEX.test(v.receipt_digest)) return reply(400, { code: 'invalid_request' });
  if (await fenced(c.ledger, reg.install_id, reg.generation)) return reply(409, { code: 'fenced' });
  // One D1 transaction: the registration flips only against a current verified proof for exactly
  // this generation and digest, and that same proof is consumed in the same breath. A retry after a
  // lost ack finds registration active + proof consumed with the same digest and answers identically.
  await c.ledger.db.batch([
    c.ledger.stmt(`UPDATE registrations SET active=1, route_verified=1, route_generation=generation
      WHERE install_id=? AND generation=? AND owner_verified=1 AND active=0
      AND EXISTS (SELECT 1 FROM mail_delivery_proofs p WHERE p.install_id=? AND p.generation=? AND p.state='verified'
        AND p.receipt_digest=? AND p.expires_ms>?)
      AND NOT EXISTS (SELECT 1 FROM mail_lifecycle_fences f WHERE f.install_id=? AND f.generation=?)`,
    reg.install_id, reg.generation, reg.install_id, reg.generation, v.receipt_digest, c.now, reg.install_id, reg.generation),
    c.ledger.stmt(`UPDATE mail_delivery_proofs SET state='consumed', challenge=NULL
      WHERE install_id=? AND generation=? AND state='verified' AND receipt_digest=?
      AND EXISTS (SELECT 1 FROM registrations r WHERE r.install_id=? AND r.generation=? AND r.active=1 AND r.route_verified=1)`,
    reg.install_id, reg.generation, v.receipt_digest, reg.install_id, reg.generation),
  ]);
  const done = await c.ledger.first(`SELECT 1 AS x FROM registrations r JOIN mail_delivery_proofs p
    ON p.install_id=r.install_id AND p.generation=r.generation
    WHERE r.install_id=? AND r.generation=? AND r.active=1 AND r.route_verified=1 AND r.route_generation=r.generation
    AND p.state='consumed' AND p.receipt_digest=?`, reg.install_id, reg.generation, v.receipt_digest);
  if (!done) return reply(409, { code: 'proof_required' });
  return c.ok({ install_id: reg.install_id, generation: reg.generation, state: 'activated', receipt_digest: v.receipt_digest });
}

const HANDLERS = { cancel, revoke, status, renew, 'proof-arm': proofArm, activate };

// `request` is a POST to an exact lifecycle path (worker.js checked method/path/query).
export async function lifecycle({ request, env, ledger, config, cp, clock, bodyTimeoutMs }) {
  if (env.MAIL_LIFECYCLE_ENABLED !== 'true') return reply(503, { code: 'lifecycle_disabled' });
  const url = new URL(request.url);
  const name = url.pathname.slice(LIFECYCLE_PREFIX.length + 1);
  const now = clock();
  // Invalid traffic shares one bounded bucket; it is charged only on refusal, never to an install.
  const refuse = async () => await ledger.limit('lifecycle:unauthenticated', 10_000, now, 60_000)
    ? reply(401, { code: 'unauthorized' }) : reply(429, { code: 'abuse_limit' });
  const id = request.headers.get('x-mail-install-id');
  const g = request.headers.get('x-mail-generation');
  const nonce = request.headers.get('x-mail-nonce');
  if (!ID.test(id ?? '') || !GEN.test(g ?? '') || !NONCE.test(nonce ?? '')) return await refuse();
  let body;
  try { body = await readRequestBody(request.body, { timeoutMs: bodyTimeoutMs, limit: LIFECYCLE_BODY_LIMIT }); }
  catch (error) {
    return error.message === 'body_timeout' ? reply(408, { code: 'body_timeout' }) : reply(413, { code: 'body_limit' });
  }
  // Charged only AFTER authentication, so a stranger who knows an install id cannot spend its budget.
  const c = { ledger, config, cp, now, id, gen: g, nonce, name, body, path: url.pathname, refuse,
    charge: () => ledger.limit(`lifecycle:${id}`, LIFECYCLE_DAILY_LIMIT, now),
    ok: (value) => signedReply({ config, install_id: id, generation: g, path: url.pathname, nonce, value, clock }) };
  if (LIFECYCLE_ROUTES[name] === 'install') {
    const reg = await ledger.registration(id);
    if (!reg?.owner_verified) return await refuse();
    if (!await verify({ request, body, publicKey: reg.public_key, key_id: reg.key_id,
      direction: 'install-to-service-lifecycle', install_id: reg.install_id, generation: reg.generation,
      claimNonce: (m) => ledger.claimNonce(m), clock })) return await refuse();
    c.reg = reg;
    if (!await c.charge()) return reply(429, { code: 'request_limit' });
  }
  try { c.value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
  catch { return reply(400, { code: 'invalid_json' }); }
  if (c.reg) {
    if (!c.value || typeof c.value !== 'object' || c.value.install_id !== c.reg.install_id
        || c.value.generation !== c.reg.generation) return reply(409, { code: 'generation_mismatch' });
  }
  const response = await HANDLERS[name](c);
  return response;
}

// Worker -> CP revoke, retried from cron. Deactivation already happened locally, so a failure here only
// leaves a CP binding that is bounded by the 60 s capability life and the denial of Worker delivery.
export async function drainCpRevokes({ ledger, cp, limit = 20 }) {
  if (!cp) return { drained: 0, pending: 0 };
  const rows = (await ledger.stmt('SELECT install_id, generation FROM mail_lifecycle_cp_revokes WHERE done=0 LIMIT ?', limit).all()).results;
  let drained = 0;
  for (const row of rows) {
    const r = await cp.revoke({ install_id: row.install_id, generation: row.generation });
    if (r.ok) {
      await ledger.run('UPDATE mail_lifecycle_cp_revokes SET done=1 WHERE install_id=? AND generation=?', row.install_id, row.generation);
      drained += 1;
    }
  }
  return { drained, pending: rows.length - drained };
}
