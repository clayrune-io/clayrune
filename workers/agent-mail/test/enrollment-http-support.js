// Harness for the enrollment ceremony HTTP tests (7d-2e-2). The shared `fixture()` only supplies keys,
// the grant trust and token/proof builders (its own DB is a scratch one); the Worker under test gets a
// separate D1 with a call counter, so "no D1 call" is a measurable claim.
import { EnrollmentDB, fixture } from './enrollment-support.js';
import { keys } from './transport-support.js';
import { base64url, verify } from '../signing.js';
import { createWorker } from '../worker.js';
import { Ledger } from '../ledger.js';

export const BASE = 'https://service.example.net';
export const SECRET = 'a'.repeat(64);
export const NONCE = 'ab'.repeat(16);
export const path = (name) => `/v1/mail/enrollment/${name}`;

export function spyDb(db) {
  const spy = { calls: 0, sql: db.sql, prepare(s) { spy.calls += 1; return db.prepare(s); },
    batch(x) { spy.calls += 1; return db.batch(x); } };
  return spy;
}

// Decodes the base64 body of a mail built by code-mail.js.
export function mailText(mail) {
  const body = mail.raw.split('\r\n\r\n')[1].replace(/\r\n/g, '');
  return new TextDecoder().decode(Uint8Array.from(atob(body), (c) => c.charCodeAt(0)));
}
export const codeOf = (mail) => mailText(mail).match(/\b[a-f0-9]{32}\b/g);

export async function world({ send, preflight, provider, flags = {}, envOver = {}, noProvider = false, fetcher } = {}) {
  const scratch = await fixture();
  const db = new EnrollmentDB(); const time = scratch.time; db.clock = () => time.now;
  const spy = spyDb(db); const service = await keys();
  const sent = []; const preflights = [];
  const log = []; const real = { error: console.error, log: console.log, warn: console.warn };
  const capture = (...a) => log.push(a.map(String).join(' '));
  console.error = capture; console.log = capture; console.warn = capture;
  const env = { MAIL_ENABLED: 'true', MAIL_ENROLLMENT_ENABLED: 'true', MAIL_DB: spy, MAIL_DOMAIN: 'mail.example.net',
    MAIL_FROM: 'agent@mail.example.net', MAIL_SERVICE_KEY_ID: 'sk1', MAIL_SERVICE_PRIVATE_KEY: service.privateKey,
    MAIL_SERVICE_ORIGIN: scratch.trust.serviceOrigin, MAIL_GRANT_ISSUER: scratch.trust.issuer,
    MAIL_CP_ISSUER: 'https://authority.example.com',
    MAIL_SERVICE_PIN: JSON.stringify({ manifest_sequence: '1', service_key_id: 'service1', service_public_key_sha256: 'b'.repeat(64) }),
    MAIL_GRANT_JWKS: JSON.stringify(scratch.trust.jwks), MAIL_CODE_KEY: base64url(new Uint8Array(32).fill(9)),
    ...flags, ...envOver };
  // `fetcher` given -> the REAL preflight client runs against it (a fake CP); otherwise a stub function is injected.
  const inject = fetcher ? {} : { preflight: async (v) => { preflights.push(v); return (preflight ?? (async () => ({ ok: true })))(v); } };
  if (!noProvider) inject.provider = provider ?? (async (address) => ({ address, verified: true }));
  const worker = createWorker({ clock: () => time.now, fetcher: fetcher ?? (async () => { throw new Error('no_network'); }),
    send: send ?? (async (mail) => { sent.push(mail); }), enrollment: inject });
  const w = { scratch, db, spy, env, worker, service, sent, preflights, log, time, ledger: new Ledger(db),
    restore() { Object.assign(console, real); } };
  w.call = (name, value, over = {}) => call(w, name, value, over);
  w.begin = (over = {}, secret = SECRET, callOver = {}) => w.call('begin',
    { value: { ...scratch.value, ...over }, flow_secret: secret }, callOver);
  return w;
}

export async function call(w, name, value, { nonce = NONCE, method = 'POST', query = '', raw, headers = {} } = {}) {
  const body = method === 'GET' ? undefined : (raw ?? JSON.stringify(value));
  const res = await w.worker.fetch(new Request(BASE + path(name) + query,
    { method, body, headers: { 'x-mail-nonce': nonce, ...headers } }), w.env);
  const text = await res.clone().text();
  return { res, text, json: (() => { try { return JSON.parse(text); } catch { return undefined; } })(), nonce, path: path(name) };
}

// Verifies the service signature on a reply exactly as the Flask side will: install_id = flow_id, generation 1.
export async function replyVerifies(w, r, flowId, publicKey = w.service.publicKey) {
  const body = new Uint8Array(await r.res.clone().arrayBuffer());
  const request = new Request(BASE + r.path, { method: 'POST', headers: r.res.headers });
  return r.res.headers.get('x-mail-nonce') === r.nonce && verify({ request, body, publicKey,
    direction: 'service-to-install-response', install_id: flowId, generation: 1, key_id: 'sk1',
    claimNonce: async () => true, now: w.time.now });
}
export const row = (w, sql, ...a) => w.db.sql.prepare(sql).get(...a);
