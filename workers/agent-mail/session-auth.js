// Pull v1 ticket/connection authority (units 06, contract sections 2-4).
// Unwired: unit 14 routes HTTP; unit 09 owns WebSocketPair/hibernation/101 and
// prior-socket close. Only admitSocket's committed attachment may reach that DO.
import { bytes, randomId, sha256, base64url, unbase64url } from './signing.js';
import { origin } from './enrollment-transcript.js';
import { verifySignedV2, PULL_SKEW_MS } from './signed-v2-verifier.js';
import { OwnerRegistry } from './owner-registry.js';
import { Ledger } from './ledger.js';
import { readRequestBody } from './request-body.js';
import { SQL_NOW, sqlClockAtOrAfter } from './sql-clock.js';

export const TICKET_PATH = '/v1/pull/ticket';
export const SOCKET_PATH = '/v1/pull/socket';
export const PULL_PROTOCOL = 'clayrune-mail-pull.v1';
const DAY = 86_400_000;
const G_MAX = 999_999_999_999_999;
const I = /^[a-f0-9]{32}$/;
const valid = (ok, code = 'invalid_request') => { if (!ok) throw new Error(code); };
const match = (re, s) => typeof s === 'string' && re.test(s);
const hash = (s) => sha256(bytes(s));
const headers = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
const CODES = new Map([[400, ['invalid_request', 'invalid_json']], [401, ['unauthorized', 'ticket_invalid']],
  [404, ['not_found']], [408, ['body_timeout']], [409, ['nonce_replayed', 'operation_conflict']],
  [413, ['body_limit']], [426, ['version_unsupported']], [429, ['request_limit', 'abuse_limit']],
  [503, ['storage_unavailable', 'service_unavailable']]].flatMap(([n, codes]) => codes.map((code) => [code, n])));

// One closed string field. Lex before JSON.parse so escaped duplicate keys,
// nested values, BOM, invalid UTF-8 and trailing bytes cannot be normalized away.
function ticketBody(raw) {
  try {
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
    const string = '"(?:[^"\\\\\\x00-\\x1f]|\\\\(?:["\\\\/bfnrt]|u[0-9a-fA-F]{4}))*"';
    valid(new RegExp(`^[ \\t\\r\\n]*\\{[ \\t\\r\\n]*${string}[ \\t\\r\\n]*:[ \\t\\r\\n]*${string}[ \\t\\r\\n]*\\}[ \\t\\r\\n]*$`).test(text), 'invalid_json');
    const v = JSON.parse(text);
    valid(Object.keys(v).length === 1 && Object.hasOwn(v, 'op_id') && match(I, v.op_id));
    return v;
  } catch (error) {
    if (error.message === 'invalid_request') throw error;
    throw new Error('invalid_json'); // Pure wire syntax, no I/O.
  }
}

/** Reviewed config only; no default origin, generated service key or auth cookie.
 * handle(request,{trustedSource}) serves POST ticket only, signed in request
 * scope. admitSocket(request,{trustedSource,install_id?}) returns an attachment
 * AFTER commit, or throws a closed error; never constructs a premature 101.
 * install_id is the router-selected DO scope, if already selected, never a key
 * supplied in a socket query. Passing it rejects foreign tickets before consume.
 */
