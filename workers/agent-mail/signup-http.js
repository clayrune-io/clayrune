// Pull signup HTTP only (contract v1.0 sections 2-4, 6.1). Not wired into
// worker.js. No provider, control-plane, tunnel, activation or management calls.
// Caller supplies reviewed service signing material and trusted edge source;
// NEVER derive trustedSource from a caller-selected forwarding header.
import { bytes, randomId, sha256, base64url, unbase64url } from './signing.js';
import { origin } from './enrollment-transcript.js';
import { verifySignedV2 } from './signed-v2-verifier.js';
import { OwnerRegistry, ownerIdentity, newAddressLocal } from './owner-registry.js';
import { VERIFICATION_TTL, confirmationPage } from './verification-mail.js';
import { readRequestBody } from './request-body.js';
import { Ledger } from './ledger.js';

export const SIGNUP_PATHS = Object.freeze(['/v1/pull/signup/start', '/v1/pull/signup/status',
  '/mail/link', '/mail/link/confirm']);
const START = SIGNUP_PATHS[0];
const DAY = 86_400_000;
const SKEW = 300_000;
const SESSION_TTL = 600_000;
const COOKIE = '__Secure-clayrune-mail';
const I = /^[a-f0-9]{32}$/;
const H = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const DEC = /^(0|[1-9][0-9]*)$/;
const START_FIELDS = ['op_id', 'flow_id', 'public_key', 'key_id', 'owner', 'flow_secret_hash',
  'fence_hash', 'consent_version', 'manifest_sequence', 'manifest_digest'];
const STATUS = new Map([[400, ['invalid_request', 'invalid_json']], [401, ['unauthorized', 'csrf_failed', 'session_required']],
  [404, ['not_found']], [408, ['body_timeout']],
  [409, ['operation_conflict', 'nonce_replayed', 'stale_generation', 'owner_limit', 'fenced', 'already_sent']],
  [410, ['flow_expired', 'token_expired']], [413, ['body_limit']],
  [429, ['request_limit', 'verification_limit', 'abuse_limit']],
  [503, ['storage_unavailable', 'service_unavailable']]]);
