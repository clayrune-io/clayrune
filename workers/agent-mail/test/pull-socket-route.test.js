import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { request as httpRequest } from 'node:http';
import { Miniflare, Log, LogLevel } from 'miniflare';
import { PullSocketRoute, resolveSocketInstall, socketTrustedSource } from '../pull-socket-route.js';
import { pullSocketConfig } from '../pull-socket-config.js';
import { bytes, sha256, base64url, unbase64url } from '../signing.js';

const ORIGIN = 'https://mail.example.com';
const INSTALL = '1'.repeat(32), OTHER = '2'.repeat(32);
const hash = (v) => sha256(bytes(v));
const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
const request = (ticket, extra = {}) => new Request(ORIGIN + '/v1/pull/socket', { headers: {
  upgrade: 'websocket', 'sec-websocket-protocol': 'clayrune-mail-pull.v1',
  authorization: `MailTicket ${ticket}`, 'cf-connecting-ip': '192.0.2.10', ...extra } });

// Test entry only selects production adapters, with no fake DO, socket,
// SessionAuth or SQL. /wrong deliberately misroutes the internal call to test
// the actual DO-id fence before any ticket consumption. Never a public route.
const wrapper = `
import { PullSocketMailbox } from './pull-socket-mailbox.js';
import { PullSocketRoute } from './pull-socket-route.js';
import { pullSocketConfig } from './pull-socket-config.js';
export { PullSocketMailbox };
export default {async fetch(r,e){
  const u=new URL(r.url);
  // Native HTTP reaches Miniflare's localhost address; match the existing
  // mailbox harness's canonical service URL, never a production URL rewrite.
  r=new Request('${ORIGIN}'+u.pathname+u.search,r);
  if(u.pathname==='/wake')return Response.json(await e.MAIL_PULL_MAILBOX
    .get(e.MAIL_PULL_MAILBOX.idFromName('${INSTALL}')).wake('${INSTALL}'));
  if(u.pathname==='/wrong')return e.MAIL_PULL_MAILBOX.get(e.MAIL_PULL_MAILBOX.idFromName('${OTHER}'))
    .admit(new Request('${ORIGIN}/v1/pull/socket',r),{install_id:'${INSTALL}',trustedSource:'edge:192.0.2.10'});
  if(u.pathname==='/race-consume'||u.pathname==='/race-fence'){
    const ns=e.MAIL_PULL_MAILBOX;
    const routed={idFromName:id=>ns.idFromName(id),get:id=>({fetch:async(req)=>{
      if(u.pathname==='/race-consume')await e.MAIL_DB.exec("UPDATE pull_tickets SET state='consumed'");
      else await e.MAIL_DB.exec("UPDATE pull_installs SET state='disabled'");
      return ns.get(id).fetch(req);
    }})};
    return new PullSocketRoute(pullSocketConfig(e),routed).handle(new Request('${ORIGIN}/v1/pull/socket',r));
  }
  return new PullSocketRoute(pullSocketConfig(e),e.MAIL_PULL_MAILBOX).handle(r);
}};`;
function modules() {
  const out = [{ type: 'ESModule', path: root + 'socket-route-test-entry.js', contents: wrapper }];
  const seen = new Set();
  function add(name) {
    if (seen.has(name)) return; seen.add(name);
    const contents = readFileSync(root + name, 'utf8');
    out.push({ type: 'ESModule', path: root + name, contents });
    for (const m of contents.matchAll(/from ['"]\.\/([^'"]+)['"]/g)) add(m[1]);
  }
  ['pull-socket-mailbox.js','pull-socket-route.js'].forEach(add);
  return out;
}
async function keypair() {
  const p = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  return { private: base64url(await crypto.subtle.exportKey('pkcs8',p.privateKey)),
    public: base64url(await crypto.subtle.exportKey('raw',p.publicKey)), verify: p.publicKey };
}
function envFor(key, now = Date.now()) {
  return { MAIL_SERVICE_ORIGIN: ORIGIN, MAIL_SERVICE_KEY_ID: 'service', MAIL_SERVICE_PRIVATE_KEY: key.private,
    MAIL_SERVICE_PIN_EXPIRES_MS: String(now + 86400000),
    MAIL_SERVICE_MANIFEST: JSON.stringify({ manifest: ['clayrune-mail-service-manifest','1',ORIGIN,'1',
      String(now-10000),String(now+172800000),'service',key.public,String(now-10000),String(now+259200000),[]],
    signature: 'A'.repeat(86) }) };
}
async function fixture(t, override = {}) {
  const service = await keypair(), candidate = await keypair();
  const mf = new Miniflare({ name: 'socket-route-test', modules: modules(), compatibilityDate: '2026-07-01',
    durableObjects: { MAIL_PULL_MAILBOX: 'PullSocketMailbox' }, d1Databases: ['MAIL_DB'],
    bindings: { ...envFor(service), ...override }, log: new Log(LogLevel.ERROR) });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('MAIL_DB');
  const dir = new URL('../migrations/', import.meta.url);
  for (const sql of [readFileSync(new URL('../schema.sql',import.meta.url),'utf8'),
    ...readdirSync(dir).filter(s=>/^\d+.*\.sql$/.test(s)).sort().map(s=>readFileSync(new URL(s,dir),'utf8'))]) {
    await db.exec(sql.replace(/--[^\n]*/g,'').replace(/[\r\n]/g,' '));
  }
  const now = Date.now();
  for (const [id, local] of [[INSTALL,'a'],[OTHER,'b']]) {
    await db.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,
      address_local,key_id,public_key,consent_version,active_since_ms,created_ms,updated_ms)
      VALUES(?,7,3,'active',?,'Owner@example.com',?,'current',?,'v1',?,?,?)`)
      .bind(id,'a'.repeat(64),'m'+local.repeat(29),candidate.public,now,now,now).run();
    await db.prepare(`INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',?,NULL)`)
      .bind(id,'a'.repeat(64),candidate.public,'f'.repeat(64),'a'.repeat(32),now).run();
    await db.prepare(`INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)`)
      .bind('m'+local.repeat(29),id,now).run();
  }
  return { mf, db, service, next: 1, config: pullSocketConfig({ ...envFor(service), MAIL_DB: db }) };
}
async function ticket(f, install = INSTALL) {
  const secret = (f.next++).toString(16).padStart(64,'0');
  const now = Date.now();
  await f.db.prepare(`INSERT INTO pull_tickets VALUES(?,?,?,?,7,3,'current','issued',?,?,NULL)`)
    .bind(await hash(secret),secret.slice(32),secret.slice(32),install,now,now+60000).run();
  return secret;
}
async function upgrade(f, token, extra = {}, path = '/v1/pull/socket') {
  return f.mf.dispatchFetch(ORIGIN+path,{ headers: Object.fromEntries(request(token,extra).headers) });
}
async function refusal(f, token, extra = {}, path = '/v1/pull/socket') {
  const url=new URL(path,await f.mf.ready);
  return new Promise((resolve,reject)=>{
    const r=httpRequest(url,{headers:{...Object.fromEntries(request(token,extra).headers),Connection:'Upgrade'}},res=>{
      let body='';res.setEncoding('utf8');res.on('data',part=>body+=part);
      res.on('end',()=>resolve({status:res.statusCode,body,headers:res.headers}));
    });
    r.on('upgrade',(_res,socket)=>{socket.destroy();reject(new Error('unexpected 101 instead of refusal'));});
    r.setTimeout(5000,()=>r.destroy(new Error('refusal timeout')));
    r.on('error',reject);r.end();
  });
}
async function hello(t, response, f, install = INSTALL) {
  assert.equal(response.status,101, response.status === 101 ? '' : await response.text());
  const ws = response.webSocket;
  const wire = new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('hello timeout')),3000);
    ws.addEventListener('message',e=>{clearTimeout(timeout);resolve(JSON.parse(e.data));},{once:true});
  });
  ws.accept(); t.after(()=>{ if(ws.readyState===1)ws.close(1000,'done'); });
  const frame = await wire;
  assert.equal(frame.type,'hello'); assert.equal(frame.install_id,install);
  assert.equal(frame.payload_digest,await sha256(unbase64url(frame.payload)));
  const input = bytes(JSON.stringify(['clayrune-agent-mail-pull-frame','1',ORIGIN,frame.type,frame.key_id,
    frame.install_id,String(frame.generation),String(frame.owner_generation),String(frame.epoch),frame.frame_id,String(frame.issued_ms),frame.payload_digest]));
  assert.equal(await crypto.subtle.verify('Ed25519',f.service.verify,unbase64url(frame.signature),input),true);
  return frame;
}

