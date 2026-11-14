// Pull send HTTP boundary (unit14a). Unwired; unit14 owns entry registration.
import { Ledger, DAY } from './ledger.js';
import { OwnerRegistry } from './owner-registry.js';
import { pullOutbound, validateSend } from './outbound.js';
import { verifySignedV2, PULL_SKEW_MS } from './signed-v2-verifier.js';
import { bytes, sha256, base64url, unbase64url } from './signing.js';
import { origin } from './enrollment-transcript.js';
import { strictMsgId } from './content-id.js';
import { readRequestBody } from './request-body.js';
import { SQL_NOW } from './sql-clock.js';

export const SEND_PATH = '/v1/pull/send', SEND_STATUS_PATH = '/v1/pull/send/status';
const I = /^[a-f0-9]{32}$/, ID = /^[A-Za-z0-9_-]{1,128}$/;
const STATES = ['reserved', 'sending', 'accepted', 'uncertain', 'failed', 'revoked'];
const FIELDS = ['intent_id', 'thread_id', 'message_id', 'state', 'provider_id'];
const HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
const CODES = new Map([[400, ['invalid_request', 'invalid_json', 'invalid_send']], [401, ['unauthorized']],
  [404, ['unknown_intent']], [408, ['body_timeout']], [409, ['operation_conflict', 'intent_conflict',
    'nonce_replayed', 'stale_generation', 'registration_inactive', 'registration_frozen', 'revoked']],
  [413, ['body_limit']], [429, ['request_limit', 'send_limit', 'abuse_limit']],
  [503, ['storage_unavailable', 'service_unavailable', 'provider_unavailable']]]
  .flatMap(([status, codes]) => codes.map((code) => [code, status])));
const valid = (ok, code = 'invalid_request') => { if (!ok) throw new Error(code); };
const match = (re, value) => typeof value === 'string' && re.test(value);
const exact = (v, fields) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).length === fields.length && fields.every((k) => Object.hasOwn(v, k));
const provider = (v) => typeof v === 'string' && (v === '' || /^[\x20-\x7e]{1,256}$/.test(v) && !!v.trim());

// Closed scalar objects, as in signup-http. Lex before parsing so duplicates,
// escaped duplicate keys, BOM, unsafe integers and malformed UTF-8 cannot vanish.
function flatJSON(raw) {
  try {
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
    let at = 0;
    const white = () => { while (/[ \t\r\n]/.test(text[at] ?? '\0')) at++; };
    const string = () => {
      const start = at; valid(text[at++] === '"');
      while (at < text.length) {
        const c = text[at++];
        if (c === '\\') at++;
        else if (c === '"') {
          const s = JSON.parse(text.slice(start, at));
          valid(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(s));
          return s;
        }
      }
      throw new Error('invalid_json');
    };
    white(); valid(text[at++] === '{'); white();
    const result = Object.create(null);
    if (text[at] !== '}') while (true) {
      const key = string(); white(); valid(text[at++] === ':'); white();
      valid(!Object.hasOwn(result, key));
      if (text[at] === '"') result[key] = string();
      else {
        const token = /^(0|[1-9][0-9]*)/.exec(text.slice(at)); valid(token);
        at += token[0].length; result[key] = Number(token[0]); valid(Number.isSafeInteger(result[key]));
      }
      white(); if (text[at] !== ',') break;
      at++; white();
    }
    valid(text[at++] === '}'); white(); valid(at === text.length); return result;
  } catch { throw new Error('invalid_json'); } // Wire syntax only, no I/O.
}

// The internal unit12 result is never spread onto the wire. This explicit
// projection pins the section7 all-or-nothing intent-receipt exception.
export function projectSendResult(result, value, requestId) {
  valid(match(I, requestId), 'storage_unavailable');
  const prefix = { v: 1, code: result?.code, request_id: requestId };
  const fields = result?.state === 'accepted' ? [...FIELDS, 'thread_expires_ms'] : FIELDS;
  if (result && exact(result, ['status', 'code', ...fields])) {
    valid(STATES.includes(result.state) && result.code === result.state
      && result.status === (result.state === 'accepted' ? 200 : 409)
      && result.intent_id === value.intent_id && result.thread_id === value.thread_id
      && match(ID, result.intent_id) && match(ID, result.thread_id)
      && typeof result.message_id === 'string' && result.message_id.length <= 8192
      && !!result.message_id && strictMsgId(result.message_id) === result.message_id
      && provider(result.provider_id)
      && (result.state === 'accepted' ? result.provider_id !== '' : true)
      && (!['reserved', 'sending', 'failed'].includes(result.state) || result.provider_id === '')
      && (result.state !== 'accepted' || Number.isSafeInteger(result.thread_expires_ms)
        && result.thread_expires_ms > 0 && result.thread_expires_ms <= Number.MAX_SAFE_INTEGER - 30 * DAY), 'storage_unavailable');
    return { status: result.status, body: { ...prefix, ...Object.fromEntries(fields.map((k) => [k, result[k]])) } };
  }
  valid(exact(result, ['status', 'code']) && CODES.get(result.code) === result.status, 'storage_unavailable');
  // revoked without a row is a common refusal, not a partial intent receipt.
  return { status: result.status, body: prefix };
}

