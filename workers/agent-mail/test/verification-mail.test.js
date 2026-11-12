import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { VerificationMail, VERIFICATION_TTL, confirmationPage, verificationMessage, verificationBinding } from '../verification-mail.js';
import { ownerIdentity } from '../owner-registry.js';
import { bytes, sha256, base64url } from '../signing.js';

const NOW = 1_794_249_600_000;
const ORIGIN = 'https://mail.example.com';
const H = 'a'.repeat(64);
const SESSION = 'b'.repeat(64);
const CSRF = 'c'.repeat(64);
const id = (n) => n.toString(16).padStart(32, '0');
const publicKey = base64url(new Uint8Array(32).fill(1));
const sealKey = new Uint8Array(32).fill(42); // Test-only, never operator configuration.

// Executes the actual schema and SQL with D1's all-or-nothing batch semantics.
// No canned query matches or mocked token/budget results.
class D1 {
  constructor() {
    this.sql = new DatabaseSync(':memory:');
    this.sql.exec('PRAGMA foreign_keys=ON');
    this.sql.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
    const dir = new URL('../migrations/', import.meta.url);
    for (const name of readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()) {
      this.sql.exec(readFileSync(new URL(name, dir), 'utf8'));
    }
  }
  prepare(sql) {
    const db = this.sql;
    return { args: [], bind(...args) { this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      runSync() { const r = db.prepare(sql).run(...this.args); return { success: true, meta: { changes: Number(r.changes) } }; } };
  }
  async batch(statements) {
    this.sql.exec('BEGIN IMMEDIATE');
    try { const r = statements.map((s) => s.runSync()); this.sql.exec('COMMIT'); return r; }
    catch (e) { this.sql.exec('ROLLBACK'); throw e; }
  }
}
const rows = (db, table) => db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1`).all();
const one = (db, table) => rows(db, table)[0];

function fence(f, { flow = id(90), mode = 'revoke', at = NOW - 1, n = 90 } = {}) {
  f.db.sql.prepare('INSERT INTO pull_fences VALUES(?,?,1,?,?,?,?,?,?)')
    .run(id(1), flow, H, mode, id(n), id(n + 1), H, at);
}
function disabled(f) {
  f.db.sql.exec("UPDATE pull_installs SET state='disabled'");
  f.db.sql.prepare(`INSERT INTO pull_generations VALUES(?,1,1,?,'candidate',?,?,?,'fenced',?,?)`)
    .run(id(1), f.flow.owner_hash, publicKey, H, id(90), NOW - 1000, NOW - 1);
  fence(f);
}

async function fixture(t, template = 'signup', managementPurpose = 'owner_change') {
  const db = new D1(); t.after(() => db.sql.close());
  const mail = new VerificationMail({ db, serviceOrigin: ORIGIN, sealKeyId: 'test-key', sealKey });
  const owner = await ownerIdentity('Owner@EXAMPLE.com');
  const target = template === 'new_owner' ? await ownerIdentity('New@example.com') : owner;
  const flow = { flow_id: id(2), install_id: id(1), purpose: template === 'signup' ? 'signup' : managementPurpose,
    expected_generation: 1, expected_owner_generation: 1, candidate_key_id: 'candidate', candidate_public_key: publicKey,
    owner_hash: owner.owner_hash, flow_secret_hash: H, fence_hash: H, input_digest: H,
    state: template === 'new_owner' ? 'approved' : 'pending', consent_version: '1', manifest_sequence: 1,
    manifest_digest: H, created_ms: NOW, expires_ms: NOW + VERIFICATION_TTL };
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,created_ms,updated_ms)
    VALUES(?,1,1,?,?,?,'m00000000000000000000000000001','candidate',?,'1',?,?)`).run(
    id(1), template === 'signup' ? 'pending' : 'active', owner.owner_hash, owner.owner_address, publicKey, NOW, NOW);
  const cols = Object.keys(flow);
  db.sql.prepare(`INSERT INTO pull_flows(${cols.join(',')}) VALUES(${cols.map(() => '?').join(',')})`).run(...Object.values(flow));
  const change = template === 'new_owner' ? { change_id: id(3), install_id: id(1), flow_id: id(2),
    old_owner_hash: owner.owner_hash, new_owner_hash: target.owner_hash, expected_generation: 1,
    expected_owner_generation: 1, state: 'pending', created_ms: NOW, expires_ms: NOW + VERIFICATION_TTL } : null;
  if (change) {
    const keys = Object.keys(change);
    db.sql.prepare(`INSERT INTO pull_owner_changes(${keys.join(',')}) VALUES(${keys.map(() => '?').join(',')})`).run(...Object.values(change));
  }
  const input = { outbox_id: id(4), template, to: target.owner_address, flow, change };
  return { db, mail, input, flow, change };
}
async function issue(f) {
  const result = await f.mail.issueStatements(f.input, NOW);
  await f.db.batch(result.statements);
  const message = await f.mail.message(f.input.outbox_id, NOW);
  const token = /token=([a-f0-9]{64})/.exec(message.body)[1];
  return { result, message, token };
}
async function browser(f, token, overrides = {}) {
  const row = { session_hash: await sha256(bytes(SESSION)), csrf_hash: await sha256(bytes(CSRF)),
    install_id: f.flow.install_id, flow_id: f.flow.flow_id, token_hash: await sha256(bytes(token)),
    purpose: 'confirmation', generation: 1, owner_generation: 1, state: 'csrf', created_ms: NOW,
    expires_ms: NOW + 600_000, ...overrides };
  const keys = Object.keys(row);
  f.db.sql.prepare(`INSERT INTO pull_browser_sessions(${keys.join(',')}) VALUES(${keys.map(() => '?').join(',')})`).run(...Object.values(row));
  return { method: 'POST', token, session_secret: SESSION, csrf: CSRF, request_origin: ORIGIN };
}
async function consume(f, request, now = NOW) {
  const result = await f.mail.consumeStatements(request, now);
  await f.db.batch(result.statements);
  return result;
}

