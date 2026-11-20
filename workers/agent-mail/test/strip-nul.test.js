// MC-1179 W11: a text/plain body that carries a NUL byte. strip() used to
// return it as readable text; pull custody refuses a NUL (pull-ledger.js
// enqueue), ingress-pull.js mapped that refusal to storage_unavailable, and the
// mail was failed at SMTP as a storage fault instead of dropped with a reason.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { strip } from '../strip.js';
import { ingress } from '../ingress.js';
import { Ledger } from '../ledger.js';
import { bytes, sha256 } from '../signing.js';
import { emailMessage } from './transport-support.js';
import { mime, multipart, plainPart } from './fixtures.js';

const HDR = ['From: owner@example.com', 'To: x@mail.example.net', 'Subject: Re: x', 'MIME-Version: 1.0'];
const refused = async (raw) => {
  const r = await strip(raw);
  assert.deepEqual({ ok: r.ok, reason_code: r.reason_code }, { ok: false, reason_code: 'text_nul' });
  assert.equal(Object.hasOwn(r, 'text'), false);
};

test('a NUL in a 7bit text/plain body is refused as text_nul, with no text returned', async () => {
  await refused(mime([...HDR, 'Content-Type: text/plain; charset=utf-8'], 'Yes\0, go ahead.\r\n'));
  await refused(mime([...HDR, 'Content-Type: text/plain; charset=utf-8'], '\0'));
});

test('a NUL decoded from base64 or quoted-printable is refused too', async () => {
  await refused(mime([...HDR, 'Content-Type: text/plain', 'Content-Transfer-Encoding: base64'],
    Buffer.from('hello\0there').toString('base64')));
  await refused(mime([...HDR, 'Content-Type: text/plain; charset=utf-8', 'Content-Transfer-Encoding: quoted-printable'],
    'hello=00there'));
});

test('a NUL in any kept part refuses the whole message', async () => {
  await refused(multipart('mixed', [plainPart('first part'), plainPart('second\0part')]));
});

test('ordinary text, other control characters and a NUL only in a stripped part are unaffected', async () => {
  const plain = await strip(mime([...HDR, 'Content-Type: text/plain; charset=utf-8'], 'Yes,\tgo ahead.\r\n'));
  assert.deepEqual([plain.ok, plain.text], [true, 'Yes,\tgo ahead.']);
  const attached = await strip(multipart('mixed', [plainPart('kept text'), {
    headers: ['Content-Type: application/octet-stream', 'Content-Transfer-Encoding: base64', 'Content-Disposition: attachment; filename=x.bin'],
    body: Buffer.from('a\0b').toString('base64') }]));
  assert.deepEqual([attached.ok, attached.text, attached.stripped.attachments], [true, 'kept text', 1]);
});

// ---- through pull ingress: dropped with a reason and an audit row -------------
const DAY = 86400000, INSTALL = '1'.repeat(32), LOCAL = 'm' + 'a'.repeat(29);
const TOKEN = 'abcdef0123456789abcdef0123456789', SENT = 'sent@example.com';
const dir = new URL('../migrations/', import.meta.url);
const sources = [readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'),
  ...readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort().map((s) => readFileSync(new URL(s, dir), 'utf8'))];
class D1 {
  constructor() { this.sql = new DatabaseSync(':memory:'); this.sql.exec('PRAGMA foreign_keys=ON'); for (const s of sources) this.sql.exec(s); }
  prepare(sql) {
    const db = this.sql;
    return { sql, args: [], bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return { success: true, results: db.prepare(sql).all(...this.args) }; },
      async run() { return this.runSync(); },
      runSync() { const s = db.prepare(sql); return s.columns().length ? { success: true, results: s.all(...this.args), meta: { changes: 0 } }
        : { success: true, meta: { changes: Number(s.run(...this.args).changes) } }; } };
  }
  async batch(statements) {
    this.sql.exec('BEGIN IMMEDIATE');
    try { const r = statements.map((s) => s.runSync()); this.sql.exec('COMMIT'); return r; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}

test('a verified owner reply with a NUL body is dropped with an audit row, not failed as a storage fault', async (t) => {
  const db = new D1(); t.after(() => db.sql.close()); const now = Date.now();
  for (const [sql, ...args] of [
    [`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,key_id,
      public_key,consent_version,created_ms,updated_ms) VALUES(?,7,3,'active',?,'owner@example.com',?,'key',?,'v1',?,?)`,
    INSTALL, 'a'.repeat(64), LOCAL, 'k'.repeat(43), now, now],
    ["INSERT INTO pull_generations VALUES(?,7,3,?,'key',?,?,?,'current',?,NULL)", INSTALL, 'a'.repeat(64), 'k'.repeat(43), 'f'.repeat(64), '2'.repeat(32), now],
    ["INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)", LOCAL, INSTALL, now],
    [`INSERT INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,thread_id,message_id,state,created_ms)
      VALUES(?,7,'intent',3,?,?,'thread',?,'accepted',?)`, INSTALL, 'a'.repeat(64), 'b'.repeat(64), SENT, now],
    ["INSERT INTO pull_threads VALUES(?,7,'thread',3,'intent',?,?,?,?,NULL)", INSTALL, await sha256(bytes(TOKEN)), SENT, now + 90 * DAY, now + 120 * DAY],
  ]) await db.prepare(sql).bind(...args).run();
  const send = async (text) => {
    const m = emailMessage({ outId: SENT, text }); m.to = `${LOCAL}+${TOKEN}@mail.example.net`;
    const result = await ingress({ message: m, ledger: new Ledger(db).pull(), now: Date.now(),
      config: { domain: 'mail.example.net', ingressVerified: true }, wake: async () => {} });
    return { result, m };
  };
  const { result, m } = await send('Approved\0 with a stray byte');
  assert.deepEqual(result, { status: 'rejected', reason: 'text_nul' });
  assert.deepEqual(m.rejects, ['Agent mail refused']);
  assert.deepEqual(db.sql.prepare('SELECT code FROM pull_audit').all().map((r) => r.code), ['text_nul']);
  assert.equal(db.sql.prepare('SELECT COUNT(*) AS n FROM pull_events').get().n, 0);
  // The same thread still takes an ordinary reply afterwards.
  assert.equal((await send('Approved')).result.status, 'queued');
});
