// Enrollment ceremony HTTP wrapper (MC-1166 7d-2e-2, contract docs/AGENT_MAIL_UNIT7D2E_CONTRACT.md
// section 2). worker.js dispatches here and nowhere else, mirroring lifecycle.js. It wraps the
// `Enrollment` core (begin / confirm / complete), which stays the only place ownership is decided.
//
//   POST /v1/mail/enrollment/begin | confirm | complete
//
// DEFAULT OFF: MAIL_ENROLLMENT_ENABLED must be exactly 'true' (and MAIL_ENABLED, checked by the caller).
// Off, or any reviewed config absent, answers 503 before one D1 call or one send.
//
// AUTH. There is no install key yet, so there is no install signature. `begin` is unauthenticated at the
// HTTP layer and is gated by the CP preflight (decision E2a: a real, active device first, fail closed).
// `confirm` and `complete` need the flow secret (only its SHA-256 is stored); wrong or missing -> 401,
// charged to the shared unauthenticated bucket, never to an install.
//
// REPLIES. Every 200 is service-signed (response-signing.js) with install_id := flow_id and
// generation := "1"; the true scope rides in the signed body. Every other status is an unsigned closed
// code and is never an acknowledgment. The one-time code is NEVER in a reply: it leaves only in the mail.
import { Enrollment } from './enrollment.js';
import { fieldsR, origin, ID, HEX } from './enrollment-transcript.js';
import { unbase64url } from './signing.js';
import { readRequestBody } from './request-body.js';
import { signedReply } from './response-signing.js';
import { sendEnrollmentCode } from './code-mail.js';
import { destinationProviderFor } from './enrollment-provider.js';
import { createPreflightClient } from './enrollment-preflight.js';
import { parseAddrSpec } from './mailbox.js';

export const ENROLLMENT_PREFIX = '/v1/mail/enrollment';
export const ENROLLMENT_ROUTES = Object.freeze(['begin', 'confirm', 'complete']);
export const ENROLLMENT_BODY_LIMIT = 4096;
const NONCE = /^[a-f0-9]{32}$/;
const SECRET = /^[a-f0-9]{64}$/;
const CODE = /^[a-f0-9]{32}$/;
const SEQUENCE = /^[1-9][0-9]{0,14}$/;
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
const keysOf = (v, required, optional = []) => v && typeof v === 'object' && !Array.isArray(v)
  && required.every((k) => Object.hasOwn(v, k))
  && Object.keys(v).every((k) => required.includes(k) || optional.includes(k));

export const isEnrollmentPath = (pathname) => pathname.startsWith(`${ENROLLMENT_PREFIX}/`)
  && ENROLLMENT_ROUTES.includes(pathname.slice(ENROLLMENT_PREFIX.length + 1));

// Core error message -> [status, code]. Closed: an exception string is never put in a reply, and a
// message not listed here is rethrown to worker.js, which answers a constant 503.
const FAILURES = new Map([
  ['enrollment_disabled', [503, 'enrollment_disabled']], ['enrollment_config_required', [503, 'enrollment_config_required']],
  ['bad_enrollment', [400, 'bad_enrollment']], ['bad_transcript', [400, 'bad_transcript']],
  ['bad_grant', [400, 'bad_grant']], ['bad_flow', [400, 'bad_flow']],
  ['key_proof_required', [403, 'key_proof_required']], ['grant_trust_required', [403, 'grant_trust_required']],
  ['unknown_grant_key', [403, 'unknown_grant_key']], ['bad_grant_header', [403, 'bad_grant_header']],
  ['bad_grant_key', [403, 'bad_grant_key']], ['bad_grant_signature', [403, 'bad_grant_signature']],
  ['bad_grant_claims', [403, 'bad_grant_claims']],
  ['flow_fenced', [409, 'flow_fenced']], ['flow_conflict', [409, 'flow_conflict']],
  ['grant_conflict', [409, 'grant_conflict']], ['challenge_unavailable', [409, 'challenge_unavailable']],
  ['mailbox_confirmation_required', [409, 'mailbox_confirmation_required']],
  ['service_pin_mismatch', [409, 'service_pin_mismatch']],
  ['destination_unavailable', [409, 'destination_unavailable']], ['destination_unverified', [409, 'destination_unverified']],
  ['enrollment_capacity', [503, 'enrollment_capacity']],
]);

const jsonOr = (text) => { try { return JSON.parse(text); } catch { return undefined; } };

