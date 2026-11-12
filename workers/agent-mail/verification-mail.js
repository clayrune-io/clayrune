// Pull v1 verification primitives (contract 4.1 / 8.1). No HTTP dispatch or
// provider calls. Batch returned statements with the caller's flow/operation/
// lifecycle writes; never execute a prefix or translate a failed guard to success.
import { bytes, randomId, sha256, unbase64url } from './signing.js';
import { origin } from './enrollment-transcript.js';
import { OwnerRegistry, ownerIdentity } from './owner-registry.js';
import { parseAddrSpec } from './mailbox.js';

export const VERIFICATION_TTL = 30 * 60_000;
const I = /^[a-f0-9]{32}$/;
const H = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const PURPOSE = { signup: 'signup', stepup: 'stepup', new_owner: 'new_owner' };
const TEMPLATES = Object.freeze({
  signup: ['Confirm your Clayrune email', 'Confirm this email address for your Clayrune installation.'],
  stepup: ['Confirm your Clayrune management request', 'Confirm the management request you started in Clayrune.'],
  new_owner: ['Confirm your new Clayrune email', 'Confirm this email address for the Clayrune owner change you requested.'],
  old_notice: ['Your Clayrune owner email changed', 'Your Clayrune owner email changed. Use this link to freeze and revert that change.'],
  revert_old: ['Your Clayrune owner change was reverted', 'Your Clayrune owner change was reverted and the installation is frozen.'],
  revert_new: ['Your Clayrune owner change was reverted', 'Your Clayrune owner change was reverted and the installation is frozen.'],
});
const MANAGEMENT_LABELS = Object.freeze({ owner_change: 'Confirm owner email change',
  key_recovery: 'Confirm mail key recovery', resume: 'Confirm mail resume',
  rotate: 'Confirm mail key rotation', delete: 'Confirm mail deletion request' });
const valid = (yes, code = 'invalid_request') => { if (!yes) throw new Error(code); };
const match = (re, s) => typeof s === 'string' && re.test(s);
const secret = () => randomId() + randomId();
const hashSecret = (s) => { valid(match(H, s)); return sha256(bytes(s)); };
const timestamp = (n) => Number.isSafeInteger(n) && !Object.is(n, -0) && n >= 0;
const generation = (n) => Number.isSafeInteger(n) && n > 0 && n <= 999_999_999_999_999;
const esc = (s) => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const receipt = Object.freeze({ state: 'confirmed' });

function binding(flow, template, change, now) {
  valid(timestamp(now) && flow && match(I, flow.install_id) && match(I, flow.flow_id)
    && generation(flow.expected_generation) && generation(flow.expected_owner_generation)
    && match(H, flow.owner_hash) && match(ID, flow.candidate_key_id));
  valid(typeof flow.candidate_public_key === 'string' && unbase64url(flow.candidate_public_key).length === 32);
  valid(Object.hasOwn(PURPOSE, template));
  const newOwner = template === 'new_owner';
  valid(template === 'signup' ? flow.purpose === 'signup' : flow.purpose !== 'signup');
  valid(['signup', 'owner_change', 'key_recovery', 'resume', 'rotate', 'delete'].includes(flow.purpose));
  valid(!newOwner || flow.purpose === 'owner_change');
  valid(newOwner ? change && match(I, change.change_id) && change.install_id === flow.install_id
    && change.flow_id === flow.flow_id && change.old_owner_hash === flow.owner_hash
    && match(H, change.new_owner_hash) && change.new_owner_hash !== change.old_owner_hash
    && change.expected_generation === flow.expected_generation
    && change.expected_owner_generation === flow.expected_owner_generation : change === null);
  const deadline = newOwner ? change : flow;
  valid(timestamp(deadline.created_ms) && timestamp(deadline.expires_ms)
    && deadline.created_ms <= now && deadline.expires_ms - deadline.created_ms === VERIFICATION_TTL);
  valid(deadline.expires_ms > now, 'token_expired');
  valid(flow.expires_ms === deadline.expires_ms);
  return { install_id: flow.install_id, flow_id: flow.flow_id, change_id: change?.change_id ?? null,
    purpose: PURPOSE[template], generation: flow.expected_generation,
    owner_generation: flow.expected_owner_generation,
    owner_hash: newOwner ? change.new_owner_hash : flow.owner_hash,
    candidate_key_id: flow.candidate_key_id, candidate_public_key: flow.candidate_public_key,
    expires_ms: deadline.expires_ms };
}