test('real socket route selects each per-install DO; consumes once and ignores forged scope/source headers',async(t)=>{
  const f=await fixture(t);const a=await ticket(f),b=await ticket(f,OTHER);
  const first=await hello(t,await upgrade(f,a,{'x-mail-install-id':OTHER,'x-install-id':OTHER,
    'x-clayrune-socket-install':OTHER,'x-clayrune-socket-source':'attacker',
    'x-trusted-source':'forged','x-forwarded-for':'203.0.113.1','x-real-ip':'203.0.113.2'}),f);
  await hello(t,await upgrade(f,b),f,OTHER);
  assert.equal(first.epoch,1);
  assert.equal((await f.db.prepare('SELECT count(*) n FROM pull_tickets WHERE state=?').bind('consumed').first()).n,2);
  assert.equal((await upgrade(f,a)).status,401);
  assert.equal((await f.db.prepare('SELECT epoch FROM pull_installs WHERE install_id=?').bind(INSTALL).first()).epoch,1);
  const bucket=`pull:http:source:${await hash('edge:192.0.2.10')}:`;
  const limits=(await f.db.prepare('SELECT bucket,count FROM rate_limits').all()).results;
  assert.equal(limits.filter(r=>r.bucket.startsWith(bucket)).reduce((n,r)=>n+r.count,0),3);
});