// A token that cannot even be decoded is a grammar error (400 bad_grant), not an operational failure:
// verifyGrant lets decode exceptions escape, and an unmapped throw would answer a transient-looking 503.
// Signature, key and claim checks stay entirely with verifyGrant; this only checks the three-part shape.
function tokenDecodes(token) {
  try {
    const parts = token.split('.');
    if (token.length > 8192 || parts.length !== 3) return false;
    return parts.slice(0, 2).every((p) => {
      const v = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(unbase64url(p)));
      return v && typeof v === 'object' && !Array.isArray(v);
    }) && unbase64url(parts[2]).length > 0;
  } catch { return false; }
}

// Reviewed config -> everything the core and the wrapper need, or null (=> 503 enrollment_config_required).
// Nothing defaults. `inject` is for tests: provider/reader and a ready preflight function.
export function enrollmentConfig(env, { config, provider, reader, preflight, fetcher, clock } = {}) {
  try {
    const pin = jsonOr(env.MAIL_SERVICE_PIN);
    const jwks = jsonOr(env.MAIL_GRANT_JWKS);
    const from = parseAddrSpec(config?.from ?? '');
    if (!origin(env.MAIL_SERVICE_ORIGIN) || !origin(env.MAIL_GRANT_ISSUER)
        || !pin || typeof pin !== 'object' || Array.isArray(pin)
        || Object.keys(pin).sort().join() !== 'manifest_sequence,service_key_id,service_public_key_sha256'
        || ![pin.manifest_sequence, pin.service_key_id, pin.service_public_key_sha256].every((x) => typeof x === 'string')
        || !SEQUENCE.test(pin.manifest_sequence) || !ID.test(pin.service_key_id) || !HEX.test(pin.service_public_key_sha256)
        || !Array.isArray(jwks?.keys) || jwks.keys.length < 1
        || !from || from.domain !== config.domain || !/^[a-z0-9.-]+$/.test(config.domain)
        || !ID.test(config.keyId ?? '') || typeof config.privateKey !== 'string' || !config.privateKey) return null;
    const codeKey = unbase64url(env.MAIL_CODE_KEY);
    if (codeKey.length !== 32) return null;
    const resolved = destinationProviderFor(env, { provider, reader });
    if (!resolved) return null;
    const pf = preflight ?? createPreflightClient({ issuer: env.MAIL_CP_ISSUER, key_id: config.keyId,
      privateKey: config.privateKey, fetcher, clock });
    return { serviceOrigin: env.MAIL_SERVICE_ORIGIN, codeKey, provider: resolved, preflight: pf,
      servicePin: { manifest_sequence: pin.manifest_sequence, service_key_id: pin.service_key_id,
        service_public_key_sha256: pin.service_public_key_sha256 },
      // jwksUrl is DERIVED from the issuer; the verifier refuses any other value (E5a: config, no runtime fetch).
      trust: { issuer: env.MAIL_GRANT_ISSUER, serviceOrigin: env.MAIL_SERVICE_ORIGIN,
        jwksUrl: `${env.MAIL_GRANT_ISSUER}/v1/mail-enrollment/jwks`, jwks: { keys: jwks.keys } } };
  } catch { return null; }
}

async function begin(c) {
  const { value } = c;
  if (!keysOf(value, ['value', 'flow_secret']) || typeof value.flow_secret !== 'string'
      || !SECRET.test(value.flow_secret)) return reply(400, { code: 'invalid_request' });
  const v = value.value;
  if (!v || typeof v !== 'object' || Array.isArray(v)
      || !['flow_id', 'device_id', 'device_public_key', 'tunnel_uuid', 'route_origin'].every((k) => typeof v[k] === 'string')
      || !ID.test(v.flow_id) || !ID.test(v.device_id)) return reply(400, { code: 'bad_enrollment' });
  // E2a: ask the CP first. Nothing below runs (no D1 write, no reservation, no mail) unless it says ok.
  const checked = await c.cfg.preflight({ flow_id: v.flow_id, device_id: v.device_id,
    device_public_key: v.device_public_key, tunnel_uuid: v.tunnel_uuid, route_origin: v.route_origin });
  if (checked?.ok !== true) {
    return checked?.refused === true ? reply(403, { code: 'preflight_refused' }) : reply(503, { code: 'preflight_unavailable' });
  }
  const out = await c.core.begin(v, value.flow_secret);
  const m = fieldsR(out.transcript);
  let codeSent = false;
  if (typeof out.code === 'string') {
    const sent = await sendEnrollmentCode({ ledger: c.ledger, flowId: v.flow_id, owner: v.owner_address,
      code: out.code, config: c.config, send: c.send, now: c.now });
    if (!sent.ok) return reply(sent.status, { code: sent.code });
    codeSent = true;
  }
  return c.ok(v.flow_id, { flow_id: v.flow_id, install_id: m.install_id, expected_generation: Number(m.expected_generation),
    transcript: out.transcript, state: out.state, code_sent: codeSent });
}

