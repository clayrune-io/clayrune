// MC-1166 W01. Internal scheduled consumer, deliberately unwired until W30.
import { bytes, randomId, sha256 } from './signing.js';
import { parseAddrSpec } from './mailbox.js';
import { verificationBinding, VERIFICATION_TTL } from './verification-mail.js';
import { SQL_NOW } from './sql-clock.js';

const LIMIT = 100, LEASE = 60_000, WAIT = 10_000, DAY = 86_400_000;
const VERIFICATION = ['signup', 'stepup', 'new_owner'];
const PERMANENT = new Set(['E_VALIDATION_ERROR', 'E_FIELD_MISSING', 'E_TOO_MANY_RECIPIENTS',
  'E_TOO_MANY_ATTACHMENTS', 'E_SENDER_NOT_VERIFIED', 'E_RECIPIENT_NOT_ALLOWED',
  'E_ACCOUNT_SUSPENDED', 'E_SENDER_DOMAIN_NOT_AVAILABLE', 'E_CONTENT_TOO_LARGE',
  'E_HEADER_NOT_ALLOWED', 'E_HEADER_USE_API_FIELD', 'E_HEADER_VALUE_INVALID',
  'E_HEADER_VALUE_TOO_LONG', 'E_HEADER_NAME_INVALID', 'E_HEADERS_TOO_LARGE', 'E_HEADERS_TOO_MANY']);
const idOK = (id) => typeof id === 'string' && /^[\x20-\x7e]{1,256}$/.test(id) && Boolean(id.trim());
const cursorSame = (a, b) => a.cursor_ms === b.cursor_ms && a.cursor_id === b.cursor_id && a.updated_ms === b.updated_ms;

/** One bounded, persisted keyset page. mail MUST be the configured
 * VerificationMail for db, binding the structured MAIL_SEND binding. Neither
 * caller content nor an active/verified-registration fiction is accepted here.
 * A claim is already conservative in-flight custody: crash before admission,
 * after admission or after provider acceptance all become uncertain on lease
 * loss. Only a timely explicit Error.code quota refusal proves safe retry.
 * Timers/clock/random are dependency seams for synthetic tests, not wire input.
 */
export async function deliverControlMail({ mail, binding, from, domain, clock = Date.now,
  timers = globalThis, random = Math.random }) {
  if (!mail?.db || typeof mail.message !== 'function' || typeof mail.openEnvelope !== 'function'
      || typeof binding?.send !== 'function' || !parseAddrSpec(from)
      || parseAddrSpec(from).domain !== domain || parseAddrSpec(`mail@${domain}`)?.domain !== domain
      || typeof clock !== 'function' || typeof timers?.setTimeout !== 'function'
      || typeof timers?.clearTimeout !== 'function' || typeof random !== 'function') {
    throw new Error('provider_config_required');
  }
  const q = new Delivery(mail, binding, from, clock, timers, random);
  try { return await q.page(); }
  catch {
    // No exception text, owner, token, ciphertext or provider payload in logs.
    console.error('[agent-mail] control_delivery_storage_unavailable');
    throw new Error('storage_unavailable');
  }
}

