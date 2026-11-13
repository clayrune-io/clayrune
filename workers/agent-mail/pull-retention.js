// MC-1166 W03. Trusted metadata maintenance only; W30 owns scheduled wiring.
import { SQL_NOW } from './sql-clock.js';

const DAY = 86400000, MONTH = 30 * DAY, YEAR = 365 * DAY, LIMIT = 100;
const TERMINAL = "('local_received','expired','revoked')";
const due = (column, grace = 0) => `${column}<=${SQL_NOW}-${grace}`;
const absent = (table, predicate) => `NOT EXISTS(SELECT 1 FROM ${table} WHERE ${predicate})`;
const eventAbsent = (table) => absent(table, `${table}.event_id=pull_dedupe.event_id`);
const threadScope = (parent) => `t.install_id=${parent}.install_id AND t.generation=${parent}.generation`;

// Ordinary D1 tables have an indexed integer rowid B-tree, including tables
// whose public keys are composite. Scan <=100 candidates, not an unbounded
// filter/sort of all expired rows. Each family keeps its own keyset position;
// rotating families every invocation prevents a growing family starving others.
// Blocked and not-yet-due rows advance the cursor too. A fixed pass high-water
// ensures continuous appends cannot prevent wrap/revisiting earlier deadlines.
// No private values or provider results enter a cursor or returned statistics.
const families = [
  ['control', 'pull_control_outbox', (q, ids) => [
    q.stmt(`UPDATE pull_control_outbox SET state='uncertain',finished_ms=COALESCE(finished_ms,${SQL_NOW}),
      lease_id=NULL,lease_until_ms=NULL WHERE rowid IN (${ids}) AND state='sending'
      AND (${due('expires_ms')} OR ${due('lease_until_ms')})`),
    q.stmt(`UPDATE pull_control_outbox SET envelope_sealed=NULL,
      state=CASE WHEN state IN ('queued','failed') AND ${due('expires_ms')} THEN 'expired' ELSE state END,
      finished_ms=COALESCE(finished_ms,${SQL_NOW}) WHERE rowid IN (${ids})
      AND (state='accepted' OR ${due('expires_ms')} OR EXISTS(SELECT 1 FROM pull_installs i
        WHERE i.install_id=pull_control_outbox.install_id AND i.state='deleted'))`),
    q.stmt(`DELETE FROM pull_control_outbox WHERE rowid IN (${ids}) AND state!='sending'
      AND envelope_sealed IS NULL AND ${due('expires_ms', MONTH)}
      AND (finished_ms IS NULL OR ${due('finished_ms', MONTH)})
      AND ${absent('pull_owner_changes c', `c.change_id=pull_control_outbox.change_id
        AND (c.old_owner_sealed IS NOT NULL OR c.new_owner_sealed IS NOT NULL)`)}`),
  ]],
  ['changes', 'pull_owner_changes', (q, ids) => [
    q.stmt(`UPDATE pull_owner_changes SET old_owner_sealed=NULL WHERE rowid IN (${ids})
      AND old_owner_sealed IS NOT NULL AND NOT(state='completed' AND revert_until_ms>${SQL_NOW})
      AND (state!='pending' OR ${due('expires_ms')} OR EXISTS(SELECT 1 FROM pull_installs i
        WHERE i.install_id=pull_owner_changes.install_id AND i.state='deleted'))`),
    q.stmt(`UPDATE pull_owner_changes SET new_owner_sealed=NULL WHERE rowid IN (${ids})
      AND new_owner_sealed IS NOT NULL AND (EXISTS(SELECT 1 FROM pull_installs i
        WHERE i.install_id=pull_owner_changes.install_id AND i.state='deleted') OR state IN ('reverted','cancelled','expired')
        OR (state='pending' AND ${due('expires_ms')})
        OR (state='completed' AND (${due('revert_until_ms')} OR ${absent('pull_control_outbox o', `o.change_id=pull_owner_changes.change_id
          AND o.envelope_sealed IS NOT NULL AND o.expires_ms>${SQL_NOW}`)})))`),
    // Completed switches, including reverted switches, enforce the annual cap.
    q.stmt(`DELETE FROM pull_owner_changes WHERE rowid IN (${ids})
      AND old_owner_sealed IS NULL AND new_owner_sealed IS NULL
      AND (revert_until_ms IS NULL OR ${due('revert_until_ms')})
      AND ((completed_ms IS NOT NULL AND ${due('completed_ms', YEAR)})
        OR (completed_ms IS NULL AND ${due('expires_ms', MONTH)}))`),
  ]],
  ['sessions', 'pull_browser_sessions', (q, ids) => [
    q.stmt(`DELETE FROM pull_browser_sessions WHERE rowid IN (${ids})
      AND (${due('expires_ms')} OR ${due('created_ms', 600000)})`),
  ]],
  ['tokens', 'pull_tokens', (q, ids) => [
    q.stmt(`UPDATE pull_tokens SET state='expired' WHERE rowid IN (${ids}) AND state='pending' AND ${due('expires_ms')}`),
    q.stmt(`DELETE FROM pull_tokens WHERE rowid IN (${ids}) AND ${due('expires_ms', MONTH)}
      AND ${absent('pull_browser_sessions s', 's.token_hash=pull_tokens.token_hash')}
      AND ${absent('pull_owner_changes c', `c.change_id=pull_tokens.change_id
        AND c.state='completed' AND c.revert_until_ms>${SQL_NOW}`)}`),
  ]],
  ...[['nonces','pull_nonces'], ['tickets','pull_tickets'], ['budgets','pull_budgets']]
    .map(([name, table]) => [name, table, (q, ids) => [
      q.stmt(`DELETE FROM ${table} WHERE rowid IN (${ids}) AND ${due('expires_ms')}`),
    ]]),
  ['slots', 'pull_owner_slots', (q, ids) => [
    q.stmt(`DELETE FROM pull_owner_slots WHERE rowid IN (${ids}) AND role!='active' AND ${due('until_ms')}`),
  ]],
  ['audit', 'pull_audit', (q, ids) => [
    // Indexed, bounded cap lookup (10,000 is the contractual maximum), never
    // COUNT/sort of an arbitrarily large audit table. Keep newest details.
    q.stmt(`INSERT INTO pull_audit_overflow(day_ms,count)
      SELECT CAST(at_ms/${DAY} AS INTEGER)*${DAY},COUNT(*) FROM pull_audit
      WHERE rowid IN (${ids}) AND at_ms>${SQL_NOW}-${MONTH}
      AND audit_id<(SELECT audit_id FROM pull_audit ORDER BY audit_id DESC LIMIT 1 OFFSET 9999)
      GROUP BY 1 ON CONFLICT(day_ms) DO UPDATE SET count=count+excluded.count`),
    q.stmt(`DELETE FROM pull_audit WHERE rowid IN (${ids}) AND (${due('at_ms', MONTH)}
      OR audit_id<(SELECT audit_id FROM pull_audit ORDER BY audit_id DESC LIMIT 1 OFFSET 9999))`),
  ]],
  ['overflow', 'pull_audit_overflow', (q, ids) => [
    q.stmt(`DELETE FROM pull_audit_overflow WHERE rowid IN (${ids}) AND ${due('day_ms', MONTH)}`),
  ]],
  ['operations', 'pull_operations', (q, ids) => [
    q.stmt(`DELETE FROM pull_operations WHERE rowid IN (${ids}) AND ${due('expires_ms')}
      AND ${due('created_ms', MONTH)}
      AND ${absent('pull_threads t', `${threadScope('pull_operations')} AND t.dedupe_until_ms>${SQL_NOW}`)}
      AND ${absent('pull_generations g JOIN pull_flows f ON f.flow_id=g.flow_id',
        `g.install_id=pull_operations.install_id AND g.generation=pull_operations.generation
         AND f.expires_ms>${SQL_NOW}-${MONTH}`)}
      AND ${absent('pull_flows f', `f.install_id=pull_operations.install_id
        AND f.expected_generation=pull_operations.generation AND f.expires_ms>${SQL_NOW}-${MONTH}`)}`),
  ]],
  ['receipts', 'pull_receipts', (q, ids) => [
    q.stmt(`DELETE FROM pull_receipts WHERE rowid IN (${ids}) AND ${due('dedupe_until_ms')}
      AND ${absent('pull_dedupe d', `d.event_id=pull_receipts.event_id AND d.dedupe_until_ms>${SQL_NOW}`)}`),
  ]],
  ['offers', 'pull_offers', (q, ids) => [
    q.stmt(`DELETE FROM pull_offers WHERE rowid IN (${ids}) AND ${due('dedupe_until_ms')}
      AND ${absent('pull_dedupe d', `d.event_id=pull_offers.event_id AND d.dedupe_until_ms>${SQL_NOW}`)}`),
  ]],
  ['events', 'pull_events', (q, ids) => [
    q.stmt(`DELETE FROM pull_events WHERE rowid IN (${ids}) AND state IN ${TERMINAL}
      AND text IS NULL AND body_bytes=0 AND ${due('terminal_ms', MONTH)}`),
  ]],
  ['dedupe', 'pull_dedupe', (q, ids) => [
    q.stmt(`DELETE FROM pull_dedupe WHERE rowid IN (${ids}) AND ${due('dedupe_until_ms')}
      AND disposition!='pending' AND ${eventAbsent('pull_events')}
      AND ${eventAbsent('pull_offers')} AND ${eventAbsent('pull_receipts')}`),
  ]],
  ['threads', 'pull_threads', (q, ids, rows) => {
    const intents = JSON.stringify(rows.map((r) => [r.install_id,r.generation,r.intent_id]));
    return [
      q.stmt(`DELETE FROM pull_threads WHERE rowid IN (${ids}) AND ${due('dedupe_until_ms')}
        AND ${due('expires_ms', MONTH)}
        AND ${absent('pull_dedupe d', `d.install_id=pull_threads.install_id AND d.generation=pull_threads.generation
          AND d.thread_id=pull_threads.thread_id`)}
        AND ${absent('pull_ingress_holds h', `h.install_id=pull_threads.install_id AND h.generation=pull_threads.generation
          AND h.thread_id=pull_threads.thread_id`)}`),
      q.stmt(`DELETE FROM pull_intents WHERE (install_id,generation,intent_id) IN
        (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]') FROM json_each(?))
        AND ${due('created_ms', MONTH)} AND ${absent('pull_threads t', `${threadScope('pull_intents')}
          AND t.intent_id=pull_intents.intent_id`)}`, intents),
    ];
  }],
];