test('indexed discovery is read-only, including successful repeated lookups',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);
  for(let n=0;n<2;n++)assert.equal(await resolveSocketInstall(request(token),f.config),INSTALL);
  assert.deepEqual(await f.db.prepare('SELECT state,consumed_ms FROM pull_tickets').first(),{state:'issued',consumed_ms:null});
  assert.equal((await f.db.prepare('SELECT epoch FROM pull_installs WHERE install_id=?').bind(INSTALL).first()).epoch,0);
  assert.equal((await f.db.prepare('SELECT count(*) n FROM rate_limits').first()).n,0);
});

test('two concurrent routes for one ticket produce one 101 and exactly one epoch',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);
  const responses=await Promise.all([upgrade(f,token),upgrade(f,token)]);
  assert.deepEqual(responses.map(r=>r.status).sort(),[101,401]);
  await hello(t,responses.find(r=>r.status===101),f);
  assert.equal((await f.db.prepare('SELECT epoch FROM pull_installs WHERE install_id=?').bind(INSTALL).first()).epoch,1);
  assert.equal((await f.db.prepare('SELECT state FROM pull_tickets').first()).state,'consumed');
});

test('actual DO refuses wrong internal scope before the ticket can be consumed',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);
  assert.equal((await upgrade(f,token,{},'/wrong')).status,401);
  assert.deepEqual(await f.db.prepare('SELECT state,consumed_ms FROM pull_tickets').first(),{state:'issued',consumed_ms:null});
  assert.equal((await f.db.prepare('SELECT sum(epoch) n FROM pull_installs').first()).n,0);
  await hello(t,await upgrade(f,token),f);
});

for(const [name,sql] of [
  ['expired ticket',"UPDATE pull_tickets SET expires_ms=created_ms"],
  ['future ticket',"UPDATE pull_tickets SET created_ms=expires_ms-1"],
  ['consumed ticket',"UPDATE pull_tickets SET state='consumed'"],
  ['invalidated ticket',"UPDATE pull_tickets SET state='invalidated'"],
  ['old owner scope',"UPDATE pull_tickets SET owner_generation=2"],
  ['wrong key scope',"UPDATE pull_tickets SET key_id='other'"],
  ['inactive install',"UPDATE pull_installs SET state='disabled'"],
  ['fenced scope',`INSERT INTO pull_fences VALUES('${INSTALL}','${'a'.repeat(32)}',7,'${'f'.repeat(64)}','revoke','${'c'.repeat(32)}','${'d'.repeat(32)}','${'b'.repeat(64)}',0)`],
])test(`real route refuses ${name} without an epoch bump`,async(t)=>{
  const f=await fixture(t);const token=await ticket(f);await f.db.exec(sql);
  const r=await refusal(f,token);const body=JSON.parse(r.body);assert.equal(r.status,401,JSON.stringify(body));
  assert.deepEqual(body,{v:1,code:'ticket_invalid'});
  assert.equal((await f.db.prepare('SELECT sum(epoch) n FROM pull_installs').first()).n,0);
});

