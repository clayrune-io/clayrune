// MC-1166 unit 13: trusted, bounded scheduled maintenance; no route wiring.
import { randomId } from './signing.js';
import { SQL_NOW } from './sql-clock.js';

const LIVE = "('pending','offered','held')", MAX = Number.MAX_SAFE_INTEGER;
const LEASE = 60000, LIMIT = 100;
const same = (a, b) => a.cursor_ms === b.cursor_ms && a.cursor_id === b.cursor_id && a.updated_ms === b.updated_ms;

/** Unit14 calls sweep({ledger,wake}) once per scheduled tick. ledger is a
 * PullLedger or PullTransportLedger; wake is the existing DO wake(install_id).
 * Each task commits at most 100 selected rows, then yields to the next tick.
 * No timer, provider I/O, control outbox or metadata retention is owned here.
 * Wake success is NOT delivery: pending/held/offered work keeps a future intent.
 */
export async function sweep({ ledger, wake }) {
  const q = ledger?.queue ?? ledger;
  if (!q?.maintenance || typeof wake !== 'function') throw new Error('invalid_request');
  return { notify: await notify(q, wake), expire: await expire(q) };
}

async function cursor(q, task) {
  return await q.first('SELECT * FROM pull_sweep_cursors WHERE task=?', task)
    ?? { task, cursor_ms: 0, cursor_id: '', updated_ms: null };
}
function cursorGuard(q, c) {
  return c.updated_ms === null
    ? q.guard('NOT EXISTS(SELECT 1 FROM pull_sweep_cursors WHERE task=?)', c.task)
    : q.guard('EXISTS(SELECT 1 FROM pull_sweep_cursors WHERE task=? AND cursor_ms=? AND cursor_id=? AND updated_ms=?)',
      c.task, c.cursor_ms, c.cursor_id, c.updated_ms);
}
function saveCursor(q, c, last) {
  return q.stmt(`INSERT INTO pull_sweep_cursors(task,cursor_ms,cursor_id,updated_ms) VALUES(?,?,?,${SQL_NOW})
    ON CONFLICT(task) DO UPDATE SET cursor_ms=excluded.cursor_ms,cursor_id=excluded.cursor_id,updated_ms=excluded.updated_ms`,
  c.task, last?.ms ?? 0, last?.id ?? '');
}
async function commitPage(q, c, statements) {
  try { return await q.maintenance([cursorGuard(q, c), ...statements]); }
  catch (error) {
    // A competing committed page wins. Never overwrite its persisted cursor.
    if (!same(c, await cursor(q, c.task))) return null;
    throw error;
  }
}

async function notify(q, wake) {
  const c = await cursor(q, 'notify');
  const due = await q.all(`SELECT * FROM pull_notifications INDEXED BY pull_notify_due
    WHERE next_attempt_ms<=${SQL_NOW} AND install_id>?
    AND (lease_id IS NULL OR lease_until_ms<=${SQL_NOW}) ORDER BY install_id LIMIT ${LIMIT}`,
  c.cursor_id);
  const lease = randomId(), last = due.at(-1);
  const r = await commitPage(q, c, [
    q.stmt(`UPDATE pull_notifications SET lease_id=?,lease_until_ms=${SQL_NOW}+${LEASE}
      WHERE install_id IN (SELECT value FROM json_each(?)) AND next_attempt_ms<=${SQL_NOW}
      AND (lease_id IS NULL OR lease_until_ms<=${SQL_NOW})`, lease, JSON.stringify(due.map((n) => n.install_id))),
    q.stmt('SELECT * FROM pull_notifications WHERE lease_id=? ORDER BY install_id', lease),
    saveCursor(q, c, last && { ms: 0, id: last.install_id }),
  ]);
  if (!r) return { claimed: 0, woken: 0 };
  const claimed = r[2].results; let woken = 0;
  for (const n of claimed) {
    // Enqueue/revision, an expired lease or another claimant may have won while
    // the previous RPC awaited. Such a claim must not perform a stale wake.
    if (!await q.first(`SELECT 1 FROM pull_notifications WHERE install_id=? AND revision=? AND lease_id=?
      AND lease_until_ms>${SQL_NOW}`, n.install_id, n.revision, lease)) continue;
    let connected = false;
    try { connected = (await wake(n.install_id))?.connected === true; woken++; }
    catch { console.error('[agent-mail] pull_sweep_wake_failed'); }
    const attempts = Math.min(n.attempts + 1, MAX);
    const ceiling = Math.min(300000, 1000 * 2 ** Math.min(attempts - 1, 9));
    const delay = connected ? LEASE : Math.max(1000, Math.floor(Math.random() * ceiling));
    await q.maintenance([
      // Clearing requires both a completed wake and authoritative empty queue.
      // A new enqueue changes revision and invalidates this CAS.
      q.stmt(`DELETE FROM pull_notifications WHERE install_id=? AND revision=? AND lease_id=? AND ?=1
        AND lease_until_ms>${SQL_NOW} AND NOT EXISTS(SELECT 1 FROM pull_events WHERE install_id=? AND state IN ${LIVE})`,
      n.install_id, n.revision, lease, connected ? 1 : 0, n.install_id),
      q.stmt(`UPDATE pull_notifications SET attempts=?,next_attempt_ms=${SQL_NOW}+?,lease_id=NULL,lease_until_ms=NULL
        WHERE install_id=? AND revision=? AND lease_id=? AND lease_until_ms>${SQL_NOW}`,
      attempts, delay, n.install_id, n.revision, lease),
    ]);
  }
  return { claimed: claimed.length, woken };
}