const CODES = new Map([...STATUS].flatMap(([status, codes]) => codes.map((code) => [code, status])));
const requireValue = (ok, code = 'invalid_request') => { if (!ok) throw new Error(code); };
const matches = (re, s) => typeof s === 'string' && re.test(s);
const freshSecret = () => randomId() + randomId();
const hash = (s) => sha256(bytes(s));
const exact = (v, names) => v && Object.keys(v).sort().join(',') === [...names].sort().join(',');
const safe = (n) => Number.isSafeInteger(n) && n >= 0 && !Object.is(n, -0);
const commonHeaders = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
const htmlHeaders = { ...commonHeaders, 'content-type': 'text/html; charset=utf-8',
  'referrer-policy': 'no-referrer', 'x-frame-options': 'DENY',
  'content-security-policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" };
const html = (text, status = 200) => new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><title>Clayrune mail</title><p>${text}</p></html>`,
  { status, headers: htmlHeaders });

// These two bodies are flat closed objects. Parse scalar tokens explicitly so
// duplicate decoded keys, BOM, -0, surrogate strings and nested bags cannot be
// normalized away by JSON.parse. JSON.parse handles each string's escape grammar.
function flatJSON(raw) {
  try {
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
    let at = 0;
    const white = () => { while (/[\x20\t\r\n]/.test(text[at] ?? '\0')) at++; };
    const string = () => {
      const start = at; requireValue(text[at++] === '"', 'invalid_json');
      while (at < text.length) {
        const c = text[at++];
        if (c === '\\') at++;
        else if (c === '"') {
          const s = JSON.parse(text.slice(start, at));
          requireValue(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(s), 'invalid_json');
          return s;
        }
      }
      throw new Error('invalid_json');
    };
    white(); requireValue(text[at++] === '{', 'invalid_json'); white();
    const result = Object.create(null);
    if (text[at] !== '}') while (true) {
      const key = string(); white(); requireValue(text[at++] === ':', 'invalid_json'); white();
      requireValue(!Object.hasOwn(result, key), 'invalid_json');
      if (text[at] === '"') result[key] = string();
      else {
        const token = /^(0|[1-9][0-9]*)/.exec(text.slice(at));
        requireValue(token, 'invalid_json'); at += token[0].length;
        result[key] = Number(token[0]); requireValue(safe(result[key]), 'invalid_json');
      }
      white(); if (text[at] !== ',') break;
      at++; white();
    }
    requireValue(text[at++] === '}', 'invalid_json'); white(); requireValue(at === text.length, 'invalid_json');
    return result;
  } catch { throw new Error('invalid_json'); } // Syntax/UTF-8 only, no I/O.
}

function formBody(raw) {
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw); }
  catch { throw new Error('invalid_request'); } // Invalid wire UTF-8, no I/O.
  // All fields are ASCII hex: refusing alternate encodings removes duplicate,
  // malformed percent and plus/whitespace interpretations across form parsers.
  requireValue(/^(token=[a-f0-9]{64}&csrf=[a-f0-9]{64}|csrf=[a-f0-9]{64}&token=[a-f0-9]{64})$/.test(text));
  return Object.fromEntries(new URLSearchParams(text));
}

function cookieSecret(request) {
  const entries = (request.headers.get('cookie') ?? '').split(';').map((p) => p.trim().split('='));
  const found = entries.filter(([name]) => name === COOKIE);
  requireValue(found.length === 1 && found[0].length === 2 && matches(H, found[0][1]), 'session_required');
  return found[0][1];
}

/** Construct only with reviewed configuration; no env defaults/key generation.
 * handle(request,{trustedSource}) handles exactly SIGNUP_PATHS. Unit 14 routes
 * only signup links here; management/new-owner link dispatch belongs to 04a/05.
 * Unit 06 may share the fixed v2 proof/accounting seam; legacy sign() is v1.
 */
export class SignupHTTP {
  constructor({ db, serviceOrigin, domain, verificationMail, serviceKeyId, servicePrivateKey, clock = Date.now }) {
    requireValue(origin(serviceOrigin) && origin(`https://${domain}`) && domain.length <= 223 && matches(ID, serviceKeyId)
      && typeof servicePrivateKey === 'string' && unbase64url(servicePrivateKey).length > 0
      && verificationMail?.db === db && verificationMail.serviceOrigin === serviceOrigin);
    Object.assign(this, { db, serviceOrigin, domain, mail: verificationMail, serviceKeyId, servicePrivateKey, clock });
    this.registry = new OwnerRegistry(db);
    this.ledger = new Ledger(db);
  }
  stmt(sql, ...args) { return this.registry.stmt(sql, ...args); }
  async first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  now() { const n = this.clock(); requireValue(safe(n) && n < Number.MAX_SAFE_INTEGER - 90 * DAY, 'service_unavailable'); return n; }

  async response(auth, path, value, status = 200) {
    if (!auth) return Response.json(value, { status, headers: commonHeaders });
    const body = bytes(JSON.stringify(value));
    const fields = { version: '2', direction: 'pull-to-install', key_id: this.serviceKeyId,
      install_id: auth.install_id, generation: '1', owner_generation: '1',
      timestamp: String(this.now()), nonce: auth.nonce, digest: await sha256(body) };
    const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', fields.direction,
      this.serviceOrigin, 'POST', path, fields.key_id, fields.install_id, '1', '1', fields.timestamp,
      fields.nonce, String(status), fields.digest]));
    const headers = new Headers({ ...commonHeaders, 'content-type': 'application/json' });
    for (const [k, v] of Object.entries(fields)) headers.set(`x-mail-${k.replaceAll('_', '-')}`, v);
    const signingKey = await crypto.subtle.importKey('pkcs8', unbase64url(this.servicePrivateKey), 'Ed25519', false, ['sign']);
    headers.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', signingKey, transcript)));
    return new Response(body, { status, headers });
  }

  async sourceLimit(trustedSource, now) {
    requireValue(typeof trustedSource === 'string' && /^[\x21-\x7e]{1,128}$/.test(trustedSource), 'service_unavailable');
    // Trusted source is hashed, never retained as an IP; one counter/source/minute.
    requireValue(await this.ledger.limit(`pull:http:source:${await hash(trustedSource)}`, 30, now, 60_000), 'abuse_limit');
  }

  async refusalLimit(now) {
    return this.ledger.limit('http:unauthenticated', 10_000, now, 60_000);
  }

  async authenticate(request, raw, value, path) {
    return verifySignedV2({ request, raw, body: value, path, serviceOrigin: this.serviceOrigin,
      clock: () => this.now(), resolveKey: async (m) => {
        requireValue(m.generation === '1' && m.owner_generation === '1', 'unauthorized');
        if (path === START) {
          requireValue(typeof value.public_key === 'string' && value.key_id === m.key_id, 'unauthorized');
          return value.public_key;
        }
        requireValue(matches(I, value.flow_id) && matches(H, value.flow_secret), 'unauthorized');
        const f = await this.first(`SELECT * FROM pull_flows WHERE flow_id=? AND install_id=? AND purpose='signup'`, value.flow_id, m.install_id);
        requireValue(f && f.candidate_key_id === m.key_id && f.expected_generation === 1
          && f.expected_owner_generation === 1 && matches(H, value.flow_secret)
          && f.flow_secret_hash === await hash(value.flow_secret), 'unauthorized');
        m.flow = f;
        return f.candidate_public_key;
      } });
  }

  async accounting(auth, now) {
    requireValue(Math.abs(now - Number(auth.timestamp)) <= SKEW, 'unauthorized');
    requireValue(!await this.first(`SELECT 1 FROM pull_nonces WHERE direction='install-to-pull' AND install_id=?
      AND generation=1 AND key_id=? AND nonce=?`, auth.install_id, auth.key_id, auth.nonce), 'nonce_replayed');
    const bucket = `pull:http:management:${auth.install_id}:${Math.floor(now / DAY)}`;
    const row = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
    requireValue(!row || row.count < 500, 'request_limit');
    return [this.stmt(`INSERT INTO pull_nonces VALUES('install-to-pull',?,1,?,?,?)`,
      auth.install_id, auth.key_id, auth.nonce, Number(auth.timestamp) + SKEW + 1),
    this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<500`,
      bucket, (Math.floor(now / DAY) + 2) * DAY), this.registry.changed()];
  }

  async existing(auth, value) {
    const op = await this.first('SELECT * FROM pull_operations WHERE install_id=? AND generation=1 AND path=? AND op_id=?',
      auth.install_id, START, value.op_id);
    if (!op) return null;
    requireValue(op.input_digest === auth.digest, 'operation_conflict');
    return JSON.parse(op.result_json);
  }

  async start(auth, v) {
    requireValue(exact(v, START_FIELDS) && matches(I, v.op_id) && matches(I, v.flow_id)
      && matches(ID, v.key_id) && v.key_id === auth.key_id && matches(H, v.flow_secret_hash)
      && matches(H, v.fence_hash) && matches(ID, v.consent_version) && matches(H, v.manifest_digest)
      && Number.isSafeInteger(v.manifest_sequence) && v.manifest_sequence > 0 && v.manifest_sequence <= 999_999_999_999_999);
    const owner = await ownerIdentity(v.owner);
    const r = this.registry;
    // Race losers re-read a durable operation; they never send or run a prefix.
    for (let attempt = 0; attempt < 2; attempt++) {
      const now = this.now();
      const accounting = await this.accounting(auth, now);
      const previous = await this.existing(auth, v);
      if (previous) { await this.db.batch(accounting); return previous; }
      requireValue(!await r.installation(auth.install_id)
        && !await this.first('SELECT 1 FROM pull_flows WHERE flow_id=?', v.flow_id), 'operation_conflict');
      requireValue(!await this.first(`SELECT 1 FROM pull_fences WHERE install_id=? AND
        (mode='revoke' OR flow_id=?) AND generation=1`, auth.install_id, v.flow_id), 'fenced');
      const slots = await this.first(`SELECT COUNT(DISTINCT install_id) AS n FROM pull_owner_slots
        WHERE owner_hash=? AND (role='active' OR until_ms>?)`, owner.owner_hash, now);
      requireValue(slots.n < 3, 'owner_limit');
      for (const [bucket, cap] of [[`pull:verify:install:${auth.install_id}:${Math.floor(now / DAY)}`, 3],
        [`pull:verify:target:${owner.owner_hash}:${Math.floor(now / DAY)}`, 3], [`pull:send:global:${Math.floor(now / DAY)}`, 1000]]) {
        const budget = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
        requireValue(!budget || budget.count < cap, 'verification_limit');
      }
      const end = now + VERIFICATION_TTL;
      const local = newAddressLocal();
      const flow = { ...v, install_id: auth.install_id, purpose: 'signup', expected_generation: 1,
        expected_owner_generation: 1, candidate_key_id: v.key_id, candidate_public_key: v.public_key,
        owner_hash: owner.owner_hash, created_ms: now, expires_ms: end };
      const result = { flow_id: v.flow_id, install_id: auth.install_id, generation: 1, owner_generation: 1,
        address: `${local}@${this.domain}`, state: 'pending', expires_ms: end, verification_state: 'queued' };
      const issued = await this.mail.issueStatements({ outbox_id: randomId(), template: 'signup', to: owner.owner_address, flow }, now);
      const statements = [...accounting,
        r.guard(`NOT EXISTS (SELECT 1 FROM pull_fences WHERE install_id=? AND generation=1 AND (mode='revoke' OR flow_id=?))`,
          auth.install_id, v.flow_id),
        this.stmt(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
          address_local,key_id,public_key,consent_version,created_ms,updated_ms) VALUES(?,1,1,'pending',?,?,?,?,?,?,?,?)`,
        auth.install_id, owner.owner_hash, owner.owner_address, local, v.key_id, v.public_key, v.consent_version, now, now),
        this.stmt(`INSERT INTO pull_generations VALUES(?,1,1,?,?,?,?,?,'current',?,NULL)`,
          auth.install_id, owner.owner_hash, v.key_id, v.public_key, v.fence_hash, v.flow_id, now),
        this.stmt(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,
          candidate_key_id,candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,
          consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms)
          VALUES(?,?,'signup',1,1,?,?,?,?,?,?,'pending',?,?,?,?,?)`,
        v.flow_id, auth.install_id, v.key_id, v.public_key, owner.owner_hash, v.flow_secret_hash, v.fence_hash,
        auth.digest, v.consent_version, v.manifest_sequence, v.manifest_digest, now, end),
        ...r.reserveSlotStatements({ owner_hash: owner.owner_hash, install_id: auth.install_id, flow_id: v.flow_id, until_ms: end }, now),
        ...r.reserveAddressStatements({ address_local: local, install_id: auth.install_id, until_ms: end }, now),
        ...issued.statements,
        this.stmt(`INSERT INTO pull_operations VALUES(?,1,?,?,?,?,200,?,?)`, auth.install_id, START, v.op_id,
          auth.digest, JSON.stringify(result), now, end + 30 * DAY)];
      requireValue(Math.abs(this.now() - Number(auth.timestamp)) <= SKEW, 'unauthorized');
      requireValue(this.now() < end, 'flow_expired');
      try { await this.db.batch(statements); return result; }
      catch (error) {
        if (attempt) throw error;
        // Re-run checks after a rolled-back race. Unexpected storage failures
        // remain failures on the second attempt; no partial success is inferred.
      }
    }
  }

  async status(auth, v) {
    requireValue(exact(v, ['flow_id', 'flow_secret']) && matches(I, v.flow_id) && matches(H, v.flow_secret));
    const now = this.now(); const f = auth.flow;
    const i = await this.registry.installation(auth.install_id);
    requireValue(i && i.key_id === f.candidate_key_id && i.public_key === f.candidate_public_key
      && i.generation === 1 && i.owner_generation === 1, 'stale_generation');
    const outbox = await this.first(`SELECT state FROM pull_control_outbox WHERE install_id=? AND flow_id=? AND template='signup'`, auth.install_id, f.flow_id);
    requireValue(outbox, 'storage_unavailable');
    const fenced = await this.first(`SELECT 1 FROM pull_fences WHERE install_id=? AND generation=1 AND (mode='revoke' OR flow_id=?)`, auth.install_id, f.flow_id);
    const state = fenced || f.state === 'cancelled' ? 'cancelled' : i.state === 'active' ? 'active'
      : f.expires_ms <= now ? 'expired' : f.state;
    requireValue(['pending', 'verified', 'active', 'expired', 'cancelled'].includes(state), 'stale_generation');
    // Status snapshot and nonce claim are guarded against a concurrent flow/key
    // mutation. It never returns the owner's private mailbox, even when signed.
    await this.db.batch([...(await this.accounting(auth, now)), this.registry.guard(`EXISTS
      (SELECT 1 FROM pull_flows f JOIN pull_installs i ON i.install_id=f.install_id WHERE f.flow_id=?
      AND f.state=? AND i.state=? AND i.generation=1 AND i.owner_generation=1 AND i.key_id=? AND i.public_key=?
      AND f.flow_secret_hash=? AND f.candidate_key_id=i.key_id AND f.candidate_public_key=i.public_key)
      AND EXISTS(SELECT 1 FROM pull_fences WHERE install_id=? AND generation=1 AND (mode='revoke' OR flow_id=?))=?`,
    f.flow_id, f.state, i.state, f.candidate_key_id, f.candidate_public_key, f.flow_secret_hash,
    auth.install_id, f.flow_id, fenced ? 1 : 0)]);
    return { flow_id: f.flow_id, install_id: auth.install_id, generation: 1, owner_generation: 1,
      address: `${i.address_local}@${this.domain}`, state, expires_ms: f.expires_ms,
      verification_state: { queued: 'queued', sending: 'uncertain', accepted: 'accepted', uncertain: 'uncertain', failed: 'failed', expired: 'failed' }[outbox.state] };
  }

  async getConfirmation(token) {
    const now = this.now();
    const { row, flow, scope } = await this.mail.readForConfirmation(token, now);
    requireValue(row.purpose === 'signup', 'not_found');
    const install = await this.registry.installation(row.install_id);
    requireValue(install && install.owner_hash === row.owner_hash, 'stale_generation');
    const sessions = await this.first('SELECT COUNT(*) AS n FROM pull_browser_sessions WHERE install_id=? AND expires_ms>?', row.install_id, now);
    requireValue(sessions.n < 30, 'abuse_limit');
    const session = freshSecret(); const csrf = freshSecret();
    const end = Math.min(now + SESSION_TTL, row.expires_ms);
    requireValue(this.now() < end, 'token_expired');
    await this.db.batch([
      ...this.mail.scopeStatements(scope, flow),
      this.stmt(`DELETE FROM pull_browser_sessions WHERE install_id=? AND expires_ms<=?`, row.install_id, now),
      this.registry.guard(`(SELECT COUNT(*) FROM pull_browser_sessions WHERE install_id=?)<30`, row.install_id),
      this.registry.guard(`EXISTS (SELECT 1 FROM pull_tokens WHERE token_hash=? AND state='pending' AND expires_ms>?)`, row.token_hash, now),
      this.stmt(`INSERT INTO pull_browser_sessions VALUES(?,?,?,?,?,'confirmation',1,1,'csrf',?,?)`,
        await hash(session), await hash(csrf), row.install_id, row.flow_id, row.token_hash, now, end),
    ]);
    const mailbox = install.owner_address;
    const page = confirmationPage({ token, csrf, purpose: 'signup', owner_mask: `***@${mailbox.slice(mailbox.lastIndexOf('@') + 1)}`,
      install_id: row.install_id, candidate_fingerprint: await sha256(unbase64url(flow.candidate_public_key)) });
    page.headers.set('set-cookie', `${COOKIE}=${session}; Path=/mail; Secure; HttpOnly; SameSite=Strict; Max-Age=${Math.floor((end - now) / 1000)}`);
    return page;
  }

  async confirm(request, token, csrf) {
    requireValue(request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
    const sessionSecret = cookieSecret(request);
    const now = this.now();
    const sessionHash = await hash(sessionSecret); const csrfHash = await hash(csrf); const tokenHash = await hash(token);
    const session = await this.first(`SELECT * FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=?
      AND token_hash=? AND purpose='confirmation' AND state IN ('csrf','consumed') AND created_ms<=?
      AND expires_ms-created_ms<=600000`, sessionHash, csrfHash, tokenHash, now);
    requireValue(session, 'csrf_failed');
    const row = await this.first('SELECT * FROM pull_tokens WHERE token_hash=?', tokenHash);
    requireValue(row?.purpose === 'signup' && row.install_id === session.install_id && row.flow_id === session.flow_id
      && row.generation === session.generation && row.owner_generation === session.owner_generation, 'csrf_failed');
    requireValue(row.expires_ms > now, 'token_expired');
    requireValue(session.expires_ms > now, 'csrf_failed');
    if (row.state === 'consumed') {
      // Only the original consumed browser session can display the fixed receipt.
      // No second consumption, send, lifecycle write or new authority on replay.
      requireValue(session.state === 'consumed' && row.result_json === '{"state":"confirmed"}', 'already_sent');
      return html('Email confirmed. Return to Clayrune to enable mail.');
    }
    const consume = await this.mail.consumeStatements({ method: request.method, token, csrf,
      session_secret: sessionSecret, request_origin: request.headers.get('origin') }, now);
    requireValue(row.expires_ms > this.now(), 'token_expired');
    requireValue(session.expires_ms > this.now(), 'csrf_failed');
    const r = this.registry;
    await this.db.batch([...consume.statements,
      this.stmt(`UPDATE pull_installs SET state='verified',updated_ms=? WHERE install_id=? AND state='pending'
        AND generation=1 AND owner_generation=1 AND key_id=(SELECT candidate_key_id FROM pull_flows WHERE flow_id=?)
        AND public_key=(SELECT candidate_public_key FROM pull_flows WHERE flow_id=?)`, now, row.install_id, row.flow_id, row.flow_id), r.changed(),
      this.stmt(`UPDATE pull_flows SET state='verified',result_json=? WHERE flow_id=? AND state='pending' AND expires_ms>?`,
        JSON.stringify(consume.receipt), row.flow_id, now), r.changed(),
      this.stmt(`UPDATE pull_browser_sessions SET state='consumed' WHERE session_hash=? AND state='csrf'`, sessionHash), r.changed(),
    ]);
    return html('Email confirmed. Return to Clayrune to enable mail.');
  }

  async handle(request, { trustedSource } = {}) {
    let auth; let path; let browser = false; let requestId;
    try {
      const url = new URL(request.url); path = url.pathname; browser = path.startsWith('/mail/');
      requireValue(url.origin === this.serviceOrigin && SIGNUP_PATHS.includes(path), 'not_found');
      await this.sourceLimit(trustedSource, this.now());
      requireValue(!request.url.includes('#') && (path === '/mail/link'
        ? /^\?token=[a-f0-9]{64}$/.test(url.search) : !request.url.includes('?')));
      requireValue([...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) <= 8192, 'body_limit');
      requireValue(!request.headers.has('content-encoding'));
      if (path === '/mail/link') {
        requireValue(request.method === 'GET' && !request.body);
        return await this.getConfirmation(url.search.slice(7));
      }
      requireValue(request.method === 'POST');
      const type = request.headers.get('content-type') ?? '';
      requireValue(browser ? /^application\/x-www-form-urlencoded(?:;\s*charset=utf-8)?$/i.test(type)
        : /^application\/json(?:;\s*charset=utf-8)?$/i.test(type));
      const limit = browser ? 4096 : 8192;
      const length = request.headers.get('content-length');
      requireValue(length === null || matches(DEC, length) && safe(Number(length)) && Number(length) <= limit, 'body_limit');
      const raw = await readRequestBody(request.body, { limit });
      if (browser) { const form = formBody(raw); return await this.confirm(request, form.token, form.csrf); }
      requireValue(!request.headers.has('origin'), 'unauthorized');
      const value = flatJSON(raw);
      auth = await this.authenticate(request, raw, value, path);
      requestId = path === START ? value.op_id : auth.nonce;
      const result = path === START ? await this.start(auth, value) : await this.status(auth, value);
      return await this.response(auth, path, { v: 1, code: 'ok', request_id: requestId, ...result });
    } catch (error) {
      let code = CODES.has(error?.message) ? error.message : 'storage_unavailable';
      if (!CODES.has(error?.message)) console.error('[agent-mail] signup_http_failed');
      if (code !== 'storage_unavailable' && code !== 'service_unavailable') {
        try { if (!await this.refusalLimit(this.now())) code = 'abuse_limit'; }
        catch {
          console.error('[agent-mail] signup_refusal_accounting_failed');
          code = 'storage_unavailable';
        }
      }
      const status = CODES.get(code);
      let retryAt;
      let response;
      try {
        if (status === 429 || status === 503) {
          const now = this.now();
          retryAt = ['request_limit', 'verification_limit'].includes(code)
            ? (Math.floor(now / DAY) + 1) * DAY : now + 60_000;
        }
        response = browser ? html(code, status) : await this.response(auth && matches(I, requestId) ? auth : null,
          path, { v: 1, code, ...(auth && matches(I, requestId) ? { request_id: requestId,
            ...(retryAt === undefined ? {} : { retry_after_ms: retryAt }) } : {}) }, status);
        if (retryAt !== undefined) response.headers.set('retry-after', String(Math.max(1, Math.ceil((retryAt - this.now()) / 1000))));
      } catch {
        // Signing/clock failures cannot escape as raw runtime diagnostics or be
        // mistaken for verified evidence. A fixed unsigned refusal is terminal.
        console.error('[agent-mail] signup_response_failed');
        return Response.json({ v: 1, code: 'service_unavailable' }, { status: 503, headers: commonHeaders });
      }
      return response;
    }
  }
}
