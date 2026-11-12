import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Miniflare, Log, LogLevel } from 'miniflare';
import WebSocket from 'ws';
import { request as httpRequest } from 'node:http';
import { bytes, base64url, unbase64url, sha256 } from '../signing.js';
import { clientFrame } from '../pull-frame.js';

const ORIGIN = 'https://mail.example.com';
const INSTALL = '1'.repeat(32);
const id = (n) => n.toString(16).padStart(32, '0');
const hash = (v) => sha256(bytes(v));
const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
const entry = `${root}mailbox-test-entry.js`;
const wrapper = `
import { PullMailbox } from './pull-mailbox.js';
import { SessionAuth } from './session-auth.js';
import { Ledger } from './ledger.js';
const config = e => ({db:e.DB,serviceOrigin:'${ORIGIN}',serviceKeyId:'service',servicePrivateKey:e.PRIVATE_KEY,
  serviceKeyExpiresMs:Number(e.KEY_EXPIRES),clock:()=>Number(e.TEST_CLOCK)||Date.now()});
export class TestMailbox extends PullMailbox {
  constructor(s,e){super(s,config(e));this.instance=crypto.randomUUID();}
  async fetch(r){
    const u=new URL(r.url);
    if(u.pathname==='/v1/pull/socket')return this.admit(r,{trustedSource:'trusted-edge',install_id:'${INSTALL}'});
    if(u.pathname==='/wake')return Response.json(await this.wake('${INSTALL}'));
    if(u.pathname==='/diagnostic')return this.exclusive(async()=>Response.json({instance:this.instance,
      attachments:this.sockets().map(w=>w.deserializeAttachment()),
      meta: Object.fromEntries(await this.state.storage.list({prefix:'socket:'})),alarm:await this.state.storage.getAlarm()}));
    if(u.pathname==='/test-config')return this.exclusive(async()=>{
      Object.assign(this.config,await r.json());await this.deadlines();return new Response('ok');});
    if(u.pathname==='/peer-error'){
      await this.webSocketError(this.sockets()[0]);return new Response('ok');}
    if(u.pathname==='/drop-meta')return this.exclusive(async()=>{
      const s=this.sockets()[0].deserializeAttachment();await this.state.storage.delete('socket:'+s.connection_id);return new Response('ok');});
    if(u.pathname==='/race-offer')return this.exclusive(async()=>{
      const atomic=this.queue.atomic.bind(this.queue);let injected=false;
      this.queue.atomic=async(s,statements)=>{
        if(!injected&&statements.some(q=>q.statement?.includes("UPDATE pull_events SET state='offered'"))){
          injected=true;await this.config.db.prepare('UPDATE pull_installs SET epoch=epoch+1 WHERE install_id=?').bind(s.install_id).run();
        }return atomic(s,statements);
      };return new Response('ok');});
    if(u.pathname==='/deadline'){
      await this.exclusive(async()=>{
        for(const w of this.sockets()){const s=w.deserializeAttachment();
          if(u.searchParams.get('kind')==='reauth')s.reauth_ms=Date.now()-1;
          else s.authenticated_ms=Date.now()-10001;
          w.serializeAttachment(s);}
        await this.state.storage.setAlarm(Date.now()+20);
      });return new Response('ok');
    }
    return new Response(null,{status:404});
  }
}
export default {async fetch(r,e){
  const u=new URL(r.url);
  if(u.pathname==='/v1/pull/ticket')return new SessionAuth(config(e)).handle(r,{trustedSource:'trusted-edge'});
  if(u.pathname==='/enqueue'){
    const v=await r.json();const p=new Ledger(e.DB).pull();const s=await p.registration('${INSTALL}');
    try{return Response.json(await p.enqueue({...s,connection_id:s.connection_id??undefined},v));}
    catch(e){return new Response(e.stack,{status:500});}
  }
  return e.BOX.get(e.BOX.idFromName('${INSTALL}')).fetch(new Request('${ORIGIN}'+u.pathname+u.search,r));
}};`;