test('scanner GET reads an inert confirm page: no consume, activation, send or deadline extension', async (t) => {
  const f = await fixture(t); const { token } = await issue(f);
  const before = ['pull_tokens', 'pull_flows', 'pull_installs', 'pull_control_outbox', 'pull_budgets', 'pull_browser_sessions']
    .map((name) => rows(f.db, name));
  for (let i = 0; i < 3; i++) {
    const view = await f.mail.readForConfirmation(token, NOW + i);
    const response = confirmationPage({ token, csrf: CSRF, purpose: view.row.purpose, owner_mask: 'O***@example.com',
      install_id: view.row.install_id, candidate_fingerprint: await sha256(new Uint8Array(32).fill(1)) });
    const html = await response.text();
    assert.equal(response.status, 200);
    assert.match(html, /<form method="post" action="\/mail\/link\/confirm">/);
    assert.match(html, /type="submit"/); assert.doesNotMatch(html, /<script|<img|<iframe|http-equiv/i);
    assert.ok(bytes(html).length < 32 * 1024);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
    assert.match(response.headers.get('content-security-policy'), /frame-ancestors 'none'/);
    assert.equal(response.headers.get('x-frame-options'), 'DENY');
  }
  assert.deepEqual(before, ['pull_tokens', 'pull_flows', 'pull_installs', 'pull_control_outbox', 'pull_budgets', 'pull_browser_sessions']
    .map((name) => rows(f.db, name)));
});

test('POST consumes once with a fixed receipt; second POST cannot consume or mutate again', async (t) => {
  const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token);
  const result = await consume(f, request);
  assert.deepEqual(result.receipt, { state: 'confirmed' });
  assert.equal(one(f.db, 'pull_tokens').state, 'consumed');
  assert.equal(one(f.db, 'pull_tokens').consumed_ms, NOW);
  assert.deepEqual(JSON.parse(one(f.db, 'pull_tokens').result_json), result.receipt);
  const before = rows(f.db, 'pull_tokens');
  await assert.rejects(consume(f, request, NOW + 1), /already_sent/);
  assert.deepEqual(rows(f.db, 'pull_tokens'), before);
  assert.equal(one(f.db, 'pull_installs').state, 'pending'); // Activation is unit 04.
  assert.equal(rows(f.db, 'pull_control_outbox').length, 1);
});

