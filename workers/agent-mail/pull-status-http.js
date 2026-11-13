// Pull lifecycle HTTP boundary (unit 14b). Unwired; unit 14 owns routing.
import { Ledger, DAY } from './ledger.js';
import { OwnerRegistry } from './owner-registry.js';
import { verifySignedV2, PULL_SKEW_MS } from './signed-v2-verifier.js';
import { bytes, sha256, base64url, unbase64url } from './signing.js';
import { origin } from './enrollment-transcript.js';
import { readRequestBody } from './request-body.js';
import { SQL_NOW } from './sql-clock.js';

export const STATUS_PATH = '/v1/pull/status';
const PATH = STATUS_PATH;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
const CODES = new Map([[400, ['invalid_request', 'invalid_json']], [401, ['unauthorized']],
  [403, ['consent_required', 'human_required']], [404, ['not_found']], [408, ['body_timeout']],
  [409, ['operation_conflict', 'nonce_replayed', 'stale_generation', 'registration_inactive',
    'registration_frozen', 'verification_pending', 'owner_limit', 'fenced', 'revoked']],
  [410, ['flow_expired']], [413, ['body_limit']], [429, ['request_limit', 'abuse_limit']],
  [503, ['storage_unavailable', 'service_unavailable']]]
  .flatMap(([status, codes]) => codes.map((code) => [code, status])));
