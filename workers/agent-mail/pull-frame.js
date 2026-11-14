// Pull v1 socket codec only. Authority and custody stay outside this module.
import { bytes, randomId, sha256, base64url, unbase64url } from './signing.js';

export const FRAME_BYTES = 160 * 1024;
export const PAYLOAD_BYTES = 112 * 1024;
export const integer = (v, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v)
  && !Object.is(v, -0) && v >= min && v <= max;
export const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).length === keys.length && keys.every((k) => Object.hasOwn(v, k));
export const valid = (v, code = 'invalid_request') => { if (!v) throw new Error(code); };
const I = /^[a-f0-9]{32}$/;
const H = /^[a-f0-9]{64}$/;
const match = (re, v) => typeof v === 'string' && re.test(v);
const holdReasons = new Set(['unknown_thread', 'queue_full', 'storage_unavailable', 'local_disabled',
  'vault_locked', 'conversation_busy']);
const serviceTypes = new Set(['hello', 'event', 'hold', 'ack_committed', 'status', 'wake', 'reauth_required', 'error']);

// Client schemas contain only two objects, with scalar values. Lex strings and
// integers before JSON.parse: duplicate/escaped keys and noncanonical numbers
// must not disappear during normalization. Arrays/deeper objects are forbidden.
export function clientFrame(message) {
  valid(!(message instanceof ArrayBuffer) || message.byteLength <= FRAME_BYTES, 'frame_limit');
  valid(typeof message === 'string', 'invalid_json');
  valid(message.length <= FRAME_BYTES && bytes(message).length <= FRAME_BYTES, 'frame_limit');
  const token = /\s*("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|-?(?:0|[1-9][0-9]*)|[{}:,])/y;
  let pos = 0;
  const take = () => {
    token.lastIndex = pos; const m = token.exec(message); valid(m, 'invalid_json');
    // JSON whitespace is exactly four ASCII characters, not JavaScript \s.
    valid(/^[ \t\r\n]*$/.test(m[0].slice(0, m[0].length - m[1].length)), 'invalid_json');
    pos = token.lastIndex; return m[1];
  };
  const string = (s) => {
    valid(s[0] === '"', 'invalid_json'); const v = JSON.parse(s);
    valid(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v), 'invalid_json'); return v;
  };
  const object = (depth) => {
    valid(depth <= 2, 'invalid_json'); const seen = new Set(); let t = take();
    if (t === '}') return;
    for (;;) {
      const key = string(t); valid(!seen.has(key), 'invalid_json'); seen.add(key);
      valid(take() === ':', 'invalid_json'); t = take();
      if (t === '{') {
        const start = pos - 1; object(depth + 1);
        if (depth === 1 && key === 'payload') valid(bytes(message.slice(start, pos)).length <= PAYLOAD_BYTES, 'frame_limit');
      }
      else if (t[0] === '"') string(t);
      else valid(/^(0|[1-9][0-9]*)$/.test(t) && integer(Number(t)), 'invalid_json');
      t = take(); if (t === '}') return;
      valid(t === ',', 'invalid_json'); t = take();
    }
  };
  valid(take() === '{', 'invalid_json'); object(1);
  valid(/^[ \t\r\n]*$/.test(message.slice(pos)), 'invalid_json');
  const f = JSON.parse(message);
  valid(exact(f, ['v','type','install_id','generation','owner_generation','epoch','request_id','payload'])
    && f.v === 1 && match(I, f.install_id) && match(I, f.request_id)
    && ['generation','owner_generation','epoch'].every((k) => integer(f[k], 1, 999999999999999)));
  const p = f.payload;
  valid(bytes(JSON.stringify(p)).length <= PAYLOAD_BYTES, 'frame_limit');
  if (f.type === 'resume') valid(exact(p, ['after_seq','limit','available_bytes'])
    && integer(p.after_seq) && integer(p.limit, 1, 10) && integer(p.available_bytes, 0, 524288));
  else if (f.type === 'custody_ack') valid(exact(p, ['event_id','event_digest','body_digest','lease_id','local_receipt_id'])
    && ['event_id','event_digest','body_digest'].every((k) => match(H, p[k]))
    && match(I, p.lease_id) && match(I, p.local_receipt_id));
  else if (f.type === 'hold') valid(exact(p, ['event_id','event_digest','lease_id','reason','retry_after_ms'])
    && match(H, p.event_id) && match(H, p.event_digest) && match(I, p.lease_id)
    && holdReasons.has(p.reason) && integer(p.retry_after_ms), 'invalid_hold');
  else throw new Error('unsupported_frame');
  return f;
}

export async function serviceFrame(config, scope, type, value) {
  valid(serviceTypes.has(type), 'unsupported_frame');
  if (type === 'wake') valid(exact(value, []));
  const raw = bytes(JSON.stringify(value)); valid(raw.length <= PAYLOAD_BYTES, 'frame_limit');
  // signing.base64url spreads its input; use bounded chunks for large payloads.
  let binary = '';
  for (let n = 0; n < raw.length; n += 16384) binary += String.fromCharCode(...raw.subarray(n, n + 16384));
  const payload = btoa(binary).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  const frame = { v: 1, type, install_id: scope.install_id, generation: scope.generation,
    owner_generation: scope.owner_generation, epoch: scope.epoch, frame_id: randomId(),
    issued_ms: config.clock(), key_id: config.serviceKeyId, payload, payload_digest: await sha256(raw) };
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-frame','1',config.serviceOrigin,type,
    frame.key_id,frame.install_id,String(frame.generation),String(frame.owner_generation),String(frame.epoch),
    frame.frame_id,String(frame.issued_ms),frame.payload_digest]));
  const key = await crypto.subtle.importKey('pkcs8', unbase64url(config.servicePrivateKey), 'Ed25519', false, ['sign']);
  frame.signature = base64url(await crypto.subtle.sign('Ed25519', key, input));
  const wire = JSON.stringify(frame); valid(bytes(wire).length <= FRAME_BYTES, 'frame_limit'); return wire;
}
