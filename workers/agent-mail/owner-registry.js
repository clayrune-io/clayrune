// Pull v1 registry primitives (contract sections 2.1, 6.1 and 8.1).
// These are D1 statement builders, NOT independent transactions or auth gates.
// The caller must batch them with its guarded operation/digest replay check,
// install/generation/flow/token/outbox writes and result. Never run a prefix,
// catch a guard error as success, or refund a committed/uncertain mail attempt.
import { bytes, randomId, sha256 } from './signing.js';
import { parseAddrSpec } from './mailbox.js';

const DAY = 86_400_000;
const LINK_TTL = 30 * 60_000;
const QUARANTINE = 90 * DAY;
const I = /^[a-f0-9]{32}$/;
const H = /^[a-f0-9]{64}$/;
const ADDRESS = /^m[a-f0-9]{29}$/;
const requireValue = (valid) => { if (!valid) throw new Error('invalid_request'); };
const matches = (pattern, value) => typeof value === 'string' && pattern.test(value);
function time(now) {
  requireValue(Number.isSafeInteger(now) && now >= 0 && now <= Number.MAX_SAFE_INTEGER - QUARANTINE);
}
function scope(owner, install, flow) {
  requireValue(matches(H, owner) && matches(I, install) && matches(I, flow));
}

/** Domain-only case folding; no provider/local-part equivalence guesses. */
export async function ownerIdentity(mailbox) {
  const parsed = parseAddrSpec(mailbox);
  requireValue(parsed !== null);
  return { owner_address: parsed.address, owner_hash: await sha256(bytes(parsed.address)) };
}

/** Service-generated opaque local part; never expose an alias-selection API. */
export function newAddressLocal() { return `m${randomId().slice(0, 29)}`; }

export class OwnerRegistry {
  constructor(db) { this.db = db; }
  stmt(sql, ...args) { return this.db.prepare(sql).bind(...args); }
  async installation(id) {
    return this.stmt('SELECT * FROM pull_installs WHERE install_id=?', id).first();
  }
  async recipient(local) {
    return this.stmt(`SELECT i.* FROM pull_addresses a JOIN pull_installs i
      ON i.install_id=a.install_id AND i.address_local=a.address_local
      WHERE a.address_local=? AND a.state='assigned' AND i.state='active'`, local).first();
  }

  // A zero-row INSERT/UPDATE does not abort a D1 batch. This conditional INSERT
  // deliberately violates the immutable version CHECK on refusal, rolling back
  // ALL preceding writes. Success writes nothing (and does not need a JS lock).
  guard(predicate, ...args) {
    return this.stmt(`INSERT INTO pull_version(slot,version) SELECT 1,0 WHERE NOT COALESCE((${predicate}),0)`, ...args);
  }
  changed() { return this.guard('changes()=1'); }

  /** Combined signup/step-up/recovery/new-owner budgets, including global send.
   * Call exactly once per new verification outbox, after exact replay resolution.
   * Normal sends and notice attempts must share pull:send:global:<UTC day>.
   */
  verificationStatements({ install_id, target_hash }, now) {
    time(now); requireValue(matches(I, install_id) && matches(H, target_hash));
    const day = Math.floor(now / DAY);
    const expires = (day + 2) * DAY;
    return [[`pull:verify:install:${install_id}:${day}`, 3],
      [`pull:verify:target:${target_hash}:${day}`, 3],
      [`pull:send:global:${day}`, 1000]].flatMap(([bucket, maximum]) => [
      this.stmt(`INSERT INTO pull_budgets(bucket,count,expires_ms) VALUES(?,1,?)
        ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<?`, bucket, expires, maximum),
      this.changed(),
    ]);
  }

  /** Reserve an install once per owner even if it has multiple live slot roles.
   * A replay cannot extend its deadline or overwrite a different live flow.
   * Revert reservations use the same admission rule but their seven-day window.
   */
  reserveSlotStatements({ owner_hash, install_id, flow_id, until_ms, role = 'pending' }, now) {
    time(now); scope(owner_hash, install_id, flow_id);
    requireValue(['pending', 'revert'].includes(role) && Number.isSafeInteger(until_ms)
      && until_ms > now && until_ms <= now + (role === 'revert' ? 7 * DAY : LINK_TTL));
    return [
      this.guard(`(SELECT COUNT(DISTINCT install_id) FROM pull_owner_slots WHERE owner_hash=?
        AND install_id!=? AND (role='active' OR until_ms>?))<3`, owner_hash, install_id, now),
      this.stmt(`INSERT INTO pull_owner_slots(owner_hash,install_id,role,flow_id,until_ms) VALUES(?,?,?,?,?)
        ON CONFLICT(owner_hash,install_id,role) DO UPDATE SET flow_id=excluded.flow_id,until_ms=excluded.until_ms
        WHERE pull_owner_slots.until_ms<=? AND pull_owner_slots.flow_id!=excluded.flow_id`,
      owner_hash, install_id, role, flow_id, until_ms, now),
      this.guard(`EXISTS (SELECT 1 FROM pull_owner_slots WHERE owner_hash=? AND install_id=?
        AND role=? AND flow_id=? AND until_ms=?)`, owner_hash, install_id, role, flow_id, until_ms),
    ];
  }

