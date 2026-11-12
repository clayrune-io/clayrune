import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Miniflare, Log, LogLevel } from 'miniflare';
import { SQL_NOW, CLOCK_TOLERANCE_MS, sqlClockAtOrAfter } from '../sql-clock.js';
import { bytes, base64url, sha256 } from '../signing.js';

const ORIGIN = 'https://mail.example.com';
const INSTALL = '1'.repeat(32), DAY = 86400000;
const id = (n) => n.toString(16).padStart(32, '0');
const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
const entry = `${root}sql-clock-test-entry.js`;
const wrapper = `
import { Ledger } from './ledger.js';
import { SessionAuth } from './session-auth.js';
import { OwnerChangeHTTP } from './owner-change.js';
import { OwnerRegistry } from './owner-registry.js';
export default {async fetch(request, env) {
  const v = await request.json();
  // Production modules execute in workerd against REAL D1. Only the supported
  // Worker clock input is controlled; D1's time functions are never replaced.
  const pull = new Ledger(env.DB).pull({clock: () => v.now});
  const config = {db:env.DB, clock: () => v.now, serviceOrigin:'${ORIGIN}',
    serviceKeyId:'service', servicePrivateKey:env.PRIVATE_KEY};
  try {
    const reg = await pull.registration('${INSTALL}');
    const scope = {...reg, connection_id: reg.connection_id ?? undefined};
    let result;
    switch(v.action) {
      case 'enqueue': result = await pull.enqueue(scope, v.value); break;
      case 'page': result = await pull.queue.page(scope); break;
      case 'offer': result = await pull.queue.offer(scope, v.event_id); break;
      case 'ack': result = await pull.queue.ack(scope, v.value); break;
      case 'reserve': result = await pull.reserveSend(reg, v.value, v.now); break;
      case 'authorize': result = await pull.authorizeSend(reg, v.intent_id, v.now); break;
      case 'finish': result = await pull.finishSend(reg, v.intent_id, 'accepted', v.now, 'provider-id'); break;
      case 'ownerGuard':
        result = await env.DB.batch([OwnerChangeHTTP.prototype.timeGuard.call(
          {registry:new OwnerRegistry(env.DB)}, v.now, v.end, v.sameDay)]); break;
      case 'admit': result = await new SessionAuth(config).admitSocket(new Request(
        '${ORIGIN}/v1/pull/socket', {headers:{authorization:'MailTicket '+v.ticket,
          upgrade:'websocket','sec-websocket-protocol':'clayrune-mail-pull.v1'}}),
        {trustedSource:'test-edge'}); break;
      case 'mint': {
        const response = await new SessionAuth(config).handle(new Request('${ORIGIN}/v1/pull/ticket',
          {method:'POST', headers:v.headers, body:v.raw}), {trustedSource:'test-edge'});
        result = {status:response.status, value:await response.json()}; break;
      }
      default: throw new Error('unknown_test_action');
    }
    return Response.json({ok:true,result});
  } catch(error) { return Response.json({ok:false,error:error.code ?? error.message}); }
}};`;