const valid = (ok, code = 'invalid_request') => { if (!ok) throw new Error(code); };
const match = (re, value) => typeof value === 'string' && re.test(value);
const exact = (v, fields) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).length === fields.length && fields.every((k) => Object.hasOwn(v, k));
const hash = (s) => sha256(bytes(s));
const integer = (n, low = 0, high = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(n)
  && !Object.is(n, -0) && n >= low && n <= high;

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

export class PullStatusHTTP {
  constructor({ db, serviceOrigin, domain, serviceKeyId, servicePrivateKey,
    clock = Date.now, bodyTimeoutMs = 10_000 }) {
    valid(db && origin(serviceOrigin) && match(ID, serviceKeyId)
      && typeof servicePrivateKey === 'string' && unbase64url(servicePrivateKey).length > 0
      && typeof clock === 'function' && integer(bodyTimeoutMs, 1, 10_000));
    Object.assign(this, { db, serviceOrigin, domain, serviceKeyId, servicePrivateKey, clock, bodyTimeoutMs });
    this.registry = new OwnerRegistry(db); this.shared = new Ledger(db); this.ledger = this.shared.pull({ clock });
  }
  stmt(sql, ...args) { return this.registry.stmt(sql, ...args); }
  first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  now() {
    const n = this.clock(); valid(integer(n, 0, Number.MAX_SAFE_INTEGER - 120 * DAY), 'service_unavailable'); return n;
  }
  async current(auth) {
    return this.first(`SELECT i.*,g.flow_id,g.state AS generation_state FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.owner_hash=i.owner_hash AND g.key_id=i.key_id AND g.public_key=i.public_key
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.key_id=?`,
    auth.install_id, Number(auth.generation), Number(auth.owner_generation), auth.key_id);
  }
  snapshotGuard(i) {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.owner_hash=i.owner_hash AND g.key_id=i.key_id AND g.public_key=i.public_key
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.key_id=? AND i.public_key=?
      AND i.owner_hash=? AND i.owner_address IS ? AND i.address_local=? AND i.state=? AND g.flow_id=? AND g.state=?
      AND i.epoch=? AND i.active_since_ms IS ? AND i.consent_version=?)`,
    i.install_id, i.generation, i.owner_generation, i.key_id, i.public_key, i.owner_hash,
    i.owner_address, i.address_local, i.state, i.flow_id, i.generation_state, i.epoch, i.active_since_ms, i.consent_version);
  }
  fresh(auth, now, until = Number.MAX_SAFE_INTEGER) {
    return this.registry.guard(`ABS(${SQL_NOW}-?)<=? AND CAST(${SQL_NOW}/${DAY} AS INTEGER)=? AND ${SQL_NOW}<?`,
      Number(auth.timestamp), PULL_SKEW_MS, Math.floor(now / DAY), until);
  }
  async accounting(auth, now) {
    valid(Math.abs(now - Number(auth.timestamp)) <= PULL_SKEW_MS, 'unauthorized');
    valid(!await this.first(`SELECT 1 FROM pull_nonces WHERE direction='install-to-pull' AND install_id=?
      AND generation=? AND key_id=? AND nonce=?`, auth.install_id, Number(auth.generation), auth.key_id, auth.nonce), 'nonce_replayed');
    const bucket = `pull:http:send:${auth.install_id}:${Math.floor(now / DAY)}`;
    const budget = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
    valid(!budget || budget.count < 200, 'request_limit');
    return [this.fresh(auth, now),
      this.stmt(`INSERT INTO pull_nonces VALUES('install-to-pull',?,?,?,?,?)`,
        auth.install_id, Number(auth.generation), auth.key_id, auth.nonce, Number(auth.timestamp) + PULL_SKEW_MS + 1),
      this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<200`,
        bucket, (Math.floor(now / DAY) + 2) * DAY), this.registry.changed()];
  }
  async authenticate(request, raw) {
    return verifySignedV2({ request, raw, body: null, path: PATH, serviceOrigin: this.serviceOrigin,
      clock: () => this.now(), resolveKey: async (m) => {
        const i = await this.current(m); valid(i, 'unauthorized'); m.current = i; return i.public_key;
      } });
  }
  async execute(auth) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const now = this.now(), i = await this.current(auth);
      valid(i && i.public_key === auth.current.public_key && i.owner_hash === auth.current.owner_hash, 'stale_generation');
      const accounting = await this.accounting(auth, now);
      let rows;
      try {
        rows = await this.db.batch([...accounting, this.snapshotGuard(i),
          this.stmt(`SELECT COUNT(*) AS n,COALESCE(SUM(body_bytes),0) AS b,MIN(received_ms) AS received,
            MIN(expires_ms) AS expires FROM pull_events WHERE install_id=? AND generation=? AND owner_generation=?
            AND state IN ('pending','offered','held') AND expires_ms>${SQL_NOW}`, i.install_id, i.generation, i.owner_generation),
          this.stmt(`SELECT COUNT(*) AS n,MIN(received_ms) AS received,MIN(expires_ms) AS expires
            FROM pull_ingress_holds WHERE install_id=? AND generation=? AND owner_generation=? AND expires_ms>${SQL_NOW}`,
          i.install_id, i.generation, i.owner_generation),
          this.stmt(`SELECT change_id,state,created_ms,revert_until_ms,expires_ms FROM pull_owner_changes
            WHERE install_id=? AND (expected_owner_generation=? OR switched_owner_generation=?)
            AND state IN ('pending','completed','reverted','expired') ORDER BY created_ms DESC,change_id DESC LIMIT 1`,
          i.install_id, i.owner_generation, i.owner_generation),
          this.stmt(`SELECT state FROM pull_control_outbox WHERE install_id=? AND template IN ('old_notice','revert_old','revert_new')
            ORDER BY created_ms DESC,outbox_id DESC LIMIT 1`, i.install_id),
          this.snapshotGuard(i), this.fresh(auth, now)]);
      } catch (error) {
        if (!attempt) continue; // Full rollback, including nonce/accounting.
        console.error('[agent-mail] pull_status_transaction_refused'); throw new Error('storage_unavailable');
      }
      const [events, holds, changes, notices] = rows.slice(-6, -2).map((r) => r.results);
      const e = events[0], h = holds[0], c = changes[0], notice = notices[0];
      valid(integer(e.n) && integer(e.b) && integer(h.n), 'storage_unavailable');
      const minimum = (...values) => values.every((n) => n === null) ? null : Math.min(...values.filter((n) => n !== null));
      valid(i.state === 'deleted' || origin(`https://${this.domain}`), 'service_unavailable');
      return { install_id: i.install_id, generation: i.generation, owner_generation: i.owner_generation,
        state: i.state, address: i.state === 'deleted' ? null : `${i.address_local}@${this.domain}`,
        owner: i.owner_address, key_id: i.key_id, queue_count: e.n + h.n, queue_bytes: e.b,
        oldest_received_ms: minimum(e.received, h.received), oldest_expires_ms: minimum(e.expires, h.expires),
        invalid_message_id_holds: h.n, current_epoch: i.epoch,
        owner_change: c ? { change_id: c.change_id, state: c.state === 'pending' && c.expires_ms <= now ? 'expired' : c.state,
          created_ms: c.created_ms, revert_until_ms: c.revert_until_ms } : null,
        last_notice_state: notice ? ({ queued: 'queued', sending: 'uncertain', accepted: 'accepted',
          uncertain: 'uncertain', failed: 'failed', expired: 'failed' })[notice.state] : null,
        reason: h.n ? 'invalid_message_id' : i.state === 'frozen' ? 'registration_frozen'
          : i.state === 'disabled' ? 'registration_inactive' : '' };
    }
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
    let auth, value;
    try {
      const now = this.now();
      valid(typeof trustedSource === 'string' && /^[\x21-\x7e]{1,128}$/.test(trustedSource), 'service_unavailable');
      valid(await this.shared.limit(`pull:http:source:${await hash(trustedSource)}`, 30, now, 60_000), 'abuse_limit');
      const url = new URL(request.url);
      valid(url.pathname === PATH && url.origin === this.serviceOrigin && request.method === 'POST'
        && !request.headers.has('origin') && !request.url.includes('?') && !request.url.includes('#')
        && !request.headers.has('content-encoding'), 'unauthorized');
      valid([...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) <= 8192, 'body_limit');
      valid(request.headers.get('content-type') === 'application/json');
      const length = request.headers.get('content-length');
      valid(length === null || /^(0|[1-9][0-9]*)$/.test(length) && Number(length) <= 8192, 'body_limit');
      const raw = await readRequestBody(request.body, { limit: 8192, timeoutMs: this.bodyTimeoutMs });
      auth = await this.authenticate(request, raw);
      value = flatJSON(raw);
      valid(exact(value, []));
      const result = await this.execute(auth, value);
      return await this.response(auth, PATH, { v: 1, code: 'ok', request_id: auth.nonce, ...result }, 200);
    } catch (error) {
      let code = CODES.has(error?.message) ? error.message : 'storage_unavailable';
      if (code === 'storage_unavailable') console.error('[agent-mail] pull_status_http_failed');
      try {
        if (!auth && !['storage_unavailable', 'service_unavailable', 'abuse_limit'].includes(code)) code = 'unauthorized';
        if (!auth && !await this.shared.limit('http:unauthenticated', 10000, this.now(), 60000)) code = 'abuse_limit';
        const status = CODES.get(code), now = this.now();
        const retry = [429, 503].includes(status) ? (auth && code === 'request_limit'
          ? (Math.floor(now / DAY) + 1) * DAY : now + 60000) : undefined;
        const body = { v: 1, code, ...(auth ? { request_id: auth.nonce,
          ...(retry === undefined ? {} : { retry_after_ms: retry }) } : {}) };
        return await this.response(auth, PATH, body, status, retry);
      } catch {
        console.error('[agent-mail] pull_status_refusal_failed');
        return Response.json({ v: 1, code: 'service_unavailable' }, { status: 503, headers: HEADERS });
      }
    }
  }
}