  /** Final enable rechecks quota and the original reservation deadline.
   * Caller supplies verified flow/key/consent/generation guards in this batch.
   */
  activateSlotStatements({ owner_hash, install_id, flow_id }, now) {
    time(now); scope(owner_hash, install_id, flow_id);
    return [
      this.guard(`EXISTS (SELECT 1 FROM pull_owner_slots WHERE owner_hash=? AND install_id=? AND flow_id=?
          AND (role='active' OR (role='pending' AND until_ms>?))) AND
        (SELECT COUNT(DISTINCT install_id) FROM pull_owner_slots WHERE owner_hash=?
          AND (role='active' OR until_ms>?))<=3`, owner_hash, install_id, flow_id, now, owner_hash, now),
      this.stmt(`INSERT INTO pull_owner_slots(owner_hash,install_id,role,flow_id,until_ms) VALUES(?,?,'active',?,NULL)
        ON CONFLICT(owner_hash,install_id,role) DO UPDATE SET flow_id=excluded.flow_id`, owner_hash, install_id, flow_id),
      this.stmt(`DELETE FROM pull_owner_slots WHERE owner_hash=? AND install_id=? AND role='pending' AND flow_id=?`,
        owner_hash, install_id, flow_id),
    ];
  }

  /** Scoped release never erases the old owner's separate security-revert slot.
   * Flow/switch/revert authorization remains the enclosing transaction's job.
   */
  releaseSlotStatements({ owner_hash, install_id, flow_id, role }) {
    scope(owner_hash, install_id, flow_id);
    requireValue(['pending', 'active', 'revert'].includes(role));
    return [this.stmt(`DELETE FROM pull_owner_slots WHERE owner_hash=? AND install_id=? AND role=? AND flow_id=?`,
      owner_hash, install_id, role, flow_id)];
  }

  reserveAddressStatements({ address_local, install_id, until_ms }, now) {
    time(now); requireValue(matches(ADDRESS, address_local) && matches(I, install_id)
      && Number.isSafeInteger(until_ms) && until_ms > now && until_ms <= now + LINK_TTL);
    return [
      // The install is inserted first in signup. Historical install stubs retain
      // their UNIQUE address; a collision needs a fresh random candidate, never
      // reassignment of that stub or inherited threads. Expired reservations
      // must go through release/quarantine, not direct reuse.
      this.guard(`EXISTS (SELECT 1 FROM pull_installs WHERE install_id=? AND address_local=?)`, install_id, address_local),
      this.stmt(`INSERT INTO pull_addresses(address_local,install_id,state,until_ms,created_ms) VALUES(?,?,'reserved',?,?)
        ON CONFLICT(address_local) DO UPDATE SET install_id=excluded.install_id,state='reserved',
          until_ms=excluded.until_ms,created_ms=excluded.created_ms
        WHERE pull_addresses.state='quarantined' AND pull_addresses.until_ms<=?`,
      address_local, install_id, until_ms, now, now),
      this.guard(`EXISTS (SELECT 1 FROM pull_addresses WHERE address_local=? AND install_id=?
        AND state='reserved' AND until_ms=?)`, address_local, install_id, until_ms),
    ];
  }

  assignAddressStatements({ address_local, install_id }, now) {
    time(now); requireValue(matches(ADDRESS, address_local) && matches(I, install_id));
    return [
      this.stmt(`UPDATE pull_addresses SET state='assigned',until_ms=NULL WHERE address_local=? AND install_id=?
        AND (state='assigned' OR (state='reserved' AND until_ms>?))`, address_local, install_id, now),
      this.changed(),
    ];
  }

  /** Only explicit human deletion/release or reservation-expiry cleanup calls
   * this, never an install-key/fence operation. Duplicate release does not
   * extend quarantine. Keep the binding for a separately authorized old-owner
   * revert; do not remove high-water install/generation/fence evidence here.
   */
  releaseAddressStatements({ address_local, install_id }, now) {
    time(now); requireValue(matches(ADDRESS, address_local) && matches(I, install_id));
    return [
      this.stmt(`UPDATE pull_addresses SET state='quarantined',until_ms=?
        WHERE address_local=? AND install_id=? AND state IN ('reserved','assigned')`,
      now + QUARANTINE, address_local, install_id),
      this.guard(`EXISTS (SELECT 1 FROM pull_addresses WHERE address_local=? AND install_id=? AND state='quarantined')`,
        address_local, install_id),
    ];
  }
}