test('exact 30-minute expiry refuses GET, POST and expired link mail', async (t) => {
  const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token);
  const end = NOW + VERIFICATION_TTL;
  await f.mail.readForConfirmation(token, end - 1);
  await assert.rejects(f.mail.readForConfirmation(token, end), /token_expired/);
  await assert.rejects(consume(f, request, end), /token_expired/);
  await assert.rejects(f.mail.message(f.input.outbox_id, end), /token_expired/);
  assert.equal(one(f.db, 'pull_tokens').state, 'pending');
});

test('retry reads the SAME outbox/token and charges the combined budgets exactly once', async (t) => {
  const f = await fixture(t); const { token, message } = await issue(f);
  const before = ['pull_tokens', 'pull_control_outbox', 'pull_budgets'].map((name) => rows(f.db, name));
  t.mock.method(crypto, 'getRandomValues', () => { throw new Error('retry must not mint randomness'); });
  const retry = await f.mail.issueStatements(f.input, NOW + 1);
  assert.deepEqual(retry, { replay: true, outbox_id: f.input.outbox_id, statements: [] });
  assert.deepEqual(await f.mail.message(f.input.outbox_id, NOW + 1), message);
  assert.deepEqual(before, ['pull_tokens', 'pull_control_outbox', 'pull_budgets'].map((name) => rows(f.db, name)));
  assert.equal(one(f.db, 'pull_tokens').token_hash, await sha256(bytes(token)));
  const budgets = rows(f.db, 'pull_budgets');
  assert.equal(budgets.length, 3); assert.ok(budgets.every((r) => r.count === 1));
  assert.ok(budgets.some((r) => r.bucket.startsWith('pull:verify:install:')));
  assert.ok(budgets.some((r) => r.bucket.startsWith('pull:verify:target:')));
  assert.ok(budgets.some((r) => r.bucket.startsWith('pull:send:global:')));
});

test('token is hash-only and private recipient/token are encrypted using exact contract blob/AAD', async (t) => {
  const f = await fixture(t); const { token } = await issue(f);
  const row = one(f.db, 'pull_control_outbox'); const verifier = one(f.db, 'pull_tokens');
  assert.ok(!JSON.stringify(verifier).includes(token)); assert.ok(!JSON.stringify(row).includes(token));
  assert.equal(verifier.token_hash, await sha256(bytes(token)));
  const plain = JSON.stringify({ to: f.input.to, token, link_kind: 'link' });
  assert.equal(row.envelope_sealed.length, bytes(plain).length + 12 + 16);
  const key = await crypto.subtle.importKey('raw', sealKey, 'AES-GCM', false, ['decrypt']);
  const aad = bytes(JSON.stringify(['clayrune-mail-pull-control', '1', row.outbox_id, row.install_id,
    row.flow_id, row.template, row.target_hash, String(row.expires_ms)]));
  const clear = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: row.envelope_sealed.slice(0, 12), additionalData: aad },
    key, row.envelope_sealed.slice(12));
  assert.equal(new TextDecoder().decode(clear), plain);
  const expected = await sha256(bytes(JSON.stringify(['clayrune-mail-pull-link', '1', 'signup', id(2), '', id(1),
    '1', '1', f.flow.owner_hash, 'candidate', publicKey, String(NOW + VERIFICATION_TTL)])));
  assert.equal(verifier.binding_digest, expected);
  assert.equal(await verificationBinding((await f.mail.readForConfirmation(token, NOW)).scope), expected);
});