function modules() {
  const result = [{type:'ESModule', path:entry, contents:wrapper}], seen = new Set();
  function add(path) {
    if (seen.has(path)) return; seen.add(path);
    const contents = readFileSync(path, 'utf8');
    result.push({type:'ESModule', path, contents});
    for (const m of contents.matchAll(/from ['"]\.\/([^'"]+)['"]/g)) add(root + m[1]);
  }
  for (const name of ['ledger.js','session-auth.js','owner-change.js','owner-registry.js']) add(root + name);
  return result;
}
async function keypair() {
  const p = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  return {key:p.privateKey, public:base64url(await crypto.subtle.exportKey('raw', p.publicKey)),
    private:base64url(await crypto.subtle.exportKey('pkcs8', p.privateKey))};
}
async function fixture(t) {
  const service = await keypair(), candidate = await keypair();
  const mf = new Miniflare({name:'sql-clock-test', modules:modules(), compatibilityDate:'2026-07-01',
    d1Databases:['DB'], bindings:{PRIVATE_KEY:service.private}, log:new Log(LogLevel.ERROR)});
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB');
  const dir = new URL('../migrations/', import.meta.url);
  const sources = [readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'),
    ...readdirSync(dir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()
      .map((s) => readFileSync(new URL(s, dir), 'utf8'))];
  // Preserve complete trigger bodies while accommodating D1.exec's line parser.
  for (const sql of sources) await db.exec(sql.replace(/--[^\n]*/g, '').replace(/[\r\n]/g, ' '));
  await db.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,epoch,created_ms,updated_ms)
    VALUES(?,7,3,'active',?,'Owner@example.com',?,'current',?,'v1',4,${SQL_NOW},${SQL_NOW})`)
    .bind(INSTALL, 'a'.repeat(64), 'm'+'a'.repeat(29), candidate.public).run();
  await db.prepare(`INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',${SQL_NOW},NULL)`)
    .bind(INSTALL, 'a'.repeat(64), candidate.public, 'f'.repeat(64), id(2)).run();
  await db.prepare(`INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,${SQL_NOW})`)
    .bind('m'+'a'.repeat(29), INSTALL).run();
  return {mf, db, candidate, next:100};
}
const sqlNow = async (f) => (await f.db.prepare(`SELECT ${SQL_NOW} AS now`).first()).now;
const rows = async (f, table) => (await f.db.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all()).results;
async function rpc(f, action, now, value = {}) {
  const r = await f.mf.dispatchFetch(ORIGIN, {method:'POST', body:JSON.stringify({action,now,...value})});
  assert.equal(r.status, 200); return r.json();
}
async function ok(f, action, now, value) {
  const r = await rpc(f, action, now, value); assert.equal(r.ok, true, JSON.stringify(r)); return r.result;
}
async function okAhead(f, action, ahead, value) {
  const now = await sqlNow(f) + ahead, result = await ok(f, action, now, value);
  if (ahead >= 4000) assert.ok(await sqlNow(f) < now, 'D1 must still be behind the Worker during this test');
  return result;
}
async function refused(f, action, now, value, code = 'storage_unavailable') {
  const r = await rpc(f, action, now, value); assert.equal(r.ok, false); assert.equal(r.error, code);
}
const send = (n) => ({intent_id:`intent-${n}`,thread_id:`thread-${n}`,digest:'b'.repeat(64),
  message_id:`out-${n}@example.com`,token_hash:n.toString(16).padStart(64,'0')});
async function queued(f) {
  const n = f.next++, value = send(n);
  const now = await sqlNow(f);
  await ok(f, 'reserve', now, {value});
  await ok(f, 'authorize', await sqlNow(f), {intent_id:value.intent_id});
  await ok(f, 'finish', await sqlNow(f), {intent_id:value.intent_id});
  const event = await ok(f, 'enqueue', await sqlNow(f), {value:{thread_id:value.thread_id,
    incoming_message_id:`reply-${n}@example.com`,text:'reply',
    auth:{ingress:'cloudflare',spf:'pass',dkim:'pass',dmarc:'pass',aligned:'spf+dkim'}}});
  return event;
}
const ack = (e, offer) => ({event_id:e.event_id,event_digest:e.event_digest,body_digest:e.body_digest,
  lease_id:offer.lease_id,local_receipt_id:id(100000 + e.seq)});
async function ticket(f, ahead) {
  const secret = 'c'.repeat(64), digest = await sha256(bytes(secret));
  await f.db.prepare(`INSERT INTO pull_tickets VALUES(?,?,?,?,7,3,'current','issued',
    ${SQL_NOW}+?,${SQL_NOW}+60000,NULL)`)
    .bind(digest,id(f.next++),id(f.next++),INSTALL,ahead).run();
  const row = (await rows(f, 'pull_tickets'))[0];
  return {secret,row};
}

