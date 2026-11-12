// MC-1166 unit 10. Auth/strip gates live in ingress.js, before this custody seam.
// Unit 14 selects Ledger.pull({clock}) and passes an internal wake(install_id),
// e.g. id => namespace.get(namespace.idFromName(id)).wake(id). No HTTP/push path.
import { randomId, sha256 } from './signing.js';
import { SQL_NOW } from './sql-clock.js';

const DAY = 86_400_000, MONTH = 30 * DAY;
const LIVE = "('pending','offered','held')";

// Invalid MID is not an event. It never allocates seq, retains text or shadows
// a future valid MID. Only already authenticated/correlated/stripped mail enters.
async function hold(ledger, scope, threadId, raw) {
  const q = ledger.queue, rawDigest = await sha256(raw);
  const args = [scope.install_id, scope.generation, scope.owner_generation, threadId, rawDigest];
  const lookup = `SELECT * FROM pull_ingress_holds WHERE install_id=? AND generation=?
    AND owner_generation=? AND thread_id=? AND raw_digest=?`;
  for (let attempt = 0; attempt < 3; attempt++) {
    const now = q.now(), end = now + MONTH, day = Math.floor(now / DAY);
    await q.authorize(scope);
    const thread = await q.thread(scope, threadId);
    const prior = await q.first(lookup, ...args);
    if (prior) {
      await q.atomic(scope, [q.timeGuard(now, prior.expires_ms), q.threadGuard(scope, thread, now),
        q.guard(`EXISTS(SELECT 1 FROM pull_ingress_holds WHERE hold_id=? AND expires_ms=?)`, prior.hold_id, prior.expires_ms),
        q.timeGuard(now, prior.expires_ms)]);
      return { status: 'duplicate', hold_id: prior.hold_id, reason: 'invalid_message_id' };
    }
    const holdId = randomId();
    try {
      await q.atomic(scope, [q.timeGuard(now, Math.min(end, thread.expires_ms), true), q.threadGuard(scope, thread, now),
        // Holds reserve one count and zero bytes. Existing enqueue owns body
        // byte admission and counts these rows in its same-transaction guards.
        q.guard(`(SELECT COUNT(*) FROM pull_events WHERE install_id=? AND state IN ${LIVE})+
          (SELECT COUNT(*) FROM pull_ingress_holds WHERE install_id=? AND expires_ms>${SQL_NOW})<100`, scope.install_id, scope.install_id),
        q.guard(`(SELECT COUNT(*) FROM pull_events WHERE state IN ${LIVE})+
          (SELECT COUNT(*) FROM pull_ingress_holds WHERE expires_ms>${SQL_NOW})<10000`),
        ...[[`pull:ingress:install:${scope.install_id}:${day}`, 200], [`pull:ingress:global:${day}`, 10000]].flatMap(([bucket, cap]) => [
          q.stmt(`INSERT INTO pull_budgets VALUES(?,1,?) ON CONFLICT(bucket)
            DO UPDATE SET count=count+1 WHERE count<?`, bucket, (day + 2) * DAY, cap), q.registry.changed()]),
        q.stmt(`INSERT INTO pull_ingress_holds VALUES(?,?,?,?,?,?,'invalid_message_id',?,?)`,
          holdId, ...args, now, end), q.timeGuard(now, Math.min(end, thread.expires_ms), true)]);
      return { status: 'held', hold_id: holdId, reason: 'invalid_message_id' };
    } catch (error) {
      if (error.message !== 'storage_unavailable') throw error;
      await q.authorize(scope);
      if (attempt < 2 && await q.first(lookup, ...args)) continue;
      const budgets = await q.all('SELECT bucket,count FROM pull_budgets WHERE bucket IN (?,?)',
        `pull:ingress:install:${scope.install_id}:${day}`, `pull:ingress:global:${day}`);
      if (budgets.some((r) => r.count >= (r.bucket.includes(':install:') ? 200 : 10000))) throw new Error('ingress_limit');
      const counts = await q.first(`SELECT
        (SELECT COUNT(*) FROM pull_events WHERE state IN ${LIVE})+
        (SELECT COUNT(*) FROM pull_ingress_holds WHERE expires_ms>${SQL_NOW}) AS global_count,
        (SELECT COUNT(*) FROM pull_events WHERE install_id=? AND state IN ${LIVE})+
        (SELECT COUNT(*) FROM pull_ingress_holds WHERE install_id=? AND expires_ms>${SQL_NOW}) AS local_count`,
        scope.install_id, scope.install_id);
      if (counts.global_count >= 10000 || counts.local_count >= 100) throw new Error('queue_full');
      throw error;
    }
  }
}

export async function persistPullIngress({ ledger, reg, decision, content, incoming_id, raw, wake }) {
  // SMTP is not bound to a socket's optional connection_id (null pre-connect).
  const scope = { install_id: reg.install_id, generation: reg.generation,
    owner_generation: reg.owner_generation, epoch: reg.epoch };
  let result;
  try {
    if (incoming_id === null) return await hold(ledger, scope, decision.thread.thread_id, raw);
    result = await ledger.enqueue(scope, { thread_id: decision.thread.thread_id,
      incoming_message_id: incoming_id, text: content.has_text ? content.text : null, auth: decision.evidence });
  } catch (error) {
    // Propagate only closed codes. Throwing prevents SMTP durable success; do
    // not turn a failed batch into a returned status the runtime could accept.
    const code = ['ingress_limit','queue_full','message_id_conflict','stale_generation','connection_superseded',
      'registration_frozen','registration_inactive','fenced','unknown_thread','event_expired','storage_unavailable']
      .includes(error.message) ? error.message : 'storage_unavailable';
    console.error(`[agent-mail] pull_ingress_${code}`);
    throw new Error(code);
  }
  // The intent was committed by enqueue, before this best-effort RPC. Never
  // clear/settle it here: the DO and unit 13 own notification reconciliation.
  if (wake) {
    try { await wake(reg.install_id); }
    catch { console.error('[agent-mail] pull_ingress_wake_failed'); }
  }
  return { status: result.duplicate ? 'duplicate' : content.has_text ? 'queued' : 'held', event_id: result.event_id };
}
