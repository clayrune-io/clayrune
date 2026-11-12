// MC-1166 pull v1, unit 07. D1 is authority; no socket, SMTP or provider I/O.
import { bytes, randomId, sha256 } from './signing.js';
import { OwnerRegistry } from './owner-registry.js';
import { strictMsgId } from './content-id.js';
import { SQL_NOW, sqlClockAtOrAfter } from './sql-clock.js';

const DAY = 86_400_000;
const MONTH = 30 * DAY;
const LEASE = 60_000;
const MAX = Number.MAX_SAFE_INTEGER;
const GMAX = 999_999_999_999_999;
const LIVE = "('pending','offered','held')";
const I = /^[a-f0-9]{32}$/;
const H = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const RESULT = /^(?:pass|fail|softfail|neutral|none|temperror|permerror|missing|other)(?:\+(?:pass|fail|softfail|neutral|none|temperror|permerror|missing|other))*$/;
const HOLDS = new Set(['unknown_thread', 'queue_full', 'storage_unavailable', 'local_disabled', 'vault_locked', 'conversation_busy']);
class Refusal extends Error { constructor(code) { super(code); this.code = code; } }
const valid = (v, code = 'invalid_request') => { if (!v) throw new Refusal(code); };
const match = (re, v) => typeof v === 'string' && re.test(v);
const integer = (v, min = 0, max = MAX) => Number.isSafeInteger(v) && !Object.is(v, -0) && v >= min && v <= max;
const hash = (s) => sha256(bytes(s));
const exact = (v, fields) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).length === fields.length && fields.every((k) => Object.hasOwn(v, k));
const dedupeSQL = 'SELECT * FROM pull_dedupe WHERE event_id=?';
const eventSQL = `SELECT e.*,d.body_digest,d.event_digest,t.expires_ms AS thread_expires_ms
  FROM pull_events e JOIN pull_dedupe d USING(event_id) JOIN pull_threads t
  ON t.install_id=e.install_id AND t.generation=e.generation AND t.thread_id=e.thread_id`;

function event(row) {
  let auth;
  try { auth = JSON.parse(row.auth_json); }
  catch { console.error('[agent-mail] pull_ledger_event_corrupt'); throw new Refusal('storage_unavailable'); }
  return { v: 1, event_id: row.event_id, install_id: row.install_id, generation: row.generation,
    owner_generation: row.owner_generation, thread_id: row.thread_id, seq: row.seq,
    incoming_message_id: row.incoming_message_id, body_digest: row.body_digest, event_digest: row.event_digest,
    received_ms: row.received_ms, expires_ms: row.expires_ms, thread_expires_ms: row.thread_expires_ms,
    auth, kind: row.event_kind, text: row.text, reason: row.reason };
}

// Maximum compact signed frame size for this exact payload, including the
// longest allowed service key id, numbers, ids and signature. The DO must use
// compact JSON; reservation is an upper bound, never a client byte assertion.
export function offerFrameBytes(e) {
  const wire = Object.fromEntries(['v','event_id','install_id','generation','owner_generation','thread_id','seq',
    'incoming_message_id','body_digest','event_digest','received_ms','expires_ms','thread_expires_ms','auth','kind','text','reason']
    .map((k)=>[k,e[k]]));
  const decoded = bytes(JSON.stringify({ event: wire, lease_id: '0'.repeat(32), lease_until_ms: MAX })).length;
  const shell = { v: 1, type: 'event', install_id: '0'.repeat(32), generation: GMAX, owner_generation: GMAX,
    epoch: GMAX, frame_id: '0'.repeat(32), issued_ms: MAX, key_id: 'k'.repeat(128), payload: '',
    payload_digest: '0'.repeat(64), signature: 's'.repeat(86) };
  return { decoded, encoded: bytes(JSON.stringify(shell)).length + Math.ceil(decoded * 4 / 3) };
}

