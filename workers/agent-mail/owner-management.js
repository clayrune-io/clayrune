// Pull contract v1 sections 4, 6-8: old-mailbox step-up only. Unwired.
// No owner/key mutation, provider call or local human/passcode substitute here.
// Construction uses SignupHTTP's reviewed configuration and shared abuse limits;
// its generation-1 signup authorization/response methods are overridden below.
import { SignupHTTP } from './signup-http.js';
import { bytes, randomId, sha256, base64url, unbase64url } from './signing.js';
import { verifyProof } from './enrollment-transcript.js';
import { VERIFICATION_TTL, confirmationPage, verificationBinding } from './verification-mail.js';
import { readRequestBody } from './request-body.js';

export const MANAGEMENT_PATHS = Object.freeze(['/v1/pull/management/start', '/v1/pull/management/status',
  '/mail/link', '/mail/link/confirm', '/mail/manage', '/mail/logout']);
const START = MANAGEMENT_PATHS[0];
const PURPOSES = ['owner_change', 'key_recovery', 'resume', 'rotate', 'delete'];
const FIELDS = ['op_id', 'flow_id', 'purpose', 'candidate_key_id', 'candidate_public_key',
  'candidate_signature', 'flow_secret_hash', 'fence_hash'];
const HEADERS = ['version', 'direction', 'key_id', 'install_id', 'generation', 'owner_generation',
  'timestamp', 'nonce', 'digest', 'signature'];
const DAY = 86_400_000;
const TTL = 600_000;
const SKEW = 300_000;
const COOKIE = '__Secure-clayrune-mail';
const I = /^[a-f0-9]{32}$/;
const H = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const DEC = /^(0|[1-9][0-9]*)$/;
const G = /^[1-9][0-9]{0,14}$/;
const valid = (ok, code = 'invalid_request') => { if (!ok) throw new Error(code); };
const match = (re, s) => typeof s === 'string' && re.test(s);
const exact = (v, fields) => v && Object.keys(v).sort().join(',') === [...fields].sort().join(',');
const hash = (s) => sha256(bytes(s));
const secret = () => randomId() + randomId();
const headers = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
const htmlHeaders = { ...headers, 'content-type': 'text/html; charset=utf-8', 'referrer-policy': 'no-referrer',
  'x-frame-options': 'DENY', 'content-security-policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" };
const page = (body, status = 200) => new Response('<!doctype html><html lang="en"><meta charset="utf-8">'
  + '<title>Clayrune mail management</title>' + body + '</html>', { status, headers: htmlHeaders });
const cookie = (s, seconds) => `${COOKIE}=${s}; Path=/mail; Secure; HttpOnly; SameSite=Strict; Max-Age=${seconds}`;
const CODES = new Map([[400, ['invalid_request', 'invalid_json']], [401, ['unauthorized', 'csrf_failed', 'session_required']],
  [404, ['not_found']], [408, ['body_timeout']],
  [409, ['operation_conflict', 'nonce_replayed', 'stale_generation', 'registration_inactive', 'registration_frozen', 'fenced', 'already_sent']],
  [410, ['flow_expired', 'token_expired']], [413, ['body_limit']],
  [429, ['request_limit', 'verification_limit', 'abuse_limit']], [503, ['storage_unavailable', 'service_unavailable']]]
  .flatMap(([status, codes]) => codes.map((code) => [code, status])));

// Management JSON contains strings only. Tokenize before JSON.parse to refuse
// duplicate decoded keys, nesting, invalid UTF-8/BOM and trailing input. Unlike
// a JSON.parse reviver, this does not silently discard duplicate authority fields.
function stringsJSON(raw) {
  try {
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
    const tokens = text.match(/"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|[{}:,]|[\x20\t\r\n]+/g) ?? [];
    valid(tokens.join('') === text, 'invalid_json');
    const significant = tokens.filter((s) => !/^[\x20\t\r\n]+$/.test(s));
    valid(significant.shift() === '{' && significant.pop() === '}', 'invalid_json');
    const result = Object.create(null);
    for (let at = 0; at < significant.length; at += 4) {
      valid(at === 0 || significant[at - 1] === ',', 'invalid_json');
      const key = JSON.parse(significant[at]);
      const value = JSON.parse(significant[at + 2]);
      valid(significant[at + 1] === ':' && typeof key === 'string' && typeof value === 'string'
        && !Object.hasOwn(result, key) && [key, value].every((s) =>
          !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(s)), 'invalid_json');
      result[key] = value;
    }
    valid(significant.length === 0 || significant.length % 4 === 3, 'invalid_json');
    return result;
  } catch { throw new Error('invalid_json'); } // Wire syntax only; no I/O.
}
function form(raw, fields) {
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw); }
  catch { throw new Error('invalid_request'); } // Wire syntax only; no I/O.
  valid(/^[a-z_]+=[a-f0-9]+(?:&[a-z_]+=[a-f0-9]+)*$/.test(text));
  const entries = [...new URLSearchParams(text)];
  const value = Object.fromEntries(entries);
  valid(entries.length === fields.length && exact(value, fields) && fields.every((k) => match(H, value[k])));
  return value;
}
function browserSecret(request) {
  const entries = (request.headers.get('cookie') ?? '').split(';').map((s) => s.trim().split('='))
    .filter(([name]) => name === COOKIE);
  valid(entries.length === 1 && entries[0].length === 2 && match(H, entries[0][1]), 'session_required');
  return entries[0][1];
}