function corrupt() { console.error('[agent-mail] pull_retention_cursor_corrupt'); throw new Error('storage_unavailable'); }
async function load(q) {
  const row = await q.first("SELECT * FROM pull_sweep_cursors WHERE task='metadata'");
  if (!row) return { row: null, phase: 0, positions: families.map(() => [0,0]) };
  let positions;
  try { positions = JSON.parse(row.cursor_id); } catch { corrupt(); } // Syntax only.
  if (!Number.isSafeInteger(row.cursor_ms) || row.cursor_ms < 0 || row.cursor_ms >= families.length
    || !Number.isSafeInteger(row.updated_ms) || row.updated_ms < 0
    || !Array.isArray(positions) || positions.length !== families.length
    || positions.some((p) => !Array.isArray(p) || p.length !== 2
      || p.some((n) => !Number.isSafeInteger(n) || n < 0) || p[0] > p[1])) corrupt();
  return { row, phase: row.cursor_ms, positions };
}

/** retain({ledger}) accepts the existing PullLedger or transport facade. One
 * invocation scans <=100 rows of ONE family and atomically advances its cursor.
 * Repeated ticks complete fair passes across every family/installation, even
 * after restart. Expiry/body retirement remains pull-sweep's separate task.
 * No provider/network, timers, authority creation, budget refund or auto-retry.
 * Durable install/generation/flow/fence/address records are not garbage-collected.
 */
