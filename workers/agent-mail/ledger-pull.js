// MC-1166 unit 08: transport-independent pull registry/send facade. No I/O.
import { OwnerRegistry } from './owner-registry.js';
import { PullLedger } from './pull-ledger.js';
import { strictMsgId } from './content-id.js';
import { SQL_NOW, sqlClockAtOrAfter } from './sql-clock.js';

const DAY = 86_400_000, MAX = Number.MAX_SAFE_INTEGER, GMAX = 999_999_999_999_999;
const I = /^[a-f0-9]{32}$/, H = /^[a-f0-9]{64}$/, ID = /^[A-Za-z0-9_-]{1,128}$/;
const valid = (ok, code = 'invalid_request') => { if (!ok) throw new Error(code); };
const match = (re, s) => typeof s === 'string' && re.test(s);
const integer = (n, low = 0, high = MAX) => Number.isSafeInteger(n) && !Object.is(n, -0) && n >= low && n <= high;
// This same predicate gates lookups AND the first/last mutation statements.
// It deliberately does not bind socket epoch: a reconnect cannot revoke a send.
const CURRENT = `FROM pull_installs i JOIN pull_generations g ON g.install_id=i.install_id
  AND g.generation=i.generation AND g.owner_generation=i.owner_generation AND g.owner_hash=i.owner_hash
  AND g.key_id=i.key_id AND g.public_key=i.public_key JOIN pull_addresses a
  ON a.install_id=i.install_id AND a.address_local=i.address_local AND a.state='assigned'
  WHERE i.state='active' AND g.state='current' AND NOT EXISTS(SELECT 1 FROM pull_fences f
    WHERE f.install_id=i.install_id AND f.generation=i.generation AND (f.mode='revoke' OR f.flow_id=g.flow_id))`;
const SNAPSHOT = `i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.owner_hash=?
  AND i.owner_address=? AND i.key_id=? AND i.public_key=? AND i.address_local=? AND g.flow_id=?`;
const intentSQL = `SELECT *,input_digest AS digest,(SELECT token_hash FROM pull_threads t
  WHERE t.install_id=pull_intents.install_id AND t.generation=pull_intents.generation
  AND t.intent_id=pull_intents.intent_id) AS token_hash FROM pull_intents WHERE install_id=? AND generation=?
  AND owner_generation=? AND owner_hash=? AND intent_id=? AND EXISTS(SELECT 1 ${CURRENT} AND ${SNAPSHOT})`;

/** Obtain via new Ledger(db,policy).pull({clock?}); legacy Ledger is unchanged.
 * registration(id,expectedGeneration?,expectedOwnerGeneration?) and recipient(local)
 * return ONLY active, assigned, unfenced current pull snapshots (or null).
 * reserveSend(reg,{intent_id,digest,thread_id,message_id,token_hash},now) atomically
 * reserves intent+thread, WITHOUT provider authorization. Exact replay returns
 * duplicate:true, including sending/uncertain/terminal states; never resend it.
 * authorizeSend(reg,id,now) CASes reserved->sending with shared global and install
 * budgets in ONE transaction. Only that successful return authorizes ONE provider
 * call, using its immutable owner snapshot. Never send after a duplicate reserve.
 * finishSend(reg,id,state,now,provider_id='') records explicit provider evidence;
 * uncertain->accepted requires a positive provider id. Unknown acceptance means
 * uncertain, never failed; failed asserts the provider positively did not accept.
 * Owner/generation changes refuse all old-scope transitions, even after revert;
 * owner-change retirement already turns in-flight sending into uncertain.
 * recordSendAcceptance(authorization,provider_id,now) retains late positive
 * evidence on an exact retired, already-authorized intent. It changes ONLY
 * provider_id/finished_ms (acceptance observation time), never state/authority.
 * Queue operations delegate directly to unit07, with its scope/epoch/input API.
 * queue exposes that PullLedger for page/offer/ack/hold and sweep methods.
 * Queue admission includes atomic ingress budgets: do not separately reserveIngress.
 * thread.owner_generation is the actual pull owner generation, distinct from g;
 * unit10 must project it into the pure legacy decide() owner-generation input.
 * limit is the shared abuse/request limiter; send budgets belong to authorizeSend.
 * claimNonce is a standalone post-crypto replay fence; transactional HTTP handlers
 * must keep nonce + operation in their existing single guarded batch instead.
 * No due/claim/settle/reconcile legacy-push methods are exposed here.
 */
