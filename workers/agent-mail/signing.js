// Mail-only Ed25519 wire contract. See docs/AGENT_MAIL_TRANSPORT.md.
const encoder = new TextEncoder();
export const SKEW_MS = 300_000;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const HEX = /^[a-f0-9]{64}$/;
const NONCE = /^[a-f0-9]{32}$/;
export const bytes = (s) => encoder.encode(s);
export function randomId() {
  return [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, '0')).join('');
}
export async function sha256(body) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', body))]
    .map((b) => b.toString(16).padStart(2, '0')).join('');
}
export function base64url(data) {
  return btoa(String.fromCharCode(...new Uint8Array(data))).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}
export function unbase64url(s) {
  if (typeof s !== 'string' || !/^[A-Za-z0-9_-]+$/.test(s)) throw new Error('bad_key_encoding');
  const out = Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
  if (base64url(out) !== s) throw new Error('bad_key_encoding');
  return out;
}
export function canonical(m) {
  if (Object.values(m).some((v) => typeof v !== 'string')
      || m.version !== '1' || !['install-to-service', 'service-to-install', 'service-to-cp'].includes(m.direction)
      || !ID.test(m.key_id) || !ID.test(m.install_id) || !/^[1-9][0-9]{0,14}$/.test(m.generation)
      || m.method !== 'POST' || !/^\/[A-Za-z0-9/_-]{1,200}$/.test(m.path)
      || !/^[1-9][0-9]{12}$/.test(m.timestamp) || !NONCE.test(m.nonce) || !HEX.test(m.digest)) {
    throw new Error('bad_signature_fields');
  }
  // JSON array has one cross-language representation: these are ASCII strings.
  return bytes(JSON.stringify(['clayrune-agent-mail', m.version, m.direction, m.key_id,
    m.install_id, m.generation, m.method, m.path, m.timestamp, m.nonce, m.digest]));
}
const FIELDS = ['version', 'direction', 'key_id', 'install_id', 'generation', 'timestamp', 'nonce', 'digest'];
export async function sign({ privateKey, key_id, install_id, generation, direction, path, body,
  now = Date.now(), nonce = randomId() }) {
  const m = { version: '1', direction, key_id, install_id, generation: String(generation),
    method: 'POST', path, timestamp: String(now), nonce, digest: await sha256(body) };
  const input = canonical(m);
  const key = await crypto.subtle.importKey('pkcs8', unbase64url(privateKey), 'Ed25519', false, ['sign']);
  const signature = base64url(await crypto.subtle.sign('Ed25519', key, input));
  const headers = new Headers({ 'content-type': 'application/json', 'x-mail-signature': signature });
  for (const field of FIELDS) headers.set(`x-mail-${field.replaceAll('_', '-')}`, m[field]);
  return headers;
}
// Replay claim MUST be durable and atomic. Call only after validation; a failed
// signature cannot burn the owner's nonce. Retain claims through the skew window.
export async function verify({ request, body, publicKey, direction, install_id, generation,
  key_id, claimNonce, now, clock = now === undefined ? Date.now : () => now }) {
  let verified;
  try {
    const m = Object.fromEntries(FIELDS.map((f) => [f, request.headers.get(`x-mail-${f.replaceAll('_', '-')}`)]));
    const url = new URL(request.url);
    m.method = request.method; m.path = url.pathname;
    if (url.search || url.hash || m.direction !== direction || m.install_id !== install_id
        || m.generation !== String(generation) || m.key_id !== key_id) return false;
    const input = canonical(m);
    if (Math.abs(clock() - Number(m.timestamp)) > SKEW_MS || m.digest !== await sha256(body)) return false;
    const raw = unbase64url(publicKey);
    if (raw.length !== 32) return false;
    const key = await crypto.subtle.importKey('raw', raw, 'Ed25519', false, ['verify']);
    const sig = unbase64url(request.headers.get('x-mail-signature'));
    if (sig.length !== 64 || !await crypto.subtle.verify('Ed25519', key, sig, input)) return false;
    // Crypto is asynchronous: an upload/verification begun in-window may finish late.
    if (Math.abs(clock() - Number(m.timestamp)) > SKEW_MS) return false;
    verified = { ...m, expires_at: Number(m.timestamp) + SKEW_MS + 1 };
  } catch {
    // Invalid public wire material is an authentication refusal, never a fallback.
    return false;
  }
  // A durable-store failure must be observable as an operational failure, not
  // disguised as bad client authentication or followed by in-memory acceptance.
  const claimed = await claimNonce(verified);
  // Cleanup may race a slow durable claim. Never accept after its window expires.
  return claimed && Math.abs(clock() - Number(verified.timestamp)) <= SKEW_MS;
}
