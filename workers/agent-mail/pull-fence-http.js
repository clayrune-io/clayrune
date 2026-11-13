// Pull lifecycle HTTP boundary (unit 14b). Unwired; unit 14 owns routing.
import { Ledger, DAY } from './ledger.js';
import { OwnerRegistry } from './owner-registry.js';
import { PULL_SKEW_MS } from './signed-v2-verifier.js';
import { bytes, sha256, randomId, base64url, unbase64url } from './signing.js';
import { origin } from './enrollment-transcript.js';
import { readRequestBody } from './request-body.js';
import { SQL_NOW } from './sql-clock.js';

export const FENCE_PATH = '/v1/pull/fence';
const PATH = FENCE_PATH;
const I = /^[a-f0-9]{32}$/, H = /^[a-f0-9]{64}$/, ID = /^[A-Za-z0-9_-]{1,128}$/;
const HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
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

export class PullFenceHTTP {
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
  async authenticate(request, raw) {
    valid(![...request.headers.keys()].some((k) => k.startsWith('x-mail-')), 'unauthorized');
    const v = flatJSON(raw);
    valid(exact(v, ['op_id','install_id','generation','flow_id','fence_token','mode','nonce','timestamp'])
      && match(I,v.op_id) && match(I,v.install_id) && integer(v.generation,1,999_999_999_999_999)
      && match(I,v.flow_id) && match(H,v.fence_token) && ['cancel','revoke'].includes(v.mode)
      && match(I,v.nonce) && integer(v.timestamp) && Math.abs(this.now()-v.timestamp)<=PULL_SKEW_MS, 'unauthorized');
    const capability = await hash(v.fence_token);
    const existing = await this.first('SELECT * FROM pull_fences WHERE install_id=? AND flow_id=? AND generation=?',
      v.install_id,v.flow_id,v.generation);
    const g = await this.first('SELECT * FROM pull_generations WHERE install_id=? AND generation=? AND flow_id=?',
      v.install_id,v.generation,v.flow_id);
    const f = await this.first('SELECT * FROM pull_flows WHERE install_id=? AND flow_id=? AND expected_generation=?',
      v.install_id,v.flow_id,v.generation);
    const i = await this.registry.installation(v.install_id);
    valid(existing ? existing.fence_hash === capability : g ? g.fence_hash === capability : f ? f.fence_hash === capability
      : !i && v.mode === 'cancel' && v.generation === 1, 'unauthorized');
    return { install_id:v.install_id,generation:String(v.generation),owner_generation:'0',key_id:`fence:${v.flow_id}`,
      nonce:v.nonce,timestamp:String(v.timestamp),capability,
      digest:await hash(JSON.stringify(['clayrune-mail-pull-fence','1',v.install_id,String(v.generation),v.flow_id,v.mode,v.op_id,capability])) };
  }
  async execute(auth, v) {
    for (let attempt=0; attempt<2; attempt++) {
      const now=this.now();
      valid(Math.abs(now-v.timestamp)<=PULL_SKEW_MS,'unauthorized');
      const prior = await this.first('SELECT * FROM pull_fences WHERE install_id=? AND op_id=?',v.install_id,v.op_id);
      const same = await this.first('SELECT * FROM pull_fences WHERE install_id=? AND flow_id=? AND generation=?',v.install_id,v.flow_id,v.generation);
      valid(!prior || prior.input_digest===auth.digest,'operation_conflict');
      valid(!same || same.op_id===v.op_id && same.input_digest===auth.digest,'operation_conflict');
      const i=await this.registry.installation(v.install_id);
      if (prior) {
        // The fence row also covers cancel-before-allocation, which cannot use
        // pull_operations (its install FK). That receipt is always 'fenced'.
        const op=i && await this.operation(auth,v);
        const result=op ? JSON.parse(op.result_json) : {install_id:v.install_id,generation:v.generation,state:'fenced',fence_receipt:prior.receipt_id};
        const bucket=`pull:http:management:${v.install_id}:${Math.floor(now/DAY)}`;
        const budget=await this.first('SELECT count FROM pull_budgets WHERE bucket=?',bucket);
        valid(!budget || budget.count<500,'request_limit');
        await this.db.batch([this.fresh(auth,now),
          this.registry.guard('EXISTS(SELECT 1 FROM pull_fences WHERE install_id=? AND op_id=? AND input_digest=? AND receipt_id=?)',
            v.install_id,v.op_id,auth.digest,prior.receipt_id),
          this.stmt(`INSERT OR IGNORE INTO pull_nonces VALUES('install-to-pull',?,?,?,?,?)`,
            v.install_id,v.generation,auth.key_id,v.nonce,v.timestamp+PULL_SKEW_MS+1),
          this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<500`,
            bucket,(Math.floor(now/DAY)+2)*DAY),this.registry.changed(),this.fresh(auth,now)]);
        return result;
      }
      const accounting=await this.accounting(auth,now), g=await this.first('SELECT * FROM pull_generations WHERE install_id=? AND generation=?',v.install_id,v.generation);
      const f=await this.first('SELECT * FROM pull_flows WHERE install_id=? AND flow_id=?',v.install_id,v.flow_id);
      // Recheck capability and absent-row snapshots inside the transaction;
      // preallocation cancel may race signup, owner switch or rotation.
      const proof=[];
      if (g?.flow_id===v.flow_id) proof.push(this.registry.guard(`EXISTS(SELECT 1 FROM pull_generations WHERE install_id=?
        AND generation=? AND flow_id=? AND fence_hash=?)`,v.install_id,v.generation,v.flow_id,auth.capability));
      else if (f && f.expected_generation===v.generation) proof.push(this.flowGuard(f),
        this.registry.guard('EXISTS(SELECT 1 FROM pull_flows WHERE flow_id=? AND fence_hash=?)',v.flow_id,auth.capability));
      else { valid(!i && v.mode==='cancel' && v.generation===1,'unauthorized');
        proof.push(this.registry.guard('NOT EXISTS(SELECT 1 FROM pull_installs WHERE install_id=?)',v.install_id)); }
      const affectsCurrent = i && i.generation===v.generation && (v.mode==='revoke' || g?.flow_id===v.flow_id);
      const retireGeneration = v.mode==='revoke' || affectsCurrent;
      const result={install_id:v.install_id,generation:v.generation,
        state:i && i.generation>v.generation?'already_superseded':g?.state==='fenced'?'already_fenced':'fenced',fence_receipt:randomId()};
      const statements=[...accounting,...proof,
        this.stmt('INSERT INTO pull_fences VALUES(?,?,?,?,?,?,?,?,?)',v.install_id,v.flow_id,v.generation,auth.capability,v.mode,result.fence_receipt,v.op_id,auth.digest,now)];
      if (i) {
        statements.push(this.registry.guard(`EXISTS(SELECT 1 FROM pull_installs WHERE install_id=? AND generation=?
          AND owner_generation=? AND state=? AND epoch=? AND key_id=? AND public_key=? AND owner_hash=?)`,
          i.install_id,i.generation,i.owner_generation,i.state,i.epoch,i.key_id,i.public_key,i.owner_hash));
        // Fence results must survive the unbounded denial-capability replay
        // horizon; no secret or private address is present in this operation.
        statements.push(this.stmt('INSERT INTO pull_operations VALUES(?,?,?,?,?,?,200,?,?)',v.install_id,v.generation,
          PATH,v.op_id,auth.digest,JSON.stringify(result),now,Number.MAX_SAFE_INTEGER));
      }
      statements.push(this.stmt(`UPDATE pull_flows SET state='cancelled' WHERE install_id=? AND
        ${retireGeneration?'expected_generation<=?':'flow_id=?'} AND state IN ('pending','verified','approved')`,
        v.install_id,retireGeneration?v.generation:v.flow_id),
      this.stmt(`UPDATE pull_tokens SET state='invalidated' WHERE install_id=? AND
        ${retireGeneration?'generation<=?':'flow_id=?'} AND NOT
        (purpose='revert' AND state='pending' AND expires_ms>${SQL_NOW} AND EXISTS(SELECT 1 FROM pull_owner_changes c
          WHERE c.change_id=pull_tokens.change_id AND c.install_id=pull_tokens.install_id AND c.state='completed'
          AND c.switched_owner_generation=? AND c.revert_until_ms=pull_tokens.expires_ms))`,
        v.install_id,retireGeneration?v.generation:v.flow_id,i?.owner_generation??0),
      this.stmt(`UPDATE pull_browser_sessions SET state='invalidated' WHERE install_id=? AND
        ${retireGeneration?'generation<=?':'flow_id=?'}`,v.install_id,retireGeneration?v.generation:v.flow_id),
      this.stmt(`UPDATE pull_control_outbox SET state=CASE WHEN state='sending' THEN 'uncertain' ELSE 'expired' END,
        envelope_sealed=NULL,finished_ms=? WHERE install_id=? AND
        ${retireGeneration?'flow_id IN (SELECT flow_id FROM pull_flows WHERE install_id=? AND expected_generation<=?)':'flow_id=?'}
        AND template IN ('signup','stepup','new_owner') AND state IN ('queued','sending','failed')`,
        now,v.install_id,...(retireGeneration?[v.install_id,v.generation]:[v.flow_id])));
      if (affectsCurrent) {
        valid(integer(i.epoch+1),'stale_generation');
        statements.push(this.stmt("UPDATE pull_generations SET state='fenced',fenced_ms=COALESCE(fenced_ms,?) WHERE install_id=? AND generation<=?",now,v.install_id,v.generation),
          this.stmt(`UPDATE pull_installs SET state=CASE WHEN state='deleted' THEN 'deleted' ELSE 'disabled' END,
            active_since_ms=NULL,epoch=epoch+1,connection_id=NULL,updated_ms=? WHERE install_id=?`,now,v.install_id),this.registry.changed(),
          this.stmt("UPDATE pull_tickets SET state='invalidated' WHERE install_id=? AND generation<=?",v.install_id,v.generation),
          this.stmt("UPDATE pull_threads SET revoked_ms=COALESCE(revoked_ms,?) WHERE install_id=? AND generation<=?",now,v.install_id,v.generation),
          this.stmt(`UPDATE pull_events SET state='revoked',text=NULL,body_bytes=0,lease_id=NULL,lease_until_ms=NULL,terminal_ms=?
            WHERE install_id=? AND generation<=? AND state IN ('pending','offered','held')`,now,v.install_id,v.generation),
          this.stmt("UPDATE pull_dedupe SET disposition='revoked' WHERE install_id=? AND generation<=? AND disposition='pending'",v.install_id,v.generation),
          this.stmt("UPDATE pull_intents SET state='revoked',finished_ms=? WHERE install_id=? AND generation<=? AND state='reserved'",now,v.install_id,v.generation),
          this.stmt("UPDATE pull_intents SET state='uncertain' WHERE install_id=? AND generation<=? AND state='sending'",v.install_id,v.generation),
          this.stmt(`DELETE FROM pull_owner_slots WHERE install_id=? AND role='pending' AND flow_id IN
            (SELECT flow_id FROM pull_owner_changes WHERE install_id=? AND expected_generation<=? AND state='pending')`,
            v.install_id,v.install_id,v.generation),
          this.stmt(`UPDATE pull_owner_changes SET state='cancelled',old_owner_sealed=NULL,new_owner_sealed=NULL
            WHERE install_id=? AND expected_generation<=? AND state='pending'`,v.install_id,v.generation),
          this.stmt('DELETE FROM pull_notifications WHERE install_id=?',v.install_id));
      }
      statements.push(this.fresh(auth,now));
      try { await this.db.batch(statements); return result; }
      catch (error) { if (!attempt) continue;
        console.error('[agent-mail] pull_fence_transaction_refused'); throw new Error('storage_unavailable'); }
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

      const result = await this.execute(auth, value);
      return await this.response(auth, PATH, { v: 1, code: 'fenced', request_id: (match(I, value?.op_id) ? value.op_id : auth.nonce), ...result }, 200);
    } catch (error) {
      // Fence failure is uniformly unsigned 401 (contract section 4), even
      // after a valid denial capability. No signed partial revoke evidence.
      console.error('[agent-mail] pull_fence_refused');
      return Response.json({ v: 1, code: 'unauthorized' }, { status: 401, headers: HEADERS });

    }
  }
}
