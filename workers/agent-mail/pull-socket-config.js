// Reviewed operator configuration only. No request field supplies signing scope
// or deadlines; clients still verify the offline-root signature and their pin.
import { MANIFEST_LIMIT, manifestHttp } from './manifest-http.js';
import { origin } from './enrollment-transcript.js';

const valid = (ok) => { if (!ok) throw new Error('service_unavailable'); };
const time = (value) => {
  valid(typeof value === 'string' && /^[1-9][0-9]{12}$/.test(value));
  return Number(value);
};

export function pullSocketConfig(env) {
  valid(origin(env.MAIL_SERVICE_ORIGIN) && env.MAIL_DB);
  const raw = env.MAIL_SERVICE_MANIFEST;
  valid(typeof raw === 'string' && raw.length <= MANIFEST_LIMIT);
  // Reuse the publisher's shape/size rules, never claim root verification here.
  valid(manifestHttp({ env }).status === 200);
  const m = JSON.parse(raw).manifest;
  valid(m[0] === 'clayrune-mail-service-manifest' && m[1] === '1'
    && m[2] === env.MAIL_SERVICE_ORIGIN && m[6] === env.MAIL_SERVICE_KEY_ID
    && /^[A-Za-z0-9_-]{1,128}$/.test(m[6]) && /^[A-Za-z0-9_-]{43}$/.test(m[7])
    && /^[1-9][0-9]{0,14}$/.test(m[3]) && !m[10].includes(m[6]));
  const issued = time(m[4]), expires = time(m[5]), activate = time(m[8]), retire = time(m[9]);
  const pin = time(env.MAIL_SERVICE_PIN_EXPIRES_MS);
  const now = Date.now();
  // Expired deadlines must remain loadable after DO hibernation so its alarm
  // can close old sockets. PullMailbox.admit/current enforce strict liveness;
  // this adapter checks configured chronology, never extends an old deadline.
  valid(issued <= activate && activate <= now && activate < expires && expires <= retire);
  valid(typeof env.MAIL_SERVICE_PRIVATE_KEY === 'string' && env.MAIL_SERVICE_PRIVATE_KEY.length > 0);
  return { db: env.MAIL_DB, serviceOrigin: env.MAIL_SERVICE_ORIGIN,
    serviceKeyId: env.MAIL_SERVICE_KEY_ID, servicePrivateKey: env.MAIL_SERVICE_PRIVATE_KEY,
    serviceKeyExpiresMs: Math.min(expires, pin, retire) };
}