export async function verificationBinding(s) {
  valid(s && ['signup', 'stepup', 'new_owner', 'revert'].includes(s.purpose)
    && match(I, s.flow_id) && match(I, s.install_id) && match(H, s.owner_hash)
    && generation(s.generation) && generation(s.owner_generation) && timestamp(s.expires_ms)
    && match(ID, s.candidate_key_id) && typeof s.candidate_public_key === 'string'
    && unbase64url(s.candidate_public_key).length === 32
    && (['new_owner', 'revert'].includes(s.purpose) ? match(I, s.change_id) : s.change_id === null));
  return sha256(bytes(JSON.stringify(['clayrune-mail-pull-link', '1', s.purpose, s.flow_id,
    s.change_id ?? '', s.install_id, String(s.generation), String(s.owner_generation),
    s.owner_hash, s.candidate_key_id, s.candidate_public_key, String(s.expires_ms)])));
}

function aad(row) {
  valid(match(I, row.outbox_id) && match(I, row.install_id) && match(I, row.flow_id)
    && Object.hasOwn(TEMPLATES, row.template) && match(H, row.target_hash) && timestamp(row.expires_ms));
  return bytes(JSON.stringify(['clayrune-mail-pull-control', '1', row.outbox_id, row.install_id,
    row.flow_id, row.template, row.target_hash, String(row.expires_ms)]));
}

/** Only generated links and fixed text. Extra caller subject/body properties
 * are never copied into a message. Sender/Message-ID/provider policy is unit 10.
 */
export function verificationMessage(serviceOrigin, template, envelope) {
  valid(origin(serviceOrigin) && Object.hasOwn(TEMPLATES, template));
  const [subject, text] = TEMPLATES[template];
  const hasLink = !['revert_old', 'revert_new'].includes(template);
  valid(envelope && match(H, envelope.token) && envelope.link_kind === (template === 'old_notice' ? 'revert' : 'link'));
  const recipient = parseAddrSpec(envelope.to);
  valid(recipient && recipient.address === envelope.to);
  const link = `${serviceOrigin}/mail/${envelope.link_kind}?token=${envelope.token}`;
  const guidance = template === 'old_notice'
    ? 'If you did not authorize this change, open the link and explicitly confirm freeze and revert.'
    : hasLink ? 'If you did not request this, do not confirm.' : 'Mail remains frozen until you explicitly resume it in Clayrune.';
  return { to: recipient.address, subject, body: `${text}${hasLink ? `\n\n${link}` : ''}\n\n${guidance}` };
}

/** Render after the HTTP unit has created bounded, token-bound CSRF state.
 * Display values come from verified registry snapshots, never agent content.
 * Rendering and readForConfirmation are inert: no token/lifecycle writes.
 */