export async function retain({ ledger }) {
  const q = ledger?.queue ?? ledger;
  if (!q?.maintenance || !q?.all || !q?.guard) throw new Error('invalid_request');
  const { row, phase, positions } = await load(q);
  const [family, table, statements] = families[phase];
  let [after, through] = positions[phase];
  if (!through) through = (await q.first(`SELECT rowid AS last FROM ${table} ORDER BY rowid DESC LIMIT 1`))?.last ?? 0;
  const rows = await q.all(`SELECT rowid AS retention_rowid${family === 'threads' ? ',install_id,generation,intent_id' : ''}
    FROM ${table} WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ${LIMIT}`, after, through);
  // Bounded list of server-read integer rowids, never request-supplied SQL.
  const ids = rows.map((r) => r.retention_rowid).join(',') || 'NULL';
  const last = rows.at(-1)?.retention_rowid ?? 0;
  positions[phase] = rows.length < LIMIT || last >= through ? [0,0] : [last,through];
  const guard = row
    ? q.guard(`EXISTS(SELECT 1 FROM pull_sweep_cursors WHERE task='metadata' AND cursor_ms=? AND cursor_id=? AND updated_ms=?)`,
      row.cursor_ms, row.cursor_id, row.updated_ms)
    : q.guard("NOT EXISTS(SELECT 1 FROM pull_sweep_cursors WHERE task='metadata')");
  try {
    const r = await q.maintenance([guard, ...statements(q, ids, rows),
      q.stmt(`INSERT INTO pull_sweep_cursors(task,cursor_ms,cursor_id,updated_ms) VALUES('metadata',?,?,${SQL_NOW})
        ON CONFLICT(task) DO UPDATE SET cursor_ms=excluded.cursor_ms,cursor_id=excluded.cursor_id,
        updated_ms=MAX(pull_sweep_cursors.updated_ms+1,excluded.updated_ms)`,
      (phase + 1) % families.length, JSON.stringify(positions)),
    ]);
    return { family, scanned: rows.length, changed: r.slice(1,-1).reduce((n, s) => n + s.meta.changes, 0) };
  } catch (error) {
    const current = (await load(q)).row;
    // Only a proven competing commit is benign; storage failures stay visible.
    if (current && (!row || current.updated_ms !== row.updated_ms || current.cursor_id !== row.cursor_id
      || current.cursor_ms !== row.cursor_ms)) return { family, scanned: 0, changed: 0 };
    throw error;
  }
}