test('two preparations before commit cannot leave an extra token or budget charge', async (t) => {
  const f = await fixture(t);
  const a = await f.mail.issueStatements(f.input, NOW);
  const b = await f.mail.issueStatements({ ...f.input, outbox_id: id(99) }, NOW);
  await f.db.batch(a.statements);
  const before = ['pull_tokens', 'pull_control_outbox', 'pull_budgets'].map((name) => rows(f.db, name));
  await assert.rejects(f.db.batch(b.statements), /UNIQUE/);
  assert.deepEqual(before, ['pull_tokens', 'pull_control_outbox', 'pull_budgets'].map((name) => rows(f.db, name)));
  assert.equal((await f.mail.issueStatements(f.input, NOW)).replay, true);
});

test('two prepared POSTs race at commit: only one single-use update wins', async (t) => {
  const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token);
  const a = await f.mail.consumeStatements(request, NOW); const b = await f.mail.consumeStatements(request, NOW);
  await f.db.batch(a.statements);
  await assert.rejects(f.db.batch(b.statements), /CHECK/);
  assert.equal(one(f.db, 'pull_tokens').consumed_ms, NOW);
});

test('caller lifecycle writes and token consumption roll back together', async (t) => {
  const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token);
  const a = await f.mail.consumeStatements(request, NOW);
  await assert.rejects(f.db.batch([...a.statements,
    f.mail.stmt("UPDATE pull_installs SET state='verified' WHERE install_id=?", id(1)), f.mail.registry.guard('0')]), /CHECK/);
  assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  assert.equal(one(f.db, 'pull_installs').state, 'pending');
  await f.db.batch([...a.statements, f.mail.stmt("UPDATE pull_installs SET state='verified' WHERE install_id=?", id(1))]);
  assert.equal(one(f.db, 'pull_tokens').state, 'consumed');
  assert.equal(one(f.db, 'pull_installs').state, 'verified');
});

for (const [name, over] of Object.entries({
  GET: { method: 'GET' }, 'foreign Origin': { request_origin: 'https://evil.example.com' },
  'absent Origin': { request_origin: undefined }, 'wrong CSRF': { csrf: H }, 'wrong cookie': { session_secret: H },
})) {
  test(`POST refuses ${name}`, async (t) => {
    const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token);
    await assert.rejects(consume(f, { ...request, ...over }));
    assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  });
}

for (const [name, over] of Object.entries({
  expired: { expires_ms: NOW }, 'future-created': { created_ms: NOW + 1 },
  'unbounded TTL': { expires_ms: NOW + 600_001 }, 'different flow': { flow_id: id(99) },
  'different generation': { generation: 2 }, 'different owner generation': { owner_generation: 2 },
  'already approved': { state: 'approved' }, 'wrong purpose': { purpose: 'delete' },
  'unbound token': { token_hash: null },
})) {
  test(`POST refuses ${name} browser session`, async (t) => {
    const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token, over);
    await assert.rejects(consume(f, request), /CHECK/);
    assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  });
}

for (const [name, sql] of Object.entries({
  cancelled: "UPDATE pull_flows SET state='cancelled'", 'rotated key': "UPDATE pull_installs SET generation=2",
  'changed owner': "UPDATE pull_installs SET owner_generation=2", 'candidate changed': "UPDATE pull_flows SET candidate_key_id='other'",
  'binding changed': "UPDATE pull_tokens SET binding_digest='" + 'b'.repeat(64) + "'",
  frozen: "UPDATE pull_installs SET state='frozen'", deleted: "UPDATE pull_installs SET state='deleted',owner_address=NULL",
})) {
  test(`POST refuses ${name} scope even after its statements were prepared`, async (t) => {
    const f = await fixture(t); const { token } = await issue(f); const request = await browser(f, token);
    const prepared = await f.mail.consumeStatements(request, NOW);
    f.db.sql.exec(sql);
    await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
    assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  });
}

test('cancellation fence invalidates a prepared confirmation and issuance batch', async (t) => {
  const f = await fixture(t); const preparedIssue = await f.mail.issueStatements(f.input, NOW);
  const { token } = await issue(f); const prepared = await f.mail.consumeStatements(await browser(f, token), NOW);
  f.db.sql.prepare(`INSERT INTO pull_fences VALUES(?,?,1,?,'cancel',?,?,?,?)`).run(id(1), id(2), H, id(8), id(9), H, NOW);
  await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
  await assert.rejects(f.db.batch(preparedIssue.statements), /CHECK/);
  assert.equal(one(f.db, 'pull_tokens').state, 'pending');
});

