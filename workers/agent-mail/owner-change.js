// Pull contract 6.2 / 8.1. Unwired hosted lifecycle transactions, no provider.
// Unit 14 dispatches new_owner links here (stepup/signup stay in their modules).
// Config and trustedSource follow OwnerManagementHTTP; no install-key shortcut.
import { OwnerManagementHTTP } from './owner-management.js';
import { ownerIdentity } from './owner-registry.js';
import { VERIFICATION_TTL, confirmationPage, verificationBinding } from './verification-mail.js';
import { bytes, randomId, sha256, unbase64url } from './signing.js';
import { readRequestBody } from './request-body.js';
import { SQL_NOW, sqlClockAtOrAfter } from './sql-clock.js';

export const OWNER_CHANGE_PATHS = Object.freeze(['/mail/owner-change', '/mail/change/cancel',
  '/mail/link', '/mail/link/confirm', '/mail/revert', '/mail/resume']);
const DAY = 86_400_000;
const WEEK = 7 * DAY;
const TTL = 600_000;
const FINAL_ENABLE_TTL = 30 * 60_000;
const MAX_G = 999_999_999_999_999;
const H = /^[a-f0-9]{64}$/;
const I = /^[a-f0-9]{32}$/;
const COOKIE = '__Secure-clayrune-mail';
const valid = (yes, code = 'invalid_request') => { if (!yes) throw new Error(code); };
const match = (re, s) => typeof s === 'string' && re.test(s);
const hash = (s) => sha256(bytes(s));
const secret = () => randomId() + randomId();
const htmlHeaders = { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
  'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer', 'x-frame-options': 'DENY',
  'content-security-policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" };
const page = (body, status = 200) => new Response('<!doctype html><html lang="en"><meta charset="utf-8">'
  + '<title>Clayrune mail</title>' + body + '</html>', { status, headers: htmlHeaders });
const receipt = () => page('<p>Mail management request recorded. Return to Clayrune for status.</p>');
const codes = new Map([[400, ['invalid_request']], [401, ['csrf_failed', 'session_required', 'unauthorized']],
  [404, ['not_found']], [408, ['body_timeout']],
  [409, ['same_owner', 'change_limit', 'owner_limit', 'operation_conflict', 'change_superseded',
    'stale_generation', 'registration_inactive', 'registration_frozen', 'already_sent', 'fenced']],
  [410, ['token_expired', 'flow_expired']], [413, ['body_limit']],
  [429, ['verification_limit', 'abuse_limit']], [503, ['storage_unavailable', 'service_unavailable']]]
  .flatMap(([status, values]) => values.map((code) => [code, status])));

function cookieSecret(request) {
  const pairs = (request.headers.get('cookie') ?? '').split(';').map((s) => s.trim().split('='))
    .filter(([name]) => name === COOKIE);
  valid(pairs.length === 1 && pairs[0].length === 2 && match(H, pairs[0][1]), 'session_required');
  return pairs[0][1];
}
function form(raw, fields) {
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw); }
  catch { throw new Error('invalid_request'); } // Syntax only, no I/O.
  valid(/^[\x21-\x7e]*$/.test(text) && !/%(?![a-fA-F0-9]{2})/.test(text));
  const entries = [...new URLSearchParams(text)];
  const v = Object.fromEntries(entries);
  valid(entries.length === fields.length && Object.keys(v).sort().join(',') === [...fields].sort().join(','));
  for (const name of fields) {
    valid(name === 'new_owner' ? bytes(v[name]).length <= 254
      : match(name === 'csrf' || name === 'token' ? H : I, v[name]));
  }
  return v;
}

