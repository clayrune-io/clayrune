// D1 serializes account admission before ANY Cloudflare call. Ambiguous creates remain
// tombstones: retries list/reconcile them but never issue another POST for the address.
import { randomId } from './signing.js';
const DAY = 86400000;
const PENDING = 7 * DAY;
const LOCK_MS = 30000;
// Admission time is read inside SQLite, so a delayed invocation cannot reuse a stale JS clock.
const DB_NOW = "(CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER))";
export const DESTINATION_FAILURES = new Set(['destination_unverified', 'destination_unavailable',
  'destination_capacity', 'destination_rate_limited', 'destination_pending', 'destination_expired']);

export function destinationProvisioning({ db, client, clock = Date.now }) {
  const stmt = (sql, ...a) => db.prepare(sql).bind(...a);
  const first = (sql, ...a) => stmt(sql, ...a).first();
  async function ensure({ device_id: device, address }) {
    const token = randomId(); const now = clock();
    const lock = await stmt(`INSERT INTO mail_destination_lock VALUES(1,?,${DB_NOW}+${LOCK_MS})
      ON CONFLICT(slot) DO UPDATE SET token=excluded.token,expires_ms=excluded.expires_ms
      WHERE mail_destination_lock.expires_ms<=${DB_NOW}`, token).run();
    if (lock.meta.changes !== 1) throw new Error('destination_unavailable');
    try {
      const prior = await first('SELECT * FROM mail_destination_attempts WHERE device_id=?', device);
      if (prior && prior.address !== address && now < prior.started_ms + DAY) throw new Error('destination_pending');
      await stmt(`INSERT INTO mail_destination_attempts
        SELECT ?,?,${DB_NOW},${DB_NOW}+${PENDING}
        WHERE EXISTS(SELECT 1 FROM mail_destination_lock WHERE token=? AND expires_ms>${DB_NOW})
        ON CONFLICT(device_id) DO UPDATE SET address=excluded.address,started_ms=excluded.started_ms,expires_ms=excluded.expires_ms
        WHERE mail_destination_attempts.address<>excluded.address AND mail_destination_attempts.started_ms+${DAY}<=${DB_NOW}`,
      device, address, token).run();
      const attempt = await first('SELECT address,expires_ms FROM mail_destination_attempts WHERE device_id=?', device);
      if (!attempt || attempt.address !== address) throw new Error('destination_pending');
      if (!await first(`SELECT 1 AS x FROM mail_destination_lock WHERE token=? AND expires_ms>${DB_NOW}`, token)) {
        throw new Error('destination_unavailable');
      }
      const rows = await client.list();
      // Once the lock expires, a suspended invocation may not admit a create.
      const held = await first(`SELECT 1 AS x FROM mail_destination_lock WHERE token=? AND expires_ms>${DB_NOW}`, token);
      if (!held) throw new Error('destination_unavailable');
      const found = rows.find((r) => r.address === address);
      // Count claims whose address has not yet appeared in the account list as capacity, too.
      for (const row of rows) await stmt('UPDATE mail_destination_creates SET observed=1 WHERE address=?', row.address).run();
      if (found) {
        if (found.verified === true) return found;
        throw new Error(clock() >= attempt.expires_ms ? 'destination_expired' : 'destination_unverified');
      }
      if (clock() >= attempt.expires_ms) throw new Error('destination_expired');
      const attempted = await first('SELECT 1 AS x FROM mail_destination_creates WHERE address=?', address);
      if (attempted) throw new Error('destination_unavailable');
      const unresolved = await first('SELECT count(*) AS n FROM mail_destination_creates WHERE observed=0');
      if (rows.length + unresolved.n >= 190) throw new Error('destination_capacity');
      const counts = await first(`SELECT count(*) AS total,
        sum(CASE WHEN device_id=? THEN 1 ELSE 0 END) AS device FROM mail_destination_creates WHERE created_ms>?`, device, clock() - DAY);
      if (counts.total >= 20 || counts.device >= 3) throw new Error('destination_rate_limited');
      // Atomic claim under the current lock; a crash before/after POST is a consumed budget.
      const claim = await stmt(`INSERT OR IGNORE INTO mail_destination_creates(address,device_id,created_ms)
        SELECT ?,?,${DB_NOW} WHERE EXISTS(SELECT 1 FROM mail_destination_lock WHERE token=? AND expires_ms>${DB_NOW})
        AND (SELECT count(*) FROM mail_destination_creates WHERE created_ms>${DB_NOW}-${DAY})<20
        AND (SELECT count(*) FROM mail_destination_creates WHERE device_id=? AND created_ms>${DB_NOW}-${DAY})<3
        AND ?+(SELECT count(*) FROM mail_destination_creates WHERE observed=0)<190`,
      address, device, token, device, rows.length).run();
      if (claim.meta.changes !== 1) throw new Error('destination_unavailable');
      const result = await client.create(address);
      if (result?.address !== address) throw new Error('destination_unavailable');
      if (result.verified !== true) throw new Error('destination_unverified');
      return result;
    } finally {
      await stmt('DELETE FROM mail_destination_lock WHERE token=?', token).run();
    }
  }
  const provider = async (address) => (await client.list()).find((r) => r.address === address) ?? { address, verified: false };
  provider.ensure = ensure;
  return provider;
}