// Inline REAL workerd DO and D1. Load only transitive production modules; no
// replacement WebSocket, state, storage, SQL, SessionAuth or PullLedger objects.
function modules() {
  const result = [{ type: 'ESModule', path: entry, contents: wrapper }];
  const seen = new Set();
  function add(path) {
    if (seen.has(path)) return; seen.add(path);
    const contents = readFileSync(path, 'utf8');
    result.push({ type: 'ESModule', path, contents });
    for (const m of contents.matchAll(/from ['"]\.\/([^'"]+)['"]/g)) add(`${root}${m[1]}`);
  }
  for (const name of ['pull-mailbox.js','session-auth.js','ledger.js']) add(`${root}${name}`);
  return result;
}
async function keypair() {
  const p = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  return { key: p.privateKey, verify: p.publicKey, public: base64url(await crypto.subtle.exportKey('raw', p.publicKey)),
    private: base64url(await crypto.subtle.exportKey('pkcs8', p.privateKey)) };
}
async function fixture(t, count = 0, body = 'reply', fixedClock = 0) {
  const service = await keypair(); const candidate = await keypair();
  const mf = new Miniflare({ name: 'mailbox-test', modules: modules(), compatibilityDate: '2026-07-01',
    durableObjects: { BOX: 'TestMailbox' }, d1Databases: ['DB'],
    bindings: { PRIVATE_KEY: service.private, KEY_EXPIRES:Date.now()+2*86400000, TEST_CLOCK:fixedClock },
    log: new Log(LogLevel.ERROR) });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB');
  // D1.exec's line parser does not accept multiline CREATE statements. Flatten
  // newlines after stripping comments, preserving triggers and quoted values.
  const migrationDir = new URL('../migrations/', import.meta.url);
  const sources = [readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'),
    ...readdirSync(migrationDir).filter((s) => /^\d+.*\.sql$/.test(s)).sort()
      .map((s) => readFileSync(new URL(s, migrationDir), 'utf8'))];
  for (const sql of sources) {
    await db.exec(sql.replace(/--[^\n]*/g, '').replace(/[\r\n]/g, ' '));
  }
  const now = Date.now();
  await db.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,active_since_ms,created_ms,updated_ms)
    VALUES(?,7,3,'active',?,'Owner@example.com',?,'current',?,'v1',?,?,?)`)
    .bind(INSTALL, 'a'.repeat(64), 'm'+'a'.repeat(29), candidate.public, now, now, now).run();
  await db.prepare(`INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',?,NULL)`)
    .bind(INSTALL, 'a'.repeat(64), candidate.public, 'f'.repeat(64), id(2), now).run();
  await db.prepare(`INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)`)
    .bind('m'+'a'.repeat(29), INSTALL, now).run();
  const f = { mf, db, service, candidate, next: 100, count: 0, body };
  for (let n = 0; n < count; n++) await enqueue(f);
  return f;
}
async function call(f, path, options) { return f.mf.dispatchFetch(ORIGIN + path, options); }
async function diagnostic(f) { return (await call(f, '/diagnostic')).json(); }
async function configure(f, value) {
  assert.equal((await call(f,'/test-config',{method:'POST',body:JSON.stringify(value)})).status,200);
}
async function enqueue(f, thread = `thread${++f.count}`) {
  const now = Date.now();
  await f.db.prepare(`INSERT OR IGNORE INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,
    thread_id,message_id,state,created_ms) VALUES(?,7,?,3,?,?,?,?,'accepted',?)`)
    .bind(INSTALL, thread, 'a'.repeat(64), 'b'.repeat(64), thread, `${thread}@example.com`, now).run();
  await f.db.prepare(`INSERT OR IGNORE INTO pull_threads VALUES(?,7,?,3,?,?,?, ?,?,NULL)`)
    .bind(INSTALL, thread, thread, await hash(thread), `${thread}@example.com`, now + 86400000, now + 31*86400000).run();
  const r = await call(f, '/enqueue', { method: 'POST', body: JSON.stringify({ thread_id: thread,
    incoming_message_id: `${id(f.next++)}@incoming.example`, text: f.body,
    auth: { ingress: 'cloudflare', spf: 'pass', dkim: 'pass', dmarc: 'pass', aligned: 'spf+dkim' } }) });
  assert.equal(r.status, 200, await r.clone().text()); return r.json();
}
async function ticket(f) {
  const raw = JSON.stringify({ op_id: id(f.next++) });
  const m = { version:'2',direction:'install-to-pull',key_id:'current',install_id:INSTALL,generation:'7',
    owner_generation:'3',timestamp:String(Date.now()),nonce:id(f.next++),digest:await hash(raw) };
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-http','1',m.direction,ORIGIN,'POST','/v1/pull/ticket',
    m.key_id,m.install_id,m.generation,m.owner_generation,m.timestamp,m.nonce,m.digest]));
  const headers = { 'content-type': 'application/json' };
  for (const [k,v] of Object.entries(m)) headers[`x-mail-${k.replaceAll('_','-')}`] = v;
  headers['x-mail-signature'] = base64url(await crypto.subtle.sign('Ed25519', f.candidate.key, input));
  const r = await call(f, '/v1/pull/ticket', { method: 'POST', headers, body: raw });
  assert.equal(r.status, 200, await r.clone().text()); return (await r.json()).ticket;
}
function inbox(ws) {
  const messages = []; const waiters = []; let closed;
  ws.addEventListener('message', (e) => { const value = JSON.parse(e.data); if (waiters.length) waiters.shift()(value); else messages.push(value); });
  ws.addEventListener('close', (e) => { closed = { code: e.code, reason: e.reason }; });
  ws.accept?.();
  return { ws, messages, next: async () => messages.length ? messages.shift() : new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('socket message timeout')), 5000);
    waiters.push((v) => { clearTimeout(timer); resolve(v); });
  }), closed: () => closed };
}
async function verified(f, wire) {
  assert.deepEqual(Object.keys(wire).sort(), ['v','type','install_id','generation','owner_generation','epoch','frame_id',
    'issued_ms','key_id','payload','payload_digest','signature'].sort());
  assert.equal(wire.install_id, INSTALL); assert.equal(wire.generation, 7); assert.equal(wire.owner_generation, 3);
  assert.equal(wire.key_id, 'service'); assert.match(wire.frame_id, /^[a-f0-9]{32}$/);
  const raw = unbase64url(wire.payload); assert.equal(await sha256(raw), wire.payload_digest);
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-frame','1',ORIGIN,wire.type,wire.key_id,wire.install_id,
    String(wire.generation),String(wire.owner_generation),String(wire.epoch),wire.frame_id,String(wire.issued_ms),wire.payload_digest]));
  assert.equal(await crypto.subtle.verify('Ed25519', f.service.verify, unbase64url(wire.signature), input), true);
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
}
async function upgrade(f, token, extra = {}) {
  return call(f, '/v1/pull/socket', { headers: { Upgrade:'websocket',Authorization:`MailTicket ${token}`,
    'Sec-WebSocket-Protocol':'clayrune-mail-pull.v1', ...extra } });
}
async function networkRefusal(f, token) {
  const url=new URL('/v1/pull/socket',await f.mf.ready);
  return new Promise((resolve,reject)=>{
    const r=httpRequest(url,{headers:{Upgrade:'websocket',Connection:'Upgrade',Authorization:'MailTicket '+token,
      'Sec-WebSocket-Protocol':'clayrune-mail-pull.v1','Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ==','Sec-WebSocket-Version':'13'}},res=>{
      let body='';res.setEncoding('utf8');res.on('data',v=>body+=v);res.on('end',()=>resolve({status:res.statusCode,body}));
    });r.on('error',reject);r.end();
  });
}
async function connect(f) {
  const r = await upgrade(f, await ticket(f)); assert.equal(r.status, 101, r.status === 101 ? '' : await r.text());
  const box = inbox(r.webSocket); const hello = await box.next(); assert.equal(hello.type, 'hello');
  box.epoch = hello.epoch; box.hello = await verified(f, hello);
  assert.equal(box.hello.reauth_ms-box.hello.authenticated_ms,86400000);return box;
}
async function networkConnect(f) {
  const token = await ticket(f); const url = new URL('/v1/pull/socket', await f.mf.ready); url.protocol='ws:';
  const ws = new WebSocket(url, 'clayrune-mail-pull.v1', { headers: { Authorization:'MailTicket '+token } });
  const box = inbox(ws);
  ws.once('open',()=>ws._receiver.once('conclude',(code,reason)=>{box.controlClose={code,reason:String(reason),at:Date.now()};}));
  await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject);});
  const hello=await box.next(); assert.equal(hello.type,'hello');
  box.epoch=hello.epoch; box.hello=await verified(f,hello); return box;
}
async function controlClose(box, timeout=15000) {
  const end=Date.now()+timeout;
  while(Date.now()<end){if(box.controlClose)return box.controlClose;await new Promise(r=>setTimeout(r,10));}
  throw new Error('close control frame timeout');
}
function send(f, box, type, payload, changes = {}) {
  box.ws.send(JSON.stringify({ v:1,type,install_id:INSTALL,generation:7,owner_generation:3,epoch:box.epoch,
    request_id:id(f.next++),payload,...changes }));
}
async function page(f, box, payload = { after_seq:0,limit:10,available_bytes:524288 }) {
  send(f, box, 'resume', payload); const events = [];
  for (;;) {
    const frame = await box.next(); const p = await verified(f, frame);
    if (frame.type === 'status') return { events, status:p };
    assert.equal(frame.type, 'event', JSON.stringify(p)); events.push(p);
  }
}
async function closed(box, timeout = 5000) {
  const end=Date.now()+timeout;
  for (;Date.now()<end;) { if (box.closed()) return box.closed(); await new Promise((r) => setTimeout(r, 10)); }
  throw new Error('socket close timeout; state='+box.ws.readyState+'; messages='+box.messages.map(m=>m.type));
}
const ack = (offer) => ({ event_id:offer.event.event_id,event_digest:offer.event.event_digest,
  body_digest:offer.event.body_digest,lease_id:offer.lease_id,local_receipt_id:id(9999) });

