// Test-only D1 facade over real SQLite, not a canned SQL mock. No network.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { base64url, bytes, sign } from '../signing.js';
import { Ledger } from '../ledger.js';
import { createWorker } from '../worker.js';
import { legacyTransportHttp } from '../legacy-transport-http.js';
import { legacyTransportHooks } from '../legacy-transport-hooks.js';
import { cfAr } from './fixtures.js';
export const NOW = 1_794_249_600_000;
export class D1 {
  constructor(path = ':memory:', init = true) {
    this.sql = new DatabaseSync(path);
    if (init) this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  }
  prepare(sql) {
    const db = this.sql;
    return { args: [], bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return { results: db.prepare(sql).all(...this.args) }; },
      async run() { return this.runSync(); },
      runSync() { const r = db.prepare(sql).run(...this.args); return { success: true, meta: { changes: Number(r.changes) } }; } };
  }
  async batch(statements) {
    this.sql.exec('BEGIN IMMEDIATE');
    try { const result = statements.map((s) => s.runSync()); this.sql.exec('COMMIT'); return result; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}
export async function keys() {
  const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  return { privateKey: base64url(await crypto.subtle.exportKey('pkcs8', k.privateKey)),
    publicKey: base64url(await crypto.subtle.exportKey('raw', k.publicKey)) };
}
export function sendValue(over = {}) {
  return { intent_id: 'intent1', thread_id: 'thread1', reply_token: 'abcdef0123456789abcdef0123456789',
    subject: 'Project / conversation', body: 'A note. Reply to continue this conversation.', kind: 'note', ...over };
}
// Direct retained boundaries; only explicit public-entry checks use createWorker.
export function transportHttp(options = {}) {
  return { fetch(request, env) {
    return legacyTransportHttp({ ...options, request, env,
      config: () => ({ domain: env.MAIL_DOMAIN, from: env.MAIL_FROM,
        ingressVerified: env.MAIL_INGRESS_VERIFIED === 'true', privateKey: env.MAIL_SERVICE_PRIVATE_KEY,
        keyId: env.MAIL_SERVICE_KEY_ID }) });
  } };
}
export async function fixture(options = {}) {
  const db = options.db ?? new D1(); const installKeys = await keys(); const serviceKeys = await keys();
  const ledger = new Ledger(db, options.policy);
  const reg = { install_id: 'install1', generation: 1, active: 1, owner_verified: 1,
    owner_address: 'owner@example.com', address_local: 'opaque1', key_id: 'ik1', public_key: installKeys.publicKey,
    route_origin: 'https://proven.example.net', route_path: '/api/agent-mail/ingress', route_generation: 1, route_verified: 1 };
  await ledger.run(`INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, ...Object.values(reg));
  const env = { MAIL_ENABLED: 'true', MAIL_INGRESS_VERIFIED: 'true', MAIL_DB: db,
    MAIL_DOMAIN: 'mail.example.net', MAIL_FROM: 'agent@mail.example.net',
    MAIL_SERVICE_KEY_ID: 'sk1', MAIL_SERVICE_PRIVATE_KEY: serviceKeys.privateKey };
  const config = { domain: env.MAIL_DOMAIN, from: env.MAIL_FROM, keyId: 'sk1',
    privateKey: serviceKeys.privateKey, ingressVerified: true };
  const sent = []; const delivered = [];
  const clock = { now: NOW };
  const workerOptions = { clock: () => clock.now, send: options.send ?? (async (m) => sent.push(m)),
    fetcher: options.fetcher ?? (async (url, init) => {
      delivered.push({ url, ...init }); const p = JSON.parse(new TextDecoder().decode(init.body));
      return Response.json({ custody: 'durable', event_id: p.event_id, body_digest: p.body_digest,
        install_id: p.install_id, generation: p.generation });
    }) };
  const worker = createWorker(workerOptions);
  const legacy = legacyTransportHooks({ ...workerOptions,
    config: (env) => ({ domain: env.MAIL_DOMAIN, from: env.MAIL_FROM,
      ingressVerified: env.MAIL_INGRESS_VERIFIED === 'true', privateKey: env.MAIL_SERVICE_PRIVATE_KEY,
      keyId: env.MAIL_SERVICE_KEY_ID }) });
  const http = options.publicEntry ? worker : transportHttp(workerOptions);
  async function request(path = '/v1/mail/send', value = sendValue(), over = {}) {
    const body = typeof value === 'string' ? bytes(value) : bytes(JSON.stringify(value));
    const headers = await sign({ ...installKeys, key_id: reg.key_id, install_id: reg.install_id,
      generation: reg.generation, direction: 'install-to-service', path, body, now: clock.now, ...over });
    return new Request(`https://service.example.net${path}`, { method: 'POST', headers, body });
  }
  return { db, ledger, reg, installKeys, serviceKeys, env, config, sent, delivered, worker, legacy, http, request, clock };
}
export function emailMessage({ text = 'A fresh reply', token = sendValue().reply_token,
  id = 'reply1@example.com', outId, from = 'owner@example.com', ar = cfAr(), extra = [],
  type = 'text/plain; charset=utf-8', envelope = 'owner@example.com' } = {}) {
  const rcpt = `opaque1+${token}@mail.example.net`;
  const fields = [ ...(ar === null ? [] : [`Authentication-Results: ${ar}`]), `From: ${from}`,
    `To: ${rcpt}`, `In-Reply-To: <${outId}>`, ...(id === null ? [] : [`Message-ID: <${id}>`]),
    ...extra, `Content-Type: ${type}` ];
  const raw = bytes([...fields, '', text].join('\r\n')); const rejects = [];
  const headers = new Headers();
  for (const field of fields) { const p = field.indexOf(':'); headers.append(field.slice(0, p), field.slice(p + 1).trim()); }
  return { to: rcpt, from: envelope, rawSize: raw.length, raw: new Blob([raw]).stream(), headers,
    setReject: (s) => rejects.push(s), rejects };
}
export async function sendAndReceive(f, options = {}) {
  const res = await f.http.fetch(await f.request(), f.env); const sent = await res.json();
  if (res.status !== 200) throw new Error(JSON.stringify(sent));
  const message = emailMessage({ outId: sent.message_id, ...options });
  const result = await f.legacy.email(message, f.env);
  return { message, result, outId: sent.message_id };
}