test('bad and unknown tickets refuse without choosing a caller-specified DO',async(t)=>{
  const f=await fixture(t);
  for(const value of ['garbage','f'.repeat(64)]) {
    const r=await upgrade(f,value,{'x-mail-install-id':INSTALL});assert.equal(r.status,401);
  }
  assert.equal((await f.db.prepare('SELECT sum(epoch) n FROM pull_installs').first()).n,0);
});

for(const path of ['/race-consume','/race-fence'])test(`DO repeats authority after route discovery: ${path}`,async(t)=>{
  const f=await fixture(t);const token=await ticket(f);
  assert.equal((await upgrade(f,token,{},path)).status,401);
  assert.equal((await f.db.prepare('SELECT sum(epoch) n FROM pull_installs').first()).n,0);
});

test('request bounds, browser Origin and protocol refusals retain ticket authority',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);
  for(const [extra,status] of [[{origin:ORIGIN},401],[{'sec-websocket-protocol':'bad'},426],
    [{'content-encoding':'gzip'},400],[{'x-padding':'a'.repeat(8200)},413]]) {
    assert.equal((await upgrade(f,token,extra)).status,status);
  }
  for(const path of ['/v1/pull/socket?install_id='+OTHER]) {
    assert.equal((await upgrade(f,token,{},path)).status,400);
  }
  // Miniflare's HTTP forwarding hop normalizes a bare '?'; exercise the wire
  // parser directly for this case, without claiming end-to-end raw URL fidelity.
  await assert.rejects(()=>resolveSocketInstall(new Request(ORIGIN+'/v1/pull/socket?',request(token)),f.config),/invalid_request/);
  assert.equal((await f.db.prepare('SELECT state FROM pull_tickets').first()).state,'issued');
});

test('trusted edge source ignores caller forwarding, canonicalizes IPv6 and groups Worker subrequests',()=>{
  const r=request('f'.repeat(64),{'x-forwarded-for':'attacker','x-trusted-source':'attacker'});
  assert.equal(socketTrustedSource(r),'edge:192.0.2.10');
  assert.equal(socketTrustedSource(request('f'.repeat(64),{'cf-connecting-ip':'2001:0db8::1'})),'edge:[2001:db8::1]');
  for(const ip of ['192.0.2.1','192.0.2.2'])assert.equal(socketTrustedSource(request('f'.repeat(64),
    {'cf-worker':'example.net','cf-connecting-ip':ip})),'cloudflare-worker');
  for(const ip of ['', 'not-an-ip','127.1','192.0.2.999','192.00.2.1'])
    assert.throws(()=>socketTrustedSource(request('f'.repeat(64),{'cf-connecting-ip':ip})),/service_unavailable/);
});

test('missing trusted edge source fails closed, without using forwarding headers',async(t)=>{
  const f=await fixture(t);const token=await ticket(f);
  const r=request(token);r.headers.delete('cf-connecting-ip');r.headers.set('x-forwarded-for','192.0.2.1');
  const response=await new PullSocketRoute(f.config,{}).handle(r);
  assert.equal(response.status,503);assert.equal((await f.db.prepare('SELECT state FROM pull_tickets').first()).state,'issued');
});

test('lookup failure is observable and closed; no fallback DO or secret in the refusal',async()=>{
  const key=await keypair();let queries=0,objects=0;
  const config={db:{prepare(){queries++;throw new Error('private SQL contents');}},serviceOrigin:ORIGIN,
    serviceKeyId:'service',servicePrivateKey:key.private};
  const route=new PullSocketRoute(config,{idFromName(){objects++;},get(){objects++;}});
  const response=await route.handle(request('f'.repeat(64)));
  assert.equal(response.status,503);assert.deepEqual(await response.json(),{v:1,code:'storage_unavailable'});
  assert.equal(queries,1);assert.equal(objects,0);
});

test('unknown-ticket refusals charge the bounded source bucket, including attempted source spoofing',async(t)=>{
  const f=await fixture(t);
  assert.equal((await upgrade(f,'f'.repeat(64),{'x-clayrune-socket-source':'forged'})).status,401);
  const prefix=`pull:http:source:${await hash('edge:192.0.2.10')}:`;
  const rows=(await f.db.prepare('SELECT bucket,count FROM rate_limits').all()).results;
  assert.equal(rows.filter(r=>r.bucket.startsWith(prefix)).reduce((n,r)=>n+r.count,0),1);
  // Seed the exhausted current AND next window; crossing a wall-clock minute
  // during this check must not make the rate-limit assertion flaky.
  const now=Date.now(),minute=Math.floor(now/60000);
  for(const n of [minute,minute+1]) {
    await f.db.prepare('INSERT INTO rate_limits VALUES(?,30,?) ON CONFLICT(bucket) DO UPDATE SET count=30')
      .bind(prefix+n,now+180000).run();
  }
  assert.equal((await upgrade(f,'e'.repeat(64),{'x-clayrune-socket-source':'new-forged'})).status,429);
  assert.equal((await f.db.prepare('SELECT sum(epoch) n FROM pull_installs').first()).n,0);
});

