// Owner-only outbound. No raw MIME, destination or callback accepted from clients.
import { bytes, sha256 } from './signing.js';
import { parseAddrSpec } from './mailbox.js';
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