for (const purpose of ['resume', 'key_recovery']) {
  test(`disabled high-water ${purpose}: exact step-up issues and consumes without restoring old authority`, async (t) => {
    const f = await fixture(t, 'stepup', purpose); disabled(f);
    const before = ['pull_installs', 'pull_generations', 'pull_fences'].map((table) => rows(f.db, table));
    const { token } = await issue(f); await consume(f, await browser(f, token));
    assert.equal(one(f.db, 'pull_tokens').state, 'consumed');
    assert.deepEqual(['pull_installs', 'pull_generations', 'pull_fences'].map((table) => rows(f.db, table)), before);
    assert.equal(f.db.sql.prepare("SELECT count(*) AS n FROM pull_generations WHERE state='current'").get().n, 0);
  });
}

test('re-entry exception is opt-in for stepup only; send/socket/activation and default guards remain fenced', async (t) => {
  const f = await fixture(t, 'stepup', 'key_recovery'); disabled(f);
  for (const purpose of [undefined, 'signup', 'new_owner', 'send', 'socket', 'activation']) {
    assert.equal(await f.mail.fenced(f.flow, purpose), true);
    await assert.rejects(f.db.batch([f.mail.fenceGuard(f.flow, purpose)]), /CHECK/);
  }
  assert.equal(await f.mail.fenced(f.flow, 'stepup'), false);
});

for (const [template, purpose] of [['signup', 'signup'], ['new_owner', 'owner_change'],
  ['stepup', 'owner_change'], ['stepup', 'rotate'], ['stepup', 'delete']]) {
  test(`prior revoke never permits ${template}/${purpose} issuance`, async (t) => {
    const f = await fixture(t, template, purpose); disabled(f);
    const prepared = await f.mail.issueStatements(f.input, NOW);
    await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
    for (const table of ['pull_tokens', 'pull_control_outbox', 'pull_budgets']) assert.equal(rows(f.db, table).length, 0);
  });
}

for (const at of [NOW, NOW + 1]) {
  test(`revoke at ${at - NOW}ms relative to flow cancels prepared issue and consume`, async (t) => {
    const f = await fixture(t, 'stepup', 'key_recovery'); disabled(f);
    const preparedIssue = await f.mail.issueStatements(f.input, NOW);
    const { token } = await issue(f); const preparedConsume = await f.mail.consumeStatements(await browser(f, token), NOW);
    fence(f, { flow: id(92), n: 92, at });
    const before = ['pull_tokens', 'pull_budgets', 'pull_control_outbox'].map((table) => rows(f.db, table));
    await assert.rejects(f.db.batch(preparedConsume.statements), /CHECK/);
    await assert.rejects(f.db.batch(preparedIssue.statements), /CHECK/);
    assert.deepEqual(['pull_tokens', 'pull_budgets', 'pull_control_outbox'].map((table) => rows(f.db, table)), before);
  });
}

for (const mode of ['cancel', 'revoke']) {
  test(`same-flow ${mode} always blocks step-up even when older than flow`, async (t) => {
    const f = await fixture(t, 'stepup', 'resume'); disabled(f);
    const { token } = await issue(f); const consumeRequest = await browser(f, token);
    const prepared = await f.mail.consumeStatements(consumeRequest, NOW);
    fence(f, { mode, flow: f.flow.flow_id, n: 92, at: NOW - 1 });
    await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
    assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  });
}

test('idempotent original revoke receipt and unrelated cancellation do not cancel a fresh recovery', async (t) => {
  const f = await fixture(t, 'stepup', 'key_recovery'); disabled(f);
  const { token } = await issue(f);
  f.db.sql.prepare('INSERT OR IGNORE INTO pull_fences VALUES(?,?,1,?,?,?,?,?,?)')
    .run(id(1), id(90), H, 'revoke', id(90), id(91), H, NOW + 1);
  assert.equal(one(f.db, 'pull_fences').created_ms, NOW - 1);
  fence(f, { mode: 'cancel', flow: id(92), n: 92, at: NOW + 1 });
  await consume(f, await browser(f, token));
  await assert.rejects(consume(f, await browser(f, token, { session_hash: 'd'.repeat(64) })), /already_sent/);
});

