// Enrollment core, deliberately NOT a public HTTP handler. Unit 7b must supply
// the authenticated originating human-flow guard. No dashboard token is a grant.
import { bytes, randomId, sha256 } from './signing.js';
import { canonicalR, fieldsR, FIELDS, ID, HEX, origin, verifyProof } from './enrollment-transcript.js';
import { CHALLENGE_TTL, randomHex, flowDigest, codeDigest } from './enrollment-challenge.js';
import { verifyGrant, OWNERSHIP_LEASE_MS } from './enrollment-grant.js';
import { parseMailbox } from './mailbox.js';
import { reserveIdentity } from './enrollment-reservations.js';

export class Enrollment {
  constructor({ db, enabled = false, serviceOrigin, servicePin, trust, codeKey, provider,
    clock = Date.now, capacity = 200 }) {
    Object.assign(this, { db, enabled, serviceOrigin, servicePin, trust, codeKey, provider, clock, capacity });
  }
  guard() {
    if (this.enabled !== true) throw new Error('enrollment_disabled');
    if (!origin(this.serviceOrigin) || !Number.isSafeInteger(this.capacity) || this.capacity < 1
        || this.capacity > 200 || typeof this.provider !== 'function') throw new Error('enrollment_config_required');
  }
  stmt(sql, ...args) { return this.db.prepare(sql).bind(...args); }
  async first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  checkServicePin(value) {
    if (!this.servicePin || value.service_origin && value.service_origin !== this.serviceOrigin
      || ['manifest_sequence', 'service_key_id', 'service_public_key_sha256']
        .some((k) => value[k] !== this.servicePin[k])) throw new Error('service_pin_mismatch');
  }
  async fenced(flowId) {
    return !!await this.first('SELECT 1 AS x FROM mail_lifecycle_fences WHERE flow_id=?', flowId);
  }
  async flow(flowId, secret) {
    if (!ID.test(flowId)) throw new Error('bad_flow');
    const f = await this.first('SELECT * FROM mail_enrollment_flows WHERE flow_id=?', flowId);
    if (!f || f.flow_digest !== await flowDigest(secret)) throw new Error('human_flow_required');
    return f;
  }
  // `value` comes from the human ceremony; claimed route fields remain mere
  // hints until the CP has independently cross-checked and signed this exact R.
  async begin(value, secret) {
    this.guard();
    const allowed = ['flow_id', 'owner_address', 'device_id', 'device_public_key', 'mail_key_id',
      'mail_public_key', 'tunnel_uuid', 'route_origin', 'manifest_sequence', 'service_key_id', 'service_public_key_sha256'];
    // fence_digest is optional (7d-2d): SHA-256 of the install's local fence token, so a later
    // lifecycle cancel/revoke can authenticate without the vault. Absent = a flow nobody can cancel.
    const hasFence = !!value && typeof value === 'object' && 'fence_digest' in value;
    if (!value || Object.keys(value).sort().join(',') !== (hasFence ? [...allowed, 'fence_digest'] : allowed).sort().join(',')
        || (hasFence && !HEX.test(value.fence_digest)) || !ID.test(value.flow_id) || !ID.test(value.device_id)
        || parseMailbox(value.owner_address)?.address !== value.owner_address
        || value.owner_address.length > 254) throw new Error('bad_enrollment');
    if (await this.fenced(value.flow_id)) throw new Error('flow_fenced');
    const fenceDigest = hasFence ? value.fence_digest : '';
    const flowHash = await flowDigest(secret);
    const ownerHash = await sha256(bytes(value.owner_address));
    this.checkServicePin(value);
    // Validate every hint BEFORE reserving durable device/capacity state.
    const check = { ...value, purpose: 'clayrune-mail-enrollment', version: '1', service_origin: this.serviceOrigin,
      challenge_id: 'preflight', challenge_nonce: '0'.repeat(64), expires_ms: String(this.clock() + CHALLENGE_TTL),
      install_id: 'preflight', expected_generation: '0', owner_address_sha256: ownerHash,
      route_path: '/api/agent-mail/ingress' };
    canonicalR(FIELDS.map((k) => check[k]));
    const previous = await this.first('SELECT * FROM mail_enrollment_flows WHERE flow_id=?', value.flow_id);
    if (previous) {
      const p = await this.flow(value.flow_id, secret); const m = fieldsR(JSON.parse(p.transcript));
      if (p.fence_digest !== fenceDigest || p.owner_address !== value.owner_address || allowed.filter((k) => !['owner_address', 'flow_id'].includes(k))
        .some((k) => m[k] !== value[k])) throw new Error('flow_conflict');
      return { transcript: JSON.parse(p.transcript), state: p.state }; // No reissue of the code.
    }
    const now = this.clock();
    const install = await reserveIdentity(this.db, value.device_id, now, this.capacity);
    const expiresAt = Math.min(now + CHALLENGE_TTL, install.expires_at ?? Infinity);
    const m = { ...value, purpose: 'clayrune-mail-enrollment', version: '1', service_origin: this.serviceOrigin,
      challenge_id: randomId(), challenge_nonce: randomHex(32), expires_ms: String(expiresAt),
      install_id: install.install_id, expected_generation: String(install.generation),
      owner_address_sha256: ownerHash, route_path: '/api/agent-mail/ingress' };
    const r = FIELDS.map((k) => m[k]); const input = canonicalR(r);
    const code = randomHex(16);
    const digest = await codeDigest(this.codeKey, value.flow_id, m.challenge_id, code);
    // The fence check is inside the INSERT: a cancel tombstone that lands after the check above still wins.
    const inserted = await this.db.batch([
      this.stmt(`UPDATE mail_enrollment_flows SET state='invalidated',code_digest=''
        WHERE install_id=? AND state='pending' AND expires_at<=?`, install.install_id, now),
      this.stmt(`INSERT INTO mail_enrollment_flows(flow_id,flow_digest,install_id,expected_generation,
        challenge_id,transcript,r_digest,owner_address,code_digest,expires_at,fence_digest)
        SELECT ?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM mail_lifecycle_fences WHERE flow_id=?)`,
      value.flow_id, flowHash, install.install_id, install.generation, m.challenge_id,
      new TextDecoder().decode(input), await sha256(input), value.owner_address, digest, expiresAt, fenceDigest,
      value.flow_id),
    ]);
    if (inserted[1].meta.changes !== 1) throw new Error('flow_fenced');
    // Adapter must send this code to ONLY the stored owner, under its own bounded
    // provisioning budget. Returning it here is not an agent-facing API.
    return { transcript: r, code, state: 'pending' };
  }
  async confirm(flowId, secret, code) {
    this.guard(); const f = await this.flow(flowId, secret);
    if (await this.fenced(flowId)) throw new Error('flow_fenced');
    if (f.state !== 'pending' || f.expires_at <= this.clock()) throw new Error('challenge_unavailable');
    const candidate = typeof code === 'string' && /^[a-f0-9]{32}$/.test(code)
      ? await codeDigest(this.codeKey, flowId, f.challenge_id, code) : '';
    // One conditional SQL update serializes the fifth failure with a concurrent
    // correct answer; a used code cannot be retried or clear a failure counter.
    await this.stmt(`UPDATE mail_enrollment_flows SET
      code_confirmed=CASE WHEN code_digest=? THEN 1 ELSE 0 END,
      code_failures=code_failures+CASE WHEN code_digest=? THEN 0 ELSE 1 END,
      state=CASE WHEN code_digest!=? AND code_failures>=4 THEN 'invalidated' ELSE state END,
      code_digest=CASE WHEN code_digest=? OR code_failures>=4 THEN '' ELSE code_digest END
      WHERE flow_id=? AND state='pending' AND code_confirmed=0 AND expires_at>?`,
    candidate, candidate, candidate, candidate, flowId, this.clock()).run();
    const updated = await this.flow(flowId, secret);
    return updated.state === 'pending' && updated.code_confirmed === 1;
  }
  async complete({ flowId, secret, token, deviceSignature, mailSignature }) {
    this.guard(); const f = await this.flow(flowId, secret); const r = JSON.parse(f.transcript);
    const m = fieldsR(r); const input = canonicalR(r);
    if (!await verifyProof(m.device_public_key, deviceSignature, input)
      || !await verifyProof(m.mail_public_key, mailSignature, input)) throw new Error('key_proof_required');
    if (typeof token !== 'string' || token.length > 8192) throw new Error('bad_grant');
    const tokenDigest = await sha256(bytes(token));
    const prior = await this.first('SELECT * FROM mail_enrollment_commits WHERE flow_id=?', flowId);
    if (prior) {
      if (prior.token_digest !== tokenDigest || prior.r_digest !== f.r_digest) throw new Error('grant_conflict');
      return this.result(f); // Exact retry reports the original generation, never a newer one.
    }
    // Historical exact retries above are nonrenewing. New commits must still
    // belong to CURRENT trusted service policy, including after a restart.
    this.checkServicePin(m);
    if (await this.fenced(flowId)) throw new Error('flow_fenced');
    if (f.state !== 'pending' || !f.code_confirmed) throw new Error('mailbox_confirmation_required');
    const grant = await verifyGrant(token, r, this.trust, this.clock);
    // Read-only, injected provider confirmation for the exact stored address.
    // Capture freshness BEFORE I/O; slow provider calls cannot extend evidence.
    const providerAt = this.clock();
    let destination;
    try { destination = await this.provider(f.owner_address); }
    catch { throw new Error('destination_unavailable'); }
    if (destination?.address !== f.owner_address || destination.verified !== true) throw new Error('destination_unverified');
    await this.stmt(`UPDATE mail_enrollment_flows SET provider_checked_at=? WHERE flow_id=? AND state='pending'`, providerAt, flowId).run();
    // Recheck JWT/challenge AFTER all asynchronous evidence work.
    await verifyGrant(token, r, this.trust, this.clock);
    this.checkServicePin(m);
    try {
      // D1 supplies admission time inside the INSERT, not from a JS clock read
      // before asynchronous crypto/SQL scheduling. All trigger checks use it.
      await this.stmt(`INSERT INTO mail_enrollment_commits VALUES(?,?,?,?,?,?,?,?,
        CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER),?)`,
        grant.jti, flowId, f.r_digest, tokenDigest, Number(m.expected_generation), grant.checked_at_ms,
        grant.checked_at_ms + OWNERSHIP_LEASE_MS, grant.record_version, grant.exp * 1000).run();
    } catch (error) {
      const raced = await this.first('SELECT * FROM mail_enrollment_commits WHERE flow_id=?', flowId);
      if (!raced || raced.token_digest !== tokenDigest || raced.r_digest !== f.r_digest) throw error;
    }
    return this.result(f);
  }
  result(f) { return { install_id: f.install_id, generation: f.expected_generation + 1,
    state: 'ownership_recorded_delivery_pending', active: false, route_verified: false }; }
}