/** handle(request,{trustedSource}) owns MANAGEMENT_PATHS, but ONLY stepup links.
 * Unit 14 dispatches signup/new-owner/revert links to their respective handlers.
 * Config is the same reviewed db/origin/domain/signing/VerificationMail object
 * as SignupHTTP. trustedSource comes from the edge, never a forwarding header.
 */
export class OwnerManagementHTTP extends SignupHTTP {
  async response(auth, path, value, status = 200) {
    if (!auth) return Response.json(value, { status, headers });
    const raw = bytes(JSON.stringify(value));
    const fields = { version: '2', direction: 'pull-to-install', key_id: this.serviceKeyId,
      install_id: auth.install_id, generation: auth.generation, owner_generation: auth.owner_generation,
      timestamp: String(this.now()), nonce: auth.nonce, digest: await sha256(raw) };
    const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', fields.direction,
      this.serviceOrigin, 'POST', path, fields.key_id, fields.install_id, fields.generation,
      fields.owner_generation, fields.timestamp, fields.nonce, String(status), fields.digest]));
    const output = new Headers({ ...headers, 'content-type': 'application/json' });
    for (const [key, value] of Object.entries(fields)) output.set(`x-mail-${key.replaceAll('_', '-')}`, value);
    const key = await crypto.subtle.importKey('pkcs8', unbase64url(this.servicePrivateKey), 'Ed25519', false, ['sign']);
    output.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', key, transcript)));
    return new Response(raw, { status, headers: output });
  }

  async authenticate(request, raw, value, path) {
    const m = Object.fromEntries(HEADERS.map((k) => [k, request.headers.get(`x-mail-${k.replaceAll('_', '-')}`)]));
    valid(m.version === '2' && m.direction === 'install-to-pull' && match(ID, m.key_id)
      && match(I, m.install_id) && match(G, m.generation) && match(G, m.owner_generation)
      && match(DEC, m.timestamp) && Number.isSafeInteger(Number(m.timestamp)) && match(I, m.nonce)
      && match(H, m.digest) && m.digest === await sha256(raw)
      && Math.abs(this.now() - Number(m.timestamp)) <= SKEW, 'unauthorized');
    const generation = await this.first('SELECT * FROM pull_generations WHERE install_id=? AND generation=? AND owner_generation=?',
      m.install_id, Number(m.generation), Number(m.owner_generation));
    valid(generation, 'unauthorized');
    let publicKey;
    if (path === START) {
      // A recovery candidate proves possession only; it cannot manage anything
      // until the CURRENT owner's mailbox approves this exact candidate.
      publicKey = value.purpose === 'key_recovery' && value.candidate_key_id === m.key_id
        ? value.candidate_public_key : generation.key_id === m.key_id ? generation.public_key : null;
    } else {
      valid(match(I, value.flow_id) && match(H, value.flow_secret), 'unauthorized');
      const flow = await this.first('SELECT * FROM pull_flows WHERE flow_id=? AND install_id=?', value.flow_id, m.install_id);
      valid(flow && PURPOSES.includes(flow.purpose) && flow.expected_generation === Number(m.generation)
        && flow.expected_owner_generation === Number(m.owner_generation) && flow.flow_secret_hash === await hash(value.flow_secret), 'unauthorized');
      publicKey = flow.purpose === 'key_recovery' ? flow.candidate_key_id === m.key_id && flow.candidate_public_key
        : generation.key_id === m.key_id && generation.public_key;
      m.flow = flow;
    }
    const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction,
      this.serviceOrigin, 'POST', path, m.key_id, m.install_id, m.generation, m.owner_generation,
      m.timestamp, m.nonce, m.digest]));
    valid(await verifyProof(publicKey, m.signature, transcript), 'unauthorized');
    return m;
  }

  async accounting(auth, now) {
    valid(Math.abs(now - Number(auth.timestamp)) <= SKEW, 'unauthorized');
    valid(!await this.first(`SELECT 1 FROM pull_nonces WHERE direction='install-to-pull' AND install_id=?
      AND generation=? AND key_id=? AND nonce=?`, auth.install_id, Number(auth.generation), auth.key_id, auth.nonce), 'nonce_replayed');
    const bucket = `pull:http:management:${auth.install_id}:${Math.floor(now / DAY)}`;
    const budget = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
    valid(!budget || budget.count < 500, 'request_limit');
    return [this.stmt(`INSERT INTO pull_nonces VALUES('install-to-pull',?,?,?,?,?)`,
      auth.install_id, Number(auth.generation), auth.key_id, auth.nonce, Number(auth.timestamp) + SKEW + 1),
    this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<500`,
      bucket, (Math.floor(now / DAY) + 2) * DAY), this.registry.changed()];
  }

  // Immutable snapshots are rechecked in the SAME D1 batch as every write.
  installGuard(i) {
    return this.registry.guard(`EXISTS (SELECT 1 FROM pull_installs WHERE install_id=? AND generation=?
      AND owner_generation=? AND owner_hash=? AND key_id=? AND public_key=? AND state=? AND consent_version=?)`,
    i.install_id, i.generation, i.owner_generation, i.owner_hash, i.key_id, i.public_key, i.state, i.consent_version);
  }
  fenceGuard(flow) {
    return this.registry.guard(`NOT EXISTS (SELECT 1 FROM pull_fences WHERE install_id=? AND generation=?
      AND (mode='revoke' OR flow_id=?))`, flow.install_id, flow.expected_generation, flow.flow_id);
  }
  flowGuard(f) {
    return this.registry.guard(`EXISTS (SELECT 1 FROM pull_flows WHERE flow_id=? AND install_id=? AND purpose=?
      AND expected_generation=? AND expected_owner_generation=? AND candidate_key_id=? AND candidate_public_key=?
      AND owner_hash=? AND flow_secret_hash=? AND fence_hash=? AND input_digest=? AND state=? AND expires_ms=? AND result_json IS ?
      AND consent_version=? AND manifest_sequence=? AND manifest_digest=? AND created_ms=?)`,
    f.flow_id, f.install_id, f.purpose, f.expected_generation, f.expected_owner_generation, f.candidate_key_id,
    f.candidate_public_key, f.owner_hash, f.flow_secret_hash, f.fence_hash, f.input_digest, f.state, f.expires_ms, f.result_json ?? null,
    f.consent_version, f.manifest_sequence, f.manifest_digest, f.created_ms);
  }

  async start(auth, v) {
    valid(exact(v, FIELDS) && match(I, v.op_id) && match(I, v.flow_id) && PURPOSES.includes(v.purpose)
      && match(ID, v.candidate_key_id) && match(H, v.flow_secret_hash) && match(H, v.fence_hash));
    const proof = bytes(JSON.stringify(['clayrune-mail-pull-candidate', '1', this.serviceOrigin, v.purpose,
      v.flow_id, auth.install_id, auth.generation, auth.owner_generation, v.candidate_key_id,
      v.candidate_public_key, v.fence_hash, v.flow_secret_hash]));
    valid(await verifyProof(v.candidate_public_key, v.candidate_signature, proof), 'unauthorized');
    for (let attempt = 0; attempt < 2; attempt++) {
      const now = this.now(); const r = this.registry;
      const accounting = await this.accounting(auth, now);
      const previous = await this.first('SELECT * FROM pull_operations WHERE install_id=? AND generation=? AND path=? AND op_id=?',
        auth.install_id, Number(auth.generation), START, v.op_id);
      if (previous) {
        valid(previous.input_digest === auth.digest, 'operation_conflict');
        // Retired keys can retrieve only this exact committed operation result.
        await this.db.batch(accounting); return JSON.parse(previous.result_json);
      }
      const i = await r.installation(auth.install_id);
      valid(i && i.generation === Number(auth.generation) && i.owner_generation === Number(auth.owner_generation), 'stale_generation');
      valid(!['pending', 'deleted'].includes(i.state), 'registration_inactive');
      valid(i.state !== 'frozen' || ['resume', 'key_recovery', 'delete'].includes(v.purpose), 'registration_frozen');
      valid(v.purpose !== 'resume' || i.state === 'frozen', 'registration_inactive');
      valid(v.purpose === 'key_recovery' ? auth.key_id === v.candidate_key_id : auth.key_id === i.key_id, 'unauthorized');
      valid(!['owner_change', 'resume', 'delete'].includes(v.purpose)
        || v.candidate_key_id === i.key_id && v.candidate_public_key === i.public_key, 'unauthorized');
      valid(!await this.first('SELECT 1 FROM pull_flows WHERE flow_id=?', v.flow_id), 'operation_conflict');
      const parent = await this.first(`SELECT f.* FROM pull_generations g JOIN pull_flows f ON f.flow_id=g.flow_id
        WHERE g.install_id=? AND g.generation=? AND g.state='current' AND g.owner_hash=? AND g.key_id=? AND g.public_key=?`,
      i.install_id, i.generation, i.owner_hash, i.key_id, i.public_key);
      valid(parent, 'stale_generation');
      const fenced = await this.first(`SELECT 1 FROM pull_fences WHERE install_id=? AND generation=? AND (mode='revoke' OR flow_id=?)`,
        i.install_id, i.generation, v.flow_id);
      valid(!fenced, 'fenced');
      const active = await this.first(`SELECT 1 FROM pull_flows WHERE install_id=? AND purpose=? AND state IN ('pending','verified','approved') AND expires_ms>?`,
        i.install_id, v.purpose, now);
      valid(!active, 'operation_conflict');
      for (const [bucket, cap] of [[`pull:verify:install:${i.install_id}:${Math.floor(now / DAY)}`, 3],
        [`pull:verify:target:${i.owner_hash}:${Math.floor(now / DAY)}`, 3], [`pull:send:global:${Math.floor(now / DAY)}`, 1000]]) {
        const row = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
        valid(!row || row.count < cap, 'verification_limit');
      }
      const end = now + VERIFICATION_TTL;
      const f = { ...v, install_id: i.install_id, expected_generation: i.generation, expected_owner_generation: i.owner_generation,
        owner_hash: i.owner_hash, consent_version: i.consent_version, manifest_sequence: parent.manifest_sequence,
        manifest_digest: parent.manifest_digest, created_ms: now, expires_ms: end };
      const result = { flow_id: f.flow_id, state: 'pending', expires_ms: end, verification_state: 'queued' };
      const issued = await this.mail.issueStatements({ outbox_id: randomId(), template: 'stepup', to: i.owner_address, flow: f }, now);
      const statements = [...accounting, this.installGuard(i), this.fenceGuard(f),
        r.guard(`EXISTS (SELECT 1 FROM pull_generations WHERE install_id=? AND generation=? AND state='current'
          AND owner_generation=? AND owner_hash=? AND key_id=? AND public_key=? AND flow_id=?)`,
        i.install_id, i.generation, i.owner_generation, i.owner_hash, i.key_id, i.public_key, parent.flow_id), this.flowGuard(parent),
        this.stmt(`UPDATE pull_flows SET state='expired' WHERE install_id=? AND purpose=?
          AND state IN ('pending','verified','approved') AND expires_ms<=?`, i.install_id, v.purpose, now),
        this.stmt(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,
          candidate_key_id,candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,
          consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)`,
        f.flow_id, i.install_id, f.purpose, i.generation, i.owner_generation, f.candidate_key_id, f.candidate_public_key,
        i.owner_hash, f.flow_secret_hash, f.fence_hash, auth.digest, f.consent_version, f.manifest_sequence, f.manifest_digest, now, end),
        ...issued.statements,
        this.stmt(`INSERT INTO pull_operations VALUES(?,?,?,?,?,?,200,?,?)`, i.install_id, i.generation, START,
          v.op_id, auth.digest, JSON.stringify(result), now, end + 30 * DAY)];
      valid(Math.abs(this.now() - Number(auth.timestamp)) <= SKEW, 'unauthorized');
      valid(this.now() < end, 'flow_expired');
      try { await this.db.batch(statements); return result; }
      catch (error) { if (attempt) throw error; } // Entire failed batch rolls back; recheck a possible race once.
    }
  }

  async status(auth, v) {
    valid(exact(v, ['flow_id', 'flow_secret']));
    const f = auth.flow; const now = this.now();
    const fenced = Boolean(await this.first(`SELECT 1 FROM pull_fences WHERE install_id=? AND generation=? AND (mode='revoke' OR flow_id=?)`,
      f.install_id, f.expected_generation, f.flow_id));
    const terminal = ['completed', 'cancelled', 'reverted'].includes(f.state);
    const state = terminal ? f.state : fenced ? 'cancelled' : f.expires_ms <= now ? 'expired' : f.state;
    valid(['pending', 'approved', 'completed', 'expired', 'cancelled', 'reverted'].includes(state), 'storage_unavailable');
    let result = null;
    if (['completed', 'reverted'].includes(state)) {
      result = JSON.parse(f.result_json);
      valid(exact(result, ['install_id', 'generation', 'owner_generation', 'state', 'change_id'])
        && result.install_id === f.install_id && Number.isSafeInteger(result.generation) && result.generation > 0
        && result.generation <= 999_999_999_999_999 && Number.isSafeInteger(result.owner_generation)
        && result.owner_generation > 0 && result.owner_generation <= 999_999_999_999_999
        && ['verified', 'frozen', 'active', 'deleted'].includes(result.state)
        && (result.change_id === null || match(I, result.change_id)), 'storage_unavailable');
      valid(f.expires_ms + 30 * DAY > now, 'flow_expired');
    }
    // No current-owner/key fields are read or exposed to a retired starting key.
    await this.db.batch([...await this.accounting(auth, now), this.flowGuard(f),
      this.registry.guard(`EXISTS (SELECT 1 FROM pull_fences WHERE install_id=? AND generation=? AND (mode='revoke' OR flow_id=?))=?`,
        f.install_id, f.expected_generation, f.flow_id, Number(fenced))]);
    return { flow_id: f.flow_id, state, expires_ms: f.expires_ms, result };
  }

  async getConfirmation(token) {
    const now = this.now();
    const { row, flow, scope } = await this.mail.readForConfirmation(token, now);
    valid(row.purpose === 'stepup', 'not_found');
    const i = await this.registry.installation(row.install_id);
    valid(i && i.owner_hash === row.owner_hash, 'stale_generation');
    const sessions = await this.first('SELECT COUNT(*) AS n FROM pull_browser_sessions WHERE install_id=? AND expires_ms>?', i.install_id, now);
    valid(sessions.n < 30, 'abuse_limit');
    const session = secret(); const csrf = secret(); const end = Math.min(now + TTL, row.expires_ms);
    valid(this.now() < end, 'token_expired');
    await this.db.batch([...this.mail.scopeStatements(scope, flow), this.flowGuard(flow), this.installGuard(i),
      this.stmt('DELETE FROM pull_browser_sessions WHERE install_id=? AND expires_ms<=?', i.install_id, now),
      this.registry.guard('(SELECT COUNT(*) FROM pull_browser_sessions WHERE install_id=?)<30', i.install_id),
      this.registry.guard(`EXISTS (SELECT 1 FROM pull_tokens WHERE token_hash=? AND state='pending' AND expires_ms>?)`, row.token_hash, now),
      this.stmt(`INSERT INTO pull_browser_sessions VALUES(?,?,?,?,?,'confirmation',?,?,'csrf',?,?)`,
        await hash(session), await hash(csrf), row.install_id, row.flow_id, row.token_hash, row.generation, row.owner_generation, now, end)]);
    const p = confirmationPage({ token, csrf, purpose: 'stepup', flow_purpose: flow.purpose,
      owner_mask: `***@${i.owner_address.slice(i.owner_address.lastIndexOf('@') + 1)}`, install_id: i.install_id,
      candidate_fingerprint: await sha256(unbase64url(flow.candidate_public_key)) });
    p.headers.set('set-cookie', cookie(session, Math.floor((end - now) / 1000))); return p;
  }

  async confirm(request, token, csrf) {
    valid(request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
    const s = browserSecret(request); const now = this.now();
    const sh = await hash(s); const ch = await hash(csrf); const th = await hash(token);
    const session = await this.first(`SELECT * FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=? AND token_hash=?`, sh, ch, th);
    valid(session && session.created_ms <= now && session.expires_ms > now && session.expires_ms - session.created_ms <= TTL, 'csrf_failed');
    const row = await this.first('SELECT * FROM pull_tokens WHERE token_hash=?', th);
    valid(row?.purpose === 'stepup' && row.install_id === session.install_id && row.flow_id === session.flow_id
      && row.generation === session.generation && row.owner_generation === session.owner_generation, 'csrf_failed');
    valid(row.expires_ms > now, 'token_expired');
    const flow = await this.first('SELECT * FROM pull_flows WHERE flow_id=?', row.flow_id);
    valid(flow && PURPOSES.includes(flow.purpose), 'stale_generation');
    if (row.state === 'consumed') {
      valid(session.purpose === flow.purpose && session.state === 'approved'
        && row.result_json === '{"state":"confirmed"}', 'already_sent');
      // Fixed receipt only, including if a later change superseded this session.
      // This replay does not mint, refresh or restore management authority.
      return page('<p>Management request confirmed.</p><a href="/mail/manage">Manage mail</a>');
    }
    valid(session.purpose === 'confirmation' && session.state === 'csrf', 'csrf_failed');
    const i = await this.registry.installation(row.install_id);
    const consume = await this.mail.consumeStatements({ method: request.method, token, csrf,
      session_secret: s, request_origin: request.headers.get('origin') }, now);
    const end = Math.min(now + TTL, flow.expires_ms);
    valid(this.now() < end && this.now() < session.expires_ms, 'csrf_failed');
    await this.db.batch([...consume.statements, this.flowGuard(flow), this.installGuard(i),
      this.stmt(`UPDATE pull_flows SET state='approved' WHERE flow_id=? AND state='pending' AND expires_ms>?`, flow.flow_id, now), this.registry.changed(),
      this.stmt(`UPDATE pull_browser_sessions SET purpose=?,state='approved',created_ms=?,expires_ms=?
        WHERE session_hash=? AND csrf_hash=? AND state='csrf' AND purpose='confirmation' AND expires_ms>?`,
      flow.purpose, now, end, sh, ch, now), this.registry.changed()]);
    const p = page('<p>Management request confirmed.</p><a href="/mail/manage">Manage mail</a>');
    p.headers.set('set-cookie', cookie(s, Math.floor((end - now) / 1000))); return p;
  }

  /** Unit 05/06 seam: validate the browser POST, then append ALL returned
   * statements to the SAME atomic batch as the purpose-specific mutation.
   * Never treat this JS snapshot alone as authorization; do not run a prefix.
   */
  async authorizeSession(request, csrf, purpose) {
    valid(request.method === 'POST' && request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
    valid(PURPOSES.includes(purpose) && match(H, csrf), 'csrf_failed');
    return this.sessionScope(request, purpose, await hash(csrf));
  }
  async sessionScope(request, purpose = null, csrfHash = null) {
    const now = this.now(); const sh = await hash(browserSecret(request));
    const s = await this.first('SELECT * FROM pull_browser_sessions WHERE session_hash=?', sh);
    valid(s && s.state === 'approved' && PURPOSES.includes(s.purpose) && (!purpose || s.purpose === purpose)
      && (!csrfHash || s.csrf_hash === csrfHash) && s.created_ms <= now && s.expires_ms > now
      && s.expires_ms - s.created_ms <= TTL, 'session_required');
    const f = await this.first('SELECT * FROM pull_flows WHERE flow_id=?', s.flow_id);
    const i = await this.registry.installation(s.install_id);
    valid(f && i && f.state === 'approved' && f.purpose === s.purpose && f.install_id === s.install_id
      && f.expected_generation === s.generation && f.expected_owner_generation === s.owner_generation
      && i.generation === s.generation && i.owner_generation === s.owner_generation && i.owner_hash === f.owner_hash
      && i.state !== 'deleted' && f.expires_ms > now
      && (!['owner_change', 'resume', 'delete'].includes(f.purpose)
        || f.candidate_key_id === i.key_id && f.candidate_public_key === i.public_key), 'session_required');
    const token = await this.first('SELECT * FROM pull_tokens WHERE token_hash=?', s.token_hash);
    valid(token?.state === 'consumed' && token.purpose === 'stepup' && token.install_id === s.install_id
      && token.flow_id === s.flow_id && token.generation === s.generation && token.owner_generation === s.owner_generation
      && token.owner_hash === f.owner_hash && token.change_id === null, 'session_required');
    // Owner-change initiation may extend the FLOW deadline, but never the old
    // token or browser session. Recompute its original candidate binding using
    // the token's immutable deadline, not that later flow deadline.
    valid(token.binding_digest === await verificationBinding({ purpose: 'stepup', flow_id: f.flow_id, change_id: null,
      install_id: f.install_id, generation: f.expected_generation, owner_generation: f.expected_owner_generation,
      owner_hash: f.owner_hash, candidate_key_id: f.candidate_key_id, candidate_public_key: f.candidate_public_key,
      expires_ms: token.expires_ms }), 'session_required');
    const statements = [this.flowGuard(f), this.installGuard(i), this.fenceGuard(f),
      this.registry.guard(`EXISTS (SELECT 1 FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=? AND install_id=?
        AND flow_id=? AND token_hash=? AND purpose=? AND generation=? AND owner_generation=? AND state='approved'
        AND created_ms=? AND expires_ms=? AND expires_ms>?)`,
      sh, s.csrf_hash, s.install_id, s.flow_id, s.token_hash, s.purpose, s.generation, s.owner_generation, s.created_ms, s.expires_ms, now),
      this.registry.guard(`EXISTS (SELECT 1 FROM pull_tokens WHERE token_hash=? AND purpose='stepup' AND state='consumed'
        AND install_id=? AND flow_id=? AND generation=? AND owner_generation=? AND owner_hash=?
        AND binding_digest=? AND expires_ms=? AND change_id IS NULL)`,
      s.token_hash, s.install_id, s.flow_id, s.generation, s.owner_generation, f.owner_hash, token.binding_digest, token.expires_ms)];
    valid(!await this.first(`SELECT 1 FROM pull_fences WHERE install_id=? AND generation=? AND (mode='revoke' OR flow_id=?)`,
      s.install_id, s.generation, s.flow_id), 'session_required');
    return { session: s, flow: f, install: i, statements };
  }

  async manage(request) {
    const scope = await this.sessionScope(request); const { session: s } = scope;
    const csrf = secret(); const ch = await hash(csrf);
    // GET rotates CSRF state only, never the absolute authority deadline.
    await this.db.batch([...scope.statements, this.stmt(`UPDATE pull_browser_sessions SET csrf_hash=? WHERE session_hash=? AND state='approved'`,
      ch, s.session_hash), this.registry.changed()]);
    const labels = { owner_change: ['owner-change', 'Change owner email'], key_recovery: ['key-recovery', 'Recover mail key'],
      resume: ['resume', 'Resume mail'], delete: ['delete', 'Delete mail registration'] };
    let body = `<h1>Manage mail</h1><p>Installation: ${s.install_id}</p>`;
    if (labels[s.purpose]) {
      const [action, label] = labels[s.purpose];
      body += `<form method="post" action="/mail/${action}"><input type="hidden" name="csrf" value="${csrf}">`
        + `<input type="hidden" name="op_id" value="${randomId()}">`
        + (s.purpose === 'owner_change' ? '<label>New owner email <input type="email" name="new_owner" required maxlength="254"></label>' : '')
        + `<button type="submit">${label}</button></form>`;
    } else body += '<p>Key rotation approved. Return to Clayrune to finish this request.</p>';
    body += `<form method="post" action="/mail/logout"><input type="hidden" name="csrf" value="${csrf}">`
      + '<button type="submit">Log out</button></form>';
    return page(body);
  }
  async logout(request, csrf) {
    const scope = await this.sessionScope(request, null, await hash(csrf));
    valid(request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
    await this.db.batch([...scope.statements, this.stmt(`UPDATE pull_browser_sessions SET state='invalidated'
      WHERE session_hash=? AND state='approved'`, scope.session.session_hash), this.registry.changed()]);
    const p = page('<p>Logged out.</p>'); p.headers.set('set-cookie', cookie('', 0)); return p;
  }

  async handle(request, { trustedSource } = {}) {
    let auth; let path; let browser = false; let requestId;
    try {
      const url = new URL(request.url); path = url.pathname; browser = path.startsWith('/mail/');
      valid(url.origin === this.serviceOrigin && MANAGEMENT_PATHS.includes(path), 'not_found');
      await this.sourceLimit(trustedSource, this.now());
      valid(!request.url.includes('#') && (path === '/mail/link' ? /^\?token=[a-f0-9]{64}$/.test(url.search) : !request.url.includes('?')));
      valid([...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) <= 8192, 'body_limit');
      valid(!request.headers.has('content-encoding'));
      if (path === '/mail/link' || path === '/mail/manage') {
        valid(request.method === 'GET' && !request.body);
        return path === '/mail/link' ? await this.getConfirmation(url.search.slice(7)) : await this.manage(request);
      }
      valid(request.method === 'POST');
      const type = request.headers.get('content-type') ?? '';
      valid(browser ? /^application\/x-www-form-urlencoded(?:;\s*charset=utf-8)?$/i.test(type) : /^application\/json(?:;\s*charset=utf-8)?$/i.test(type));
      const limit = browser ? 4096 : 8192; const length = request.headers.get('content-length');
      valid(length === null || match(DEC, length) && Number.isSafeInteger(Number(length)) && Number(length) <= limit, 'body_limit');
      const raw = await readRequestBody(request.body, { limit });
      if (browser) {
        valid(request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
        const v = form(raw, path === '/mail/logout' ? ['csrf'] : ['csrf', 'token']);
        return path === '/mail/logout' ? await this.logout(request, v.csrf) : await this.confirm(request, v.token, v.csrf);
      }
      valid(!request.headers.has('origin'), 'unauthorized');
      const v = stringsJSON(raw); auth = await this.authenticate(request, raw, v, path);
      requestId = path === START ? v.op_id : auth.nonce;
      const result = path === START ? await this.start(auth, v) : await this.status(auth, v);
      return await this.response(auth, path, { v: 1, code: 'ok', request_id: requestId, ...result });
    } catch (error) {
      let code = CODES.has(error?.message) ? error.message : 'storage_unavailable';
      if (!CODES.has(error?.message)) console.error('[agent-mail] owner_management_failed');
      try {
        if (!['storage_unavailable', 'service_unavailable'].includes(code) && !await this.refusalLimit(this.now())) code = 'abuse_limit';
        const status = CODES.get(code); let retryAt;
        if (status === 429 || status === 503) {
          const now = this.now(); retryAt = ['request_limit', 'verification_limit'].includes(code) ? (Math.floor(now / DAY) + 1) * DAY : now + 60_000;
        }
        const output = browser ? page(`<p>${code}</p>`, status) : await this.response(auth && match(I, requestId) ? auth : null,
          path, { v: 1, code, ...(auth && match(I, requestId) ? { request_id: requestId,
            ...(retryAt === undefined ? {} : { retry_after_ms: retryAt }) } : {}) }, status);
        if (retryAt !== undefined) output.headers.set('retry-after', String(Math.max(1, Math.ceil((retryAt - this.now()) / 1000))));
        return output;
      } catch {
        console.error('[agent-mail] owner_management_response_failed');
        return browser ? page('<p>service_unavailable</p>', 503) : Response.json({ v: 1, code: 'service_unavailable' }, { status: 503, headers });
      }
    }
  }
}