export class PullTransportLedger {
  // Only the actual successful CAS return may supply in-flight provider proof;
  // a copied/HTTP-supplied scope cannot manufacture an authorization receipt.
  #sendAuthorizations = new WeakSet();
  constructor({ ledger, clock = Date.now }) {
    valid(ledger?.db && typeof clock === 'function');
    this.db = ledger.db; this.policy = ledger.policy; this.clock = clock; this.shared = ledger;
    this.registry = new OwnerRegistry(this.db); this.queue = new PullLedger({ db: this.db, clock });
  }
  stmt(sql, ...args) { return this.shared.stmt(sql, ...args); }
  async first(sql, ...args) {
    try { return await this.shared.first(sql, ...args); }
    catch { console.error('[agent-mail] pull_transport_read_failed'); throw new Error('storage_unavailable'); }
  }
  time(now = this.clock()) { valid(integer(now, 0, MAX - 120 * DAY)); return now; }
  scope(reg) {
    valid(reg && match(I, reg.install_id) && integer(reg.generation, 1, GMAX)
      && integer(reg.owner_generation, 1, GMAX) && match(H, reg.owner_hash)
      && typeof reg.owner_address === 'string' && match(ID, reg.key_id)
      && match(/^[A-Za-z0-9_-]{43}$/, reg.public_key) && match(/^m[a-f0-9]{29}$/, reg.address_local)
      && match(I, reg.flow_id));
  }
  args(reg) { this.scope(reg); return [reg.install_id, reg.generation, reg.owner_generation, reg.owner_hash,
    reg.owner_address, reg.key_id, reg.public_key, reg.address_local, reg.flow_id]; }
  authority(reg) { return this.registry.guard(`EXISTS(SELECT 1 ${CURRENT} AND ${SNAPSHOT})`, ...this.args(reg)); }
  async active(reg) {
    valid(await this.first(`SELECT 1 ${CURRENT} AND ${SNAPSHOT}`, ...this.args(reg)), 'stale_generation');
  }
  async atomic(reg, statements) {
    try { return await this.db.batch([this.authority(reg), ...statements, this.authority(reg)]); }
    catch {
      await this.active(reg);
      console.error('[agent-mail] pull_transport_transaction_refused'); throw new Error('storage_unavailable');
    }
  }
  timeGuard(now, day = false) {
    return this.registry.guard(`${sqlClockAtOrAfter()}${day ? ` AND CAST(${SQL_NOW}/${DAY} AS INTEGER)=?` : ''}`,
      now, ...(day ? [Math.floor(now / DAY)] : []));
  }
  async registration(id, generation = null, ownerGeneration = null) {
    valid(match(I, id) && (generation === null || integer(generation, 1, GMAX))
      && (ownerGeneration === null || integer(ownerGeneration, 1, GMAX)));
    return this.first(`SELECT i.*,g.flow_id,1 AS active,1 AS owner_verified ${CURRENT} AND i.install_id=?
      AND (? IS NULL OR i.generation=?) AND (? IS NULL OR i.owner_generation=?)`,
    id, generation, generation, ownerGeneration, ownerGeneration);
  }
  async recipient(local) {
    valid(match(/^m[a-f0-9]{29}$/, local));
    return this.first(`SELECT i.*,g.flow_id,1 AS active,1 AS owner_verified ${CURRENT} AND a.address_local=?`, local);
  }
  async claimNonce(m) {
    valid(m && match(I, m.install_id) && integer(Number(m.generation), 1, GMAX)
      && match(ID, m.key_id) && match(I, m.nonce) && m.direction === 'install-to-pull'
      && integer(m.expires_at));
    try {
      const r = await this.shared.run('INSERT OR IGNORE INTO pull_nonces VALUES(?,?,?,?,?,?)',
        m.direction, m.install_id, Number(m.generation), m.key_id, m.nonce, m.expires_at);
      return r.meta.changes === 1;
    } catch { console.error('[agent-mail] pull_transport_nonce_failed'); throw new Error('storage_unavailable'); }
  }
  async limit(bucket, maximum, now, window = DAY) { return this.shared.limit(bucket, maximum, now, window); }
  async reject(reason, now, install = '') {
    valid(match(/^[a-z][a-z0-9_]{0,63}$/, reason) && (install === '' || match(I, install))); this.time(now);
    try {
      await this.db.batch([
        this.stmt(`INSERT INTO pull_audit_overflow SELECT ?,1 WHERE (SELECT COUNT(*) FROM pull_audit)>=10000
          ON CONFLICT(day_ms) DO UPDATE SET count=count+1`, Math.floor(now / DAY) * DAY),
        this.stmt(`INSERT INTO pull_audit(install_id,at_ms,code) SELECT ?,?,? WHERE (SELECT COUNT(*) FROM pull_audit)<10000`,
          install, now, reason),
      ]);
    } catch { console.error('[agent-mail] pull_transport_rejection_failed'); throw new Error('storage_unavailable'); }
  }
  async thread(hash) {
    valid(match(H, hash)); const now = this.time();
    const row = await this.first(`SELECT t.*,t.expires_ms AS expires_at FROM pull_threads t JOIN pull_intents n
      ON n.install_id=t.install_id AND n.generation=t.generation AND n.intent_id=t.intent_id
      AND n.owner_generation=t.owner_generation WHERE t.token_hash=? AND t.revoked_ms IS NULL AND t.expires_ms>?
      AND n.state IN ('sending','accepted','uncertain') AND EXISTS(SELECT 1 ${CURRENT}
        AND i.install_id=t.install_id AND i.generation=t.generation AND i.owner_generation=t.owner_generation
        AND i.owner_hash=n.owner_hash)`, hash, now);
    return row && { ...row, revoked: false, message_ids: [row.message_id] };
  }
  intentStatement(reg, id) {
    this.scope(reg); valid(match(ID, id));
    return this.stmt(intentSQL, reg.install_id, reg.generation, reg.owner_generation, reg.owner_hash, id, ...this.args(reg));
  }
  async intent(reg, id) {
    reg = { ...reg };
    valid(match(ID, id));
    await this.active(reg);
    const row = await this.first(intentSQL, reg.install_id, reg.generation, reg.owner_generation, reg.owner_hash, id, ...this.args(reg));
    if (!row) await this.active(reg);
    return row;
  }
  same(prior, value) {
    valid(prior.digest === value.digest && prior.thread_id === value.thread_id && prior.message_id === value.message_id
      && prior.token_hash === value.token_hash, 'intent_conflict');
    return { ...prior, duplicate: true };
  }
  async reserveSend(reg, value, now) {
    reg = { ...reg }; value = { ...value };
    this.scope(reg); this.time(now);
    valid(value && match(ID, value.intent_id) && match(ID, value.thread_id) && match(H, value.digest)
      && match(H, value.token_hash) && strictMsgId(value.message_id) === value.message_id);
    await this.active(reg);
    const prior = await this.intent(reg, value.intent_id);
    if (prior) return this.same(prior, value);
    const expires = now + this.policy.threadTTL;
    valid(integer(expires, now + 1, MAX - 30 * DAY));
    try {
      const result = await this.atomic(reg, [this.timeGuard(now),
        this.stmt(`INSERT INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,
          thread_id,message_id,state,created_ms) VALUES(?,?,?,?,?,?,?,?,'reserved',?)`,
        reg.install_id, reg.generation, value.intent_id, reg.owner_generation, reg.owner_hash, value.digest,
        value.thread_id, value.message_id, now),
        this.stmt(`INSERT INTO pull_threads VALUES(?,?,?,?,?,?,?,?,?,NULL)`, reg.install_id, reg.generation,
          value.thread_id, reg.owner_generation, value.intent_id, value.token_hash, value.message_id, expires, expires + 30 * DAY),
        this.timeGuard(now), this.intentStatement(reg, value.intent_id),
      ]);
      return { ...result.at(-2).results[0], duplicate: false };
    } catch (error) {
      const raced = await this.intent(reg, value.intent_id);
      if (raced) return this.same(raced, value);
      throw error;
    }
  }
  async authorizeSend(reg, id, now) {
    reg = { ...reg };
    this.scope(reg); this.time(now); valid(match(ID, id));
    const prior = await this.intent(reg, id); valid(prior?.state === 'reserved', 'intent_conflict');
    const day = Math.floor(now / DAY), charges = [];
    const buckets = [[`pull:send:install:${reg.install_id}:${day}`, this.policy.sendsPerDay],
      [`pull:send:global:${day}`, this.policy.globalSendsPerDay]];
    for (const [bucket, cap] of buckets) {
      valid(integer(cap, 1));
      const row = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
      valid(!row || row.count < cap, 'send_limit');
      charges.push(this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket)
        DO UPDATE SET count=count+1 WHERE count<?`, bucket, (day + 2) * DAY, cap), this.registry.changed());
    }
    let result;
    try { result = await this.atomic(reg, [this.timeGuard(now, true),
      this.stmt(`UPDATE pull_intents SET state='sending' WHERE install_id=? AND generation=? AND owner_generation=?
        AND owner_hash=? AND intent_id=? AND state='reserved' AND EXISTS(SELECT 1 FROM pull_threads t
          WHERE t.install_id=pull_intents.install_id AND t.generation=pull_intents.generation
          AND t.intent_id=pull_intents.intent_id AND t.owner_generation=pull_intents.owner_generation
          AND t.revoked_ms IS NULL AND t.expires_ms>${SQL_NOW})`,
      reg.install_id, reg.generation, reg.owner_generation, reg.owner_hash, id), this.registry.changed(),
      ...charges, this.timeGuard(now, true), this.intentStatement(reg, id),
    ]); } catch (error) {
      // A competing authorization or budget reservation is a refusal, never a
      // second handoff. Classification is read-only after the complete rollback.
      const raced = await this.intent(reg, id); valid(raced?.state === 'reserved', 'intent_conflict');
      for (const [bucket, cap] of buckets) {
        const row = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
        valid(!row || row.count < cap, 'send_limit');
      }
      throw error;
    }
    const authorized = Object.freeze({ ...reg, ...result.at(-2).results[0], authorized: true, authorized_ms: now });
    this.#sendAuthorizations.add(authorized);
    return authorized;
  }
  async recordSendAcceptance(authorization, providerId, now) {
    const a = { ...authorization };
    this.scope(a); this.time(now);
    valid(this.#sendAuthorizations.has(authorization) && a.authorized === true
      && integer(a.authorized_ms) && a.authorized_ms <= now
      && match(ID, a.intent_id) && match(ID, a.thread_id) && match(H, a.input_digest)
      && strictMsgId(a.message_id) === a.message_id
      && typeof providerId === 'string' && /^[\x20-\x7e]{1,256}$/.test(providerId) && providerId.trim());
    // No current-owner predicate: this is inert evidence for a fenced scope.
    // Every immutable intent/generation binding still participates in the CAS.
    const where = `install_id=? AND generation=? AND owner_generation=? AND owner_hash=? AND intent_id=?
      AND input_digest=? AND thread_id=? AND message_id=? AND state='uncertain' AND created_ms<=?
      AND (provider_id='' OR provider_id=?) AND EXISTS(SELECT 1 FROM pull_generations g
        WHERE g.install_id=pull_intents.install_id AND g.generation=pull_intents.generation
        AND g.owner_generation=pull_intents.owner_generation AND g.owner_hash=pull_intents.owner_hash
        AND g.key_id=? AND g.public_key=? AND g.flow_id=? AND g.state='fenced')`;
    const args = [a.install_id, a.generation, a.owner_generation, a.owner_hash, a.intent_id,
      a.input_digest, a.thread_id, a.message_id, a.authorized_ms, providerId, a.key_id, a.public_key, a.flow_id];
    try {
      const result = await this.db.batch([this.timeGuard(now),
        this.stmt(`UPDATE pull_intents SET finished_ms=CASE WHEN provider_id='' THEN ? ELSE finished_ms END,
          provider_id=? WHERE ${where}`, now, providerId, ...args), this.registry.changed(),
        this.timeGuard(now), this.stmt(`SELECT * FROM pull_intents WHERE ${where}`, ...args),
      ]);
      return result.at(-1).results[0];
    } catch {
      console.error('[agent-mail] pull_send_evidence_refused');
      throw new Error('storage_unavailable');
    }
  }
  async finishSend(reg, id, state, now, providerId = '') {
    reg = { ...reg };
    this.scope(reg); this.time(now);
    valid(match(ID, id) && ['accepted','uncertain','failed','revoked'].includes(state)
      && typeof providerId === 'string' && providerId.length <= 1024 && !/[\x00-\x1f\x7f]/.test(providerId)
      && (state === 'accepted' ? providerId.length > 0 : providerId === ''));
    const prior = await this.intent(reg, id); valid(prior, 'not_found');
    valid(prior.state === 'sending' && state !== 'revoked'
      || prior.state === 'reserved' && ['failed','revoked'].includes(state)
      || prior.state === 'uncertain' && ['uncertain','accepted'].includes(state)
      || prior.state === state && prior.provider_id === providerId, 'intent_conflict');
    const result = await this.atomic(reg, [this.timeGuard(now),
      this.stmt(`UPDATE pull_intents SET state=?,provider_id=?,finished_ms=? WHERE install_id=? AND generation=?
        AND owner_generation=? AND owner_hash=? AND intent_id=? AND state=? AND provider_id=?`,
      state, providerId, prior.state === state ? prior.finished_ms : now, reg.install_id, reg.generation, reg.owner_generation,
      reg.owner_hash, id, prior.state, prior.provider_id), this.registry.changed(),
      this.timeGuard(now), this.intentStatement(reg, id),
    ]);
    return result.at(-2).results[0];
  }
  async enqueue(scope, event) { return this.queue.enqueue(scope, event); }
}
