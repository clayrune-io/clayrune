// Owner-only outbound. No raw MIME, destination or callback accepted from clients.
import { bytes, sha256 } from './signing.js';
import { parseAddrSpec } from './mailbox.js';
import { cloudflareEmailSending } from './email-sending.js';
const ID = /^[A-Za-z0-9_-]{1,128}$/;
export function validateSend(value) {
  const fields = ['intent_id', 'thread_id', 'reply_token', 'subject', 'body', 'kind'];
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some((k) => !fields.includes(k))
      || typeof value.intent_id !== 'string' || !ID.test(value.intent_id)
      || typeof value.thread_id !== 'string' || !ID.test(value.thread_id)
      || typeof value.reply_token !== 'string' || !/^[a-f0-9]{32}$/.test(value.reply_token) || !['note', 'question'].includes(value.kind)
      || typeof value.subject !== 'string' || !value.subject.trim() || /[\r\n\x00-\x1f\x7f]/.test(value.subject)
      || bytes(value.subject).length > 200 || typeof value.body !== 'string'
      || !value.body.trim() || bytes(value.body).length > 16_384 || value.body.includes('\0')) return false;
  return true;
}
function b64(s) { return btoa(String.fromCharCode(...bytes(s))); }
function subjectWords(subject) {
  const chunks = []; let chunk = '';
  for (const point of subject) {
    if (bytes(chunk + point).length > 45) { chunks.push(chunk); chunk = ''; }
    chunk += point;
  }
  if (chunk) chunks.push(chunk);
  return chunks.map((s) => `=?UTF-8?B?${b64(s)}?=`).join('\r\n ');
}
export function constructMail(reg, value, message_id, config) {
  const owner = parseAddrSpec(reg.owner_address);
  const from = parseAddrSpec(config.from);
  if (!owner || !from || from.domain !== config.domain || !/^[a-z0-9][a-z0-9._-]{0,30}$/.test(reg.address_local)
      || !/^[a-z0-9.-]+$/.test(config.domain)) throw new Error('invalid_service_address');
  const reply = `${reg.address_local}+${value.reply_token}@${config.domain}`;
  if (!/^[a-f0-9]{32}$/.test(value.reply_token) || !parseAddrSpec(reply)) throw new Error('invalid_service_address');
  return { from: config.from, to: reg.owner_address,
    raw: [`From: ${config.from}`, `To: ${reg.owner_address}`, `Reply-To: ${reply}`,
      `Message-ID: <${message_id}>`, `Subject: ${subjectWords(value.subject)}`,
      'MIME-Version: 1.0', 'Content-Type: text/plain; charset=utf-8',
      'Content-Transfer-Encoding: base64', 'Auto-Submitted: auto-generated', 'X-Clayrune-Agent-Mail: 1', '',
      b64(value.body).match(/.{1,76}/g).join('\r\n'), ''].join('\r\n') };
}
export async function outbound({ ledger, reg, value, body, config, send, now }) {
  if (!validateSend(value)) return { status: 400, code: 'invalid_send' };
  if (!reg.active || !reg.owner_verified) return { status: 409, code: 'registration_inactive' };
  const digest = await sha256(body);
  const prior = await ledger.intent(reg, value.intent_id);
  if (prior) return prior.digest !== digest ? { status: 409, code: 'intent_conflict' }
    : { status: prior.state === 'accepted' ? 200 : 409, code: prior.state, message_id: prior.message_id };
  // Validate service configuration before reserving an irreversible send attempt.
  const message_id = `${await sha256(bytes(`${reg.install_id}:${reg.generation}:${value.intent_id}`))}@${config.domain}`;
  const mail = constructMail(reg, value, message_id, config);
  if (!await ledger.limit(`send:${reg.install_id}`, ledger.policy.sendsPerDay, now)
      || !await ledger.limit('send:global', ledger.policy.globalSendsPerDay, now)) return { status: 429, code: 'send_limit' };
  let reserved;
  try {
    reserved = await ledger.reserveSend(reg, { ...value, digest, message_id,
      token_hash: await sha256(bytes(value.reply_token)) }, now);
  } catch (error) {
    // A racing identical reservation owns the send. Any other constraint/storage
    // failure propagates; no mail may leave without both durable records.
    const raced = await ledger.intent(reg, value.intent_id);
    if (!raced) throw error;
    return { status: 409, code: raced.digest === digest ? raced.state : 'intent_conflict' };
  }
  if (!reserved) return { status: 409, code: 'registration_inactive' };
  const fresh = await ledger.registration(reg.install_id);
  if (!fresh?.active || !fresh.owner_verified || fresh.generation !== reg.generation) {
    await ledger.finishSend(reg, value.intent_id, 'revoked', now);
    return { status: 409, code: 'revoked' };
  }
  try {
    await send(mail);
  } catch {
    // A throw can follow provider acceptance. Never retry an ambiguous send.
    await ledger.finishSend(reg, value.intent_id, 'uncertain', now);
    return { status: 409, code: 'uncertain', message_id };
  }
  await ledger.finishSend(reg, value.intent_id, 'accepted', now);
  return { status: 200, code: 'accepted', message_id };
}