test('real Miniflare hibernating eviction preserves socket attachment, window, resume state and signed ack', async (t) => {
  const f = await fixture(t, 2); const box = await connect(f); const first = await page(f, box);
  assert.equal(first.events.length, 2); const before = await diagnostic(f);
  assert.deepEqual(Object.keys(before.attachments[0]).sort(), ['v','install_id','generation','owner_generation','key_id',
    'epoch','connection_id','authenticated_ms','reauth_ms'].sort());
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  const after = await diagnostic(f);
  assert.notEqual(after.instance, before.instance); assert.deepEqual(after.attachments, before.attachments);
  assert.deepEqual(after.meta, before.meta); assert.equal(box.closed(), undefined);
  send(f, box, 'custody_ack', ack(first.events[0]));
  const r = await box.next(); assert.equal(r.type, 'ack_committed'); const receipt = await verified(f, r);
  assert.equal(receipt.local_receipt_id, id(9999)); assert.equal((await box.next()).type, 'status');
  const row = await f.db.prepare('SELECT state,text FROM pull_events WHERE event_id=?').bind(receipt.event_id).first();
  assert.deepEqual(row, {state:'local_received',text:null});
});

test('tickets are single-use, expire, reject browser/protocol/foreign scope before epoch commit', async (t) => {
  const f = await fixture(t); const token = await ticket(f); const r = await upgrade(f, token);
  assert.equal(r.status, 101); const box = inbox(r.webSocket); await box.next();
  assert.equal((await upgrade(f, token)).status, 401);
  const expired = await ticket(f);
  await f.db.prepare('UPDATE pull_tickets SET expires_ms=? WHERE ticket_hash=?').bind(Date.now()-1, await hash(expired)).run();
  assert.equal((await upgrade(f, expired)).status, 401);
  const token2 = await ticket(f);
  assert.equal((await upgrade(f, token2, { Origin:ORIGIN })).status, 401);
  assert.equal((await upgrade(f, token2, { 'Sec-WebSocket-Protocol':'wrong' })).status, 426);
  assert.equal((await f.db.prepare('SELECT epoch FROM pull_installs').first()).epoch, 1);
  const second=await upgrade(f,token2);assert.equal(second.status,101);const next=inbox(second.webSocket);await next.next();
  const foreign=await ticket(f);
  await f.db.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
    address_local,key_id,public_key,consent_version,created_ms,updated_ms)
    SELECT ?,generation,owner_generation,state,owner_hash,owner_address,?,key_id,public_key,consent_version,created_ms,updated_ms
    FROM pull_installs WHERE install_id=?`).bind(id(2),'m'+'b'.repeat(29),INSTALL).run();
  await f.db.prepare(`INSERT INTO pull_generations SELECT ?,generation,owner_generation,owner_hash,key_id,public_key,
    fence_hash,flow_id,state,created_ms,fenced_ms FROM pull_generations WHERE install_id=?`).bind(id(2),INSTALL).run();
  await f.db.prepare('UPDATE pull_tickets SET install_id=? WHERE ticket_hash=?').bind(id(2),await hash(foreign)).run();
  assert.equal((await upgrade(f,foreign)).status,401);
});

test('new epoch closes old socket 4001; forged old epoch on new connection cannot ack', async (t) => {
  const f = await fixture(t, 1); const old = await connect(f); const offered = await page(f, old);
  const box = await connect(f); assert.equal(box.epoch, old.epoch+1);
  assert.equal((await closed(old)).code, 4001);
  send(f, box, 'custody_ack', ack(offered.events[0]), { epoch:old.epoch });
  assert.equal((await closed(box)).code, 4001);
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n, 0);
});

test('late ack after epoch commit is refused even before the old transport has been closed',async(t)=>{
  const f=await fixture(t,1);const box=await connect(f);const p=await page(f,box);
  await f.db.prepare('UPDATE pull_installs SET epoch=epoch+1,connection_id=? WHERE install_id=?').bind(id(99),INSTALL).run();
  send(f,box,'custody_ack',ack(p.events[0]));assert.equal((await closed(box)).code,4001);
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,0);
  assert.equal((await f.db.prepare('SELECT text FROM pull_events').first()).text,'reply');
});

test('two concurrent ticket admissions leave exactly one current hibernating socket',async(t)=>{
  const f=await fixture(t);const boxes=await Promise.all([networkConnect(f),networkConnect(f)]);
  const low=boxes[0].epoch<boxes[1].epoch?0:1;assert.equal((await controlClose(boxes[low])).code,4001);
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  assert.equal((await closed(boxes[low])).code,4001);
  const d=await diagnostic(f);assert.equal(d.attachments.length,1);assert.equal(d.attachments[0].epoch,2);
  assert.equal(Object.keys(d.meta).length,1);
});

test('epoch change between offer preparation and D1 commit rolls back lease and refuses old transport',async(t)=>{
  const f=await fixture(t,1);const box=await connect(f);assert.equal((await call(f,'/race-offer')).status,200);
  send(f,box,'resume',{after_seq:0,limit:10,available_bytes:524288});assert.equal((await closed(box)).code,4001);
  assert.equal(box.messages.length,0);
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_offers').first()).n,0);
  assert.deepEqual(await f.db.prepare('SELECT state,text FROM pull_events').first(),{state:'pending',text:'reply'});
});

test('ten outstanding events survive leases and eviction; ack opens window and duplicate ack replays one receipt', async (t) => {
  const f = await fixture(t, 11); const box = await connect(f); const first = await page(f, box);
  assert.equal(first.events.length, 10); assert.equal(first.status.scan_complete, false);
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  const full = await page(f, box); assert.equal(full.events.length, 0);
  send(f, box, 'custody_ack', ack(first.events[0]));
  const committed = await box.next(); const receipt = await verified(f, committed); assert.equal(committed.type,'ack_committed');
  const next = await box.next(); assert.equal(next.type,'event'); await verified(f,next); assert.equal((await box.next()).type,'status');
  send(f, box, 'custody_ack', ack(first.events[0]));
  const duplicate = await box.next(); assert.equal(duplicate.type,'ack_committed');
  const repeated = await verified(f,duplicate); assert.equal(repeated.receipt_id,receipt.receipt_id);
  assert.equal(repeated.committed_ms,receipt.committed_ms); assert.equal((await box.next()).type,'status');
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,1);
});

test('generation switch mid-connection closes at next wake and cannot delete body', async (t) => {
  const f = await fixture(t,1); const box = await connect(f); const p = await page(f,box);
  await f.db.prepare('UPDATE pull_installs SET generation=generation+1').run();
  await call(f,'/wake'); assert.equal((await closed(box)).code,4003);
  assert.equal((await f.db.prepare('SELECT text FROM pull_events WHERE event_id=?').bind(p.events[0].event.event_id).first()).text,'reply');
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,0);
});

test('hold retains body; wake resumes a bounded page; zero byte capacity offers nothing', async (t) => {
  const f = await fixture(t,1); const box = await connect(f);
  assert.equal((await page(f,box,{after_seq:0,limit:10,available_bytes:0})).events.length,0);
  const p = await page(f,box); const e=p.events[0];
  send(f,box,'hold',{event_id:e.event.event_id,event_digest:e.event.event_digest,lease_id:e.lease_id,
    reason:'conversation_busy',retry_after_ms:0});
  const reply=await box.next(); assert.equal(reply.type,'hold'); const hold=await verified(f,reply);
  assert.ok(hold.retry_after_ms>=Date.now()+59000); assert.equal((await box.next()).type,'status');
  assert.equal((await f.db.prepare('SELECT text FROM pull_events').first()).text,'reply');
  await enqueue(f); assert.deepEqual(await (await call(f,'/wake')).json(),{connected:true});
  assert.equal((await box.next()).type,'event'); assert.equal((await box.next()).type,'status');
});

test('resume timeout and absolute reauth deadlines close through hibernation-compatible alarm', async (t) => {
  const f=await fixture(t); const box=await networkConnect(f);
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  const timeout=await controlClose(box);assert.equal(timeout.code,4002);
  assert.ok(timeout.at>=box.hello.authenticated_ms+10000);
  // workerd's forwarding hop can keep TCP in CLOSING until idle eviction. The
  // real peer already observed the exact close control frame; force that real
  // eviction rather than replacing a socket/runtime or waiting its idle timer.
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  assert.equal((await closed(box)).code,4002);
  const again=await networkConnect(f); await page(f,again);
  await call(f,'/deadline?kind=reauth'); assert.equal((await controlClose(again)).code,4004);
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  assert.equal((await closed(again)).code,4004);
  assert.equal((await diagnostic(f)).alarm,null);
});

for (const [name,wire,code,reason] of [
  ['oversized', ' '.repeat(160*1024+1),1009,'frame_limit'],
  ['invalid JSON','{"v":1,"v":1}',4002,'invalid_json'],
  ['binary',new Uint8Array([1,2]),4002,'invalid_json'],
  ['oversized binary',new Uint8Array(160*1024+1),1009,'frame_limit'],
]) test(`${name} frame receives signed error and contract close`,async(t)=>{
  const f=await fixture(t);const box=await connect(f);box.ws.send(wire);
  const e=await box.next();assert.equal(e.type,'error');assert.equal((await verified(f,e)).code,reason);
  assert.equal((await closed(box)).code,code);
});

test('over-count resumes refuse with request_limit, including after actual eviction',async(t)=>{
  const f=await fixture(t,0,'reply',Date.now());const box=await connect(f);
  for(let n=0;n<60;n++) {const p=await page(f,box);assert.equal(p.events.length,0);}
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  send(f,box,'resume',{after_seq:0,limit:10,available_bytes:524288});
  const e=await box.next();assert.equal(e.type,'error');assert.equal((await verified(f,e)).code,'request_limit');
  assert.equal((await closed(box)).code,4002);
});

test('encoded byte window stops below ten events and one exact ack reopens byte capacity',async(t)=>{
  const f=await fixture(t,4,'\x01'.repeat(16384));const box=await connect(f);const p=await page(f,box);
  assert.equal(p.events.length,3);assert.equal(p.status.scan_complete,false);
  const d=await diagnostic(f);const m=Object.values(d.meta)[0];
  assert.equal(Object.keys(m.outstanding).length,3);
  assert.ok(Object.values(m.outstanding).reduce((n,v)=>n+v,0)<=524288);
  send(f,box,'custody_ack',ack(p.events[0]));assert.equal((await box.next()).type,'ack_committed');
  const offered=await box.next();assert.equal(offered.type,'event');await verified(f,offered);
  assert.equal((await box.next()).type,'status');
});

test('expired lease can be reoffered without growing the window, then original lease ack still commits',async(t)=>{
  const f=await fixture(t,1);const box=await connect(f);const p=await page(f,box);const original=p.events[0];
  const until=Date.now()-1;
  await f.db.prepare('UPDATE pull_events SET lease_until_ms=?').bind(until).run();
  await f.db.prepare('UPDATE pull_offers SET lease_until_ms=?').bind(until).run();
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  const again=await page(f,box);assert.equal(again.events.length,1);
  assert.equal(again.events[0].event.event_id,original.event.event_id);assert.notEqual(again.events[0].lease_id,original.lease_id);
  assert.equal(Object.keys(Object.values((await diagnostic(f)).meta)[0].outstanding).length,1);
  send(f,box,'custody_ack',ack(original));assert.equal((await box.next()).type,'ack_committed');
  assert.equal((await box.next()).type,'status');
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,1);
});

test('lost ack confirmation replays before resume after reconnect and detail purge without another receipt',async(t)=>{
  const f=await fixture(t,1);const old=await connect(f);const p=await page(f,old);
  send(f,old,'custody_ack',ack(p.events[0]));const wire=await old.next();const receipt=await verified(f,wire);
  assert.equal(wire.type,'ack_committed');assert.equal((await old.next()).type,'status');
  await f.db.prepare('DELETE FROM pull_events').run();
  const box=await connect(f);send(f,box,'custody_ack',ack(p.events[0]));
  const replay=await box.next();assert.equal(replay.type,'ack_committed');
  const result=await verified(f,replay);assert.equal(result.receipt_id,receipt.receipt_id);
  assert.equal(result.committed_ms,receipt.committed_ms);assert.equal((await page(f,box)).events.length,0);
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,1);
});

test('thread head blocks its later event across pages while another thread can be offered',async(t)=>{
  const f=await fixture(t);const first=await enqueue(f,'same');const second=await enqueue(f,'same');await enqueue(f,'unrelated');
  const box=await connect(f);const head=await page(f,box,{after_seq:0,limit:1,available_bytes:524288});
  assert.equal(head.events.length,1);assert.equal(head.events[0].event.event_id,first.event_id);
  const blocked=await page(f,box,{after_seq:first.seq,limit:1,available_bytes:524288});
  assert.equal(blocked.events.length,0);assert.equal(blocked.status.next_seq,second.seq);
  const other=await page(f,box,{after_seq:second.seq,limit:1,available_bytes:524288});assert.equal(other.events.length,1);
  send(f,box,'custody_ack',ack(head.events[0]));assert.equal((await box.next()).type,'ack_committed');
  const next=await box.next();assert.equal(next.type,'event');assert.equal((await verified(f,next)).event.event_id,second.event_id);
  assert.equal((await box.next()).type,'status');
});

test('120 total client frames count failed ack operations and survive eviction',async(t)=>{
  const f=await fixture(t,0,'reply',Date.now());const box=await connect(f);await page(f,box);
  const value={event_id:'f'.repeat(64),event_digest:'f'.repeat(64),body_digest:'f'.repeat(64),lease_id:id(1),local_receipt_id:id(9)};
  for(let n=0;n<119;n++){
    send(f,box,'custody_ack',value);const e=await box.next();assert.equal(e.type,'error');assert.equal((await verified(f,e)).code,'not_found');
    if(n===60)await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  }
  assert.equal(Object.values((await diagnostic(f)).meta)[0].frames,120);
  send(f,box,'custody_ack',value);const e=await box.next();assert.equal(e.type,'error');
  assert.equal((await verified(f,e)).code,'request_limit');assert.equal((await closed(box)).code,4002);
});

test('hello signing failure burns the admitted ticket but cleans socket, metadata and alarm',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);await configure(f,{servicePrivateKey:'AA'});
  const r=await upgrade(f,token);assert.equal(r.status,503);assert.equal(r.headers.get('retry-after'),'60');
  const d=await diagnostic(f);assert.equal(d.attachments.length,0);assert.deepEqual(d.meta,{});assert.equal(d.alarm,null);
  assert.equal((await f.db.prepare('SELECT state FROM pull_tickets WHERE ticket_hash=?').bind(await hash(token)).first()).state,'consumed');
  assert.equal((await f.db.prepare('SELECT epoch FROM pull_installs').first()).epoch,1);
  assert.equal((await upgrade(f,token)).status,401);
});

test('retired service key refuses admission without consuming a ticket',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);await configure(f,{serviceKeyExpiresMs:1});
  const r=await networkRefusal(f,token);assert.equal(r.status,503);assert.equal(JSON.parse(r.body).code,'service_unavailable');
  assert.equal((await f.db.prepare('SELECT state FROM pull_tickets').first()).state,'issued');
  assert.equal((await diagnostic(f)).attachments.length,0);
});

test('service key change closes current socket before wake can offer or delete mail',async(t)=>{
  const f=await fixture(t,1);const box=await connect(f);await page(f,box,{after_seq:0,limit:10,available_bytes:0});
  await configure(f,{serviceKeyId:'replacement'});await call(f,'/wake');
  assert.equal((await closed(box)).code,4004);
  assert.equal((await f.db.prepare('SELECT state,text FROM pull_events').first()).state,'pending');
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,0);
});

test('missing socket state closes for retry without an overdue alarm loop or lost cloud custody',async(t)=>{
  const f=await fixture(t,1);const box=await connect(f);await page(f,box);
  assert.equal((await call(f,'/drop-meta')).status,200);await call(f,'/wake');
  assert.equal((await closed(box)).code,1013);assert.equal((await diagnostic(f)).alarm,null);
  assert.equal((await f.db.prepare('SELECT text FROM pull_events').first()).text,'reply');
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_receipts').first()).n,0);
});

test('service-key retirement sends signed reauth notice, stops offers and closes at strict deadline',async(t)=>{
  const f=await fixture(t,1);const box=await networkConnect(f);await page(f,box,{after_seq:0,limit:10,available_bytes:0});
  const expiry=Date.now()+5000;await configure(f,{serviceKeyExpiresMs:expiry});
  const wire=await box.next();assert.equal(wire.type,'reauth_required');const notice=await verified(f,wire);
  assert.deepEqual(notice,{reason:'service_key_changed',close_at_ms:expiry});
  send(f,box,'resume',{after_seq:0,limit:10,available_bytes:524288});
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_offers').first()).n,0);
  const result=await controlClose(box);assert.equal(result.code,4004);assert.ok(result.at>=expiry);
  assert.equal(box.messages.length,0);
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  assert.equal((await closed(box)).code,4004);
});

test('fragmented messages are checked as one bounded message before JSON or ledger work',async(t)=>{
  const f=await fixture(t);const box=await networkConnect(f);
  box.ws.send(' '.repeat(90*1024),{fin:false});box.ws.send(' '.repeat(90*1024),{fin:true});
  const e=await box.next();assert.equal(e.type,'error');assert.equal((await verified(f,e)).code,'frame_limit');
  assert.equal((await controlClose(box)).code,1009);
  await f.mf.unsafeEvictDurableObject('mailbox-test','TestMailbox',{name:INSTALL,webSockets:'hibernate'});
  assert.equal((await closed(box)).code,1009);
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM pull_offers').first()).n,0);
});

const frame = { v:1,type:'resume',install_id:INSTALL,generation:7,owner_generation:3,epoch:1,request_id:id(1),
  payload:{after_seq:0,limit:10,available_bytes:524288} };
for(const [name,wire] of [
  ['escaped duplicate',JSON.stringify(frame).replace('"v":1','"v":1,"\\u0076":1')],
  ['payload duplicate',JSON.stringify(frame).replace('"limit":10','"limit":10,"limit":9')],
  ['unknown field',JSON.stringify({...frame,extra:1})],
  ['unknown type',JSON.stringify({...frame,type:'send'})],
  ['negative zero',JSON.stringify(frame).replace('"after_seq":0','"after_seq":-0')],
  ['fraction',JSON.stringify(frame).replace('"after_seq":0','"after_seq":0.0')],
  ['BOM','\ufeff'+JSON.stringify(frame)],
  ['non-JSON whitespace','\u00a0'+JSON.stringify(frame)],
  ['surrogate',JSON.stringify(frame).replace('"resume"','"\\ud800"')],
  ['array',JSON.stringify({...frame,payload:[]})],
  ['deep object',JSON.stringify({...frame,payload:{a:{b:1}}})],
  ['over limit',JSON.stringify({...frame,payload:{after_seq:0,limit:11,available_bytes:524288}})],
  ['unsafe number',JSON.stringify(frame).replace('"after_seq":0','"after_seq":9007199254740992')],
  ['trailing',JSON.stringify(frame)+'x'],
  ['decoded payload over bound',JSON.stringify({...frame,payload:{...frame.payload,padding:'x'.repeat(112*1024)}})],
  ['decoded raw whitespace over bound',JSON.stringify(frame).replace('"payload":{','"payload":{'+ ' '.repeat(112*1024))],
])test(`strict client codec rejects ${name}`,()=>assert.throws(()=>clientFrame(wire)));
