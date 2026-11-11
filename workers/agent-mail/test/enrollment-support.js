// Test-only ephemeral keys and SQLite D1 adapter. No provider/network calls.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { base64url, bytes, sha256 } from '../signing.js';
import { canonicalR, fieldsR } from '../enrollment-transcript.js';
import { Enrollment } from '../enrollment.js';
import { GRANT_TYPE, GRANT_AUDIENCE } from '../enrollment-grant.js';
export const NOW = 1_794_249_600_000;
export class EnrollmentDB {
  constructor(path = ':memory:', init = true) {
    this.sql = new DatabaseSync(path);
    this.clock = Date.now;
    // Test-only deterministic equivalent of SQLite's database wall clock.
    this.sql.function('strftime', { varargs: true }, (format) => {
      const now = this.clock();
      if (format === '%s') return String(Math.floor(now / 1000));
      if (format === '%f') return `${String(Math.floor(now / 1000) % 60).padStart(2, '0')}.${String(now % 1000).padStart(3, '0')}`;
      throw new Error('unsupported_test_clock_format');
    });
    if (init) {
      this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
      this.sql.exec(readFileSync(new URL('../migrations/0001_enrollment.sql', import.meta.url), 'utf8'));
      // 0003 (fence_digest column, fences, fence-aware commit trigger) only needs 0001; 0002 stays opt-in.
      this.sql.exec(readFileSync(new URL('../migrations/0003_lifecycle.sql', import.meta.url), 'utf8'));
      this.sql.exec(readFileSync(new URL('../migrations/0004_destinations.sql', import.meta.url), 'utf8'));
    }
  }
  prepare(sql) {
    const db = this.sql;
    return { args: [], bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return { results: db.prepare(sql).all(...this.args) }; },
      async run() { return this.runSync(); },
      runSync() { const r = db.prepare(sql).run(...this.args); return { meta: { changes: Number(r.changes) } }; } };
  }
  async batch(statements) {
    this.sql.exec('BEGIN IMMEDIATE');
    try { const r = statements.map((s) => s.runSync()); this.sql.exec('COMMIT'); return r; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}
export async function edKeys() {
  const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { privateKey: k.privateKey, publicKey: base64url(await crypto.subtle.exportKey('raw', k.publicKey)) };
}
export async function fixture(options = {}) {
  const db = options.db ?? new EnrollmentDB(); const device = await edKeys(); const mail = await edKeys();
  const es = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const jwk = { ...await crypto.subtle.exportKey('jwk', es.publicKey), kid: 'mail-grant-test', alg: 'ES256', use: 'sig' };
  const trust = { issuer: 'https://authority.example.com', serviceOrigin: 'https://mail.example.com',
    jwksUrl: 'https://authority.example.com/v1/mail-enrollment/jwks', jwks: { keys: [jwk] } };
  const time = { now: NOW }; const secret = 'a'.repeat(64);
  db.clock = () => time.now;
  const value = { flow_id: 'flow1', owner_address: 'owner@example.com', device_id: 'device1',
    device_public_key: device.publicKey, mail_key_id: 'mail1', mail_public_key: mail.publicKey,
    tunnel_uuid: '12345678-1234-1234-1234-123456789abc', route_origin: 'https://device.example.com',
    manifest_sequence: '1', service_key_id: 'service1', service_public_key_sha256: 'b'.repeat(64) };
  if (options.fenceDigest) value.fence_digest = options.fenceDigest; // 7d-2d: lifecycle cancel/revoke authority
  const config = { db, enabled: true, serviceOrigin: trust.serviceOrigin, trust, servicePin: { ...value },
    codeKey: new Uint8Array(32).fill(9), clock: () => time.now,
    provider: async (address) => ({ address, verified: true }), ...options };
  const enrollment = new Enrollment(config);
  const started = await enrollment.begin(value, secret); const r = started.transcript; const m = fieldsR(r);
  const proof = async (privateKey, transcript = r) => base64url(await crypto.subtle.sign('Ed25519', privateKey, canonicalR(transcript)));
  async function token(claims = {}, header = {}, transcript = r) {
    const fields = fieldsR(transcript);
    const c = { iss: trust.issuer, aud: GRANT_AUDIENCE, token_use: GRANT_AUDIENCE, sub: 'account1', jti: 'grant1',
      iat: Math.floor(time.now / 1000), nbf: Math.floor(time.now / 1000), exp: Math.floor(time.now / 1000) + 60,
      checked_at_ms: time.now, record_version: 'c'.repeat(64), r_sha256: await sha256(canonicalR(transcript)),
      ...Object.fromEntries(['device_id', 'tunnel_uuid', 'route_origin', 'install_id', 'expected_generation'].map((k) => [k, fields[k]])), ...claims };
    const h = { alg: 'ES256', typ: GRANT_TYPE, kid: jwk.kid, ...header };
    const data = `${base64url(bytes(JSON.stringify(h)))}.${base64url(bytes(JSON.stringify(c)))}`;
    return `${data}.${base64url(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, es.privateKey, bytes(data)))}`;
  }
  async function complete(over = {}) {
    return enrollment.complete({ flowId: value.flow_id, secret, token: await token(),
      deviceSignature: await proof(device.privateKey), mailSignature: await proof(mail.privateKey), ...over });
  }
  async function confirm() { return enrollment.confirm(value.flow_id, secret, started.code); }
  return { db, config, enrollment, secret, value, started, r, m, trust, time, token, proof, complete, confirm, device, mail };
}