class Delivery {
  constructor(mail, binding, from, clock, timers, random) {
    Object.assign(this, { mail, db: mail.db, r: mail.registry, binding, from, clock, timers, random });
  }
  stmt(sql, ...args) { return this.r.stmt(sql, ...args); }
  first(sql, ...args) { return this.stmt(sql, ...args).first(); }
  async all(sql, ...args) { return (await this.stmt(sql, ...args).all()).results; }
  async cursor() {
    return await this.first("SELECT * FROM pull_sweep_cursors WHERE task='control'")
      ?? { cursor_ms: 0, cursor_id: '', updated_ms: null };
  }
  async page() {
    const c = await this.cursor();
    // Disjoint indexed arms include expired terminal envelopes without making
    // uncertain/failed work sendable. Sending next_attempt_ms is its lease end.
    const rows = await this.all(`SELECT * FROM (
      SELECT next_attempt_ms AS ms,outbox_id AS id FROM pull_control_outbox INDEXED BY pull_control_due
        WHERE state IN ('queued','sending') AND next_attempt_ms<=${SQL_NOW}
        AND (next_attempt_ms,outbox_id)>(?,?)
      UNION ALL SELECT expires_ms AS ms,outbox_id AS id FROM pull_control_outbox INDEXED BY pull_control_expiry
        WHERE envelope_sealed IS NOT NULL AND expires_ms<=${SQL_NOW}
        AND (state NOT IN ('queued','sending') OR next_attempt_ms>${SQL_NOW})
        AND (expires_ms,outbox_id)>(?,?)) ORDER BY ms,id LIMIT ${LIMIT}`,
    c.cursor_ms, c.cursor_id, c.cursor_ms, c.cursor_id);
    const last = rows.at(-1);
    try {
      await this.db.batch([
        c.updated_ms === null
          ? this.stmt(`INSERT INTO pull_sweep_cursors(task,cursor_ms,cursor_id,updated_ms)
            VALUES('control',?,?,${SQL_NOW}) ON CONFLICT(task) DO NOTHING`, last?.ms ?? 0, last?.id ?? '')
          : this.stmt(`UPDATE pull_sweep_cursors SET cursor_ms=?,cursor_id=?,updated_ms=${SQL_NOW}
            WHERE task='control' AND cursor_ms=? AND cursor_id=? AND updated_ms=?`,
          last?.ms ?? 0, last?.id ?? '', c.cursor_ms, c.cursor_id, c.updated_ms),
        this.r.changed(),
      ]);
    } catch (error) {
      if (!cursorSame(c, await this.cursor())) return { scanned: 0, submitted: 0 };
      throw error;
    }
    let submitted = 0;
    for (const row of rows) submitted += await this.deliver(row.id);
    return { scanned: rows.length, submitted };
  }
  async expire(id) {
    // Preserve positive evidence but remove the envelope even if acceptance was
    // superseded by another lifecycle writer. Never turn uncertainty into retry.
    await this.db.batch([this.stmt(`UPDATE pull_control_outbox SET
      state=CASE WHEN state='accepted' THEN state ELSE 'expired' END,envelope_sealed=NULL,
      lease_id=NULL,lease_until_ms=NULL,finished_ms=COALESCE(finished_ms,${SQL_NOW})
      WHERE outbox_id=? AND expires_ms<=${SQL_NOW}`, id)]);
  }
  async deliver(id) {
    await this.expire(id);
    await this.db.batch([this.stmt(`UPDATE pull_control_outbox SET state='uncertain',
      lease_id=NULL,lease_until_ms=NULL,finished_ms=${SQL_NOW}
      WHERE outbox_id=? AND state='sending' AND (lease_until_ms IS NULL OR lease_until_ms<=${SQL_NOW})`, id)]);
    const lease = randomId();
    await this.db.batch([this.stmt(`UPDATE pull_control_outbox SET state='sending',lease_id=?,
      lease_until_ms=${SQL_NOW}+${LEASE},next_attempt_ms=${SQL_NOW}+${LEASE}
      WHERE outbox_id=? AND state='queued' AND envelope_sealed IS NOT NULL
      AND next_attempt_ms<=${SQL_NOW} AND created_ms<=${SQL_NOW} AND expires_ms>${SQL_NOW}`, lease, id)]);
    const row = await this.first("SELECT * FROM pull_control_outbox WHERE outbox_id=? AND state='sending' AND lease_id=?", id, lease);
    if (!row) return 0;
    let message, guards;
    try {
      const { now } = await this.first(`SELECT ${SQL_NOW} AS now`);
      // message is the ONLY source of provider recipient/subject/text.
      message = await this.mail.message(id, now);
      guards = await this.scope(row);
    } catch {
      console.error('[agent-mail] control_delivery_message_unavailable');
      await this.settle(row, 'failed', 'control_message_unavailable');
      return 0;
    }
    if (!guards) {
      await this.settle(row, 'expired', 'control_scope_expired');
      return 0;
    }
    // Verification issuance already paid its first same-day global reservation.
    // Notices, later-day handoffs and positive-nonacceptance retries reserve a
    // new global attempt here. No refund on refusal or uncertain outcome.
    const paid = VERIFICATION.includes(row.template) && row.attempts === 0;
    const { now } = await this.first(`SELECT ${SQL_NOW} AS now`);
    const day = Math.floor(now / DAY), needsBudget = !paid || Math.floor(row.created_ms / DAY) !== day;
    const bucket = `pull:send:global:${day}`;
    const budget = needsBudget ? [this.stmt(`INSERT INTO pull_budgets(bucket,count,expires_ms) VALUES(?,1,?)
      ON CONFLICT(bucket) DO UPDATE SET count=count+1 WHERE count<1000`, bucket, (day + 2) * DAY), this.r.changed()] : [];
    try {
      await this.db.batch([
        ...guards, this.rowGuard(row),
        this.r.guard(`CAST(${SQL_NOW}/${DAY} AS INTEGER)=?`, day),
        ...budget,
        this.stmt(`UPDATE pull_control_outbox SET attempts=attempts+1 WHERE outbox_id=? AND lease_id=?
          AND state='sending' AND attempts<?`, id, lease, Number.MAX_SAFE_INTEGER), this.r.changed(),
        ...guards, this.rowGuard(row),
      ]);
    } catch (error) {
      // Only recognizable concurrent refusal is handled here. Storage errors
      // surface; the sending lease safely owns the unknown admission outcome.
      const current = await this.first('SELECT * FROM pull_control_outbox WHERE outbox_id=?', id);
      if (!current || current.state !== 'sending' || current.lease_id !== lease) return 0;
      const fresh = await this.scope(row);
      if (!fresh || !await this.live(row)) {
        await this.settle(row, 'expired', 'control_scope_expired'); return 0;
      }
      const b = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
      if (needsBudget && b?.count >= 1000) {
        await this.settle(row, 'queued', 'provider_quota', '', Math.max(1000, (day + 1) * DAY - now)); return 0;
      }
      throw error;
    }
    // Admission is the serialization point. A lifecycle change after this
    // commit cannot recall an already authorized provider attempt. Settlement
    // may retain its evidence, but can never restore lifecycle/flow authority.
    const outcome = await this.send(message);
    await this.settle(row, outcome.state, outcome.code, outcome.provider_id);
    return outcome.called === false ? 0 : 1;
  }
  rowGuard(row) {
    return this.r.guard(`EXISTS(SELECT 1 FROM pull_control_outbox WHERE outbox_id=? AND install_id=?
      AND flow_id=? AND change_id IS ? AND template=? AND target_hash=? AND idempotency_key=?
      AND seal_key_id=? AND envelope_sealed=? AND expires_ms=? AND state='sending' AND lease_id=?
      AND lease_until_ms>${SQL_NOW} AND expires_ms>${SQL_NOW})`, row.outbox_id, row.install_id,
    row.flow_id, row.change_id, row.template, row.target_hash, row.idempotency_key, row.seal_key_id,
    row.envelope_sealed, row.expires_ms, row.lease_id);
  }
  live(row) {
    return this.first(`SELECT 1 FROM pull_control_outbox WHERE outbox_id=? AND state='sending'
      AND lease_id=? AND lease_until_ms>${SQL_NOW} AND expires_ms>${SQL_NOW}`,
    row.outbox_id, row.lease_id);
  }
  async scope(row) {
    const f = await this.first('SELECT * FROM pull_flows WHERE flow_id=? AND install_id=?', row.flow_id, row.install_id);
    const i = await this.first('SELECT * FROM pull_installs WHERE install_id=?', row.install_id);
    if (!f || !i) return null;
    const { now } = await this.first(`SELECT ${SQL_NOW} AS now`);
    const envelope = await this.mail.openEnvelope(row);
    const tokenHash = await sha256(bytes(envelope.token));
    const c = row.change_id === null ? null : await this.first(
      'SELECT * FROM pull_owner_changes WHERE change_id=? AND flow_id=? AND install_id=?', row.change_id, f.flow_id, i.install_id);
    const guards = [this.r.guard(`EXISTS(SELECT 1 FROM pull_installs WHERE install_id=? AND generation=?
      AND owner_generation=? AND owner_hash=? AND state=? AND epoch=? AND key_id=? AND public_key=?)`,
    i.install_id, i.generation, i.owner_generation, i.owner_hash, i.state, i.epoch, i.key_id, i.public_key)];
    if (VERIFICATION.includes(row.template)) {
      const deadline = row.template === 'new_owner' ? c : f;
      // Match VerificationMail's issuance/confirmation lifetime and purpose.
      // Reading a previously valid envelope is not current flow authority.
      if (!deadline || deadline.created_ms > now || deadline.expires_ms - deadline.created_ms !== VERIFICATION_TTL
          || (row.template === 'new_owner' && f.purpose !== 'owner_change')) return null;
      const newOwner = row.template === 'new_owner';
      guards.push(this.r.guard(`EXISTS(SELECT 1 FROM ${newOwner ? 'pull_owner_changes' : 'pull_flows'}
        WHERE ${newOwner ? 'change_id' : 'flow_id'}=? AND created_ms=? AND expires_ms=?
        AND created_ms<=${SQL_NOW} AND expires_ms-created_ms=?)`, newOwner ? c.change_id : f.flow_id,
      deadline.created_ms, deadline.expires_ms, VERIFICATION_TTL));
      const s = { install_id: i.install_id, flow_id: f.flow_id, change_id: row.change_id, purpose: row.template,
        generation: f.expected_generation, owner_generation: f.expected_owner_generation, owner_hash: row.target_hash,
        candidate_key_id: f.candidate_key_id, candidate_public_key: f.candidate_public_key, expires_ms: row.expires_ms };
      const token = await this.first(`SELECT 1 FROM pull_tokens WHERE token_hash=? AND install_id=? AND flow_id=?
        AND change_id IS ? AND purpose=? AND generation=? AND owner_generation=? AND owner_hash=?
        AND binding_digest=? AND state='pending' AND expires_ms=? AND expires_ms>${SQL_NOW}`,
      tokenHash, s.install_id, s.flow_id, s.change_id, s.purpose, s.generation, s.owner_generation, s.owner_hash,
      await verificationBinding(s), s.expires_ms);
      if (!token || row.expires_ms !== f.expires_ms || i.generation !== f.expected_generation
          || i.owner_generation !== f.expected_owner_generation || i.owner_hash !== f.owner_hash
          || f.state !== (row.template === 'new_owner' ? 'approved' : 'pending')
          || i.state === 'deleted' || (row.template !== 'stepup' && i.state === 'frozen')
          || (row.template === 'signup' && (i.state !== 'pending' || f.purpose !== 'signup'))
          || (row.template !== 'signup' && f.purpose === 'signup')
          || (row.template !== 'new_owner' && (row.change_id !== null || row.target_hash !== f.owner_hash))
          || (row.template === 'new_owner' && (!c || c.state !== 'pending' || c.new_owner_hash !== row.target_hash
            || c.old_owner_hash !== f.owner_hash || c.expected_generation !== f.expected_generation
            || c.expected_owner_generation !== f.expected_owner_generation || c.expires_ms !== row.expires_ms))
          || await this.mail.fenced(f, row.template)) return null;
      const reentry = this.mail.reentryQuery(f);
      if (row.template === 'stepup' && i.state === 'disabled' && !await this.first(reentry.sql, ...reentry.args)) return null;
      guards.push(...this.mail.scopeStatements(s, f), this.r.guard(`EXISTS(SELECT 1 FROM pull_tokens
        WHERE token_hash=? AND state='pending' AND binding_digest=? AND expires_ms>${SQL_NOW})`,
      tokenHash, await verificationBinding(s)));
    } else {
      if (!c || f.purpose !== 'owner_change') return null;
      if (row.template === 'old_notice') {
        if (c.state !== 'completed' || c.revert_until_ms !== row.expires_ms || c.revert_until_ms <= now
            || i.owner_generation !== c.switched_owner_generation || i.owner_hash !== c.new_owner_hash
            || row.target_hash !== c.old_owner_hash) return null;
        const digest = await verificationBinding({ purpose: 'revert', install_id: i.install_id,
          flow_id: f.flow_id, change_id: c.change_id, generation: c.switched_generation,
          owner_generation: c.switched_owner_generation, owner_hash: c.old_owner_hash,
          candidate_key_id: f.candidate_key_id, candidate_public_key: f.candidate_public_key, expires_ms: row.expires_ms });
        if (!await this.first(`SELECT 1 FROM pull_tokens WHERE token_hash=? AND purpose='revert'
          AND state='pending' AND binding_digest=? AND expires_ms>${SQL_NOW}`, tokenHash, digest)) return null;
        guards.push(this.r.guard(`EXISTS(SELECT 1 FROM pull_tokens WHERE token_hash=? AND purpose='revert'
          AND state='pending' AND binding_digest=? AND expires_ms>${SQL_NOW})`, tokenHash, digest));
      } else {
        if (!['revert_old', 'revert_new'].includes(row.template) || c.state !== 'reverted' || f.state !== 'reverted'
            || i.owner_hash !== c.old_owner_hash || i.owner_generation !== c.switched_owner_generation + 1
            || row.target_hash !== (row.template === 'revert_old' ? c.old_owner_hash : c.new_owner_hash)
            || row.created_ms !== c.reverted_ms || row.expires_ms !== c.reverted_ms + 7 * DAY
            || i.state === 'deleted') return null;
      }
      guards.push(this.r.guard(`EXISTS(SELECT 1 FROM pull_owner_changes WHERE change_id=? AND install_id=?
        AND flow_id=? AND state=? AND old_owner_hash=? AND new_owner_hash=? AND switched_generation=?
        AND switched_owner_generation=? AND revert_until_ms IS ? AND reverted_ms IS ?)`, c.change_id,
      c.install_id, c.flow_id, c.state, c.old_owner_hash, c.new_owner_hash, c.switched_generation,
      c.switched_owner_generation, c.revert_until_ms, c.reverted_ms));
    }
    return guards;
  }
  async send(message) {
    const unknown = { state: 'uncertain', code: 'uncertain', provider_id: '' };
    const started = this.clock(), deadline = started + WAIT;
    if (!Number.isSafeInteger(started) || started < 0 || !Number.isSafeInteger(deadline)) return { ...unknown, called: false };
    const timely = () => {
      try { const n = this.clock(); return Number.isSafeInteger(n) && n >= started && n < deadline; }
      catch { return false; } // A failed clock cannot prove timely provider acceptance.
    };
    let timer;
    try {
      const timeout = new Promise((resolve) => { timer = this.timers.setTimeout(() => resolve(unknown), WAIT); });
      const attempt = (async () => {
        try {
          const receipt = await this.binding.send({ from: this.from, to: message.to,
            subject: message.subject, text: message.body,
            headers: { 'Auto-Submitted': 'auto-generated', 'X-Clayrune-Agent-Mail': '1' } });
          if (!timely() || !idOK(receipt?.messageId)) return unknown;
          return { state: 'accepted', code: 'ok', provider_id: receipt.messageId };
        } catch (error) {
          if (!timely()) return unknown;
          const code = error instanceof Error ? error.code : undefined;
          if (['E_RATE_LIMIT_EXCEEDED', 'E_DAILY_LIMIT_EXCEEDED'].includes(code)) {
            return { state: 'queued', code: 'provider_quota', provider_id: '' };
          }
          if (code === 'E_RECIPIENT_SUPPRESSED' || PERMANENT.has(code)) return {
            state: 'failed', code: code === 'E_RECIPIENT_SUPPRESSED' ? 'provider_suppressed' : 'provider_unavailable', provider_id: '' };
          return unknown;
        }
      })();
      return await Promise.race([attempt, timeout]);
    } finally { this.timers.clearTimeout(timer); }
  }
  async settle(row, state, code, provider_id = '', delay = null) {
    const attempts = row.attempts + 1;
    const ceiling = Math.min(300_000, 1000 * 2 ** Math.min(attempts - 1, 9));
    const jitter = Math.max(1000, Math.floor(this.random() * ceiling));
    const result = await this.db.batch([
      this.stmt(`UPDATE pull_control_outbox SET
        state=CASE WHEN lease_until_ms IS NULL OR lease_until_ms<=${SQL_NOW} THEN 'uncertain' ELSE ? END,
        provider_id=CASE WHEN ?!='' THEN ? ELSE provider_id END,
        envelope_sealed=CASE WHEN ? IN ('accepted','expired') THEN NULL ELSE envelope_sealed END,
        next_attempt_ms=${SQL_NOW}+?,finished_ms=CASE WHEN ?='queued' AND lease_until_ms>${SQL_NOW} THEN NULL ELSE ${SQL_NOW} END,
        lease_id=NULL,lease_until_ms=NULL WHERE outbox_id=? AND state='sending' AND lease_id=?`,
      state, provider_id, provider_id, state, delay ?? jitter, state, row.outbox_id, row.lease_id),
      // A lifecycle writer may already have made sending uncertain/expired.
      // Positive late evidence is still retained, without reopening its state.
      this.stmt(`UPDATE pull_control_outbox SET provider_id=?,envelope_sealed=NULL
        WHERE outbox_id=? AND state IN ('uncertain','expired') AND provider_id='' AND ?!=''`,
      provider_id, row.outbox_id, provider_id),
    ]);
    if (result[0].meta.changes || (provider_id && result[1].meta.changes)) {
      await this.db.batch([
        this.stmt(`INSERT INTO pull_audit_overflow SELECT CAST(${SQL_NOW}/${DAY} AS INTEGER)*${DAY},1
          WHERE (SELECT COUNT(*) FROM pull_audit)>=10000
          ON CONFLICT(day_ms) DO UPDATE SET count=count+1`),
        this.stmt(`INSERT INTO pull_audit(install_id,at_ms,code,related_id) SELECT ?,${SQL_NOW},?,?
          WHERE (SELECT COUNT(*) FROM pull_audit)<10000`, row.install_id, code, row.outbox_id),
      ]);
    }
    await this.expire(row.outbox_id);
  }
}