/** Pull-only entry for unit14. ledger is Ledger.pull(), binding is the reviewed
 * structured Email Sending binding. value includes op_id; body is the exact
 * authenticated request bytes. The legacy entry above remains until route cutover.
 */
export async function pullOutbound({ ledger, reg, value, body, config, binding, clock = Date.now, timers = globalThis }) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { status: 400, code: 'invalid_send' };
  const { op_id, ...payload } = value;
  if (typeof op_id !== 'string' || !/^[a-f0-9]{32}$/.test(op_id) || !validateSend(payload)
      || [payload.subject, payload.body].some((s) => new TextDecoder().decode(bytes(s)) !== s)) {
    return { status: 400, code: 'invalid_send' };
  }
  reg = Object.freeze({ ...reg });
  value = Object.freeze(payload);
  if (![true, 1].includes(reg.active) || ![true, 1].includes(reg.owner_verified)) {
    return { status: 409, code: 'registration_inactive' };
  }
  const digest = await sha256(new Uint8Array(body));
  const receipt = (row) => ({ status: row.state === 'accepted' ? 200 : 409, code: row.state,
    intent_id: row.intent_id, thread_id: row.thread_id, message_id: row.message_id,
    state: row.state, provider_id: row.provider_id });
  try {
    const prior = await ledger.intent(reg, value.intent_id);
    if (prior) return prior.digest === digest ? receipt(prior) : { status: 409, code: 'intent_conflict' };
    // Validate provider configuration before durable reservation/authorization.
    const provider = cloudflareEmailSending({ ...config, binding, clock, timers });
    const message_id = `${await sha256(bytes(`${reg.install_id}:${reg.generation}:${value.intent_id}`))}@${config.domain}`;
    const reserved = await ledger.reserveSend(reg, { ...value, digest, message_id,
      token_hash: await sha256(bytes(value.reply_token)) }, clock());
    if (reserved.duplicate) return receipt(reserved);
    // This CAS is the only authorization point. No fresh lookup can replace the
    // recipient with a later owner after it commits, even across a switch/revert.
    const authorized = await ledger.authorizeSend(reg, value.intent_id, clock());
    let result;
    try { result = await provider.send({ reg: authorized, value }); }
    catch {
      console.error('[agent-mail] pull_send_provider_uncertain');
      result = { outcome: 'uncertain', provider_id: '' };
    }
    const state = result.outcome === 'accepted' ? 'accepted'
      : ['retryable', 'permanent'].includes(result.outcome) ? 'failed' : 'uncertain';
    const providerId = state === 'accepted' ? result.provider_id : '';
    const observed = clock();
    try {
      return receipt(await ledger.finishSend(reg, value.intent_id, state, observed, providerId));
    } catch (error) {
      if (error.message !== 'stale_generation') throw error;
      // Retirement already holds an authorized send as uncertain. Positive
      // evidence is retained without promoting that retired state to accepted.
      if (state === 'accepted') return receipt(await ledger.recordSendAcceptance(authorized, providerId, observed));
      return receipt({ ...reserved, state: 'uncertain', provider_id: '' });
    }
  } catch (error) {
    if (['stale_generation', 'intent_conflict', 'registration_inactive'].includes(error.message)) {
      return { status: 409, code: error.message };
    }
    if (error.message === 'send_limit') return { status: 429, code: 'send_limit' };
    throw error; // Storage/configuration failures cannot report a successful send.
  }
}