export class OwnerChangeHTTP extends OwnerManagementHTTP {
  installGuard(i) {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_installs WHERE install_id=? AND generation=?
      AND owner_generation=? AND owner_hash=? AND owner_address IS ? AND address_local=? AND key_id=?
      AND public_key=? AND state=? AND consent_version=?)`, i.install_id, i.generation, i.owner_generation,
    i.owner_hash, i.owner_address, i.address_local, i.key_id, i.public_key, i.state, i.consent_version);
  }
  // Wall-time guards belong at BOTH ends of each batch: queued D1 writes must
  // not commit an expired browser/token or charge yesterday's verification cap.
  timeGuard(now, end, sameDay = false) {
    return this.registry.guard(`${sqlClockAtOrAfter()} AND ${SQL_NOW}<?${sameDay ? ` AND CAST(${SQL_NOW}/86400000 AS INTEGER)=?` : ''}`,
      now, end, ...(sameDay ? [Math.floor(now / DAY)] : []));
  }
  currentGuard(i, state = 'current') {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_generations WHERE install_id=? AND generation=?
      AND owner_generation=? AND owner_hash=? AND key_id=? AND public_key=? AND state=?)`,
    i.install_id, i.generation, i.owner_generation, i.owner_hash, i.key_id, i.public_key, state);
  }
  epochGuard(i) {
    return this.registry.guard('EXISTS(SELECT 1 FROM pull_installs WHERE install_id=? AND epoch=? AND connection_id IS ?)',
      i.install_id, i.epoch, i.connection_id);
  }
  frequencyPredicate() {
    return `NOT EXISTS(SELECT 1 FROM pull_owner_changes WHERE install_id=? AND completed_ms IS NOT NULL
      AND (completed_ms>? OR revert_until_ms>?)) AND
      (SELECT COUNT(*) FROM pull_owner_changes WHERE install_id=? AND completed_ms>?)<5`;
  }
  frequencyArgs(id, now) { return [id, now - WEEK, now, id, now - 365 * DAY]; }
  async frequency(id, now) {
    valid(await this.first(`SELECT 1 WHERE ${this.frequencyPredicate()}`, ...this.frequencyArgs(id, now)), 'change_limit');
    return this.registry.guard(this.frequencyPredicate(), ...this.frequencyArgs(id, now));
  }
  changeGuard(c) {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_owner_changes WHERE change_id=? AND install_id=?
      AND flow_id=? AND old_owner_hash=? AND new_owner_hash=? AND expected_generation=? AND expected_owner_generation=?
      AND state=? AND created_ms=? AND expires_ms=? AND completed_ms IS ? AND revert_until_ms IS ?
      AND switched_generation IS ? AND switched_owner_generation IS ? AND old_owner_sealed IS ? AND new_owner_sealed IS ?)`,
    c.change_id, c.install_id, c.flow_id, c.old_owner_hash, c.new_owner_hash, c.expected_generation,
    c.expected_owner_generation, c.state, c.created_ms, c.expires_ms, c.completed_ms, c.revert_until_ms,
    c.switched_generation, c.switched_owner_generation, c.old_owner_sealed, c.new_owner_sealed);
  }
  ownerAAD(c, side) {
    return bytes(JSON.stringify(['clayrune-mail-pull-owner', '1', c.change_id, side, c[`${side}_owner_hash`]]));
  }
  async sealOwner(c, side, address) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: this.ownerAAD(c, side) },
      await this.mail.key, bytes(address)));
    const blob = new Uint8Array(iv.length + cipher.length); blob.set(iv); blob.set(cipher, iv.length); return blob;
  }
  async openOwner(c, side) {
    // The associated control row retains the seal key id (no schema extension).
    const template = side === 'old' && c.completed_ms !== null ? 'old_notice' : 'new_owner';
    const metadata = await this.first('SELECT seal_key_id FROM pull_control_outbox WHERE change_id=? AND template=?', c.change_id, template);
    valid(metadata?.seal_key_id === this.mail.sealKeyId, 'storage_unavailable');
    const blob = c[`${side}_owner_sealed`];
    valid(blob instanceof Uint8Array && blob.length >= 28 && blob.length <= 512, 'storage_unavailable');
    const clear = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: this.ownerAAD(c, side) },
      await this.mail.key, blob.slice(12));
    const owner = await ownerIdentity(new TextDecoder('utf-8', { fatal: true }).decode(clear));
    valid(owner.owner_hash === c[`${side}_owner_hash`], 'storage_unavailable'); return owner.owner_address;
  }
  operation(i, path, op, digest, result, now) {
    return this.stmt('INSERT INTO pull_operations VALUES(?,?,?,?,?,?,200,?,?)',
      i.install_id, i.generation, path, op, digest, JSON.stringify(result), now, now + 37 * DAY);
  }
  async replay(request, v, purpose, path, digest) {
    valid(request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
    const s = await this.first('SELECT * FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=?',
      await hash(cookieSecret(request)), await hash(v.csrf));
    valid(s && s.purpose === purpose && s.created_ms <= this.now() && s.expires_ms > this.now()
      && s.expires_ms - s.created_ms <= TTL, 'session_required');
    const op = await this.first('SELECT * FROM pull_operations WHERE install_id=? AND generation=? AND path=? AND op_id=?',
      s.install_id, s.generation, path, v.op_id);
    if (!op) return false;
    valid(op.input_digest === digest, 'operation_conflict');
    return true; // Original receipt only, no scope refresh/current-owner read.
  }

  async initiate(request, v) {
    const target = await ownerIdentity(v.new_owner);
    const digest = await hash(JSON.stringify(['owner_change', target.owner_address]));
    if (await this.replay(request, v, 'owner_change', '/mail/owner-change', digest)) return receipt();
    const scope = await this.authorizeSession(request, v.csrf, 'owner_change');
    const { install: i, flow: f, session: s } = scope; const now = this.now(); const end = now + VERIFICATION_TTL;
    valid(['active', 'verified'].includes(i.state), 'registration_inactive');
    valid(target.owner_hash !== i.owner_hash, 'same_owner');
    const frequency = await this.frequency(i.install_id, now);
    valid(!await this.first("SELECT 1 FROM pull_owner_changes WHERE install_id=? AND state='pending' AND expires_ms>?", i.install_id, now), 'operation_conflict');
    const cap = await this.first(`SELECT COUNT(DISTINCT install_id) AS n FROM pull_owner_slots
      WHERE owner_hash=? AND install_id!=? AND (role='active' OR until_ms>?)`, target.owner_hash, i.install_id, now);
    valid(cap.n < 3, 'owner_limit');
    for (const [bucket, maximum] of [[`pull:verify:install:${i.install_id}:${Math.floor(now / DAY)}`, 3],
      [`pull:verify:target:${target.owner_hash}:${Math.floor(now / DAY)}`, 3], [`pull:send:global:${Math.floor(now / DAY)}`, 1000]]) {
      const b = await this.first('SELECT count FROM pull_budgets WHERE bucket=?', bucket);
      valid(!b || b.count < maximum, 'verification_limit');
    }
    const c = { change_id: randomId(), install_id: i.install_id, flow_id: f.flow_id, old_owner_hash: i.owner_hash,
      new_owner_hash: target.owner_hash, expected_generation: i.generation, expected_owner_generation: i.owner_generation,
      created_ms: now, expires_ms: end };
    const nextFlow = { ...f, expires_ms: end };
    const issued = await this.mail.issueStatements({ outbox_id: randomId(), template: 'new_owner', to: target.owner_address,
      flow: nextFlow, change: c }, now);
    const result = { state: 'pending', change_id: c.change_id };
    await this.db.batch([this.timeGuard(now, s.expires_ms, true), ...scope.statements, this.currentGuard(i), frequency,
      ...this.cancelExpired(i.install_id, now),
      this.stmt("UPDATE pull_flows SET expires_ms=? WHERE flow_id=?", end, f.flow_id), this.registry.changed(),
      this.stmt(`INSERT INTO pull_owner_changes(change_id,install_id,flow_id,old_owner_hash,new_owner_hash,old_owner_sealed,new_owner_sealed,
        expected_generation,expected_owner_generation,state,created_ms,expires_ms) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)`,
      c.change_id, i.install_id, f.flow_id, i.owner_hash, target.owner_hash, await this.sealOwner(c, 'old', i.owner_address),
      await this.sealOwner(c, 'new', target.owner_address), i.generation, i.owner_generation, now, end),
      ...this.registry.reserveSlotStatements({ owner_hash: target.owner_hash, install_id: i.install_id, flow_id: f.flow_id, until_ms: end }, now),
      ...issued.statements, this.operation(i, '/mail/owner-change', v.op_id, digest, result, now),
      this.timeGuard(now, s.expires_ms, true)]);
    return receipt();
  }
  cancelExpired(id, now) {
    return [this.stmt(`DELETE FROM pull_owner_slots WHERE install_id=? AND role='pending' AND flow_id IN
      (SELECT flow_id FROM pull_owner_changes WHERE install_id=? AND state='pending' AND expires_ms<=?)`, id, id, now),
    this.stmt(`UPDATE pull_owner_changes SET state='expired',old_owner_sealed=NULL,new_owner_sealed=NULL
      WHERE install_id=? AND state='pending' AND expires_ms<=?`, id, now)];
  }

  async browser(request, token, csrf, purpose) {
    valid(match(H, token) && match(H, csrf) && request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
    const s = cookieSecret(request); const now = this.now();
    const session = await this.first('SELECT * FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=? AND token_hash=?',
      await hash(s), await hash(csrf), await hash(token));
    valid(session && session.created_ms <= now && session.expires_ms > now && session.expires_ms - session.created_ms <= TTL, 'csrf_failed');
    const row = await this.first('SELECT * FROM pull_tokens WHERE token_hash=?', session.token_hash);
    valid(row?.purpose === purpose && row.install_id === session.install_id && row.flow_id === session.flow_id
      && row.generation === session.generation && row.owner_generation === session.owner_generation, 'csrf_failed');
    // Lifecycle invalidation removes ALL authority, but not the original fixed
    // receipt. This branch cannot write, restore a session or touch later state.
    if (row.consumed_ms !== null && row.result_json === '{"state":"confirmed"}') return { replay: true };
    valid(row.expires_ms > now, 'token_expired');
    valid(row.state === 'pending' && session.state === 'csrf'
      && session.purpose === (purpose === 'revert' ? 'revert' : 'confirmation'), 'change_superseded');
    return { session, row, sessionSecret: s, now };
  }
  sessionGuard(s, now) {
    return this.registry.guard(`EXISTS(SELECT 1 FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=?
      AND token_hash=? AND install_id=? AND flow_id=? AND generation=? AND owner_generation=? AND state='csrf'
      AND purpose=? AND created_ms=? AND expires_ms=? AND expires_ms>?)`,
    s.session_hash, s.csrf_hash, s.token_hash, s.install_id, s.flow_id, s.generation, s.owner_generation,
    s.purpose, s.created_ms, s.expires_ms, now);
  }
  async getConfirmation(token) {
    const now = this.now(); const { row, flow, scope } = await this.mail.readForConfirmation(token, now);
    valid(row.purpose === 'new_owner', 'not_found');
    const c = await this.first('SELECT * FROM pull_owner_changes WHERE change_id=?', row.change_id);
    const address = await this.openOwner(c, 'new');
    const fingerprint = await sha256(unbase64url(flow.candidate_public_key));
    return this.createBrowser(row, now, 'confirmation', [...this.mail.scopeStatements(scope, flow), this.changeGuard(c)],
      (csrf) => confirmationPage({ token, csrf, purpose: 'new_owner',
        owner_mask: `***@${address.slice(address.lastIndexOf('@') + 1)}`, install_id: row.install_id,
        candidate_fingerprint: fingerprint }));
  }
  async createBrowser(row, now, purpose, guards, render) {
    const count = await this.first('SELECT COUNT(*) AS n FROM pull_browser_sessions WHERE install_id=? AND expires_ms>?', row.install_id, now);
    valid(count.n < 30, 'abuse_limit');
    const s = secret(); const csrf = secret(); const end = Math.min(now + TTL, row.expires_ms);
    // Compute before writes, so HTML/crypto failure cannot strand CSRF state.
    const output = render(csrf);
    await this.db.batch([this.timeGuard(now, end), ...guards,
      this.stmt('DELETE FROM pull_browser_sessions WHERE install_id=? AND expires_ms<=?', row.install_id, now),
      this.registry.guard('(SELECT COUNT(*) FROM pull_browser_sessions WHERE install_id=?)<30', row.install_id),
      this.registry.guard("EXISTS(SELECT 1 FROM pull_tokens WHERE token_hash=? AND state='pending' AND expires_ms>?)", row.token_hash, now),
      this.stmt(`INSERT INTO pull_browser_sessions VALUES(?,?,?,?,?,?,?,?,'csrf',?,?)`,
        await hash(s), await hash(csrf), row.install_id, row.flow_id, row.token_hash, purpose, row.generation, row.owner_generation, now, end),
      this.timeGuard(now, end)]);
    output.headers.set('set-cookie', `${COOKIE}=${s}; Path=/mail; Secure; HttpOnly; SameSite=Strict; Max-Age=${Math.floor((end - now) / 1000)}`);
    return output;
  }

  // This batch retires every executable old-generation scope, including delayed
  // step-ups. Revert is the explicit exception; receipts remain inert metadata.
  retire(i, now) {
    const id = i.install_id; const g = i.generation;
    return [this.stmt("UPDATE pull_generations SET state='fenced',fenced_ms=COALESCE(fenced_ms,?) WHERE install_id=? AND generation<=?", now, id, g),
      this.stmt("UPDATE pull_flows SET state='cancelled' WHERE install_id=? AND expected_generation<=? AND state IN ('pending','approved','verified')", id, g),
      this.stmt(`UPDATE pull_tokens SET state='invalidated' WHERE install_id=? AND generation<=? AND NOT
        (purpose='revert' AND state='pending' AND expires_ms>? AND EXISTS(SELECT 1 FROM pull_owner_changes c
          WHERE c.change_id=pull_tokens.change_id AND c.install_id=pull_tokens.install_id AND c.state='completed'
          AND c.switched_owner_generation=? AND c.revert_until_ms=pull_tokens.expires_ms))`, id, g, now, i.owner_generation),
      this.stmt("UPDATE pull_browser_sessions SET state='invalidated' WHERE install_id=? AND generation<=?", id, g),
      this.stmt("UPDATE pull_tickets SET state='invalidated' WHERE install_id=? AND generation<=?", id, g),
      this.stmt("UPDATE pull_threads SET revoked_ms=COALESCE(revoked_ms,?) WHERE install_id=? AND generation<=?", now, id, g),
      this.stmt(`UPDATE pull_events SET state='revoked',text=NULL,body_bytes=0,lease_id=NULL,lease_until_ms=NULL,terminal_ms=?
        WHERE install_id=? AND generation<=? AND state IN ('pending','offered','held')`, now, id, g),
      this.stmt("UPDATE pull_dedupe SET disposition='revoked' WHERE install_id=? AND generation<=? AND disposition='pending'", id, g),
      this.stmt("UPDATE pull_intents SET state='revoked',finished_ms=? WHERE install_id=? AND generation<=? AND state='reserved'", now, id, g),
      this.stmt("UPDATE pull_intents SET state='uncertain' WHERE install_id=? AND generation<=? AND state='sending'", id, g),
      this.stmt('DELETE FROM pull_notifications WHERE install_id=?', id),
      this.stmt(`DELETE FROM pull_owner_slots WHERE install_id=? AND role='pending' AND flow_id IN
        (SELECT flow_id FROM pull_owner_changes WHERE install_id=? AND expected_generation<=? AND state='pending')`, id, id, g),
      this.stmt(`UPDATE pull_owner_changes SET state='cancelled',old_owner_sealed=NULL,new_owner_sealed=NULL
        WHERE install_id=? AND expected_generation<=? AND state='pending'`, id, g),
      this.stmt(`UPDATE pull_control_outbox SET state=CASE WHEN state='sending' THEN 'uncertain' ELSE 'expired' END,
        envelope_sealed=NULL,finished_ms=? WHERE install_id=? AND template IN ('signup','stepup','new_owner')
        AND state IN ('queued','sending','failed')`, now, id)];
  }
  nextGeneration(i, f, owner, state, now, ownerStep) {
    valid(i.generation < MAX_G && (!ownerStep || i.owner_generation < MAX_G)
      && Number.isSafeInteger(i.epoch + 1), 'stale_generation');
    return [this.stmt(`UPDATE pull_installs SET generation=?,owner_generation=?,owner_hash=?,owner_address=?,
      state=?,active_since_ms=NULL,epoch=epoch+1,connection_id=NULL,updated_ms=? WHERE install_id=?`,
    i.generation + 1, i.owner_generation + Number(ownerStep), owner.owner_hash, owner.owner_address, state, now, i.install_id), this.registry.changed(),
    this.stmt(`INSERT INTO pull_generations VALUES(?,?,?,?,?,?,?,?,'current',?,NULL)`, i.install_id,
      i.generation + 1, i.owner_generation + Number(ownerStep), owner.owner_hash, i.key_id, i.public_key, f.fence_hash, f.flow_id, now)];
  }
  async notice(c, template, address, now, end, token = secret()) {
    const target = await ownerIdentity(address); const outbox = randomId();
    const row = { outbox_id: outbox, install_id: c.install_id, flow_id: c.flow_id, template, target_hash: target.owner_hash, expires_ms: end };
    const envelope = await this.mail.sealEnvelope(row, { to: address, token, link_kind: template === 'old_notice' ? 'revert' : 'link' });
    return [this.stmt(`INSERT INTO pull_control_outbox(outbox_id,install_id,flow_id,change_id,template,target_hash,
      idempotency_key,envelope_sealed,seal_key_id,state,next_attempt_ms,created_ms,expires_ms) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)`,
    outbox, c.install_id, c.flow_id, c.change_id, template, target.owner_hash,
    `pull:control:${template}:${c.flow_id}:${c.change_id}:${target.owner_hash}`, envelope, this.mail.sealKeyId, now, now, end)];
  }
  deletedNotice(c, now) {
    const outbox = randomId();
    // No recipient survives deletion. A terminal hash-only intent plus its
    // content-free reason is observable through the existing audit schema.
    // No envelope, no lease, and no due time before expiry: never retry it.
    return [this.stmt(`INSERT INTO pull_control_outbox(outbox_id,install_id,flow_id,change_id,template,target_hash,
      idempotency_key,envelope_sealed,seal_key_id,state,next_attempt_ms,created_ms,expires_ms,finished_ms)
      VALUES(?,?,?,?,'revert_new',?,?,NULL,?,'failed',?,?,?,?)`, outbox, c.install_id, c.flow_id, c.change_id,
    c.new_owner_hash, `pull:control:revert_new:${c.flow_id}:${c.change_id}:${c.new_owner_hash}`,
    this.mail.sealKeyId, now + WEEK, now, now + WEEK, now),
    this.stmt("INSERT INTO pull_audit(install_id,at_ms,code,related_id) VALUES(?,?,'recipient_deleted',?)", c.install_id, now, outbox)];
  }
  async confirm(request, token, csrf) {
    const b = await this.browser(request, token, csrf, 'new_owner'); if (b.replay) return receipt();
    const { now, row, session } = b;
    const { flow: f } = await this.mail.readForConfirmation(token, now);
    const c = await this.first('SELECT * FROM pull_owner_changes WHERE change_id=?', row.change_id);
    const i = await this.registry.installation(row.install_id);
    valid(['active', 'verified'].includes(i?.state), 'registration_inactive');
    valid(i.generation === f.expected_generation && i.owner_generation === f.expected_owner_generation
      && i.owner_hash === f.owner_hash && i.key_id === f.candidate_key_id && i.public_key === f.candidate_public_key
      && i.generation < MAX_G && i.owner_generation < MAX_G, 'stale_generation');
    const frequency = await this.frequency(i.install_id, now);
    const newOwner = await ownerIdentity(await this.openOwner(c, 'new'));
    const oldAddress = await this.openOwner(c, 'old');
    const consume = await this.mail.consumeStatements({ method: request.method, token, csrf,
      session_secret: b.sessionSecret, request_origin: request.headers.get('origin') }, now);
    const revertToken = secret(); const end = now + WEEK;
    const revertScope = { purpose: 'revert', flow_id: f.flow_id, change_id: c.change_id, install_id: i.install_id,
      generation: i.generation + 1, owner_generation: i.owner_generation + 1, owner_hash: c.old_owner_hash,
      candidate_key_id: f.candidate_key_id, candidate_public_key: f.candidate_public_key, expires_ms: end };
    const result = { install_id: i.install_id, generation: i.generation + 1, owner_generation: i.owner_generation + 1,
      state: 'verified', change_id: c.change_id };
    const until = Math.min(row.expires_ms, session.expires_ms);
    await this.db.batch([this.timeGuard(now, until), ...consume.statements, this.installGuard(i), this.epochGuard(i),
      this.currentGuard(i), this.flowGuard(f), this.changeGuard(c), frequency,
      ...this.registry.activateSlotStatements({ owner_hash: c.new_owner_hash, install_id: i.install_id, flow_id: f.flow_id }, now),
      ...this.registry.reserveSlotStatements({ owner_hash: c.old_owner_hash, install_id: i.install_id, flow_id: f.flow_id,
        role: 'revert', until_ms: end }, now),
      this.stmt("DELETE FROM pull_owner_slots WHERE owner_hash=? AND install_id=? AND role='active'", c.old_owner_hash, i.install_id),
      this.stmt(`UPDATE pull_owner_changes SET state='completed',completed_ms=?,revert_until_ms=?,switched_generation=?,
        switched_owner_generation=?,new_owner_sealed=NULL WHERE change_id=?`, now, end, i.generation + 1, i.owner_generation + 1, c.change_id), this.registry.changed(),
      ...this.retire(i, now), ...this.nextGeneration(i, f, newOwner, 'verified', now, true),
      this.stmt("UPDATE pull_flows SET state='completed',result_json=?,expires_ms=? WHERE flow_id=?", JSON.stringify(result), now + FINAL_ENABLE_TTL, f.flow_id), this.registry.changed(),
      this.stmt(`INSERT INTO pull_tokens(token_hash,install_id,flow_id,change_id,purpose,generation,owner_generation,owner_hash,
        binding_digest,state,created_ms,expires_ms) VALUES(?,?,?,?,'revert',?,?,?,?,'pending',?,?)`,
      await hash(revertToken), i.install_id, f.flow_id, c.change_id, revertScope.generation, revertScope.owner_generation,
      c.old_owner_hash, await verificationBinding(revertScope), now, end),
      ...await this.notice(c, 'old_notice', oldAddress, now, end, revertToken), this.timeGuard(now, until)]);
    return receipt();
  }

  async revertScope(token, now) {
    valid(match(H, token));
    const row = await this.first('SELECT * FROM pull_tokens WHERE token_hash=?', await hash(token));
    valid(row?.purpose === 'revert', 'not_found'); valid(row.expires_ms > now, 'token_expired');
    valid(row.state === 'pending', 'change_superseded');
    const c = await this.first('SELECT * FROM pull_owner_changes WHERE change_id=?', row.change_id);
    const f = c && await this.first('SELECT * FROM pull_flows WHERE flow_id=?', c.flow_id);
    const i = c && await this.registry.installation(c.install_id);
    valid(c && f && i && c.state === 'completed' && c.revert_until_ms > now && c.revert_until_ms === row.expires_ms
      && c.install_id === row.install_id && c.flow_id === row.flow_id && c.old_owner_hash === row.owner_hash
      && c.switched_generation === row.generation && c.switched_owner_generation === row.owner_generation
      && i.owner_generation === c.switched_owner_generation && i.owner_hash === c.new_owner_hash, 'change_superseded');
    valid(row.binding_digest === await verificationBinding({ purpose: 'revert', flow_id: c.flow_id, change_id: c.change_id,
      install_id: c.install_id, generation: c.switched_generation, owner_generation: c.switched_owner_generation,
      owner_hash: c.old_owner_hash, candidate_key_id: f.candidate_key_id, candidate_public_key: f.candidate_public_key,
      expires_ms: c.revert_until_ms }), 'change_superseded');
    return { row, c, f, i };
  }
  async getRevert(token) {
    const now = this.now(); const { row, c, f, i } = await this.revertScope(token, now);
    const address = await this.openOwner(c, 'old');
    // Only registry-derived, escaped display values; no agent-provided copy.
    const mask = `***@${address.slice(address.lastIndexOf('@') + 1)}`.replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    const fingerprint = await sha256(unbase64url(f.candidate_public_key));
    return this.createBrowser(row, now, 'revert', [this.changeGuard(c), this.installGuard(i), this.flowGuard(f)],
      (csrf) => page(`<h1>Freeze and revert owner change</h1><p>Owner: ${mask}</p><p>Installation: ${i.install_id}</p>`
        + `<p>Candidate key: ${fingerprint}</p>`
        + `<form method="post" action="/mail/revert"><input type="hidden" name="token" value="${token}">`
        + `<input type="hidden" name="csrf" value="${csrf}"><button type="submit">Freeze and revert</button></form>`));
  }
  async revert(request, token, csrf) {
    const b = await this.browser(request, token, csrf, 'revert'); if (b.replay) return receipt();
    const { now, session } = b; const { row, c, f, i } = await this.revertScope(token, now);
    const address = await this.openOwner(c, 'old');
    const owner = await ownerIdentity(address);
    const newAddress = i.owner_address;
    valid(newAddress !== null || i.state === 'deleted', 'storage_unavailable');
    const result = { install_id: i.install_id, generation: i.generation + 1, owner_generation: i.owner_generation + 1,
      state: 'frozen', change_id: c.change_id };
    const until = Math.min(row.expires_ms, session.expires_ms);
    await this.db.batch([this.timeGuard(now, until), this.installGuard(i), this.epochGuard(i), this.changeGuard(c), this.flowGuard(f),
      this.sessionGuard(session, now), this.currentGuard(i, ['disabled', 'deleted'].includes(i.state) ? 'fenced' : 'current'),
      this.registry.guard("EXISTS(SELECT 1 FROM pull_owner_slots WHERE owner_hash=? AND install_id=? AND role='revert' AND flow_id=? AND until_ms=?)",
        c.old_owner_hash, i.install_id, f.flow_id, c.revert_until_ms),
      this.stmt(`UPDATE pull_tokens SET state='consumed',consumed_ms=?,result_json='{"state":"confirmed"}'
        WHERE token_hash=? AND state='pending' AND binding_digest=? AND expires_ms>?`, now, row.token_hash, row.binding_digest, now), this.registry.changed(),
      ...this.retire(i, now), ...this.nextGeneration(i, f, owner, 'frozen', now, true),
      this.stmt("DELETE FROM pull_owner_slots WHERE install_id=? AND owner_hash=? AND role IN ('active','pending')", i.install_id, c.new_owner_hash),
      this.stmt("INSERT INTO pull_owner_slots VALUES(?,?,'active',?,NULL) ON CONFLICT(owner_hash,install_id,role) DO UPDATE SET flow_id=excluded.flow_id",
        c.old_owner_hash, i.install_id, f.flow_id),
      ...this.registry.releaseSlotStatements({ owner_hash: c.old_owner_hash, install_id: i.install_id, flow_id: f.flow_id, role: 'revert' }),
      this.stmt("UPDATE pull_addresses SET state='assigned',until_ms=NULL WHERE install_id=? AND address_local=?", i.install_id, i.address_local), this.registry.changed(),
      this.stmt("UPDATE pull_owner_changes SET state='reverted',reverted_ms=? WHERE change_id=?", now, c.change_id), this.registry.changed(),
      this.stmt("UPDATE pull_flows SET state='reverted',result_json=? WHERE flow_id=?", JSON.stringify(result), f.flow_id), this.registry.changed(),
      ...await this.notice(c, 'revert_old', address, now, now + WEEK),
      ...(newAddress === null ? this.deletedNotice(c, now) : await this.notice(c, 'revert_new', newAddress, now, now + WEEK)),
      this.timeGuard(now, until)]);
    return receipt();
  }
  async resume(request, v) {
    const digest = await hash('resume');
    if (await this.replay(request, v, 'resume', '/mail/resume', digest)) return receipt();
    const scope = await this.authorizeSession(request, v.csrf, 'resume');
    const { install: i, flow: f, session: s } = scope; const now = this.now();
    valid(['frozen', 'disabled'].includes(i.state), 'registration_inactive');
    const result = { install_id: i.install_id, generation: i.generation + 1, owner_generation: i.owner_generation,
      state: 'verified', change_id: null };
    await this.db.batch([this.timeGuard(now, s.expires_ms), ...scope.statements, this.epochGuard(i),
      this.currentGuard(i, i.state === 'disabled' ? 'fenced' : 'current'),
      ...this.retire(i, now), ...this.nextGeneration(i, f, i, 'verified', now, false),
      this.stmt("UPDATE pull_flows SET state='completed',result_json=?,expires_ms=? WHERE flow_id=?", JSON.stringify(result), now + FINAL_ENABLE_TTL, f.flow_id), this.registry.changed(),
      this.operation(i, '/mail/resume', v.op_id, digest, result, now), this.timeGuard(now, s.expires_ms)]);
    return receipt();
  }
  async cancel(request, v) {
    const now = this.now();
    const s = await this.first('SELECT * FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=?',
      await hash(cookieSecret(request)), await hash(v.csrf));
    valid(request.headers.get('origin') === this.serviceOrigin && s?.purpose === 'owner_change'
      && s.created_ms <= now && s.expires_ms > now && s.expires_ms - s.created_ms <= TTL, 'session_required');
    const c = await this.first('SELECT * FROM pull_owner_changes WHERE change_id=? AND flow_id=?', v.change_id, s.flow_id);
    valid(c && c.install_id === s.install_id, 'change_superseded');
    valid(['pending', 'cancelled'].includes(c.state), 'change_superseded');
    if (c.state === 'cancelled') return receipt();
    const scope = await this.authorizeSession(request, v.csrf, 'owner_change');
    await this.db.batch([this.timeGuard(now, scope.session.expires_ms), ...scope.statements, this.changeGuard(c),
      this.stmt("UPDATE pull_owner_changes SET state='cancelled',old_owner_sealed=NULL,new_owner_sealed=NULL WHERE change_id=?", c.change_id), this.registry.changed(),
      ...this.registry.releaseSlotStatements({ owner_hash: c.new_owner_hash, install_id: c.install_id, flow_id: c.flow_id, role: 'pending' }),
      this.stmt("UPDATE pull_tokens SET state='invalidated' WHERE change_id=? AND purpose='new_owner'", c.change_id),
      this.stmt("UPDATE pull_browser_sessions SET state='invalidated' WHERE token_hash IN (SELECT token_hash FROM pull_tokens WHERE change_id=?)", c.change_id),
      this.stmt("UPDATE pull_flows SET state='cancelled' WHERE flow_id=?", c.flow_id), this.registry.changed(),
      this.stmt("UPDATE pull_browser_sessions SET state='invalidated' WHERE flow_id=?", c.flow_id),
      this.stmt("UPDATE pull_tokens SET state='invalidated' WHERE flow_id=? AND purpose='stepup'", c.flow_id),
      this.stmt("UPDATE pull_control_outbox SET state='expired',envelope_sealed=NULL,finished_ms=? WHERE change_id=? AND state!='accepted'", now, c.change_id),
      this.timeGuard(now, scope.session.expires_ms)]);
    return receipt();
  }

  async handle(request, { trustedSource } = {}) {
    try {
      const url = new URL(request.url); const path = url.pathname;
      valid(url.origin === this.serviceOrigin && OWNER_CHANGE_PATHS.includes(path), 'not_found');
      await this.sourceLimit(trustedSource, this.now());
      valid(!request.url.includes('#') && !request.headers.has('content-encoding'));
      valid([...request.headers].reduce((n, [k, v]) => n + bytes(k).length + bytes(v).length + 4, 0) <= 8192, 'body_limit');
      if (request.method === 'GET' && ['/mail/link', '/mail/revert'].includes(path)) {
        valid(!request.body && /^\?token=[a-f0-9]{64}$/.test(url.search));
        return path === '/mail/link' ? await this.getConfirmation(url.search.slice(7)) : await this.getRevert(url.search.slice(7));
      }
      valid(request.method === 'POST' && !request.url.includes('?'));
      valid(request.headers.get('origin') === this.serviceOrigin, 'csrf_failed');
      valid(/^application\/x-www-form-urlencoded(?:;\s*charset=utf-8)?$/i.test(request.headers.get('content-type') ?? ''));
      const length = request.headers.get('content-length');
      valid(length === null || /^(0|[1-9][0-9]*)$/.test(length) && Number(length) <= 4096, 'body_limit');
      const fields = path === '/mail/owner-change' ? ['csrf', 'op_id', 'new_owner'] : path === '/mail/resume' ? ['csrf', 'op_id']
        : path === '/mail/change/cancel' ? ['csrf', 'change_id'] : ['csrf', 'token'];
      const v = form(await readRequestBody(request.body, { limit: 4096 }), fields);
      if (path === '/mail/owner-change') return await this.initiate(request, v);
      if (path === '/mail/resume') return await this.resume(request, v);
      if (path === '/mail/change/cancel') return await this.cancel(request, v);
      if (path === '/mail/link/confirm') return await this.confirm(request, v.token, v.csrf);
      if (path === '/mail/revert') return await this.revert(request, v.token, v.csrf);
      throw new Error('invalid_request');
    } catch (error) {
      let code = codes.has(error?.message) ? error.message : 'storage_unavailable';
      if (!codes.has(error?.message)) console.error('[agent-mail] owner_change_failed');
      try {
        if (!['storage_unavailable', 'service_unavailable'].includes(code) && !await this.refusalLimit(this.now())) code = 'abuse_limit';
        const status = codes.get(code); const output = page(`<p>${code}</p>`, status);
        if ([429, 503].includes(status)) output.headers.set('retry-after', String(code === 'verification_limit'
          ? Math.ceil(((Math.floor(this.now() / DAY) + 1) * DAY - this.now()) / 1000) : 60));
        return output;
      } catch {
        console.error('[agent-mail] owner_change_response_failed'); return page('<p>service_unavailable</p>', 503);
      }
    }
  }
}