for (const mutation of ["UPDATE pull_flows SET created_ms=created_ms+1",
  "UPDATE pull_flows SET purpose='rotate'", "UPDATE pull_flows SET candidate_key_id='other'",
  "UPDATE pull_flows SET state='cancelled'", "UPDATE pull_installs SET state='active'",
  'UPDATE pull_installs SET generation=2', 'UPDATE pull_installs SET owner_generation=2',
  "UPDATE pull_installs SET owner_hash='" + 'f'.repeat(64) + "'",
  "UPDATE pull_generations SET state='current'", "UPDATE pull_generations SET key_id='other'",
  'UPDATE pull_generations SET owner_generation=2']) {
  test(`recovery scope race refuses changed durable binding: ${mutation}`, async (t) => {
    const f = await fixture(t, 'stepup', 'key_recovery'); disabled(f);
    const { token } = await issue(f); const prepared = await f.mail.consumeStatements(await browser(f, token), NOW);
    f.db.sql.exec(mutation);
    await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
    assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  });
}

test('revoked recovery token expires at original deadline and cannot revive the old generation', async (t) => {
  const f = await fixture(t, 'stepup', 'key_recovery'); disabled(f);
  const { token } = await issue(f); const request = await browser(f, token);
  await assert.rejects(consume(f, request, NOW + VERIFICATION_TTL), /token_expired/);
  assert.equal(one(f.db, 'pull_tokens').state, 'pending');
  assert.equal(one(f.db, 'pull_installs').state, 'disabled');
});

for (const bucket of ['install', 'target', 'global']) {
  test(`${bucket} verification budget failure rolls back token/outbox and all other charges`, async (t) => {
    const f = await fixture(t); const day = Math.floor(NOW / 86_400_000);
    const key = bucket === 'install' ? `pull:verify:install:${id(1)}:${day}`
      : bucket === 'target' ? `pull:verify:target:${f.flow.owner_hash}:${day}` : `pull:send:global:${day}`;
    f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,?,?)').run(key, bucket === 'global' ? 1000 : 3, NOW + 86_400_000);
    const before = rows(f.db, 'pull_budgets'); const prepared = await f.mail.issueStatements(f.input, NOW);
    await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
    assert.deepEqual(rows(f.db, 'pull_budgets'), before);
    assert.equal(rows(f.db, 'pull_tokens').length, 0); assert.equal(rows(f.db, 'pull_control_outbox').length, 0);
  });
}

test('late caller failure rolls back all new verification records and budget reservations', async (t) => {
  const f = await fixture(t); const prepared = await f.mail.issueStatements(f.input, NOW);
  await assert.rejects(f.db.batch([...prepared.statements, f.mail.registry.guard('0')]), /CHECK/);
  for (const table of ['pull_tokens', 'pull_control_outbox', 'pull_budgets']) assert.equal(rows(f.db, table).length, 0);
});

for (const purpose of ['owner_change', 'key_recovery', 'resume', 'rotate', 'delete']) {
  test(`fixed step-up template/token supports only the bound ${purpose} flow`, async (t) => {
    const f = await fixture(t, 'stepup', purpose); const { token, message } = await issue(f);
    assert.equal(message.subject, 'Confirm your Clayrune management request');
    assert.equal(one(f.db, 'pull_tokens').purpose, 'stepup');
    await consume(f, await browser(f, token));
    assert.equal(one(f.db, 'pull_tokens').state, 'consumed');
    assert.equal(one(f.db, 'pull_flows').state, 'pending'); // Unit 04a owns session promotion.
  });
}