async function confirm(c) {
  const v = c.value;
  if (!keysOf(v, ['flow_id', 'code'], ['flow_secret']) || typeof v.flow_id !== 'string' || !ID.test(v.flow_id)) {
    return reply(400, { code: 'invalid_request' });
  }
  // A malformed code can never be real; refuse it before the core counts it as one of five strikes.
  if (typeof v.code !== 'string' || !CODE.test(v.code)) return reply(400, { code: 'invalid_request' });
  const secret = typeof v.flow_secret === 'string' ? v.flow_secret : '';
  const confirmed = await c.core.confirm(v.flow_id, secret, v.code);
  const flow = await c.core.flow(v.flow_id, secret);
  return c.ok(v.flow_id, { flow_id: v.flow_id, confirmed, state: flow.state });
}

async function complete(c) {
  const v = c.value;
  if (!keysOf(v, ['flow_id', 'device_signature', 'mail_signature'], ['flow_secret', 'token'])
      || typeof v.flow_id !== 'string' || !ID.test(v.flow_id)
      || typeof v.device_signature !== 'string' || typeof v.mail_signature !== 'string'
      || v.device_signature.length > 128 || v.mail_signature.length > 128) return reply(400, { code: 'invalid_request' });
  if (v.token !== undefined && (typeof v.token !== 'string' || !tokenDecodes(v.token))) return reply(400, { code: 'bad_grant' });
  const secret = typeof v.flow_secret === 'string' ? v.flow_secret : '';
  // `token` is optional only because of E3 (a lost-reply retry after commit); the core refuses a missing
  // token when no commit exists (bad_grant).
  const result = await c.core.complete({ flowId: v.flow_id, secret, token: v.token,
    deviceSignature: v.device_signature, mailSignature: v.mail_signature });
  return c.ok(v.flow_id, { flow_id: v.flow_id, ...result });
}

const HANDLERS = { begin, confirm, complete };

// `send(mail)` is the Worker's sender seam; `inject` carries test doubles (provider/reader/preflight).
export async function enrollmentHttp({ request, env, ledger, config, send, fetcher, clock, bodyTimeoutMs, inject = {} }) {
  if (env.MAIL_ENROLLMENT_ENABLED !== 'true') return reply(503, { code: 'enrollment_disabled' });
  const cfg = enrollmentConfig(env, { ...inject, config, fetcher, clock });
  if (!cfg) return reply(503, { code: 'enrollment_config_required' });
  const url = new URL(request.url);
  const nonce = request.headers.get('x-mail-nonce');
  if (!NONCE.test(nonce ?? '')) return reply(400, { code: 'invalid_request' });
  let raw;
  try { raw = await readRequestBody(request.body, { timeoutMs: bodyTimeoutMs, limit: ENROLLMENT_BODY_LIMIT }); }
  catch (error) {
    return error.message === 'body_timeout' ? reply(408, { code: 'body_timeout' }) : reply(413, { code: 'body_limit' });
  }
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw)); }
  catch { return reply(400, { code: 'invalid_request' }); }
  const now = clock();
  // Invalid traffic shares one bounded bucket, applied only on refusal so abuse cannot lock out a valid caller.
  const unauthorized = async () => await ledger.limit('http:unauthenticated', 10_000, now, 60_000)
    ? reply(401, { code: 'unauthorized' }) : reply(429, { code: 'abuse_limit' });
  const core = new Enrollment({ db: env.MAIL_DB, enabled: true, serviceOrigin: cfg.serviceOrigin,
    servicePin: cfg.servicePin, trust: cfg.trust, codeKey: cfg.codeKey, provider: cfg.provider, clock });
  const ctx = { value, cfg, core, ledger, config, send, now,
    ok: (flowId, body) => signedReply({ config, install_id: flowId, generation: 1, path: url.pathname, nonce, value: body, clock }) };
  try {
    return await HANDLERS[url.pathname.slice(ENROLLMENT_PREFIX.length + 1)](ctx);
  } catch (error) {
    if (error?.message === 'human_flow_required') return await unauthorized();
    const known = FAILURES.get(error?.message);
    if (known) return reply(known[0], { code: known[1] });
    throw error;
  }
}
