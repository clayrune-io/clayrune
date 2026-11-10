// Unproved identities expire; only the ownership-commit trigger makes them permanent.
import { randomId } from './signing.js';
import { CHALLENGE_TTL, randomHex } from './enrollment-challenge.js';

export async function reserveIdentity(db, deviceId, now, capacity) {
  const stmt = (sql, ...args) => db.prepare(sql).bind(...args);
  // Keep historical flow IDs/transcripts, but erase codes and free provisional
  // addresses. Proven identities never participate in this expiry cleanup.
  await db.batch([
    stmt(`UPDATE mail_enrollment_flows SET state='invalidated',code_digest=''
      WHERE state='pending' AND expires_at<=?`, now),
    stmt('DELETE FROM mail_identity_reservations WHERE expires_at<=?', now),
  ]);
  const established = await stmt('SELECT * FROM mail_installations WHERE device_id=?', deviceId).first();
  if (established) return established;
  // The single conditional INSERT serializes concurrent admissions at capacity.
  await stmt(`INSERT OR IGNORE INTO mail_identity_reservations(install_id,device_id,address_local,expires_at)
    SELECT ?,?,?,? WHERE (SELECT COUNT(*) FROM mail_installations)
      +(SELECT COUNT(*) FROM mail_identity_reservations WHERE expires_at>?)<?
      AND NOT EXISTS (SELECT 1 FROM mail_installations WHERE device_id=?)`,
  randomId(), deviceId, randomHex(15), now + CHALLENGE_TTL, now, capacity, deviceId).run();
  // A concurrent commit may already have promoted this device.
  const install = await stmt('SELECT * FROM mail_installations WHERE device_id=?', deviceId).first();
  if (install) return install;
  const pending = await stmt('SELECT * FROM mail_identity_reservations WHERE device_id=? AND expires_at>?', deviceId, now).first();
  if (!pending) throw new Error('enrollment_capacity');
  return { ...pending, generation: 0 };
}