test('new-owner token binds exact change and target; stale/cancelled change cannot consume', async (t) => {
  const f = await fixture(t, 'new_owner'); const { token, message } = await issue(f);
  assert.equal(message.to, 'New@example.com'); assert.equal(one(f.db, 'pull_tokens').change_id, id(3));
  const prepared = await f.mail.consumeStatements(await browser(f, token), NOW);
  f.db.sql.exec("UPDATE pull_owner_changes SET state='cancelled'");
  await assert.rejects(f.db.batch(prepared.statements), /CHECK/);
  f.db.sql.exec("UPDATE pull_owner_changes SET state='pending'");
  await f.db.batch(prepared.statements);
  assert.equal(one(f.db, 'pull_installs').owner_generation, 1); // Switching is unit 05.
  assert.equal(one(f.db, 'pull_tokens').state, 'consumed');
});

test('no replacement token after accepted envelope purge or uncertain provider status', async (t) => {
  const f = await fixture(t); await issue(f);
  f.db.sql.exec("UPDATE pull_control_outbox SET state='uncertain'");
  await assert.rejects(f.mail.message(id(4), NOW), /invalid_state/);
  assert.equal((await f.mail.issueStatements(f.input, NOW)).replay, true);
  f.db.sql.exec("UPDATE pull_control_outbox SET state='accepted',envelope_sealed=NULL");
  assert.equal((await f.mail.issueStatements(f.input, NOW)).replay, true);
  await assert.rejects(f.mail.message(id(4), NOW), /invalid_state/);
  assert.equal(rows(f.db, 'pull_tokens').length, 1);
  assert.ok(rows(f.db, 'pull_budgets').every((r) => r.count === 1));
});

test('envelope authenticates every AAD field, tag, key and key id; no fallback', async (t) => {
  const f = await fixture(t); await issue(f); const row = one(f.db, 'pull_control_outbox');
  for (const over of [{ outbox_id: id(8) }, { install_id: id(8) }, { flow_id: id(8) }, { template: 'stepup' },
    { target_hash: H }, { expires_ms: row.expires_ms + 1 }, { seal_key_id: 'unknown' }]) {
    await assert.rejects(f.mail.openEnvelope({ ...row, ...over }));
  }
  const corrupt = new Uint8Array(row.envelope_sealed); corrupt[corrupt.length - 1] ^= 1;
  await assert.rejects(f.mail.openEnvelope({ ...row, envelope_sealed: corrupt }));
  const wrong = new VerificationMail({ db: f.db, serviceOrigin: ORIGIN, sealKeyId: 'test-key', sealKey: new Uint8Array(32) });
  await assert.rejects(wrong.openEnvelope(row));
});

test('fixed mail never reflects caller subject/body, and page escapes display strings', async (t) => {
  const f = await fixture(t); const { token } = await issue(f);
  const message = verificationMessage(ORIGIN, 'signup', { to: f.input.to, token, link_kind: 'link',
    subject: 'ATTACK-SUBJECT', body: 'ATTACK-BODY' });
  assert.doesNotMatch(JSON.stringify(message), /ATTACK/);
  const page = confirmationPage({ token, csrf: CSRF, purpose: 'signup', owner_mask: '<img src=x>@example.com',
    install_id: id(1), candidate_fingerprint: H });
  const html = await page.text(); assert.doesNotMatch(html, /<img/); assert.match(html, /&lt;img/);
  for (const template of ['stepup', 'new_owner', 'old_notice', 'revert_old', 'revert_new']) {
    const notice = verificationMessage(ORIGIN, template, { to: f.input.to, token, link_kind: template === 'old_notice' ? 'revert' : 'link' });
    assert.ok(notice.subject.length < 200); assert.doesNotMatch(notice.body, /ATTACK/);
    assert.equal(notice.body.includes('?token='), !template.startsWith('revert_'));
  }
});