async function expire(q) {
  const c = await cursor(q, 'expire');
  const { now } = await q.first(`SELECT ${SQL_NOW} AS now`);
  // Each arm uses its expiry index; the prefix disambiguates event and hold ids.
  // Shared time ordering means neither kind can monopolize a completed pass.
  const rows = await q.all(`SELECT * FROM (
    SELECT expires_ms AS ms,'e:'||event_id AS id,event_id AS row_id,install_id,'event' AS kind
      FROM pull_events INDEXED BY pull_event_expiry WHERE state IN ${LIVE} AND expires_ms<=${SQL_NOW}
      AND (expires_ms,'e:'||event_id)>(?,?)
    UNION ALL SELECT expires_ms AS ms,'h:'||hold_id AS id,hold_id AS row_id,install_id,'hold' AS kind
      FROM pull_ingress_holds INDEXED BY pull_ingress_hold_expiry WHERE expires_ms<=${SQL_NOW}
      AND (expires_ms,'h:'||hold_id)>(?,?)) ORDER BY ms,id LIMIT ${LIMIT}`,
  c.cursor_ms, c.cursor_id, c.cursor_ms, c.cursor_id);
  const events = JSON.stringify(rows.filter((r) => r.kind === 'event').map((r) => r.row_id));
  const holds = JSON.stringify(rows.filter((r) => r.kind === 'hold').map((r) => r.row_id));
  const r = await commitPage(q, c, [
    q.stmt(`UPDATE pull_dedupe SET disposition='expired' WHERE disposition='pending' AND event_id IN
      (SELECT event_id FROM pull_events WHERE event_id IN (SELECT value FROM json_each(?))
      AND state IN ${LIVE} AND expires_ms<=${SQL_NOW})`, events),
    q.stmt(`UPDATE pull_events SET state='expired',text=NULL,body_bytes=0,lease_id=NULL,lease_until_ms=NULL,terminal_ms=${SQL_NOW}
      WHERE event_id IN (SELECT value FROM json_each(?)) AND state IN ${LIVE} AND expires_ms<=${SQL_NOW}`, events),
    q.stmt(`DELETE FROM pull_ingress_holds WHERE hold_id IN (SELECT value FROM json_each(?)) AND expires_ms<=${SQL_NOW}`, holds),
    ...[...new Set(rows.filter((r) => r.kind === 'event').map((r) => r.install_id))]
      .flatMap((id) => q.notify(id, now)),
    saveCursor(q, c, rows.at(-1)),
  ]);
  return { scanned: r ? rows.length : 0, events: r ? r[2].meta.changes : 0, holds: r ? r[3].meta.changes : 0 };
}
