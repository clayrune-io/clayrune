// Enrollment core, deliberately NOT a public HTTP handler. Unit 7b must supply
// the authenticated originating human-flow guard. No dashboard token is a grant.
import { bytes, randomId, sha256 } from './signing.js';
import { canonicalR, fieldsR, FIELDS, ID, origin, verifyProof } from './enrollment-transcript.js';
import { CHALLENGE_TTL, randomHex, flowDigest, codeDigest } from './enrollment-challenge.js';
import { verifyGrant, OWNERSHIP_LEASE_MS } from './enrollment-grant.js';
import { parseMailbox } from './mailbox.js';

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
    if (!value || Object.keys(value).sort().join(',') !== [...allowed].sort().join(',')
        || !ID.test(value.flow_id) || !ID.test(value.device_id)
        || parseMailbox(value.owner_address)?.address !== value.owner_address
        || value.owner_address.length > 254) throw new Error('bad_enrollment');
    const flowHash = await flowDigest(secret);
    const ownerHash = await sha256(bytes(value.owner_address));
    if (!this.servicePin || ['manifest_sequence', 'service_key_id', 'service_public_key_sha256']
      .some((k) => value[k] !== this.servicePin[k])) throw new Error('service_pin_mismatch');
    // Validate every hint BEFORE reserving durable device/capacity state.
    const check = { ...value, purpose: 'clayrune-mail-enrollment', version: '1', service_origin: this.serviceOrigin,
      challenge_id: 'preflight', challenge_nonce: '0'.repeat(64), expires_ms: String(this.clock() + CHALLENGE_TTL),
      install_id: 'preflight', expected_generation: '0', owner_address_sha256: ownerHash,
      route_path: '/api/agent-mail/ingress' };
    canonicalR(FIELDS.map((k) => check[k]));
    const previous = await this.first('SELECT * FROM mail_enrollment_flows WHERE flow_id=?', value.flow_id);
    if (previous) {
      const p = await this.flow(value.flow_id, secret); const m = fieldsR(JSON.parse(p.transcript));
      if (p.owner_address !== value.owner_address || allowed.filter((k) => !['owner_address', 'flow_id'].includes(k))
        .some((k) => m[k] !== value[k])) throw new Error('flow_conflict');
      return { transcript: JSON.parse(p.transcript), state: p.state }; // No reissue of the code.
    }
    const now = this.clock();
    let install = await this.first('SELECT * FROM mail_installations WHERE device_id=?', value.device_id);
    if (!install) {
      await this.stmt(`INSERT OR IGNORE INTO mail_installations(install_id,device_id,address_local)
        SELECT ?,?,? WHERE (SELECT COUNT(*) FROM mail_installations)<?`,
      randomId(), value.device_id, randomHex(15), this.capacity).run();
      install = await this.first('SELECT * FROM mail_installations WHERE device_id=?', value.device_id);
      if (!install) throw new Error('enrollment_capacity');
    }
    const m = { ...value, purpose: 'clayrune-mail-enrollment', version: '1', service_origin: this.serviceOrigin,
      challenge_id: randomId(), challenge_nonce: randomHex(32), expires_ms: String(now + CHALLENGE_TTL),
      install_id: install.install_id, expected_generation: String(install.generation),
      owner_address_sha256: ownerHash, route_path: '/api/agent-mail/ingress' };
    const r = FIELDS.map((k) => m[k]); const input = canonicalR(r);
    const code = randomHex(16);
    const digest = await codeDigest(this.codeKey, value.flow_id, m.challenge_id, code);
    await this.db.batch([
      this.stmt(`UPDATE mail_enrollment_flows SET state='invalidated',code_digest=''
        WHERE install_id=? AND state='pending' AND expires_at<=?`, install.install_id, now),
      this.stmt(`INSERT INTO mail_enrollment_flows(flow_id,flow_digest,install_id,expected_generation,
        challenge_id,transcript,r_digest,owner_address,code_digest,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)`,
      value.flow_id, flowHash, install.install_id, install.generation, m.challenge_id,
      new TextDecoder().decode(input), await sha256(input), value.owner_address, digest, now + CHALLENGE_TTL),
    ]);
    // Adapter must send this code to ONLY the stored owner, under its own bounded
    // provisioning budget. Returning it here is not an agent-facing API.
    return { transcript: r, code, state: 'pending' };
  }
  async confirm(flowId, secret, code) {
    this.guard(); const f = await this.flow(flowId, secret);
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