test('reviewed deadlines use earliest manifest/pin/retirement and reject missing or mismatched config',async()=>{
  const key=await keypair(),now=Date.now();const env={...envFor(key,now),MAIL_DB:{}};
  assert.equal(pullSocketConfig(env).serviceKeyExpiresMs,now+86400000);
  env.MAIL_SERVICE_PIN_EXPIRES_MS=String(now+345600000);
  assert.equal(pullSocketConfig(env).serviceKeyExpiresMs,now+172800000);
  for(const change of [{MAIL_SERVICE_PIN_EXPIRES_MS:undefined},{MAIL_SERVICE_PIN_EXPIRES_MS:'1'},
    {MAIL_SERVICE_KEY_ID:'different'},
    {MAIL_SERVICE_ORIGIN:'https://other.example.com'},{MAIL_SERVICE_MANIFEST:'{}'}])
    assert.throws(()=>pullSocketConfig({...env,...change}),/service_unavailable/);
  const m=JSON.parse(env.MAIL_SERVICE_MANIFEST);
  assert.equal(pullSocketConfig({...env,MAIL_SERVICE_PIN_EXPIRES_MS:String(now-1)}).serviceKeyExpiresMs,now-1);
  for(const [index,value] of [[9,String(now+10000)],[8,String(now+10000)],[10,['service']]]) {
    const changed=structuredClone(m);changed.manifest[index]=value;
    assert.throws(()=>pullSocketConfig({...env,MAIL_SERVICE_MANIFEST:JSON.stringify(changed)}),/service_unavailable/);
  }
});

test('expired reviewed pin loads for hibernation cleanup but refuses real admission without consume',async(t)=>{
  const f=await fixture(t,{MAIL_SERVICE_PIN_EXPIRES_MS:String(Date.now()-1)});const token=await ticket(f);
  const r=await refusal(f,token);assert.equal(r.status,503);assert.equal(JSON.parse(r.body).code,'service_unavailable');
  assert.equal((await f.db.prepare('SELECT state FROM pull_tickets').first()).state,'issued');
  assert.equal((await f.db.prepare('SELECT sum(epoch) n FROM pull_installs').first()).n,0);
});

test('hibernating production wrapper can reload at pin expiry and close without losing ticket history',async(t)=>{
  const expires=Date.now()+9000;
  const f=await fixture(t,{MAIL_SERVICE_PIN_EXPIRES_MS:String(expires)});const token=await ticket(f);
  await hello(t,await upgrade(f,token),f);
  await f.mf.unsafeEvictDurableObject('socket-route-test','PullSocketMailbox',{name:INSTALL,webSockets:'hibernate'});
  await new Promise(resolve=>setTimeout(resolve,Math.max(0,expires-Date.now()+100)));
  const wake=await f.mf.dispatchFetch(ORIGIN+'/wake');
  assert.equal(wake.status,200);assert.deepEqual(await wake.json(),{connected:false});
  assert.equal((await f.db.prepare('SELECT state FROM pull_tickets').first()).state,'consumed');
  assert.equal((await f.db.prepare('SELECT epoch FROM pull_installs WHERE install_id=?').bind(INSTALL).first()).epoch,1);
});

test('Node factory remains importable; production entry alone exports the real DO binding',async()=>{
  const worker=await import('../worker.js');assert.equal(typeof worker.createWorker,'function');
  const entry=readFileSync(new URL('../production.js',import.meta.url),'utf8');
  assert.match(entry,/export \{ default \} from '\.\/worker.js'/);
  assert.match(entry,/export \{ PullSocketMailbox \} from '\.\/pull-socket-mailbox.js'/);
  const config=readFileSync(new URL('../wrangler.toml.example',import.meta.url),'utf8');
  assert.match(config,/main = "production.js"/);assert.match(config,/class_name = "PullSocketMailbox"/);
});