/** Internal API for units 08/09/10/13, deliberately unwired.
 * new PullLedger({db,clock?}); scope={install_id,generation,owner_generation,epoch}.
 * A SessionAuth attachment can also be passed; if supplied key/connection ids
 * must match. SMTP adapters must read the current epoch too (zero before a
 * first connection is valid for enqueue). Socket methods require epoch >=1.
 * enqueue takes {thread_id,incoming_message_id,text,auth}; null text is an
 * authenticated empty-body descriptor. Caller already proved From/token/reply
 * correlation; this module rechecks immutable thread and current authority.
 * page returns all pending descriptors, including not-due/leased/held heads.
 * offer returns event+lease only AFTER commit. ack/hold take contractual payloads.
 * All refusals are closed Error.code/message, no database contents in errors.
 * Maintenance methods take NO caller scope: trusted sweep-only deletion APIs,
 * bounded keyset pages with returned cursor, including retired installations.
 * count is rows changed, cursor is last row scanned; continue until cursor=null
 * even if a competing sweep changed all selected rows first. Reset for a new pass.
 */
export class PullLedger {
  constructor({ db, clock = Date.now }) { valid(db && typeof clock === 'function'); this.db = db; this.clock = clock; this.registry = new OwnerRegistry(db); }
  stmt(sql, ...args) { return this.registry.stmt(sql, ...args); }
  guard(sql, ...args) { return this.registry.guard(sql, ...args); }
  async first(sql, ...args) {
    try { return await this.stmt(sql, ...args).first(); }
    catch { console.error('[agent-mail] pull_ledger_read_failed'); throw new Refusal('storage_unavailable'); }
  }
  async all(sql, ...args) {
    try { return (await this.stmt(sql, ...args).all()).results; }
    catch { console.error('[agent-mail] pull_ledger_read_failed'); throw new Refusal('storage_unavailable'); }
  }
  now() { const n = this.clock(); valid(integer(n, 0, MAX - 91 * DAY), 'service_unavailable'); return n; }
  scope(s, socket = false) {
    valid(s && match(I, s.install_id) && integer(s.generation, 1, GMAX)
      && integer(s.owner_generation, 1, GMAX) && integer(s.epoch, socket ? 1 : 0, GMAX));
    valid(s.key_id === undefined || match(ID, s.key_id));
    valid(s.connection_id === undefined || match(I, s.connection_id));
  }
  authority(s) {
    return this.guard(`EXISTS(SELECT 1 FROM pull_installs i JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation AND g.owner_generation=i.owner_generation
      AND g.key_id=i.key_id AND g.public_key=i.public_key AND g.owner_hash=i.owner_hash
      WHERE i.install_id=? AND i.generation=? AND i.owner_generation=? AND i.epoch=? AND i.state='active'
      AND g.state='current' AND (? IS NULL OR i.key_id=?) AND (? IS NULL OR i.connection_id=?)
      AND NOT EXISTS(SELECT 1 FROM pull_fences f WHERE f.install_id=i.install_id AND f.generation=i.generation
        AND (f.mode='revoke' OR f.flow_id=g.flow_id)))`, s.install_id, s.generation, s.owner_generation, s.epoch,
    s.key_id ?? null, s.key_id ?? null, s.connection_id ?? null, s.connection_id ?? null);
  }
  async authorize(s) {
    const i = await this.first(`SELECT i.*,g.state AS generation_state,g.flow_id,g.key_id AS gkey,g.public_key AS gpublic,
      g.owner_hash AS gowner,g.owner_generation AS gog FROM pull_installs i LEFT JOIN pull_generations g
      ON g.install_id=i.install_id AND g.generation=i.generation WHERE i.install_id=?`, s.install_id);
    valid(i && i.generation === s.generation && i.owner_generation === s.owner_generation, 'stale_generation');
    valid(i.epoch === s.epoch && (s.connection_id === undefined || i.connection_id === s.connection_id), 'connection_superseded');
    valid(i.state !== 'frozen', 'registration_frozen'); valid(i.state === 'active', 'registration_inactive');
    valid(i.generation_state === 'current' && i.gkey === i.key_id && i.gpublic === i.public_key
      && i.gowner === i.owner_hash && i.gog === i.owner_generation && (s.key_id === undefined || i.key_id === s.key_id), 'fenced');
    valid(!await this.first(`SELECT 1 FROM pull_fences WHERE install_id=? AND generation=? AND (mode='revoke' OR flow_id=?)`,
      s.install_id, s.generation, i.flow_id), 'fenced'); return i;
  }
  timeGuard(now, end = MAX, day = false) {
    return this.guard(`${sqlClockAtOrAfter()} AND ${SQL_NOW}<?${day ? ` AND CAST(${SQL_NOW}/${DAY} AS INTEGER)=?` : ''}`,
      ...[now, end, ...(day ? [Math.floor(now / DAY)] : [])]);
  }
  async atomic(s, statements) {
    try { return await this.db.batch([this.authority(s), ...statements, this.authority(s)]); }
    catch (error) {
      if (error instanceof Refusal) throw error;
      await this.authorize(s); // Classify a concurrent fence, without leaking DB strings.
      console.error('[agent-mail] pull_ledger_transaction_refused'); throw new Refusal('storage_unavailable');
    }
  }
  notify(install, now) {
    return [this.stmt(`INSERT INTO pull_notifications(install_id,revision,dirty_from_seq,next_attempt_ms)
      SELECT ?,1,MIN(seq),? FROM pull_events WHERE install_id=? AND state IN ${LIVE} HAVING COUNT(*)>0
      ON CONFLICT(install_id) DO UPDATE SET revision=revision+1,dirty_from_seq=excluded.dirty_from_seq,
      next_attempt_ms=excluded.next_attempt_ms,lease_id=NULL,lease_until_ms=NULL WHERE revision<?`, install, now, install, MAX),
    this.guard(`NOT EXISTS(SELECT 1 FROM pull_notifications WHERE install_id=? AND revision>=?)`, install, MAX),
    this.stmt(`DELETE FROM pull_notifications WHERE install_id=? AND NOT EXISTS
      (SELECT 1 FROM pull_events WHERE install_id=? AND state IN ${LIVE})`, install, install)];
  }
  async thread(s, thread) {
    const t = await this.first(`SELECT * FROM pull_threads WHERE install_id=? AND generation=? AND owner_generation=? AND thread_id=?`,
      s.install_id, s.generation, s.owner_generation, thread);
    valid(t && t.revoked_ms === null, 'unknown_thread'); return t;
  }
  threadGuard(s, t, now, unexpired = true) {
    return this.guard(`EXISTS(SELECT 1 FROM pull_threads WHERE install_id=? AND generation=? AND owner_generation=?
      AND thread_id=? AND revoked_ms IS NULL AND expires_ms=? AND dedupe_until_ms=?${unexpired ? ` AND expires_ms>${SQL_NOW}` : ''})`,
    s.install_id, s.generation, s.owner_generation, t.thread_id, t.expires_ms, t.dedupe_until_ms);
  }
  async enqueue(s, v) {
    this.scope(s); valid(exact(v, ['thread_id','incoming_message_id','text','auth']) && match(ID, v.thread_id));
    valid(typeof v.incoming_message_id === 'string' && v.incoming_message_id.length <= 8192
      && strictMsgId(v.incoming_message_id) === v.incoming_message_id);
    valid(v.text === null || (typeof v.text === 'string' && v.text.trim() && !v.text.includes('\0')
      && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v.text) && bytes(v.text).length <= 16384));
    const a = v.auth;
    valid(exact(a, ['ingress','spf','dkim','dmarc','aligned']) && a.ingress === 'cloudflare'
      && ['spf','dkim','dmarc'].every((k) => match(RESULT, a[k]) && a[k].length <= 256)
      && a.dmarc === 'pass' && ['spf','dkim','spf+dkim'].includes(a.aligned)
      && (!a.aligned.includes('spf') || a.spf.split('+').includes('pass'))
      && (!a.aligned.includes('dkim') || a.dkim.split('+').includes('pass')));
    const event_id = await hash(JSON.stringify([s.install_id, String(s.generation), v.thread_id, v.incoming_message_id]));
    const body_digest = await hash(v.text ?? ''); const incoming = await hash(v.incoming_message_id);
    for (let attempt = 0; attempt < 3; attempt++) {
      const now = this.now(); const i = await this.authorize(s); const t = await this.thread(s, v.thread_id);
      valid(integer(t.expires_ms) && integer(t.dedupe_until_ms) && t.dedupe_until_ms === t.expires_ms + MONTH, 'storage_unavailable');
      const old = await this.first(dedupeSQL, event_id);
      if (old) {
        valid(old.dedupe_until_ms > now, 'event_expired');
        const same = old.body_digest === body_digest;
        await this.atomic(s, [this.timeGuard(now, old.dedupe_until_ms), this.threadGuard(s, t, now, false),
          this.guard('EXISTS(SELECT 1 FROM pull_dedupe WHERE event_id=? AND body_digest=? AND event_digest=?)', event_id, old.body_digest, old.event_digest),
          ...(!same ? this.conflict(s.install_id, event_id, now) : []), this.timeGuard(now, old.dedupe_until_ms)]);
        valid(same, 'message_id_conflict'); return { duplicate: true, ...old };
      }
      const end = Math.min(now + MONTH, t.expires_ms); valid(end > now, 'event_expired');
      valid(integer(i.next_seq, 1, MAX - 1), 'storage_unavailable');
      const kind = v.text === null ? 'held' : 'text'; const reason = kind === 'held' ? 'empty_body' : '';
      const e = { v: 1, event_id, install_id:s.install_id,generation:s.generation,owner_generation:s.owner_generation,
        thread_id: v.thread_id, seq: i.next_seq, incoming_message_id: v.incoming_message_id,
        body_digest, received_ms: now, expires_ms: end, thread_expires_ms: t.expires_ms, kind, reason, auth: a };
      const digest = await hash(JSON.stringify(['clayrune-mail-pull-event','1',s.install_id,String(s.generation),String(s.owner_generation),
        event_id,v.thread_id,String(e.seq),v.incoming_message_id,body_digest,String(now),String(end),String(t.expires_ms),
        kind,reason,a.ingress,a.spf,a.dkim,a.dmarc,a.aligned]));
      const size = bytes(v.text ?? '').length; const day = Math.floor(now / DAY);
      const statements = [this.timeGuard(now, end, true), this.threadGuard(s, t, now),
        this.stmt('UPDATE pull_installs SET next_seq=next_seq+1 WHERE install_id=? AND next_seq=?', s.install_id, e.seq), this.registry.changed(),
        this.guard(`(SELECT COUNT(*) FROM pull_events WHERE install_id=? AND state IN ${LIVE})+
          (SELECT COUNT(*) FROM pull_ingress_holds WHERE install_id=? AND expires_ms>${SQL_NOW})<100
          AND COALESCE((SELECT SUM(body_bytes) FROM pull_events WHERE install_id=? AND state IN ${LIVE}),0)+?<=2097152`, s.install_id,s.install_id,s.install_id,size),
        this.guard(`(SELECT COUNT(*) FROM pull_events WHERE state IN ${LIVE})+
          (SELECT COUNT(*) FROM pull_ingress_holds WHERE expires_ms>${SQL_NOW})<10000
          AND COALESCE((SELECT SUM(body_bytes) FROM pull_events WHERE state IN ${LIVE}),0)+?<=209715200`, size),
        ...[[`pull:ingress:install:${s.install_id}:${day}`,200],[`pull:ingress:global:${day}`,10000]].flatMap(([bucket, limit]) => [
          this.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<?`, bucket,(day+2)*DAY,limit),this.registry.changed()]),
        this.stmt(`INSERT INTO pull_dedupe VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?)`, event_id,s.install_id,s.generation,s.owner_generation,
          v.thread_id,incoming,body_digest,digest,e.seq,now,end,t.dedupe_until_ms),
        this.stmt(`INSERT INTO pull_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)`, event_id,s.install_id,s.generation,
          s.owner_generation,v.thread_id,e.seq,v.incoming_message_id,kind,v.text,size,JSON.stringify(a),reason,kind === 'held' ? 'held' : 'pending',now,end,now),
        ...this.notify(s.install_id,now),this.timeGuard(now,end,true)];
      try { await this.atomic(s, statements); return { duplicate: false, ...e, event_digest: digest, text: v.text }; }
      catch (error) {
        if (!(error instanceof Refusal) || error.code !== 'storage_unavailable') throw error;
        const current = await this.authorize(s);
        if (attempt < 2 && (current.next_seq !== e.seq || await this.first(dedupeSQL,event_id))) continue;
        const quota = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', `pull:ingress:install:${s.install_id}:${day}`);
        const global = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', `pull:ingress:global:${day}`);
        valid(!(quota?.count >= 200 || global?.count >= 10000), 'ingress_limit');
        const capacity = await this.first(`SELECT COUNT(*) AS n,COALESCE(SUM(body_bytes),0) AS b FROM pull_events WHERE state IN ${LIVE}`);
        const local = await this.first(`SELECT COUNT(*) AS n,COALESCE(SUM(body_bytes),0) AS b FROM pull_events WHERE install_id=? AND state IN ${LIVE}`,s.install_id);
        const holds = await this.first(`SELECT COUNT(*) AS n,SUM(install_id=?) AS local FROM pull_ingress_holds WHERE expires_ms>${SQL_NOW}`,s.install_id);
        valid(capacity.n+holds.n < 10000 && local.n+(holds.local??0)<100 && capacity.b+size<=209715200 && local.b+size<=2097152,'queue_full');
        throw error;
      }
    }
  }
  conflict(install, eventId, now) {
    return [this.stmt(`INSERT INTO pull_audit_overflow SELECT ?,1 WHERE (SELECT COUNT(*) FROM pull_audit)>=10000
      ON CONFLICT(day_ms) DO UPDATE SET count=MIN(count+1,?)`, Math.floor(now/DAY)*DAY,MAX),
    this.stmt(`INSERT INTO pull_audit(install_id,at_ms,code,related_id) SELECT ?,?,'message_id_conflict',?
      WHERE (SELECT COUNT(*) FROM pull_audit)<10000`,install,now,eventId)];
  }
  async page(s, { after_seq = 0, limit = 10 } = {}) {
    this.scope(s,true); valid(integer(after_seq) && integer(limit,1,10)); const now=this.now();
    const r = await this.atomic(s,[this.timeGuard(now),this.stmt(`${eventSQL} WHERE e.install_id=? AND e.generation=?
      AND e.owner_generation=? AND e.state IN ${LIVE} AND e.expires_ms>${SQL_NOW} AND e.seq>? ORDER BY e.seq LIMIT ?`,
    s.install_id,s.generation,s.owner_generation,after_seq,limit+1),
    this.stmt(`SELECT COUNT(*) AS pending_count,MIN(expires_ms) AS oldest_expires_ms FROM pull_events
      WHERE install_id=? AND generation=? AND owner_generation=? AND state IN ${LIVE} AND expires_ms>${SQL_NOW}`,
    s.install_id,s.generation,s.owner_generation)]);
    const rows=r[2].results; const complete=rows.length<=limit; const page=rows.slice(0,limit);
    return { events: page.map((row)=>({event:event(row),state:row.state,next_attempt_ms:row.next_attempt_ms,
      lease_id:row.lease_id,lease_until_ms:row.lease_until_ms})),next_seq:complete?0:page.at(-1).seq,scan_complete:complete,
      ...r[3].results[0] };
  }
  async offered(s, eventId) {
    const row=await this.first(`${eventSQL} WHERE e.event_id=? AND e.install_id=? AND e.generation=? AND e.owner_generation=?`,
      eventId,s.install_id,s.generation,s.owner_generation); valid(row,'not_found'); return row;
  }
  async offer(s, eventId, { available_bytes = 524288 } = {}) {
    this.scope(s,true); valid(match(H,eventId) && integer(available_bytes,0,524288));
    const now=this.now(); await this.authorize(s); const row=await this.offered(s,eventId);
    valid(row.expires_ms>now,'event_expired'); valid(['pending','offered','held'].includes(row.state),'operation_conflict');
    valid(row.next_attempt_ms<=now && (row.lease_until_ms===null || row.lease_until_ms<=now),'operation_conflict');
    const t=await this.thread(s,row.thread_id); const e=event(row); const size=offerFrameBytes(e);
    valid(size.decoded<=112*1024 && size.encoded<=160*1024,'frame_limit'); valid(size.encoded<=available_bytes,'queue_full');
    const active=await this.all(`${eventSQL} WHERE e.install_id=? AND e.generation=? AND e.state='offered'
      AND e.lease_until_ms>${SQL_NOW} AND e.expires_ms>${SQL_NOW} ORDER BY e.lease_id LIMIT 11`,s.install_id,s.generation);
    valid(active.length<10 && active.reduce((n,r)=>n+offerFrameBytes(event(r)).encoded,0)+size.encoded<=524288,'queue_full');
    const leases=JSON.stringify(active.map((r)=>r.lease_id)); const lease=randomId(); const until=now+LEASE;
    await this.atomic(s,[this.timeGuard(now,Math.min(row.expires_ms,until)),this.threadGuard(s,t,now),
      // Any new concurrent offer changes the snapshot and aborts the batch.
      this.guard(`(SELECT json_group_array(lease_id) FROM (SELECT lease_id FROM pull_events WHERE install_id=?
        AND generation=? AND state='offered' AND lease_until_ms>${SQL_NOW} AND expires_ms>${SQL_NOW} ORDER BY lease_id))=?`,s.install_id,s.generation,leases),
      this.guard(`NOT EXISTS(SELECT 1 FROM pull_events WHERE install_id=? AND generation=? AND thread_id=? AND seq<? AND state IN ${LIVE} AND expires_ms>${SQL_NOW})`,s.install_id,s.generation,row.thread_id,row.seq),
      this.stmt(`DELETE FROM pull_offers WHERE lease_id IN (SELECT lease_id FROM pull_offers WHERE event_id=? AND lease_until_ms<=${SQL_NOW}
        ORDER BY offered_ms,lease_id LIMIT MAX(0,(SELECT COUNT(*) FROM pull_offers WHERE event_id=?)-63))`,eventId,eventId),
      this.guard('(SELECT COUNT(*) FROM pull_offers WHERE event_id=?)<64',eventId),
      this.stmt(`UPDATE pull_events SET state='offered',lease_id=?,lease_until_ms=? WHERE event_id=? AND state IN ${LIVE}
        AND next_attempt_ms<=${SQL_NOW} AND (lease_until_ms IS NULL OR lease_until_ms<=${SQL_NOW}) AND expires_ms>${SQL_NOW}`,
      lease,until,eventId),this.registry.changed(),
      this.stmt(`INSERT INTO pull_offers VALUES(?,?,?,?,?,?,?,?)`,lease,eventId,s.install_id,s.generation,s.epoch,now,until,t.dedupe_until_ms),
      this.timeGuard(now,Math.min(row.expires_ms,until))]);
    return {event:e,lease_id:lease,lease_until_ms:until};
  }
  async ack(s,v) {
    this.scope(s,true); valid(exact(v,['event_id','event_digest','body_digest','lease_id','local_receipt_id'])
      && ['event_id','event_digest','body_digest'].every((k)=>match(H,v[k])) && match(I,v.lease_id) && match(I,v.local_receipt_id));
    for(let attempt=0;attempt<2;attempt++) {
      const now=this.now(); await this.authorize(s); const d=await this.first(dedupeSQL,v.event_id);
      valid(d && d.install_id===s.install_id && d.generation===s.generation && d.owner_generation===s.owner_generation,'not_found');
      valid(d.dedupe_until_ms>now,'event_expired'); valid(d.body_digest===v.body_digest && d.event_digest===v.event_digest,'ack_conflict');
      const old=await this.first('SELECT * FROM pull_receipts WHERE event_id=?',v.event_id);
      if(old) {
        valid(old.local_receipt_id===v.local_receipt_id && old.body_digest===v.body_digest && old.event_digest===v.event_digest,'ack_conflict');
        await this.atomic(s,[this.timeGuard(now,d.dedupe_until_ms),this.guard(`EXISTS(SELECT 1 FROM pull_receipts WHERE event_id=?
          AND receipt_id=? AND local_receipt_id=? AND event_digest=? AND body_digest=?)`,v.event_id,old.receipt_id,v.local_receipt_id,v.event_digest,v.body_digest),this.timeGuard(now,d.dedupe_until_ms)]);
        return old;
      }
      valid(d.disposition==='pending','event_expired'); valid(d.expires_ms>now,'event_expired');
      valid(await this.first(`SELECT 1 FROM pull_offers WHERE lease_id=? AND event_id=? AND install_id=? AND generation=?`,
        v.lease_id,v.event_id,s.install_id,s.generation),'lease_unknown');
      const row=await this.offered(s,v.event_id); valid(['pending','offered','held'].includes(row.state),'event_expired');
      const t=await this.thread(s,d.thread_id); const receipt=randomId();
      try {
        const r=await this.atomic(s,[this.timeGuard(now,d.expires_ms),this.threadGuard(s,t,now),
          this.guard(`EXISTS(SELECT 1 FROM pull_offers WHERE lease_id=? AND event_id=? AND install_id=? AND generation=?)`,v.lease_id,v.event_id,s.install_id,s.generation),
          this.stmt(`INSERT INTO pull_receipts VALUES(?,?,?,?,?,?,?,?,${SQL_NOW},?)`,v.event_id,receipt,v.local_receipt_id,s.install_id,s.generation,s.owner_generation,v.event_digest,v.body_digest,d.dedupe_until_ms),
          this.stmt(`UPDATE pull_dedupe SET disposition='local_received' WHERE event_id=? AND disposition='pending'
            AND event_digest=? AND body_digest=? AND expires_ms>${SQL_NOW}`,v.event_id,v.event_digest,v.body_digest),this.registry.changed(),
          this.stmt(`UPDATE pull_events SET state='local_received',text=NULL,body_bytes=0,lease_id=NULL,lease_until_ms=NULL,terminal_ms=${SQL_NOW}
            WHERE event_id=? AND state IN ${LIVE} AND expires_ms>${SQL_NOW}`,v.event_id),this.registry.changed(),
          ...this.notify(s.install_id,now),this.timeGuard(now,d.expires_ms),
          this.stmt('SELECT * FROM pull_receipts WHERE event_id=?',v.event_id)]); return {...r.at(-2).results[0]};
      } catch(error) {
        if(attempt===0 && error.code==='storage_unavailable' && await this.first('SELECT 1 FROM pull_receipts WHERE event_id=?',v.event_id)) continue;
        if(error.code==='storage_unavailable' && await this.first(`SELECT 1 FROM pull_receipts WHERE install_id=? AND generation=? AND local_receipt_id=?`,s.install_id,s.generation,v.local_receipt_id)) throw new Refusal('ack_conflict');
        if(error.code==='storage_unavailable') {
          valid(d.expires_ms>this.now(),'event_expired');
          valid(await this.first('SELECT 1 FROM pull_offers WHERE lease_id=? AND event_id=? AND install_id=? AND generation=?',
            v.lease_id,v.event_id,s.install_id,s.generation),'lease_unknown');
        }
        throw error;
      }
    }
  }
  async hold(s,v) {
    this.scope(s,true); valid(exact(v,['event_id','event_digest','lease_id','reason','retry_after_ms']) && match(H,v.event_id)
      && match(H,v.event_digest) && match(I,v.lease_id) && HOLDS.has(v.reason) && integer(v.retry_after_ms));
    const now=this.now(); await this.authorize(s); const row=await this.offered(s,v.event_id);
    valid(row.event_digest===v.event_digest,'ack_conflict'); valid(row.expires_ms>now,'event_expired');
    valid(row.state==='offered' && row.lease_id===v.lease_id && await this.first(`SELECT 1 FROM pull_offers
      WHERE event_id=? AND lease_id=? AND install_id=? AND generation=? AND epoch=?`,
    v.event_id,v.lease_id,s.install_id,s.generation,s.epoch),'lease_unknown');
    const r=await this.atomic(s,[this.timeGuard(now,row.expires_ms),
      this.guard(`EXISTS(SELECT 1 FROM pull_offers WHERE event_id=? AND lease_id=? AND install_id=? AND generation=? AND epoch=?)`,v.event_id,v.lease_id,s.install_id,s.generation,s.epoch),
      this.stmt(`UPDATE pull_events SET state='held',next_attempt_ms=MIN(expires_ms,MAX(${SQL_NOW}+${LEASE},?)),lease_id=NULL,lease_until_ms=NULL WHERE event_id=?
        AND state='offered' AND lease_id=? AND expires_ms>${SQL_NOW}`,v.retry_after_ms,v.event_id,v.lease_id),this.registry.changed(),
      ...this.notify(s.install_id,now),this.timeGuard(now,row.expires_ms),
      this.stmt('SELECT next_attempt_ms FROM pull_events WHERE event_id=?',v.event_id)]);
    return {event_id:v.event_id,reason:v.reason,retry_after_ms:r.at(-2).results[0].next_attempt_ms};
  }
  cursor(v={}) { const {after_ms=0,after_id='',limit=100}=v; valid(integer(after_ms) && (after_id===''||match(H,after_id)) && integer(limit,1,100)); return {after_ms,after_id,limit}; }
  async maintenance(statements) {
    try{return await this.db.batch(statements);}catch {console.error('[agent-mail] pull_ledger_maintenance_refused');throw new Refusal('storage_unavailable');}
  }
  async expire(options) {
    const c=this.cursor(options); const now=this.now();
    const rows=await this.all(`SELECT event_id,install_id,expires_ms FROM pull_events WHERE state IN ${LIVE} AND expires_ms<=${SQL_NOW}
      AND (expires_ms,event_id)>(?,?) ORDER BY expires_ms,event_id LIMIT ?`,c.after_ms,c.after_id,c.limit);
    if(!rows.length)return {count:0,cursor:null}; const ids=JSON.stringify(rows.map((r)=>r.event_id));
    const r=await this.maintenance([this.timeGuard(now),
      this.stmt(`UPDATE pull_dedupe SET disposition='expired' WHERE disposition='pending' AND event_id IN
        (SELECT event_id FROM pull_events WHERE event_id IN (SELECT value FROM json_each(?)) AND state IN ${LIVE} AND expires_ms<=${SQL_NOW})`,ids),
      this.stmt(`UPDATE pull_events SET state='expired',text=NULL,body_bytes=0,lease_id=NULL,lease_until_ms=NULL,terminal_ms=?
        WHERE event_id IN (SELECT value FROM json_each(?)) AND state IN ${LIVE} AND expires_ms<=${SQL_NOW}`,now,ids),
      ...[...new Set(rows.map((r)=>r.install_id))].flatMap((id)=>this.notify(id,now))]);
    return {count:r[2].meta.changes,cursor:{after_ms:rows.at(-1).expires_ms,after_id:rows.at(-1).event_id}};
  }
  async purgeDetails(options) {
    const c=this.cursor(options); const now=this.now();
    const rows=await this.all(`SELECT event_id,terminal_ms FROM pull_events WHERE terminal_ms IS NOT NULL AND terminal_ms<=${SQL_NOW}-${MONTH}
      AND (terminal_ms,event_id)>(?,?) ORDER BY terminal_ms,event_id LIMIT ?`,c.after_ms,c.after_id,c.limit);
    if(!rows.length)return {count:0,cursor:null}; const ids=JSON.stringify(rows.map((r)=>r.event_id));
    const r=await this.maintenance([this.timeGuard(now),this.stmt(`DELETE FROM pull_events WHERE event_id IN (SELECT value FROM json_each(?))
      AND state IN ('local_received','expired','revoked') AND text IS NULL AND body_bytes=0 AND terminal_ms<=${SQL_NOW}-${MONTH}`,ids)]);
    return {count:r[1].meta.changes,cursor:{after_ms:rows.at(-1).terminal_ms,after_id:rows.at(-1).event_id}};
  }
  async purgeDedupe(options) {
    const c=this.cursor(options); const now=this.now();
    const rows=await this.all(`SELECT event_id,install_id,dedupe_until_ms FROM pull_dedupe WHERE dedupe_until_ms<=${SQL_NOW}
      AND (dedupe_until_ms,event_id)>(?,?) ORDER BY dedupe_until_ms,event_id LIMIT ?`,c.after_ms,c.after_id,c.limit);
    if(!rows.length)return {count:0,cursor:null}; const ids=JSON.stringify(rows.map((r)=>r.event_id));
    const r=await this.maintenance([this.timeGuard(now),...['pull_events','pull_offers','pull_receipts'].map((table)=>this.stmt(`DELETE FROM ${table}
      WHERE event_id IN (SELECT event_id FROM pull_dedupe WHERE event_id IN (SELECT value FROM json_each(?)) AND dedupe_until_ms<=${SQL_NOW})`,ids)),
    this.stmt(`DELETE FROM pull_dedupe WHERE event_id IN (SELECT value FROM json_each(?)) AND dedupe_until_ms<=${SQL_NOW}`,ids),
    ...[...new Set(rows.map((r)=>r.install_id))].flatMap((id)=>this.notify(id,now))]);
    return {count:r[4].meta.changes,cursor:{after_ms:rows.at(-1).dedupe_until_ms,after_id:rows.at(-1).event_id}};
  }
}
