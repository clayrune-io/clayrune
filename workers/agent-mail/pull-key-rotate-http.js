// Pull lifecycle HTTP boundary (unit 14b). Unwired; unit 14 owns routing.
import { Ledger, DAY } from './ledger.js';
import { OwnerRegistry } from './owner-registry.js';
import { verifySignedV2, PULL_SKEW_MS } from './signed-v2-verifier.js';
import { bytes, sha256, base64url, unbase64url } from './signing.js';
import { origin, verifyProof } from './enrollment-transcript.js';
import { readRequestBody } from './request-body.js';
import { SQL_NOW } from './sql-clock.js';

export const KEY_ROTATE_PATH = '/v1/pull/key/rotate';
const PATH = KEY_ROTATE_PATH;
const I = /^[a-f0-9]{32}$/, H = /^[a-f0-9]{64}$/, ID = /^[A-Za-z0-9_-]{1,128}$/;
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

export class PullKeyRotateHTTP {
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
  flowGuard(f) {
    const fields = ['flow_id','install_id','purpose','expected_generation','expected_owner_generation',
      'candidate_key_id','candidate_public_key','owner_hash','flow_secret_hash','fence_hash','input_digest',
      'state','consent_version','manifest_sequence','manifest_digest','result_json','created_ms','expires_ms'];
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_flows WHERE ${fields.map((k) => `${k} IS ?`).join(' AND ')})`,
      ...fields.map((k) => f[k]));
  }
  fresh(auth, now, until = Number.MAX_SAFE_INTEGER) {
    return this.registry.guard(`ABS(${SQL_NOW}-?)<=? AND CAST(${SQL_NOW}/${DAY} AS INTEGER)=? AND ${SQL_NOW}<?`,
      Number(auth.timestamp), PULL_SKEW_MS, Math.floor(now / DAY), until);
  }
  async accounting(auth, now) {
    valid(Math.abs(now - Number(auth.timestamp)) <= PULL_SKEW_MS, 'unauthorized');
    valid(!await this.first(`SELECT 1 FROM pull_nonces WHERE direction='install-to-pull' AND install_id=?
      AND generation=? AND key_id=? AND nonce=?`, auth.install_id, Number(auth.generation), auth.key_id, auth.nonce), 'nonce_replayed');
    const bucket = `pull:http:management:${auth.install_id}:${Math.floor(now / DAY)}`;
    const budget = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
    valid(!budget || budget.count < 500, 'request_limit');
    return [this.fresh(auth, now),
      this.stmt(`INSERT INTO pull_nonces VALUES('install-to-pull',?,?,?,?,?)`,
        auth.install_id, Number(auth.generation), auth.key_id, auth.nonce, Number(auth.timestamp) + PULL_SKEW_MS + 1),
      this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<500`,
        bucket, (Math.floor(now / DAY) + 2) * DAY), this.registry.changed()];
  }
  async operation(auth, value) {
    const op = await this.first('SELECT * FROM pull_operations WHERE install_id=? AND generation=? AND path=? AND op_id=?',
      auth.install_id, Number(auth.generation), PATH, value.op_id);
    valid(!op || op.input_digest === auth.digest, 'operation_conflict'); return op;
  }
  save(auth, value, result, now) {
    return this.stmt('INSERT INTO pull_operations VALUES(?,?,?,?,?,?,200,?,?)', auth.install_id,
      Number(auth.generation), PATH, value.op_id, auth.digest, JSON.stringify(result), now, now + 120 * DAY);
  }
  async authenticate(request, raw) {
    return verifySignedV2({ request, raw, body: null, path: PATH, serviceOrigin: this.serviceOrigin,
      clock: () => this.now(), resolveKey: async (m) => {
        const i = await this.current(m);
        if (i) { m.current = i; return i.public_key; }
        // A retired key can retrieve ONLY its exact committed rotation receipt.
        // It gains no current-key, status, activation or new rotation authority.
        const value = flatJSON(raw), op = match(I, value.op_id) && await this.operation(m, value);
        const g = op && await this.first(`SELECT * FROM pull_generations WHERE install_id=? AND generation=?
          AND owner_generation=? AND key_id=?`, m.install_id, Number(m.generation), Number(m.owner_generation), m.key_id);
        valid(g, 'unauthorized'); m.replay = op; m.historical = g; return g.public_key;
      } });
  }
  async execute(auth, value) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const now = this.now(), accounting = await this.accounting(auth, now), op = await this.operation(auth, value);
      if (op) {
        await this.db.batch([...accounting,
          this.registry.guard(`EXISTS(SELECT 1 FROM pull_operations WHERE install_id=? AND generation=?
            AND path=? AND op_id=? AND input_digest=? AND result_json=?)`, auth.install_id, Number(auth.generation),
          PATH, value.op_id, auth.digest, op.result_json), this.fresh(auth, now)]);
        return JSON.parse(op.result_json);
      }
      valid(auth.current, 'stale_generation');
      const i = await this.current(auth);
      valid(i && i.public_key === auth.current.public_key && i.owner_hash === auth.current.owner_hash, 'stale_generation');
      valid(i.state !== 'frozen', 'registration_frozen');
      valid(i.state === 'active' && await this.ledger.registration(i.install_id, i.generation, i.owner_generation), 'registration_inactive');
      const f = await this.first('SELECT * FROM pull_flows WHERE flow_id=? AND install_id=?', value.flow_id, i.install_id);
      valid(f && f.purpose === 'rotate' && f.state === 'approved', 'human_required');
      valid(f.expected_generation === i.generation && f.expected_owner_generation === i.owner_generation
        && f.owner_hash === i.owner_hash, 'stale_generation');
      valid(f.expires_ms > now, 'flow_expired');
      valid(f.candidate_key_id === value.new_key_id && f.candidate_public_key === value.new_public_key
        && f.fence_hash === value.fence_hash && (value.new_key_id !== i.key_id || value.new_public_key !== i.public_key));
      const proof = bytes(JSON.stringify(['clayrune-mail-pull-candidate','1',this.serviceOrigin,'rotate', f.flow_id,
        i.install_id,String(i.generation),String(i.owner_generation),value.new_key_id,value.new_public_key,
        value.fence_hash,f.flow_secret_hash]));
      valid(await verifyProof(value.new_public_key, value.new_signature, proof), 'unauthorized');
      valid(integer(i.generation, 1, 999_999_999_999_998) && integer(i.epoch + 1), 'stale_generation');
      const result = { install_id: i.install_id, generation: i.generation + 1, owner_generation: i.owner_generation,
        key_id: value.new_key_id, state: 'verified' };
      const flowResult = { install_id: i.install_id, generation: i.generation + 1, owner_generation: i.owner_generation,
        state: 'verified', change_id: null };
      const end = now + 30 * 60_000;
      try {
        await this.db.batch([...accounting, this.fresh(auth, now, f.expires_ms), this.snapshotGuard(i), this.flowGuard(f),
          this.ledger.authority(i),
          this.registry.guard(`NOT EXISTS(SELECT 1 FROM pull_fences WHERE install_id=? AND generation=? AND (mode='revoke' OR flow_id=?))`,
            i.install_id, i.generation, f.flow_id),
          this.stmt("UPDATE pull_generations SET state='fenced',fenced_ms=COALESCE(fenced_ms,?) WHERE install_id=? AND generation<=?", now, i.install_id, i.generation),
          this.stmt("UPDATE pull_flows SET state='cancelled' WHERE install_id=? AND expected_generation<=? AND state IN ('pending','verified','approved')", i.install_id, i.generation),
          this.stmt(`UPDATE pull_tokens SET state='invalidated' WHERE install_id=? AND generation<=?
            AND NOT (purpose='revert' AND state='pending' AND expires_ms>${SQL_NOW} AND EXISTS(SELECT 1 FROM pull_owner_changes c
              WHERE c.change_id=pull_tokens.change_id AND c.install_id=pull_tokens.install_id AND c.state='completed'
              AND c.switched_owner_generation=? AND c.revert_until_ms=pull_tokens.expires_ms))`,
            i.install_id, i.generation, i.owner_generation),
          this.stmt("UPDATE pull_browser_sessions SET state='invalidated' WHERE install_id=? AND generation<=?", i.install_id, i.generation),
          this.stmt("UPDATE pull_tickets SET state='invalidated' WHERE install_id=? AND generation<=?", i.install_id, i.generation),
          this.stmt("UPDATE pull_threads SET revoked_ms=COALESCE(revoked_ms,?) WHERE install_id=? AND generation<=?", now, i.install_id, i.generation),
          this.stmt(`UPDATE pull_events SET state='revoked',text=NULL,body_bytes=0,lease_id=NULL,lease_until_ms=NULL,terminal_ms=?
            WHERE install_id=? AND generation<=? AND state IN ('pending','offered','held')`, now, i.install_id, i.generation),
          this.stmt("UPDATE pull_dedupe SET disposition='revoked' WHERE install_id=? AND generation<=? AND disposition='pending'", i.install_id, i.generation),
          this.stmt("UPDATE pull_intents SET state='revoked',finished_ms=? WHERE install_id=? AND generation<=? AND state='reserved'", now, i.install_id, i.generation),
          this.stmt("UPDATE pull_intents SET state='uncertain' WHERE install_id=? AND generation<=? AND state='sending'", i.install_id, i.generation),
          this.stmt('DELETE FROM pull_notifications WHERE install_id=?', i.install_id),
          this.stmt(`UPDATE pull_control_outbox SET state=CASE WHEN state='sending' THEN 'uncertain' ELSE 'expired' END,
            envelope_sealed=NULL,finished_ms=? WHERE install_id=? AND template IN ('signup','stepup','new_owner')
            AND state IN ('queued','sending','failed')`, now, i.install_id),
          this.stmt(`DELETE FROM pull_owner_slots WHERE install_id=? AND role='pending' AND flow_id IN
            (SELECT flow_id FROM pull_owner_changes WHERE install_id=? AND expected_generation<=? AND state='pending')`, i.install_id, i.install_id, i.generation),
          this.stmt(`UPDATE pull_owner_changes SET state='cancelled',old_owner_sealed=NULL,new_owner_sealed=NULL
            WHERE install_id=? AND expected_generation<=? AND state='pending'`, i.install_id, i.generation),
          this.stmt(`UPDATE pull_installs SET generation=generation+1,key_id=?,public_key=?,state='verified',active_since_ms=NULL,
            epoch=epoch+1,connection_id=NULL,updated_ms=? WHERE install_id=?`, value.new_key_id,value.new_public_key,now,i.install_id), this.registry.changed(),
          this.stmt("INSERT INTO pull_generations VALUES(?,?,?,?,?,?,?,?,'current',?,NULL)", i.install_id,i.generation+1,
            i.owner_generation,i.owner_hash,value.new_key_id,value.new_public_key,value.fence_hash,f.flow_id,now),
          this.stmt("UPDATE pull_flows SET state='completed',result_json=?,expires_ms=? WHERE flow_id=?", JSON.stringify(flowResult),end,f.flow_id), this.registry.changed(),
          this.save(auth, value, result, now), this.fresh(auth, now, f.expires_ms)]);
        return result;
      } catch (error) {
        if (!attempt) continue;
        console.error('[agent-mail] pull_key_rotate_transaction_refused'); throw new Error('storage_unavailable');
      }
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
      valid(exact(value, ['op_id','flow_id','new_key_id','new_public_key','new_signature','fence_hash'])
        && match(I, value.op_id) && match(I, value.flow_id) && match(ID, value.new_key_id)
        && match(/^[A-Za-z0-9_-]{43}$/, value.new_public_key) && typeof value.new_signature === 'string' && match(H, value.fence_hash));
      const result = await this.execute(auth, value);
      return await this.response(auth, PATH, { v: 1, code: 'ok', request_id: (match(I, value?.op_id) ? value.op_id : auth.nonce), ...result }, 200);
    } catch (error) {
      let code = CODES.has(error?.message) ? error.message : 'storage_unavailable';
      if (code === 'storage_unavailable') console.error('[agent-mail] pull_key_rotate_http_failed');
      try {
        if (!auth && !['storage_unavailable', 'service_unavailable', 'abuse_limit'].includes(code)) code = 'unauthorized';
        if (!auth && !await this.shared.limit('http:unauthenticated', 10000, this.now(), 60000)) code = 'abuse_limit';
        const status = CODES.get(code), now = this.now();
        const retry = [429, 503].includes(status) ? (auth && code === 'request_limit'
          ? (Math.floor(now / DAY) + 1) * DAY : now + 60000) : undefined;
        const body = { v: 1, code, ...(auth ? { request_id: (match(I, value?.op_id) ? value.op_id : auth.nonce),
          ...(retry === undefined ? {} : { retry_after_ms: retry }) } : {}) };
        return await this.response(auth, PATH, body, status, retry);
      } catch {
        console.error('[agent-mail] pull_key_rotate_refusal_failed');
        return Response.json({ v: 1, code: 'service_unavailable' }, { status: 503, headers: HEADERS });
      }
    }
  }
}