test('real workerd/D1 clock consistency and strict authority windows', {timeout:60000}, async (t) => {
  const f = await fixture(t);
  await t.test('exact 5000ms helper boundary uses one D1 statement and unmodified SQL time', async () => {
    assert.equal(CLOCK_TOLERANCE_MS, 5000);
    const r = await f.db.prepare(`SELECT ${sqlClockAtOrAfter(`(${SQL_NOW}+5000)`)} AS accepted,
      ${sqlClockAtOrAfter(`(${SQL_NOW}+5001)`)} AS refused`).first();
    assert.equal(r.accepted, 1); assert.equal(r.refused, 0);
  });
  for (const ahead of [0, 1, 4000, 5000]) await t.test(`page/offer/ack and send reserve/authorize/finish with Worker ${ahead}ms ahead`, async () => {
    const e = await queued(f);
    assert.ok((await okAhead(f, 'page', ahead)).events.some((v) => v.event.event_id === e.event_id));
    const offer = await okAhead(f, 'offer', ahead, {event_id:e.event_id});
    const receipt = await okAhead(f, 'ack', ahead, {value:ack(e,offer)});
    assert.equal(receipt.event_id, e.event_id);
    assert.equal((await rows(f,'pull_events')).find((v) => v.event_id === e.event_id).text, null);
    const value = send(f.next++);
    assert.equal((await okAhead(f, 'reserve', ahead, {value})).state, 'reserved');
    assert.equal((await okAhead(f, 'authorize', ahead, {intent_id:value.intent_id})).state, 'sending');
    assert.equal((await okAhead(f, 'finish', ahead, {intent_id:value.intent_id})).state, 'accepted');
    await okAhead(f, 'ownerGuard', ahead, {end:Number.MAX_SAFE_INTEGER});
  });
  await t.test('page/offer/ack/send/owner guards refuse beyond tolerance without partial writes', async () => {
    const e = await queued(f);
    const offer = await ok(f, 'offer', await sqlNow(f), {event_id:e.event_id});
    const pending = await queued(f);
    const reserved = send(f.next++), sending = send(f.next++);
    await ok(f, 'reserve', await sqlNow(f), {value:reserved});
    await ok(f, 'reserve', await sqlNow(f), {value:sending});
    await ok(f, 'authorize', await sqlNow(f), {intent_id:sending.intent_id});
    const snapshot = async () => Promise.all(['pull_events','pull_offers','pull_receipts','pull_dedupe',
      'pull_intents','pull_threads','pull_budgets','pull_installs'].map((name) => rows(f,name)));
    for (const [action, value] of [['page',{}],['offer',{event_id:pending.event_id}],['ack',{value:ack(e,offer)}],
      ['reserve',{value:send(f.next++)}],['authorize',{intent_id:reserved.intent_id}],
      ['finish',{intent_id:sending.intent_id}],['ownerGuard',{end:Number.MAX_SAFE_INTEGER}]]) {
      const before = await snapshot(), now = await sqlNow(f) + 6000;
      const result = await rpc(f, action, now, value);
      // Fail rather than silently weaken the scenario if a paused host lets
      // real D1 catch up during this call. No sleeps or compensating offsets.
      assert.ok(await sqlNow(f) < now - CLOCK_TOLERANCE_MS);
      assert.equal(result.ok, false, action);
      if (action !== 'ownerGuard') assert.equal(result.error, 'storage_unavailable', action);
      assert.deepEqual(await snapshot(), before, action);
    }
  });
  await t.test('D1 expiry blocks offer/ack/authorize even while Worker time is still before it', async () => {
    const e = await queued(f), offer = await ok(f, 'offer', await sqlNow(f), {event_id:e.event_id});
    const pending = await queued(f);
    const reserved = send(f.next++); await ok(f, 'reserve', await sqlNow(f), {value:reserved});
    await f.db.batch([
      f.db.prepare(`UPDATE pull_events SET expires_ms=${SQL_NOW} WHERE event_id IN (?,?)`).bind(e.event_id,pending.event_id),
      f.db.prepare(`UPDATE pull_dedupe SET expires_ms=${SQL_NOW} WHERE event_id IN (?,?)`).bind(e.event_id,pending.event_id),
      f.db.prepare(`UPDATE pull_threads SET expires_ms=${SQL_NOW} WHERE thread_id=?`).bind(reserved.thread_id),
    ]);
    const event = (await rows(f,'pull_events')).find((v) => v.event_id === e.event_id);
    const now = event.expires_ms - 1;
    assert.ok(!(await ok(f,'page',now)).events.some((v) => v.event.event_id === e.event_id));
    await refused(f,'offer',now,{event_id:pending.event_id});
    await refused(f,'ack',now,{value:ack(e,offer)});
    await refused(f,'authorize',now,{intent_id:reserved.intent_id});
    assert.equal((await rows(f,'pull_events')).find((v) => v.event_id === e.event_id).text, 'reply');
    assert.ok(!(await rows(f,'pull_receipts')).some((v) => v.event_id === e.event_id));
    const owner = await rpc(f,'ownerGuard',now,{end:event.expires_ms});
    assert.equal(owner.ok,false); assert.match(owner.error,/CHECK constraint failed/);
  });
  for (const ahead of [4000, 5000, 6000]) await t.test(`SQL ticket creation ${ahead}ms ahead, with Worker at its own mint time`, async () => {
    await f.db.prepare('DELETE FROM pull_tickets').run();
    const {secret,row} = await ticket(f,ahead);
    const before = (await rows(f,'pull_installs'))[0];
    if (ahead <= CLOCK_TOLERANCE_MS) {
      const result = await ok(f,'admit',row.created_ms,{ticket:secret});
      assert.equal(result.epoch, before.epoch + 1);
      assert.equal((await rows(f,'pull_tickets'))[0].state,'consumed');
    } else {
      await refused(f,'admit',row.created_ms,{ticket:secret},'ticket_invalid');
      assert.ok(await sqlNow(f) < row.created_ms - CLOCK_TOLERANCE_MS);
      assert.deepEqual((await rows(f,'pull_installs'))[0],before);
      assert.deepEqual((await rows(f,'pull_tickets'))[0],row);
    }
  });
  await t.test('ticket expiry is strict against D1 while Worker is before expiry', async () => {
    await f.db.prepare('DELETE FROM pull_tickets').run();
    const {secret} = await ticket(f,-10000);
    await f.db.prepare(`UPDATE pull_tickets SET expires_ms=${SQL_NOW}`).run();
    const row = (await rows(f,'pull_tickets'))[0], before = (await rows(f,'pull_installs'))[0];
    await refused(f,'admit',row.expires_ms - 1,{ticket:secret},'ticket_invalid');
    assert.deepEqual((await rows(f,'pull_installs'))[0],before);
    assert.deepEqual((await rows(f,'pull_tickets'))[0],row);
  });
  for (const offset of [-300001,300001]) await t.test(`signed proof ${offset}ms from Worker is refused, with no tolerance added`, async () => {
    const now = await sqlNow(f), raw = JSON.stringify({op_id:id(f.next++)});
    const m = {version:'2',direction:'install-to-pull',key_id:'current',install_id:INSTALL,generation:'7',
      owner_generation:'3',timestamp:String(now+offset),nonce:id(f.next++),digest:await sha256(bytes(raw))};
    const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http','1',m.direction,ORIGIN,'POST',
      '/v1/pull/ticket',m.key_id,INSTALL,'7','3',m.timestamp,m.nonce,m.digest]));
    const headers = {'content-type':'application/json'};
    for (const [k,v] of Object.entries(m)) headers['x-mail-'+k.replaceAll('_','-')] = v;
    headers['x-mail-signature'] = base64url(await crypto.subtle.sign('Ed25519',f.candidate.key,input));
    const before = await rows(f,'pull_tickets');
    assert.equal((await ok(f,'mint',now,{raw,headers})).status,401);
    assert.deepEqual(await rows(f,'pull_tickets'),before);
  });
  await t.test('UTC quota-day equality stays strict despite tolerated lower bound', async () => {
    const now = await sqlNow(f), differentDay = (Math.floor(now / DAY) - 1) * DAY;
    const result = await rpc(f,'ownerGuard',differentDay,{end:Number.MAX_SAFE_INTEGER,sameDay:true});
    assert.equal(result.ok,false);
  });
});
