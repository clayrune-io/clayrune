// Sole mail ingress: bound raw stream, preserve ordered headers, authenticate,
// strip, then and only then persist plain text. No forward(), reply() or fetch().
import { decide } from './auth.js';
import { strip } from './strip.js';
import { strictMsgId } from './content-id.js';
import { bytes, sha256 } from './signing.js';
export async function readBounded(stream, limit) {
  const reader = stream.getReader(); const chunks = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error('body_too_large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const out = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.byteLength; }
  return out;
}
export function orderedHeaders(raw) {
  // A gate only: values are not decoded, and bodies are never consulted here.
  // Decode the header block only. Binary/legacy-charset MIME bodies are owned
  // by unit 3's bounded parser, not interpreted as UTF-8 by this header gate.
  let end = -1;
  for (let i = 0; i < raw.length - 1; i++) {
    if (raw[i] === 10 && raw[i + 1] === 10) { end = i; break; }
    if (raw[i] === 13 && raw[i + 1] === 10 && raw[i + 2] === 13 && raw[i + 3] === 10) { end = i; break; }
  }
  if (end < 0) throw new Error('bad_headers');
  const text = new TextDecoder('utf-8', { fatal: true }).decode(raw.subarray(0, end));
  const result = [];
  for (const line of text.split(/\r?\n/)) {
    if (/^[ \t]/.test(line)) {
      if (!result.length) throw new Error('bad_headers');
      result.at(-1).value += ` ${line.trim()}`;
    } else {
      const match = /^([!-9;-~]+):[ \t]*(.*)$/.exec(line);
      if (!match || result.length >= 400) throw new Error('bad_headers');
      result.push({ key: match[1].toLowerCase(), value: match[2] });
    }
    if (result.at(-1).value.length > 8192) throw new Error('bad_headers');
  }
  return result;
}
export async function ingress({ message, ledger, config, now }) {
  async function drop(reason) {
    await ledger.reject(reason, now);
    message.setReject('Agent mail refused');
    return { status: 'rejected', reason };
  }
  // This is a launch gate, not evidence that Cloudflare's header provenance has
  // been verified. Real provider fixtures are still required before enabling it.
  if (config.ingressVerified !== true) return drop('ingress_not_verified');
  if (!await ledger.limit('ingress:global', ledger.policy.globalIngressPerDay, now)) return drop('ingress_limit');
  if (!Number.isFinite(message.rawSize) || message.rawSize > 65_536) return drop('mime_too_large');
  const match = /^([a-z0-9][a-z0-9._-]{0,63})\+([a-z0-9]{26,64})@([a-z0-9.-]+)$/.exec(message.to ?? '');
  if (!match || match[3] !== config.domain) return drop('unknown_recipient');
  const reg = await ledger.recipient(match[1]);
  if (!reg?.active || !reg.owner_verified) return drop('owner_inactive');
  if (!await ledger.limit(`ingress:${reg.install_id}`, ledger.policy.ingressPerDay, now)) return drop('ingress_limit');
  let raw, headers;
  try { raw = await readBounded(message.raw, 65_536); headers = orderedHeaders(raw); }
  catch { return drop('malformed_or_oversize'); }
  // Require agreement with Cloudflare's separate ingress header surface. Never
  // manufacture a trusted AR header from sender bytes or a folded Headers list.
  const ar = headers.filter((h) => h.key === 'authentication-results');
  if (!ar.length || message.headers.get('authentication-results') !== ar.map((h) => h.value).join(', ')) {
    return drop('ingress_header_mismatch');
  }
  if (headers.some((h) => h.key === 'x-clayrune-agent-mail'
      || (h.key === 'auto-submitted' && h.value.toLowerCase() !== 'no')
      || (h.key === 'precedence' && /^(bulk|list|junk)$/i.test(h.value))) || !message.from) return drop('loop_or_bounce');
  const decision = await decide({ rcptTo: message.to, headers, now }, { ...reg, active: true },
    async (token) => ledger.thread(await sha256(bytes(token))));
  if (!decision.accept) return drop(decision.reason_code);
  const content = await strip(raw);
  if (!content.ok) return drop(content.reason_code);
  if (!content.has_text) return drop('no_plain_text');
  const ids = headers.filter((h) => h.key === 'message-id');
  const incoming_id = ids.length === 1 ? strictMsgId(ids[0].value) : null;
  const digest = await sha256(bytes(content.text));
  // An invalid identifier is retained as a visible, non-deliverable hold. No
  // untrusted identifier is permitted to shadow a real owner's later Message-ID.
  const identity = incoming_id ?? `missing:${await sha256(raw)}`;
  const event_id = await sha256(bytes(JSON.stringify([reg.install_id, reg.generation, decision.thread.thread_id, identity])));
  const payload = { version: 1, event_id, install_id: reg.install_id, generation: reg.generation,
    thread_id: decision.thread.thread_id, incoming_message_id: incoming_id, text: content.text,
    body_digest: digest, auth: decision.evidence, received_at: now, expires_at: now + ledger.policy.queueTTL };
  return ledger.enqueue(reg, { event_id, incoming_id: identity, thread_id: decision.thread.thread_id,
    digest, payload, held: incoming_id === null }, now);
}