/** Reviewed service keys/origin/sender only; no environment defaults or startup
 * I/O. handle(request,{trustedSource}) serves exactly SEND_PATH/SEND_STATUS_PATH.
 * trustedSource is router/edge-selected, never a claimed install or HTTP header.
 * config={from,domain}, binding=reviewed structured Email Sending binding.
 * Body timeout/timers are injectable for synthetic tests; production is 10s.
 */
export class PullSendHTTP {
  constructor({ db, serviceOrigin, serviceKeyId, servicePrivateKey, config, binding,
    clock = Date.now, timers = globalThis, bodyTimeoutMs = 10_000 }) {
    valid(db && origin(serviceOrigin) && match(ID, serviceKeyId)
      && typeof servicePrivateKey === 'string' && unbase64url(servicePrivateKey).length > 0
      && typeof clock === 'function' && Number.isInteger(bodyTimeoutMs) && bodyTimeoutMs > 0 && bodyTimeoutMs <= 10_000);
    Object.assign(this, { db, serviceOrigin, serviceKeyId, servicePrivateKey, config, binding, clock, timers, bodyTimeoutMs });
    this.registry = new OwnerRegistry(db); this.shared = new Ledger(db); this.ledger = this.shared.pull({ clock });
  }
  stmt(sql, ...args) { return this.registry.stmt(sql, ...args); }
  first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  now() {
    const n = this.clock(); valid(Number.isSafeInteger(n) && n >= 0
      && n < Number.MAX_SAFE_INTEGER - 120 * DAY, 'service_unavailable'); return n;
  }
  async current(auth) {
    return this.first(`SELECT i.*,g.flow_id,g.state AS generation_state FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.owner_hash=i.owner_hash AND g.key_id=i.key_id AND g.public_key=i.public_key
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.key_id=?
      AND i.state NOT IN ('pending','deleted')`,
    auth.install_id, Number(auth.generation), Number(auth.owner_generation), auth.key_id);
  }
  snapshotGuard(i) {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.owner_hash=i.owner_hash AND g.key_id=i.key_id AND g.public_key=i.public_key
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.key_id=? AND i.public_key=?
      AND i.owner_hash=? AND i.owner_address=? AND i.address_local=? AND i.state=? AND g.flow_id=? AND g.state=?)`,
    i.install_id, i.generation, i.owner_generation, i.key_id, i.public_key, i.owner_hash,
    i.owner_address, i.address_local, i.state, i.flow_id, i.generation_state);
  }
  fresh(auth, now) {
    return this.registry.guard(`ABS(${SQL_NOW}-?)<=? AND CAST(${SQL_NOW}/${DAY} AS INTEGER)=?`,
      Number(auth.timestamp), PULL_SKEW_MS, Math.floor(now / DAY));
  }
  async checkRequest(auth, value, path, now) {
    valid(Math.abs(now - Number(auth.timestamp)) <= PULL_SKEW_MS, 'unauthorized');
    const current = await this.current(auth);
    valid(current && current.public_key === auth.current.public_key
      && current.owner_hash === auth.current.owner_hash, 'stale_generation');
    if (path === SEND_PATH) {
      valid(current.state !== 'frozen', 'registration_frozen');
      valid(current.state === 'active', 'registration_inactive');
      valid(await this.ledger.registration(auth.install_id, Number(auth.generation), Number(auth.owner_generation)), 'revoked');
    }
    valid(!await this.first(`SELECT 1 FROM pull_nonces WHERE direction='install-to-pull' AND install_id=?
      AND generation=? AND key_id=? AND nonce=?`, auth.install_id, Number(auth.generation), auth.key_id, auth.nonce), 'nonce_replayed');
    const bucket = `pull:http:send:${auth.install_id}:${Math.floor(now / DAY)}`;
    const budget = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
    valid(!budget || budget.count < 200, 'request_limit');
    if (path === SEND_PATH) {
      const op = await this.first('SELECT input_digest FROM pull_operations WHERE install_id=? AND generation=? AND path=? AND op_id=?',
        auth.install_id, Number(auth.generation), path, value.op_id);
      valid(!op || op.input_digest === auth.digest, 'operation_conflict');
    }
    return { current, bucket };
  }
  async guards(auth, value, path, now) {
    const { current, bucket } = await this.checkRequest(auth, value, path, now), r = this.registry;
    const statements = [this.fresh(auth, now), this.snapshotGuard(auth.current),
      this.stmt(`INSERT INTO pull_nonces VALUES('install-to-pull',?,?,?,?,?)`,
        auth.install_id, Number(auth.generation), auth.key_id, auth.nonce, Number(auth.timestamp) + PULL_SKEW_MS + 1),
      this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<200`,
        bucket, (Math.floor(now / DAY) + 2) * DAY), r.changed()];
    if (path === SEND_PATH) {
      // Only inert identity metadata, never plaintext/token or a fictional send
      // result. Intent state remains the authority for subsequent reconciliation.
      statements.push(this.stmt(`INSERT INTO pull_operations VALUES(?,?,?,?,?, ?,200,?,?)
        ON CONFLICT(install_id,generation,path,op_id) DO NOTHING`, auth.install_id, Number(auth.generation), path,
      value.op_id, auth.digest, JSON.stringify({ intent_id: value.intent_id, thread_id: value.thread_id }), now, now + 120 * DAY),
      r.guard(`EXISTS(SELECT 1 FROM pull_operations WHERE install_id=? AND generation=? AND path=? AND op_id=? AND input_digest=?)`,
        auth.install_id, Number(auth.generation), path, value.op_id, auth.digest));
    }
    statements.push(this.fresh(auth, now));
    return { statements, current };
  }
  async send(auth, value, raw) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const { statements } = await this.guards(auth, value, SEND_PATH, this.now());
      let committed = false;
      try {
        const reg = await this.ledger.registration(auth.install_id, Number(auth.generation), Number(auth.owner_generation));
        valid(reg, 'stale_generation');
        const result = await pullOutbound({ ledger: this.ledger, reg, value, body: raw, config: this.config,
          binding: this.binding, clock: () => this.now(), timers: this.timers, requestGuards: statements,
          onRequestCommitted: () => { committed = true; } });
        if (result.state === 'accepted') {
          // Read the reservation's immutable deadline, including on replay.
          // No client-supplied TTL or local preparation time participates.
          const thread = await this.first(`SELECT expires_ms FROM pull_threads WHERE install_id=?
            AND generation=? AND owner_generation=? AND intent_id=? AND thread_id=?`,
          auth.install_id, Number(auth.generation), Number(auth.owner_generation), value.intent_id, value.thread_id);
          valid(thread, 'storage_unavailable');
          result.thread_expires_ms = thread.expires_ms;
        }
        return projectSendResult(result, value, value.op_id);
      } catch (error) {
        if (error.message !== 'storage_unavailable' || committed) throw error;
        // Classify only a fully rolled-back first batch. A fresh-nonce losing
        // reservation may now be read transactionally; never repeat a handoff.
        await this.checkRequest(auth, value, SEND_PATH, this.now());
        if (attempt) throw error;
      }
    }
  }
  async status(auth, value) {
    const now = this.now(), { statements } = await this.guards(auth, value, SEND_STATUS_PATH, now);
    let results;
    try {
      results = await this.db.batch([...statements,
        this.stmt(`SELECT intent_id,thread_id,message_id,state,provider_id FROM pull_intents
          WHERE install_id=? AND generation=? AND generation<=? AND owner_generation=? AND owner_hash=? AND intent_id=?`,
        auth.install_id, value.intent_generation, Number(auth.generation), Number(auth.owner_generation), auth.current.owner_hash, value.intent_id),
        this.snapshotGuard(auth.current), this.fresh(auth, now)]);
    } catch {
      await this.checkRequest(auth, value, SEND_STATUS_PATH, this.now());
      console.error('[agent-mail] pull_send_status_transaction_refused'); throw new Error('storage_unavailable');
    }
    const row = results.at(-3).results[0]; valid(row, 'unknown_intent');
    valid(exact(row, FIELDS) && STATES.includes(row.state) && provider(row.provider_id)
      && match(ID, row.intent_id) && match(ID, row.thread_id) && typeof row.message_id === 'string'
      && row.message_id.length > 0 && row.message_id.length <= 8192 && strictMsgId(row.message_id) === row.message_id
      && (row.state !== 'accepted' || row.provider_id !== '')
      && (!['reserved', 'sending', 'failed'].includes(row.state) || row.provider_id === ''), 'storage_unavailable');
    return { status: 200, body: { v: 1, code: 'ok', request_id: auth.nonce, ...row } };
  }
  async response(auth, path, body, status, retry) {
    const output = new Headers({ ...HEADERS, 'content-type': 'application/json' });
    if (retry !== undefined) output.set('retry-after', String(Math.max(1, Math.ceil((retry - this.now()) / 1000))));
    const raw = bytes(JSON.stringify(body));
    if (auth) {
      const m = { version: '2', direction: 'pull-to-install', key_id: this.serviceKeyId,
        install_id: auth.install_id, generation: auth.generation, owner_generation: auth.owner_generation,
        timestamp: String(this.now()), nonce: auth.nonce, digest: await sha256(raw) };
      const transcript = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction, this.serviceOrigin,
        'POST', path, m.key_id, m.install_id, m.generation, m.owner_generation, m.timestamp, m.nonce, String(status), m.digest]));
      const key = await crypto.subtle.importKey('pkcs8', unbase64url(this.servicePrivateKey), 'Ed25519', false, ['sign']);
      for (const [k, v] of Object.entries(m)) output.set(`x-mail-${k.replaceAll('_', '-')}`, v);
      output.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', key, transcript)));
    }
    return new Response(raw, { status, headers: output });
  }
  async handle(request, { trustedSource } = {}) {
    let auth, value, path;
    try {
      const now = this.now();
      valid(typeof trustedSource === 'string' && /^[\x21-\x7e]{1,128}$/.test(trustedSource), 'service_unavailable');
      valid(await this.shared.limit(`pull:http:source:${await sha256(bytes(trustedSource))}`, 30, now, 60_000), 'abuse_limit');
      const url = new URL(request.url); path = url.pathname;
      valid([SEND_PATH, SEND_STATUS_PATH].includes(path) && url.origin === this.serviceOrigin
        && request.method === 'POST' && !request.headers.has('origin')
        && !request.url.includes('?') && !request.url.includes('#') && !request.headers.has('content-encoding'), 'unauthorized');
      valid([...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) <= 8192, 'body_limit');
      valid(request.headers.get('content-type') === 'application/json');
      const cap = path === SEND_PATH ? 131072 : 8192, length = request.headers.get('content-length');
      valid(length === null || /^(0|[1-9][0-9]*)$/.test(length) && Number(length) <= cap, 'body_limit');
      const raw = await readRequestBody(request.body, { limit: cap, timeoutMs: this.bodyTimeoutMs });
      auth = await verifySignedV2({ request, raw, body: null, path, serviceOrigin: this.serviceOrigin,
        clock: () => this.now(), resolveKey: async (m) => {
          const i = await this.current(m); valid(i, 'unauthorized'); m.current = i; return i.public_key;
        } });
      value = flatJSON(raw);
      if (path === SEND_PATH) {
        const { op_id, ...payload } = value;
        valid(match(I, op_id) && validateSend(payload), 'invalid_send');
      } else valid(exact(value, ['intent_id', 'intent_generation']) && match(ID, value.intent_id)
        && Number.isSafeInteger(value.intent_generation) && value.intent_generation >= 1
        && value.intent_generation <= 999999999999999);
      const result = path === SEND_PATH ? await this.send(auth, value, raw) : await this.status(auth, value);
      const retry = [429, 503].includes(result.status) ? (Math.floor(this.now() / DAY) + 1) * DAY : undefined;
      if (retry !== undefined) result.body.retry_after_ms = retry;
      return await this.response(auth, path, result.body, result.status, retry);
    } catch (error) {
      let code = CODES.has(error?.message) ? error.message
        : ['provider_config_required', 'invalid_service_address'].includes(error?.message) ? 'provider_unavailable' : 'storage_unavailable';
      if (code === 'storage_unavailable') console.error('[agent-mail] pull_send_http_failed');
      try {
        if (!auth && !['storage_unavailable', 'service_unavailable', 'abuse_limit'].includes(code)) code = 'unauthorized';
        if (!auth && !await this.shared.limit('http:unauthenticated', 10000, this.now(), 60000)) code = 'abuse_limit';
        const status = CODES.get(code), now = this.now();
        const retry = [429, 503].includes(status) ? (auth && code === 'request_limit'
          ? (Math.floor(now / DAY) + 1) * DAY : now + 60000) : undefined;
        const body = { v: 1, code, ...(auth ? { request_id: path === SEND_PATH && match(I, value?.op_id)
          ? value.op_id : auth.nonce, ...(retry === undefined ? {} : { retry_after_ms: retry }) } : {}) };
        return await this.response(auth, path, body, status, retry);
      } catch {
        console.error('[agent-mail] pull_send_http_refusal_failed');
        return Response.json({ v: 1, code: 'service_unavailable' }, { status: 503, headers: HEADERS });
      }
    }
  }
}