export function confirmationPage({ token, csrf, purpose, flow_purpose, owner_mask, install_id, candidate_fingerprint }) {
  valid(match(H, token) && match(H, csrf) && Object.hasOwn(PURPOSE, purpose)
    && match(I, install_id) && match(H, candidate_fingerprint)
    && typeof owner_mask === 'string' && bytes(owner_mask).length <= 254 && !/[\x00-\x1f\x7f]/.test(owner_mask));
  valid(purpose !== 'stepup' || Object.hasOwn(MANAGEMENT_LABELS, flow_purpose));
  const label = purpose === 'stepup' ? MANAGEMENT_LABELS[flow_purpose]
    : { signup: 'Confirm email', new_owner: 'Confirm new owner email' }[purpose];
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><title>${label}</title>`
    + `<h1>${label}</h1><p>Owner: ${esc(owner_mask)}</p><p>Installation: ${install_id}</p>`
    + `<p>Candidate key: ${candidate_fingerprint}</p><form method="post" action="/mail/link/confirm">`
    + `<input type="hidden" name="token" value="${token}"><input type="hidden" name="csrf" value="${csrf}">`
    + `<button type="submit">${label}</button></form></html>`, { headers: {
      'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
      'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    } });
}

export class VerificationMail {
  constructor({ db, serviceOrigin, sealKeyId, sealKey }) {
    valid(origin(serviceOrigin) && match(ID, sealKeyId) && sealKey instanceof Uint8Array && sealKey.length === 32);
    Object.assign(this, { db, serviceOrigin, sealKeyId });
    // Provisioning belongs to the operator; import only, never create a key.
    this.key = crypto.subtle.importKey('raw', sealKey, 'AES-GCM', false, ['encrypt', 'decrypt']);
    this.registry = new OwnerRegistry(db);
  }
  stmt(sql, ...args) { return this.registry.stmt(sql, ...args); }

  async sealEnvelope(row, envelope) {
    const target = await ownerIdentity(envelope.to);
    valid(target.owner_address === envelope.to && target.owner_hash === row.target_hash
      && match(H, envelope.token) && envelope.link_kind === (row.template === 'old_notice' ? 'revert' : 'link')
      && Object.keys(envelope).sort().join(',') === 'link_kind,to,token');
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: aad(row) },
      await this.key, bytes(JSON.stringify(envelope))));
    const blob = new Uint8Array(iv.length + encrypted.length); blob.set(iv); blob.set(encrypted, iv.length);
    return blob;
  }

  async openEnvelope(row) {
    valid(row.seal_key_id === this.sealKeyId && row.envelope_sealed instanceof Uint8Array
      && row.envelope_sealed.length >= 28 && row.envelope_sealed.length <= 1024, 'invalid_state');
    const blob = row.envelope_sealed;
    const clear = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: aad(row) },
      await this.key, blob.slice(12));
    const envelope = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(clear));
    valid(envelope && typeof envelope === 'object' && !Array.isArray(envelope)
      && Object.keys(envelope).sort().join(',') === 'link_kind,to,token'
      && match(H, envelope.token) && envelope.link_kind === (row.template === 'old_notice' ? 'revert' : 'link'), 'invalid_state');
    const target = await ownerIdentity(envelope.to);
    valid(target.owner_address === envelope.to && target.owner_hash === row.target_hash, 'invalid_state');
    return envelope;
  }

  // Guards repeat the immutable binding inside the committing batch, closing
  // read/crypto-to-write races with cancellation, key change and owner switch.
  scopeStatements(s, flow) {
    const r = this.registry;
    const newOwner = s.purpose === 'new_owner';
    return [r.guard(`EXISTS (SELECT 1 FROM pull_flows f JOIN pull_installs i ON i.install_id=f.install_id
      WHERE f.flow_id=? AND f.install_id=? AND f.purpose=? AND f.state=? AND f.owner_hash=?
      AND f.expected_generation=? AND f.expected_owner_generation=? AND f.candidate_key_id=?
      AND f.candidate_public_key=? AND f.expires_ms=? AND i.generation=f.expected_generation
      AND i.owner_generation=f.expected_owner_generation AND i.owner_hash=f.owner_hash
      AND i.state!='deleted' AND (?='stepup' OR i.state!='frozen')
      AND (?!='signup' OR i.state='pending')
      AND NOT EXISTS (SELECT 1 FROM pull_fences z WHERE z.install_id=f.install_id
        AND z.generation=f.expected_generation AND (z.mode='revoke' OR z.flow_id=f.flow_id)))`,
    s.flow_id, s.install_id, flow.purpose, newOwner ? 'approved' : 'pending', flow.owner_hash,
    s.generation, s.owner_generation, s.candidate_key_id, s.candidate_public_key, s.expires_ms, s.purpose, s.purpose),
    ...(newOwner ? [r.guard(`EXISTS (SELECT 1 FROM pull_owner_changes WHERE change_id=? AND flow_id=?
      AND install_id=? AND state='pending' AND old_owner_hash=? AND new_owner_hash=?
      AND expected_generation=? AND expected_owner_generation=? AND expires_ms=?)`,
    s.change_id, s.flow_id, s.install_id, flow.owner_hash, s.owner_hash, s.generation, s.owner_generation, s.expires_ms)] : [])];
  }

  /** Caller inserts the immutable flow/change before these statements. Exact
   * durable replay returns no secret/statements and charges nothing. Concurrent
   * duplicate inserts fail UNIQUE and roll back the ENTIRE caller batch; retry
   * resolves that persisted row, never makes a replacement token/envelope.
   */
  async issueStatements({ outbox_id, template, to, flow, change = null }, now) {
    valid(match(I, outbox_id));
    const s = binding(flow, template, change, now);
    const target = await ownerIdentity(to);
    valid(target.owner_hash === s.owner_hash);
    const digest = await verificationBinding(s);
    const key = `pull:control:${template}:${s.flow_id}:${s.change_id ?? ''}:${s.owner_hash}`;
    const previous = await this.stmt('SELECT * FROM pull_control_outbox WHERE outbox_id=? OR idempotency_key=?', outbox_id, key).first();
    if (previous) {
      valid(previous.outbox_id === outbox_id && previous.idempotency_key === key
        && previous.install_id === s.install_id && previous.flow_id === s.flow_id
        && previous.change_id === s.change_id && previous.template === template
        && previous.target_hash === s.owner_hash && previous.expires_ms === s.expires_ms, 'operation_conflict');
      const persisted = await this.stmt(`SELECT 1 FROM pull_tokens WHERE install_id=? AND flow_id=? AND change_id IS ?
        AND purpose=? AND generation=? AND owner_generation=? AND owner_hash=? AND binding_digest=? AND expires_ms=?`,
      s.install_id, s.flow_id, s.change_id, s.purpose, s.generation, s.owner_generation, s.owner_hash, digest, s.expires_ms).first();
      valid(persisted, 'operation_conflict');
      return { replay: true, outbox_id, statements: [] };
    }
    const token = secret();
    const tokenHash = await hashSecret(token);
    const row = { outbox_id, install_id: s.install_id, flow_id: s.flow_id, template,
      target_hash: s.owner_hash, expires_ms: s.expires_ms };
    const envelope = await this.sealEnvelope(row, { to: target.owner_address, token, link_kind: 'link' });
    const r = this.registry;
    return { replay: false, outbox_id, statements: [
      ...this.scopeStatements(s, flow),
      ...r.verificationStatements({ install_id: s.install_id, target_hash: s.owner_hash }, now),
      this.stmt(`INSERT INTO pull_tokens(token_hash,install_id,flow_id,change_id,purpose,generation,owner_generation,
        owner_hash,binding_digest,state,created_ms,expires_ms) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)`,
      tokenHash, s.install_id, s.flow_id, s.change_id, s.purpose, s.generation, s.owner_generation,
      s.owner_hash, digest, now, s.expires_ms),
      this.stmt(`INSERT INTO pull_control_outbox(outbox_id,install_id,flow_id,change_id,template,target_hash,
        idempotency_key,envelope_sealed,seal_key_id,state,next_attempt_ms,created_ms,expires_ms)
        VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)`,
      outbox_id, s.install_id, s.flow_id, s.change_id, template, s.owner_hash, key, envelope, this.sealKeyId, now, now, s.expires_ms),
    ] };
  }

  /** No state changes or sends: safe for scanner GETs. HTTP adapters own exact
   * query/form parsing, source abuse limits, CSRF cookie creation and error HTML.
   */
  async readForConfirmation(token, now) {
    valid(timestamp(now));
    const row = await this.stmt('SELECT * FROM pull_tokens WHERE token_hash=?', await hashSecret(token)).first();
    valid(row, 'not_found');
    valid(row.expires_ms > now, 'token_expired');
    valid(row.state === 'pending', 'already_sent');
    valid(Object.hasOwn(PURPOSE, row.purpose), 'invalid_state');
    const flow = await this.stmt('SELECT * FROM pull_flows WHERE flow_id=?', row.flow_id).first();
    const change = row.change_id === null ? null : await this.stmt('SELECT * FROM pull_owner_changes WHERE change_id=?', row.change_id).first();
    const s = binding(flow, row.purpose, change, now);
    valid(row.binding_digest === await verificationBinding(s)
      && row.install_id === s.install_id && row.generation === s.generation
      && row.owner_generation === s.owner_generation && row.owner_hash === s.owner_hash
      && row.expires_ms === s.expires_ms, 'stale_generation');
    return { row, flow, scope: s };
  }

  /** Single-use POST consumption only. The caller MUST append its signup
   * verification / owner-switch / session-promotion writes in this SAME batch.
   * A consumed token refuses a second consume; the hosted HTTP unit may instead
   * display its durable fixed receipt after validating that original session.
   */
  async consumeStatements({ method, token, session_secret, csrf, request_origin }, now) {
    valid(method === 'POST' && request_origin === this.serviceOrigin, 'unauthorized');
    const { row, flow, scope: s } = await this.readForConfirmation(token, now);
    const sessionHash = await hashSecret(session_secret);
    const csrfHash = await hashSecret(csrf);
    const result = JSON.stringify(receipt);
    const r = this.registry;
    return { receipt, purpose: row.purpose, flow_id: row.flow_id, statements: [
      ...this.scopeStatements(s, flow),
      r.guard(`EXISTS (SELECT 1 FROM pull_browser_sessions WHERE session_hash=? AND csrf_hash=?
        AND token_hash=? AND install_id=? AND flow_id=? AND generation=? AND owner_generation=?
        AND purpose='confirmation' AND state='csrf' AND created_ms<=? AND expires_ms>?
        AND expires_ms-created_ms<=600000)`,
      sessionHash, csrfHash, row.token_hash, row.install_id, row.flow_id, row.generation, row.owner_generation, now, now),
      this.stmt(`UPDATE pull_tokens SET state='consumed',consumed_ms=?,result_json=?
        WHERE token_hash=? AND state='pending' AND expires_ms>? AND binding_digest=?`,
      now, result, row.token_hash, now, row.binding_digest), r.changed(),
    ] };
  }

  /** Retry reads the original sealed envelope; never issues or charges again.
   * Unit 10 owns leasing, positive pre-submission retry proof and status/purge.
   */
  async message(outbox_id, now) {
    valid(match(I, outbox_id) && timestamp(now));
    const row = await this.stmt('SELECT * FROM pull_control_outbox WHERE outbox_id=?', outbox_id).first();
    valid(row, 'not_found');
    valid(row.expires_ms > now, 'token_expired');
    valid(['queued', 'sending'].includes(row.state), 'invalid_state');
    return verificationMessage(this.serviceOrigin, row.template, await this.openEnvelope(row));
  }
}
