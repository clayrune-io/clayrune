// D1 state ownership. Every claim/limit/custody transition is SQL-atomic;
// multi-statement invariants use D1's transactional batch, never JS locks.
import { randomId } from './signing.js';
export const DAY = 86_400_000;
export const POLICY = Object.freeze({ queueTTL: 30 * DAY, threadTTL: 90 * DAY,
  metadataTTL: 30 * DAY, lease: 60_000, queueLimit: 100, globalQueueLimit: 10_000,
  sendsPerDay: 20, globalSendsPerDay: 1000, ingressPerDay: 200, globalIngressPerDay: 10_000 });
const ACTIVE = `EXISTS (SELECT 1 FROM registrations r WHERE r.install_id=? AND r.generation=?
  AND r.active=1 AND r.owner_verified=1)`;
export class Ledger {
  constructor(db, policy = {}) { this.db = db; this.policy = { ...POLICY, ...policy }; }
  stmt(sql, ...args) { return this.db.prepare(sql).bind(...args); }
  async first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  async run(sql, ...args) { return this.stmt(sql, ...args).run(); }
  async registration(id) { return this.first('SELECT * FROM registrations WHERE install_id=?', id); }
  async recipient(local) { return this.first('SELECT * FROM registrations WHERE address_local=?', local); }
  async claimNonce(m) {
    const r = await this.run(`INSERT OR IGNORE INTO nonces VALUES(?,?,?,?,?,?)`,
      m.direction, m.install_id, Number(m.generation), m.key_id, m.nonce, m.expires_at);
    return r.meta.changes === 1;
  }
  async limit(bucket, maximum, now, window = DAY) {
    const key = `${bucket}:${Math.floor(now / window)}`;
    const r = await this.run(`INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(bucket)
      DO UPDATE SET count=count+1 WHERE count < ?`, key, now + window * 2, maximum);
    return r.meta.changes === 1;
  }
  async reserveIngress(install, now) {
    const day = Math.floor(now / DAY);
    const ownerKey = `ingress:${install}:${day}`;
    const globalKey = `ingress-accepted:global:${day}`;
    // D1 batches serialize these statements in one transaction. Only charge the
    // owner if global has room; changes() ties the global charge to that exact
    // owner admission. A failed statement rolls both back, with no refund gap.
    const result = await this.db.batch([
      this.stmt(`INSERT INTO rate_limits SELECT ?,1,? WHERE ? > 0 AND
        COALESCE((SELECT count FROM rate_limits WHERE bucket=?),0) < ?
        ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count < ?`,
        ownerKey, now + 2 * DAY, this.policy.ingressPerDay,
        globalKey, this.policy.globalIngressPerDay, this.policy.ingressPerDay),
      this.stmt(`INSERT INTO rate_limits SELECT ?,1,? WHERE changes()=1
        ON CONFLICT(bucket) DO UPDATE SET count=count+1`, globalKey, now + 2 * DAY),
    ]);
    return result[0].meta.changes === 1;
  }
  async reject(reason, now) {
    // Callers pass constant reason codes only. Never persist raw exception text.
    await this.db.batch([
      this.stmt(`INSERT INTO rejection_overflow SELECT ?,1 WHERE (SELECT COUNT(*) FROM rejections)>=10000
        ON CONFLICT(day) DO UPDATE SET count=count+1`, Math.floor(now / DAY) * DAY),
      this.stmt(`INSERT INTO rejections(at,reason) SELECT ?,? WHERE
        (SELECT COUNT(*) FROM rejections) < 10000`, now, reason),
    ]);
  }
  async thread(hash) {
    const t = await this.first('SELECT * FROM threads WHERE token_hash=?', hash);
    return t && { ...t, owner_generation: t.generation, revoked: t.revoked !== 0, message_ids: [t.message_id] };
  }
  async intent(reg, id) {
    return this.first('SELECT * FROM intents WHERE install_id=? AND generation=? AND intent_id=?',
      reg.install_id, reg.generation, id);
  }
  async reserveSend(reg, value, now) {
    // An existing thread is immutable; every intent creates one outbound thread.
    // A token or thread collision rolls back BOTH statements instead of sending.
    await this.db.batch([
      this.stmt(`INSERT INTO intents SELECT ?,?,?,?,?,?,'sending',?,NULL WHERE ${ACTIVE}`,
        reg.install_id, reg.generation, value.intent_id, value.digest, value.thread_id, value.message_id,
        now, reg.install_id, reg.generation),
      this.stmt(`INSERT INTO threads SELECT ?,?,?,?,?,?,0 WHERE EXISTS
        (SELECT 1 FROM intents WHERE install_id=? AND generation=? AND intent_id=? AND state='sending')`,
        reg.install_id, reg.generation, value.thread_id, value.token_hash, value.message_id,
        now + this.policy.threadTTL, reg.install_id, reg.generation, value.intent_id),
    ]);
    return this.intent(reg, value.intent_id);
  }
  async finishSend(reg, id, state, now) {
    await this.run(`UPDATE intents SET state=?,finished_at=? WHERE install_id=? AND generation=?
      AND intent_id=? AND (state='sending' OR (state='uncertain' AND ?='accepted'))`,
      state, now, reg.install_id, reg.generation, id, state);
  }
  async enqueue(reg, event, now) {
    const prior = await this.first(`SELECT * FROM events WHERE install_id=? AND generation=? AND thread_id=? AND incoming_id=?`,
      reg.install_id, reg.generation, event.thread_id, event.incoming_id);
    if (prior) {
      if (prior.digest !== event.digest) {
        await this.reject('message_id_conflict', now);
        return { status: 'conflict', event_id: prior.event_id };
      }
      return { status: 'duplicate', event_id: prior.event_id };
    }
    const result = await this.run(`INSERT OR IGNORE INTO events(event_id,install_id,generation,thread_id,
      incoming_id,digest,payload,state,reason,received_at,expires_at,next_attempt)
      SELECT ?,?,?,?,?,?,?,?, ?,?,?,? WHERE ${ACTIVE}
      AND EXISTS (SELECT 1 FROM threads WHERE install_id=? AND generation=? AND thread_id=? AND revoked=0 AND expires_at>?)
      AND (SELECT COUNT(*) FROM events WHERE install_id=? AND payload IS NOT NULL) < ?
      AND (SELECT COUNT(*) FROM events WHERE payload IS NOT NULL) < ?`,
      event.event_id, reg.install_id, reg.generation, event.thread_id, event.incoming_id, event.digest,
      JSON.stringify(event.payload), event.held ? 'held' : 'pending', event.held ? 'incoming_id_missing' : null,
      now, now + this.policy.queueTTL, now, reg.install_id, reg.generation,
      reg.install_id, reg.generation, event.thread_id, now, reg.install_id, this.policy.queueLimit, this.policy.globalQueueLimit);
    if (result.meta.changes === 1) return { status: event.held ? 'held' : 'queued', event_id: event.event_id };
    const raced = await this.first('SELECT * FROM events WHERE event_id=?', event.event_id);
    if (raced) {
      if (raced.digest !== event.digest) await this.reject('message_id_conflict', now);
      return { status: raced.digest === event.digest ? 'duplicate' : 'conflict', event_id: raced.event_id };
    }
    await this.reject('queue_full_or_revoked', now);
    return { status: 'refused' };
  }
  async reconcile(now) {
    await this.db.batch([
      this.stmt(`UPDATE intents SET state='uncertain',finished_at=? WHERE state='sending' AND created_at<=?`, now, now - this.policy.lease),
      this.stmt(`UPDATE events SET state='revoked',reason='generation_revoked',payload=NULL,terminal_at=?,lease_id=NULL
        WHERE payload IS NOT NULL AND NOT EXISTS (SELECT 1 FROM registrations r WHERE r.install_id=events.install_id
        AND r.generation=events.generation AND r.active=1 AND r.owner_verified=1)`, now),
      this.stmt(`UPDATE events SET state='revoked',reason='thread_revoked',payload=NULL,terminal_at=?,lease_id=NULL
        WHERE payload IS NOT NULL AND NOT EXISTS (SELECT 1 FROM threads t WHERE t.install_id=events.install_id
        AND t.generation=events.generation AND t.thread_id=events.thread_id AND t.revoked=0 AND t.expires_at>?)`, now, now),
      this.stmt(`UPDATE events SET state='expired',reason='queue_expired',payload=NULL,terminal_at=?,lease_id=NULL
        WHERE payload IS NOT NULL AND expires_at<=?`, now, now),
      this.stmt(`UPDATE events SET state='pending',lease_id=NULL,lease_until=NULL
        WHERE state='delivering' AND lease_until<=?`, now),
      this.stmt('DELETE FROM nonces WHERE expires_at<=?', now),
      this.stmt('DELETE FROM rejections WHERE at<=?', now - this.policy.metadataTTL),
      this.stmt('DELETE FROM rejection_overflow WHERE day<=?', now - this.policy.metadataTTL),
      this.stmt('DELETE FROM events WHERE terminal_at<=?', now - this.policy.metadataTTL),
      this.stmt('DELETE FROM rate_limits WHERE expires_at<=?', now),
      this.stmt('DELETE FROM threads WHERE expires_at<=?', now - this.policy.metadataTTL),
      this.stmt(`DELETE FROM intents WHERE COALESCE(finished_at,created_at)<=? AND state!='sending'`, now - this.policy.threadTTL - this.policy.metadataTTL),
    ]);
  }
  async due(now, install = null) {
    return (await this.stmt(`SELECT event_id FROM events WHERE state='pending' AND next_attempt<=?
      AND (? IS NULL OR install_id=?) AND NOT EXISTS (SELECT 1 FROM events earlier
        WHERE earlier.install_id=events.install_id AND earlier.generation=events.generation
        AND earlier.seq<events.seq AND earlier.state IN ('pending','delivering','held'))
      ORDER BY seq LIMIT 50`, now, install, install).all()).results;
  }
  async claim(event_id, now) {
    const lease = randomId();
    const result = await this.run(`UPDATE events SET state='delivering',lease_id=?,lease_until=?,attempts=attempts+1
      WHERE event_id=? AND state='pending' AND next_attempt<=? AND expires_at>?
      AND NOT EXISTS (SELECT 1 FROM events earlier WHERE earlier.install_id=events.install_id
        AND earlier.generation=events.generation AND earlier.seq<events.seq
        AND earlier.state IN ('pending','delivering','held'))`, lease, now + this.policy.lease, event_id, now, now);
    return result.meta.changes === 1 ? this.first('SELECT * FROM events WHERE event_id=?', event_id) : null;
  }
  async settle(event, custody, now, reason = 'delivery_retry') {
    const delay = Math.min(3_600_000, 1000 * 2 ** Math.min(event.attempts, 12));
    return this.run(`UPDATE events SET state=?,payload=CASE WHEN ? THEN NULL ELSE payload END,
      terminal_at=CASE WHEN ? THEN ? ELSE NULL END,reason=?,next_attempt=?,lease_id=NULL,lease_until=NULL
      WHERE event_id=? AND state='delivering' AND lease_id=?`, custody ? 'local_received' : 'pending',
      custody ? 1 : 0, custody ? 1 : 0, now, custody ? null : reason, now + delay, event.event_id, event.lease_id);
  }
}