test('bad scope/input/unknown tokens refuse without allocating state', async (t) => {
  const f = await fixture(t);
  for (const over of [{ template: '__proto__' }, { template: 'old_notice' }, { to: 'Other@example.com' },
    { outbox_id: 'bad' }, { flow: { ...f.flow, expires_ms: f.flow.expires_ms + 1 } },
    { flow: { ...f.flow, candidate_public_key: 'invalid' } }]) {
    await assert.rejects(f.mail.issueStatements({ ...f.input, ...over }, NOW));
  }
  await assert.rejects(f.mail.readForConfirmation(H, NOW), /not_found/);
  await assert.rejects(f.mail.readForConfirmation('bad', NOW), /invalid_request/);
  await assert.rejects(f.mail.message(id(99), NOW), /not_found/);
  for (const serviceOrigin of ['http://mail.example.com', 'https://mail.example.com/', 'https://127.0.0.1', 'https://mail.example.com:443']) {
    assert.throws(() => new VerificationMail({ db: f.db, serviceOrigin, sealKeyId: 'test-key', sealKey }));
  }
  assert.equal(rows(f.db, 'pull_tokens').length, 0); assert.equal(rows(f.db, 'pull_budgets').length, 0);
});

test('changed outbox id for the same issuance is an operation conflict, never a resend', async (t) => {
  const f = await fixture(t); await issue(f);
  await assert.rejects(f.mail.issueStatements({ ...f.input, outbox_id: id(99) }, NOW), /operation_conflict/);
  assert.equal(rows(f.db, 'pull_control_outbox').length, 1);
  assert.ok(rows(f.db, 'pull_budgets').every((r) => r.count === 1));
});

test('same outbox cannot replay under a different candidate key or generation binding', async (t) => {
  const f = await fixture(t); await issue(f);
  for (const flow of [{ ...f.flow, candidate_key_id: 'different' }, { ...f.flow, expected_generation: 2 },
    { ...f.flow, candidate_public_key: base64url(new Uint8Array(32).fill(2)) }]) {
    await assert.rejects(f.mail.issueStatements({ ...f.input, flow }, NOW), /operation_conflict/);
  }
  assert.equal(rows(f.db, 'pull_tokens').length, 1);
  assert.ok(rows(f.db, 'pull_budgets').every((r) => r.count === 1));
});

test('new flow/install/slot and verification can commit in ONE caller batch', async (t) => {
  const f = await fixture(t);
  const install = one(f.db, 'pull_installs');
  f.db.sql.exec('DELETE FROM pull_flows; DELETE FROM pull_installs');
  const prepared = await f.mail.issueStatements(f.input, NOW);
  const insert = (table, value) => {
    const keys = Object.keys(value);
    return f.mail.stmt(`INSERT INTO ${table}(${keys.join(',')}) VALUES(${keys.map(() => '?').join(',')})`, ...Object.values(value));
  };
  const statements = [insert('pull_installs', install), insert('pull_flows', f.flow),
    ...f.mail.registry.reserveSlotStatements({ owner_hash: f.flow.owner_hash, install_id: id(1), flow_id: id(2),
      until_ms: NOW + VERIFICATION_TTL }, NOW), ...prepared.statements];
  await assert.rejects(f.db.batch([...statements, f.mail.registry.guard('0')]), /CHECK/);
  for (const table of ['pull_installs', 'pull_flows', 'pull_owner_slots', 'pull_tokens', 'pull_control_outbox', 'pull_budgets']) {
    assert.equal(rows(f.db, table).length, 0);
  }
  await f.db.batch(statements);
  for (const table of ['pull_installs', 'pull_flows', 'pull_owner_slots', 'pull_tokens', 'pull_control_outbox']) {
    assert.equal(rows(f.db, table).length, 1);
  }
  assert.ok(rows(f.db, 'pull_budgets').every((r) => r.count === 1));
});

test('step-up confirmation page names the exact management purpose', async (t) => {
  const f = await fixture(t, 'stepup', 'delete'); const { token } = await issue(f);
  const view = { token, csrf: CSRF, purpose: 'stepup', owner_mask: 'O***@example.com',
    install_id: id(1), candidate_fingerprint: H };
  assert.throws(() => confirmationPage(view));
  const html = await confirmationPage({ ...view, flow_purpose: f.flow.purpose }).text();
  assert.match(html, /Confirm mail deletion request/);
  assert.doesNotMatch(html, /Confirm owner email change/);
});