export class SessionAuth {
  constructor({ db, serviceOrigin, serviceKeyId, servicePrivateKey, clock = Date.now }) {
    valid(db && origin(serviceOrigin) && match(/^[A-Za-z0-9_-]{1,128}$/, serviceKeyId)
      && typeof servicePrivateKey === 'string' && unbase64url(servicePrivateKey).length > 0);
    Object.assign(this, { db, serviceOrigin, serviceKeyId, servicePrivateKey, clock });
    this.registry = new OwnerRegistry(db); this.ledger = new Ledger(db);
  }
  stmt(sql, ...args) { return this.registry.stmt(sql, ...args); }
  async first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  now() {
    const n = this.clock();
    valid(Number.isSafeInteger(n) && n >= 0 && n < Number.MAX_SAFE_INTEGER - 32 * DAY, 'service_unavailable');
    return n;
  }
  async sourceLimit(source, now) {
    valid(typeof source === 'string' && /^[\x21-\x7e]{1,128}$/.test(source), 'service_unavailable');
    valid(await this.ledger.limit(`pull:http:source:${await hash(source)}`, 30, now, 60_000), 'abuse_limit');
  }
  bounds(request, path) {
    const url = new URL(request.url);
    valid(url.origin === this.serviceOrigin && url.pathname === path, 'not_found');
    valid(!request.url.includes('?') && !request.url.includes('#') && !request.headers.has('content-encoding'));
    valid([...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) <= 8192, 'body_limit');
  }
  // All key/state/fence checks repeat in the mutation batch. No historical,
  // pending, unverified, disabled or frozen key can acquire ticket authority.
  async active(scope) {
    return this.first(`SELECT i.*,g.flow_id FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.key_id=i.key_id AND g.public_key=i.public_key AND g.owner_hash=i.owner_hash
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.key_id=? AND i.state='active'
      AND g.state='current' AND NOT EXISTS(SELECT 1 FROM pull_fences f WHERE f.install_id=i.install_id
        AND f.generation=i.generation AND (f.mode='revoke' OR f.flow_id=g.flow_id))`,
    scope.install_id, Number(scope.generation), Number(scope.owner_generation), scope.key_id);
  }
  scopeGuard(i) {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.key_id=i.key_id AND g.public_key=i.public_key AND g.owner_hash=i.owner_hash
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.key_id=? AND i.public_key=?
      AND i.owner_hash=? AND i.state='active' AND g.state='current' AND g.flow_id=?
      AND NOT EXISTS(SELECT 1 FROM pull_fences f WHERE f.install_id=i.install_id AND f.generation=i.generation
        AND (f.mode='revoke' OR f.flow_id=g.flow_id)))`,
    i.install_id, i.generation, i.owner_generation, i.key_id, i.public_key, i.owner_hash, i.flow_id);
  }
  async checkNonce(auth, now) {
    valid(Math.abs(now - Number(auth.timestamp)) <= PULL_SKEW_MS, 'unauthorized');
    valid(!await this.first(`SELECT 1 FROM pull_nonces WHERE direction='install-to-pull' AND install_id=?
      AND generation=? AND key_id=? AND nonce=?`, auth.install_id, Number(auth.generation), auth.key_id, auth.nonce), 'nonce_replayed');
  }
  async accounting(auth, now) {
    const day = Math.floor(now / DAY); const minute = Math.floor(now / 60_000);
    const buckets = [[`pull:http:management:${auth.install_id}:${day}`, 500, (day + 2) * DAY],
      [`pull:ticket:${auth.install_id}:${minute}`, 10, (minute + 2) * 60_000]];
    const statements = [this.stmt(`INSERT INTO pull_nonces VALUES('install-to-pull',?,?,?,?,?)`,
      auth.install_id, Number(auth.generation), auth.key_id, auth.nonce, Number(auth.timestamp) + PULL_SKEW_MS + 1)];
    for (const [bucket, cap, expires] of buckets) {
      const row = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
      valid(!row || row.count < cap, 'request_limit');
      statements.push(this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket)
        DO UPDATE SET count=count+1 WHERE count<?`, bucket, expires, cap), this.registry.changed());
    }
    return statements;
  }
  async mint(auth, value) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const now = this.now(); const i = await this.active(auth);
      valid(i && i.public_key === auth.public_key, 'unauthorized');
      await this.checkNonce(auth, now);
      valid(!await this.first(`SELECT 1 FROM pull_operations WHERE install_id=? AND generation=? AND path=? AND op_id=?`,
        i.install_id, i.generation, TICKET_PATH, value.op_id), 'operation_conflict');
      valid(!await this.first('SELECT 1 FROM pull_tickets WHERE install_id=? AND generation=? AND op_id=?',
        i.install_id, i.generation, value.op_id), 'operation_conflict');
      const accounting = await this.accounting(auth, now);
      const secret = randomId() + randomId(); const digest = await hash(secret);
      const expires = now + 60_000;
      const fresh = () => this.registry.guard(`ABS(${SQL_NOW}-?)<=? AND ?>${SQL_NOW}
        AND CAST(${SQL_NOW}/60000 AS INTEGER)=? AND CAST(${SQL_NOW}/86400000 AS INTEGER)=?`,
        Number(auth.timestamp), PULL_SKEW_MS, expires, Math.floor(now / 60000), Math.floor(now / DAY));
      const statements = [fresh(), ...accounting, this.scopeGuard(i),
        this.stmt(`INSERT INTO pull_operations VALUES(?,?,?,?,?,'{}',200,?,?)`,
          i.install_id, i.generation, TICKET_PATH, value.op_id, auth.digest, now, expires + 30 * DAY),
        this.stmt(`INSERT INTO pull_tickets VALUES(?,?,?,?,?,?,?,'issued',?,?,NULL)`,
          digest, randomId(), value.op_id, i.install_id, i.generation, i.owner_generation, i.key_id, now, expires), fresh()];
      valid(Math.abs(this.now() - Number(auth.timestamp)) <= PULL_SKEW_MS && this.now() < expires, 'unauthorized');
      try { await this.db.batch(statements); }
      catch (error) { if (attempt) throw error; continue; } // Re-read after full rollback; never execute a prefix.
      valid(this.now() < expires && Math.abs(this.now() - Number(auth.timestamp)) <= PULL_SKEW_MS, 'unauthorized');
      return { ticket: secret, expires_ms: expires, protocol: PULL_PROTOCOL };
    }
  }
  async response(auth, value, status) {
    if (!auth) return Response.json(value, { status, headers });
    const raw = bytes(JSON.stringify(value));
    const m = { version: '2', direction: 'pull-to-install', key_id: this.serviceKeyId,
      install_id: auth.install_id, generation: auth.generation, owner_generation: auth.owner_generation,
      timestamp: String(this.now()), nonce: auth.nonce, digest: await sha256(raw) };
    const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http', '1', m.direction, this.serviceOrigin,
      'POST', TICKET_PATH, m.key_id, m.install_id, m.generation, m.owner_generation, m.timestamp,
      m.nonce, String(status), m.digest]));
    const output = new Headers({ ...headers, 'content-type': 'application/json' });
    for (const [k, v] of Object.entries(m)) output.set(`x-mail-${k.replaceAll('_', '-')}`, v);
    const key = await crypto.subtle.importKey('pkcs8', unbase64url(this.servicePrivateKey), 'Ed25519', false, ['sign']);
    output.set('x-mail-signature', base64url(await crypto.subtle.sign('Ed25519', key, input)));
    return new Response(raw, { status, headers: output });
  }
  async handle(request, { trustedSource } = {}) {
    let auth; let value;
    try {
      this.bounds(request, TICKET_PATH);
      await this.sourceLimit(trustedSource, this.now());
      valid(request.method === 'POST' && !request.headers.has('origin'), 'unauthorized');
      valid(request.headers.get('content-type') === 'application/json');
      const length = request.headers.get('content-length');
      valid(length === null || /^(0|[1-9][0-9]*)$/.test(length) && Number(length) <= 8192, 'body_limit');
      const raw = await readRequestBody(request.body, { limit: 8192 });
      value = ticketBody(raw);
      auth = await verifySignedV2({ request, raw, body: value, path: TICKET_PATH,
        serviceOrigin: this.serviceOrigin, clock: () => this.now(), resolveKey: async (m) => {
          const i = await this.active(m); valid(i, 'unauthorized');
          m.public_key = i.public_key;
          return i.public_key;
        } });
      return await this.response(auth, { v: 1, code: 'ok', request_id: value.op_id, ...await this.mint(auth, value) }, 200);
    } catch (error) {
      let code = CODES.has(error?.message) ? error.message : 'storage_unavailable';
      if (!CODES.has(error?.message)) console.error('[agent-mail] ticket_failed');
      try {
        if (!['storage_unavailable', 'service_unavailable'].includes(code)
          && !await this.ledger.limit('http:unauthenticated', 10000, this.now(), 60_000)) code = 'abuse_limit';
        const status = CODES.get(code);
        const now = this.now();
        let retry = status === 429 || status === 503 ? now + 60_000 : undefined;
        if (auth && code === 'request_limit') {
          const day = Math.floor(now / DAY);
          const budget = await this.first('SELECT count FROM pull_budgets WHERE bucket=?',
            `pull:http:management:${auth.install_id}:${day}`);
          retry = budget?.count >= 500 ? (day + 1) * DAY : (Math.floor(now / 60_000) + 1) * 60_000;
        }
        const response = await this.response(auth, { v: 1, code, ...(auth ? { request_id: value.op_id,
          ...(retry === undefined ? {} : { retry_after_ms: retry }) } : {}) }, status);
        if (retry !== undefined) response.headers.set('retry-after', String(Math.max(1, Math.ceil((retry - this.now()) / 1000))));
        return response;
      } catch {
        console.error('[agent-mail] ticket_refusal_failed');
        return Response.json({ v: 1, code: 'service_unavailable' }, { status: 503, headers });
      }
    }
  }
  async admitSocket(request, { trustedSource, install_id } = {}) {
    try { return await this.consumeSocket(request, { trustedSource, install_id }); }
    catch (error) {
      let code = CODES.has(error?.message) ? error.message : 'storage_unavailable';
      if (!CODES.has(error?.message)) console.error('[agent-mail] socket_admission_failed');
      if (!['storage_unavailable', 'service_unavailable'].includes(code)) {
        try {
          if (!await this.ledger.limit('http:unauthenticated', 10000, this.now(), 60_000)) code = 'abuse_limit';
        } catch {
          console.error('[agent-mail] socket_refusal_accounting_failed');
          code = 'storage_unavailable';
        }
      }
      throw new Error(code);
    }
  }
  // Internal implementation; callers use admitSocket to get sanitized errors
  // and shared global refusal accounting, including failed upgrade attempts.
  async consumeSocket(request, { trustedSource, install_id }) {
    this.bounds(request, SOCKET_PATH);
    await this.sourceLimit(trustedSource, this.now());
    valid(request.method === 'GET' && !request.body && !request.headers.has('origin'), 'ticket_invalid');
    valid(request.headers.get('upgrade')?.toLowerCase() === 'websocket'
      && request.headers.get('sec-websocket-protocol') === PULL_PROTOCOL, 'version_unsupported');
    const authorization = request.headers.get('authorization');
    valid(typeof authorization === 'string' && /^MailTicket [a-f0-9]{64}$/.test(authorization), 'ticket_invalid');
    valid(install_id === undefined || match(I, install_id), 'ticket_invalid');
    const digest = await hash(authorization.slice(11));
    for (let attempt = 0; attempt < 2; attempt++) {
      const now = this.now();
      const ticket = await this.first('SELECT * FROM pull_tickets WHERE ticket_hash=?', digest);
      valid(ticket && ticket.state === 'issued' && ticket.expires_ms > now && ticket.created_ms <= now
        && (install_id === undefined || ticket.install_id === install_id), 'ticket_invalid');
      const i = await this.active(ticket);
      valid(i && Number.isSafeInteger(i.epoch) && i.epoch >= 0 && i.epoch < G_MAX, 'ticket_invalid');
      const connection = randomId();
      const fresh = () => this.registry.guard(`?>${SQL_NOW} AND ${sqlClockAtOrAfter()}`, ticket.expires_ms, ticket.created_ms);
      const statements = [fresh(), this.scopeGuard(i),
        this.stmt(`UPDATE pull_tickets SET state='consumed',consumed_ms=? WHERE ticket_hash=? AND state='issued'
          AND install_id=? AND generation=? AND owner_generation=? AND key_id=? AND expires_ms>? AND created_ms<=?`,
        now, digest, i.install_id, i.generation, i.owner_generation, i.key_id, now, now), this.registry.changed(),
        this.stmt(`UPDATE pull_installs SET epoch=epoch+1,connection_id=?,updated_ms=? WHERE install_id=? AND epoch=?
          AND generation=? AND owner_generation=? AND state='active' AND key_id=?`,
        connection, now, i.install_id, i.epoch, i.generation, i.owner_generation, i.key_id), this.registry.changed(), fresh()];
      valid(this.now() < ticket.expires_ms, 'ticket_invalid');
      try { await this.db.batch(statements); }
      catch (error) {
        if (attempt) {
          // Classify a refused SQL time window after the entire batch rolled
          // back. A failed read still propagates as storage_unavailable.
          valid(await this.first(`SELECT 1 WHERE ?>${SQL_NOW} AND ${sqlClockAtOrAfter()}`,
            ticket.expires_ms, ticket.created_ms), 'ticket_invalid');
          throw error;
        }
        continue;
      }
      valid(this.now() < ticket.expires_ms, 'ticket_invalid');
      return { v: 1, install_id: i.install_id, generation: i.generation, owner_generation: i.owner_generation,
        key_id: i.key_id, epoch: i.epoch + 1, connection_id: connection, authenticated_ms: now, reauth_ms: now + DAY };
    }
  }
}
