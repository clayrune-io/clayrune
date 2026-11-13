// Real SQLite/D1 rollback and real Ed25519; only time and fault hooks are fake.
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { PullActivateHTTP, ACTIVATE_PATH } from '../../pull-activate-http.js';
import { PullStatusHTTP, STATUS_PATH } from '../../pull-status-http.js';
import { PullFenceHTTP, FENCE_PATH } from '../../pull-fence-http.js';
import { PullKeyRotateHTTP, KEY_ROTATE_PATH } from '../../pull-key-rotate-http.js';
import { bytes, sha256, base64url, unbase64url } from '../../signing.js';

export const NOW=1794249600000, DAY=86400000, ORIGIN='https://mail.example.com';
export const SECRET='e'.repeat(64), FENCE='f'.repeat(64), LOCAL=`m${'a'.repeat(29)}`;
export const id=(n)=>n.toString(16).padStart(32,'0');
export const hash=(s)=>sha256(bytes(s));
export const routes={activate:ACTIVATE_PATH,status:STATUS_PATH,fence:FENCE_PATH,rotate:KEY_ROTATE_PATH};
export const rows=(f,table)=>f.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all().map((r)=>({...r}));
export const one=(f,table='pull_installs')=>rows(f,table)[0];
export const snapshot=(f)=>f.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pull_%' ORDER BY name")
  .all().map(({name})=>[name,rows(f,name)]);
export async function keypair() {
  const key=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']);
  return {key:key.privateKey,verify:key.publicKey,publicKey:base64url(await crypto.subtle.exportKey('raw',key.publicKey)),
    privateKey:base64url(await crypto.subtle.exportKey('pkcs8',key.privateKey))};
}
export async function fixture(t,kind='status',state=kind==='activate'?'verified':'active') {
  const sql=new DatabaseSync(':memory:'); t.after(()=>sql.close()); sql.exec('PRAGMA foreign_keys=ON');
  sql.exec(readFileSync(new URL('../../schema.sql',import.meta.url),'utf8'));
  const dir=new URL('../../migrations/',import.meta.url);
  for (const n of readdirSync(dir).filter((s)=>/^\d+.*\.sql$/.test(s)).sort()) sql.exec(readFileSync(new URL(n,dir),'utf8'));
  const f={sql,kind,time:NOW,nonce:100,source:'trusted-edge',candidate:await keypair(),service:await keypair(),next:await keypair()};
  sql.function('strftime',(format,at)=>{
    assert.equal(at,'now'); const time=f.sqlTime??f.time;
    return format==='%s'?String(Math.floor(time/1000)):`${String(Math.floor(time/1000)%60).padStart(2,'0')}.${String(time%1000).padStart(3,'0')}`;
  });
  f.db={
    prepare(query) { return {sql:query,args:[],bind(...args){assert.ok(args.length<=100);this.args=args;return this;},
      async first(){if(f.db.beforeRead) await f.db.beforeRead(query);return sql.prepare(query).get(...this.args)??null;},
      async run(){return this.runSync();},runSync(){const q=sql.prepare(query);
        return q.columns().length?{success:true,results:q.all(...this.args),meta:{changes:0}}
          :{success:true,meta:{changes:Number(q.run(...this.args).changes)}};}};},
    async batch(statements){if(this.beforeBatch) await this.beforeBatch(statements);sql.exec('BEGIN IMMEDIATE');
      try{const out=statements.map((s,n)=>{if(this.inBatch)this.inBatch(s,n,statements.length);return s.runSync();});sql.exec('COMMIT');return out;}
      catch(error){sql.exec('ROLLBACK');throw error;}}
  };
  const owner='Owner+tag@example.com', ownerHash=await hash(owner);
  sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,
    key_id,public_key,consent_version,epoch,active_since_ms,created_ms,updated_ms)
    VALUES(?,7,3,?,?,?,?, 'current',?,'v1',4,?,?,?)`).run(id(1),state,ownerHash,owner,LOCAL,f.candidate.publicKey,state==='active'?NOW-1000:null,NOW-1000,NOW-1000);
  sql.prepare(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,candidate_key_id,
    candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms)
    VALUES(?,?,'signup',7,3,'current',?,?,?,?,?,'verified','v1',8,?,?,?)`).run(id(2),id(1),f.candidate.publicKey,ownerHash,
    await hash(SECRET),await hash(FENCE),'b'.repeat(64),'c'.repeat(64),NOW-1000,NOW+30*60000-1000);
  sql.prepare("INSERT INTO pull_generations VALUES(?,7,3,?,'current',?,?,?,'current',?,NULL)")
    .run(id(1),ownerHash,f.candidate.publicKey,await hash(FENCE),id(2),NOW-1000);
  sql.prepare("INSERT INTO pull_addresses VALUES(?,?,?, ?,?)").run(LOCAL,id(1),kind==='activate'?'reserved':'assigned',kind==='activate'?NOW+30*60000-1000:null,NOW-1000);
  sql.prepare("INSERT INTO pull_owner_slots VALUES(?,?,?, ?,?)").run(ownerHash,id(1),kind==='activate'?'pending':'active',id(2),kind==='activate'?NOW+30*60000-1000:null);
  if(kind==='activate') {
    sql.exec('UPDATE pull_installs SET generation=1,owner_generation=1; UPDATE pull_generations SET generation=1,owner_generation=1; UPDATE pull_flows SET expected_generation=1,expected_owner_generation=1;');
    f.scope={generation:'1',owner_generation:'1'};
  }
  f.config={db:f.db,serviceOrigin:ORIGIN,domain:'mail.example.net',serviceKeyId:'service-key',servicePrivateKey:f.service.privateKey,clock:()=>f.time};
  const classes={activate:PullActivateHTTP,status:PullStatusHTTP,fence:PullFenceHTTP,rotate:PullKeyRotateHTTP};
  f.http=new classes[kind](f.config); f.ledger=f.http.ledger;
  if(kind==='rotate') await rotation(f);
  return f;
}
export async function rotation(f) {
  const i=one(f), old=one(f,'pull_flows');
  f.sql.prepare("UPDATE pull_flows SET state='completed' WHERE flow_id=?").run(id(2));
  f.sql.prepare(`INSERT INTO pull_flows(flow_id,install_id,purpose,expected_generation,expected_owner_generation,candidate_key_id,
    candidate_public_key,owner_hash,flow_secret_hash,fence_hash,input_digest,state,consent_version,manifest_sequence,manifest_digest,created_ms,expires_ms)
    VALUES(?,?,'rotate',7,3,'next',?,?,?,?,?,'approved','v1',8,?,?,?)`).run(id(4),id(1),f.next.publicKey,i.owner_hash,old.flow_secret_hash,
    await hash('a'.repeat(64)),'d'.repeat(64),old.manifest_digest,NOW,NOW+30*60000);
}
export async function value(f,extra={}) {
  if(f.kind==='status') return {...extra};
  if(f.kind==='activate') return {op_id:id(10),flow_id:id(2),flow_secret:SECRET,consent_version:'v1',...extra};
  if(f.kind==='fence') return {op_id:id(10),install_id:id(1),generation:7,flow_id:id(2),fence_token:FENCE,mode:'revoke',nonce:id(f.nonce++),timestamp:f.time,...extra};
  const flow=rows(f,'pull_flows').find((r)=>r.flow_id===id(4));
  const v={op_id:id(10),flow_id:id(4),new_key_id:'next',new_public_key:f.next.publicKey,fence_hash:flow.fence_hash,...extra};
  const transcript=bytes(JSON.stringify(['clayrune-mail-pull-candidate','1',ORIGIN,'rotate',v.flow_id,id(1),'7','3',
    v.new_key_id,v.new_public_key,v.fence_hash,flow.flow_secret_hash]));
  return {...v,new_signature:base64url(await crypto.subtle.sign('Ed25519',f.next.key,transcript)),...extra};
}
export async function machine(f,v,options={}) {
  const path=options.path??routes[f.kind], raw=options.raw??bytes(JSON.stringify(v));
  const headers=new Headers({'content-type':'application/json'});
  if(f.kind!=='fence') {
    const m={version:'2',direction:'install-to-pull',key_id:'current',install_id:id(1),generation:'7',owner_generation:'3',
      timestamp:String(f.time),nonce:id(f.nonce++),digest:await sha256(raw),...f.scope,...options.fields};
    const transcript=bytes(JSON.stringify(['clayrune-agent-mail-pull-http','1',m.direction,ORIGIN,'POST',options.signPath??path,
      m.key_id,m.install_id,m.generation,m.owner_generation,m.timestamp,m.nonce,m.digest]));
    for(const[k,v]of Object.entries(m))headers.set(`x-mail-${k.replaceAll('_','-')}`,v);
    headers.set('x-mail-signature',base64url(await crypto.subtle.sign('Ed25519',options.key??f.candidate.key,transcript)));
  }
  for(const[k,v]of Object.entries(options.headers??{})) headers.set(k,v);
  return new Request(`${options.origin??ORIGIN}${path}${options.suffix??''}`,{method:options.method??'POST',headers,body:raw});
}
export async function signed(f,response,request) {
  const raw=new Uint8Array(await response.arrayBuffer()),field=(n)=>response.headers.get(`x-mail-${n}`);
  assert.equal(field('version'),'2');assert.equal(field('direction'),'pull-to-install');assert.equal(field('key-id'),'service-key');
  assert.equal(field('digest'),await sha256(raw));
  const fence=f.kind==='fence',v=fence?JSON.parse(await request.clone().text()):null;
  for(const name of ['install-id','generation','owner-generation','nonce']) {
    const expected=fence?({'install-id':v.install_id,generation:String(v.generation),'owner-generation':'0',nonce:v.nonce})[name]:request.headers.get(`x-mail-${name}`);
    assert.equal(field(name),expected);
  }
  const transcript=(path,status)=>bytes(JSON.stringify(['clayrune-agent-mail-pull-http','1','pull-to-install',ORIGIN,'POST',path,
    'service-key',field('install-id'),field('generation'),field('owner-generation'),field('timestamp'),field('nonce'),String(status),field('digest')]));
  const path=new URL(request.url).pathname,signature=unbase64url(field('signature'));
  assert.equal(await crypto.subtle.verify('Ed25519',f.service.verify,signature,transcript(path,response.status)),true);
  assert.equal(await crypto.subtle.verify('Ed25519',f.service.verify,signature,transcript('/wrong',response.status)),false);
  assert.equal(await crypto.subtle.verify('Ed25519',f.service.verify,signature,transcript(path,201)),false);
  assert.equal(response.headers.get('cache-control'),'no-store');assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  return JSON.parse(new TextDecoder().decode(raw));
}
export async function run(f,v,options={}) {
  v??=await value(f);const request=await machine(f,v,options);
  // Fence verification reads its body too; preserve a verification copy.
  const proof=request.clone(),response=await f.http.handle(request,{trustedSource:options.source??f.source});
  const body=response.headers.has('x-mail-signature')?await signed(f,response,proof):await response.json();
  return {request:proof,response,body};
}
export const common=(body)=>assert.deepEqual(Object.keys(body).sort(),['v','code','request_id'].sort());

export async function seedDelivery(f) {
  const reg=await f.ledger.registration(id(1));
  for(const [n,name,state] of [[60,'accepted','accepted'],[61,'sending','sending'],[62,'reserved','reserved']]) {
    await f.ledger.reserveSend(reg,{intent_id:name,thread_id:name,digest:id(n).repeat(2),token_hash:id(n+10).repeat(2),message_id:`${name}@example.net`},NOW);
    if(state!=='reserved')await f.ledger.authorizeSend(reg,name,NOW);
    if(state==='accepted')await f.ledger.finishSend(reg,name,'accepted',NOW,'provider');
  }
  await f.ledger.enqueue({install_id:id(1),generation:7,owner_generation:3,epoch:4},
    {thread_id:'accepted',incoming_message_id:'incoming@example.net',text:'private body',
      auth:{ingress:'cloudflare',spf:'pass',dkim:'pass',dmarc:'pass',aligned:'spf+dkim'}});
  f.sql.prepare("INSERT INTO pull_tickets VALUES(?,?,?,?,?,?,?,'issued',?,?,NULL)")
    .run('b'.repeat(64),id(63),id(64),id(1),7,3,'current',NOW,NOW+60000);
  f.sql.prepare("INSERT INTO pull_browser_sessions VALUES(?,?,?,?,?,'rotate',7,3,'approved',?,?)")
    .run('c'.repeat(64),'d'.repeat(64),id(1),id(2),null,NOW,NOW+600000);
  f.sql.prepare(`INSERT INTO pull_tokens(token_hash,install_id,flow_id,purpose,generation,owner_generation,owner_hash,
    binding_digest,state,created_ms,expires_ms) VALUES(?,?,?,'stepup',7,3,?,?,'pending',?,?)`)
    .run('e'.repeat(64),id(1),id(2),reg.owner_hash,'f'.repeat(64),NOW,NOW+600000);
}

export function seedRevert(f) {
  const current=one(f),end=NOW+7*DAY;
  f.sql.prepare(`INSERT INTO pull_owner_changes(change_id,install_id,flow_id,old_owner_hash,new_owner_hash,
    expected_generation,expected_owner_generation,switched_generation,switched_owner_generation,state,
    created_ms,expires_ms,completed_ms,revert_until_ms) VALUES(?,?,?,?,?,6,2,7,3,'completed',?,?,?,?)`)
    .run(id(80),id(1),id(2),'b'.repeat(64),current.owner_hash,NOW-1000,NOW+600000,NOW-1000,end);
  for(const [token,ownerGeneration,expiry]of [['1'.repeat(64),3,end],['2'.repeat(64),2,end],['3'.repeat(64),3,NOW]]) {
    f.sql.prepare(`INSERT INTO pull_tokens(token_hash,install_id,flow_id,change_id,purpose,generation,owner_generation,
      owner_hash,binding_digest,state,created_ms,expires_ms) VALUES(?,?,?,?,'revert',7,?,?,?,'pending',?,?)`)
      .run(token,id(1),id(2),id(80),ownerGeneration,'b'.repeat(64),'c'.repeat(64),NOW-1000,expiry);
  }
  // The SQL retirement guard is tied to the registry's current owner generation
  // through the completed change, so an unrelated change id cannot survive.
  f.sql.prepare('UPDATE pull_tokens SET change_id=? WHERE token_hash=?').run(id(81),'2'.repeat(64));
}

export function boundaryTests(test,kind) {
  for(const options of [{method:'PUT'},{origin:'https://other.example'},{suffix:'?x=1'},{suffix:'#x'},
    {path:'/v1/pull/not-this-route'},{headers:{origin:ORIGIN}},{headers:{'content-encoding':'gzip'}},
    {headers:{'content-type':'text/plain'}},{headers:{'content-length':'8193'}},
    {headers:{'x-padding':'x'.repeat(8192)}},{raw:bytes('x'.repeat(8193))}]) {
    test(`${kind}: wire boundary ${JSON.stringify(options).slice(0,75)} refuses without mutation`,async(t)=>{
      const f=await fixture(t,kind),before=snapshot(f),r=await run(f,await value(f),options);
      assert.equal(r.response.status,401);assert.deepEqual(r.body,{v:1,code:'unauthorized'});
      assert.equal(r.response.headers.has('x-mail-signature'),false);assert.deepEqual(snapshot(f),before);
    });
  }
  for(const raw of ['{}x','\ufeff{}','{"op_id":"a","op_id":"b"}','{"op_id":"a","\\u006fp_id":"b"}',
    '{"x":-0}','{"x":9007199254740992}','{"x":1.5}','{"x":{}}','{"x":"\\ud800"}']) {
    test(`${kind}: strict JSON ${raw.slice(0,40)}`,async(t)=>{
      const f=await fixture(t,kind),r=await run(f,await value(f),{raw:bytes(raw)});
      assert.equal(r.response.status,kind==='fence'?401:400);
      assert.equal(r.body.code,kind==='fence'?'unauthorized':'invalid_json');assert.equal(rows(f,'pull_nonces').length,0);
    });
  }
  test(`${kind}: closed extra fields cannot supply authority`,async(t)=>{
    const f=await fixture(t,kind),r=await run(f,await value(f,{to:'unrelated@example.net'}));
    assert.equal(r.response.status,kind==='fence'?401:400);assert.equal(rows(f,'pull_nonces').length,0);
  });
  if(kind!=='fence') {
    for(const options of [{fields:{key_id:'unknown'}},{fields:{install_id:id(99)}},{fields:{generation:'999'}},
      {fields:{owner_generation:'999'}},{fields:{timestamp:String(NOW-300001)}},{signPath:'/v1/pull/send'},
      {headers:{'x-mail-signature':'a'.repeat(86)}}]) {
      test(`${kind}: cryptographic scope ${JSON.stringify(options)}`,async(t)=>{
        const f=await fixture(t,kind),before=snapshot(f),r=await run(f,await value(f),options);
        assert.equal(r.response.status,401);assert.deepEqual(r.body,{v:1,code:'unauthorized'});assert.deepEqual(snapshot(f),before);
      });
    }
    test(`${kind}: durable nonce replay refuses`,async(t)=>{
      const f=await fixture(t,kind),v=await value(f),first=await run(f,v);
      assert.equal(first.response.status,200);const before=snapshot(f);
      const r=await run(f,v,{fields:{nonce:first.request.headers.get('x-mail-nonce')}});
      assert.equal(r.response.status,409);assert.equal(r.body.code,'nonce_replayed');common(r.body);assert.deepEqual(snapshot(f),before);
    });
    test(`${kind}: global preauth/source throttles leak no registration`,async(t)=>{
      const f=await fixture(t,kind);f.http.shared.limit=async()=>false;
      const r=await run(f);assert.equal(r.response.status,429);assert.deepEqual(r.body,{v:1,code:'abuse_limit'});
      assert.equal(r.response.headers.has('x-mail-signature'),false);assert.equal(rows(f,'pull_nonces').length,0);
    });
    test(`${kind}: quota shares the contractual bucket and retry boundary`,async(t)=>{
      const f=await fixture(t,kind),cap=kind==='status'?200:500;
      const bucket=`pull:http:${kind==='status'?'send':'management'}:${id(1)}:${Math.floor(f.time/DAY)}`;
      f.sql.prepare('INSERT INTO pull_budgets VALUES(?,?,?)').run(bucket,cap-1,NOW+2*DAY);
      const v=await value(f);assert.equal((await run(f,v)).response.status,200);
      const r=await run(f,v);assert.equal(r.response.status,429);assert.equal(r.body.code,'request_limit');
      assert.equal(r.body.retry_after_ms,(Math.floor(NOW/DAY)+1)*DAY);
      assert.equal(r.response.headers.get('retry-after'),String(Math.ceil((r.body.retry_after_ms-NOW)/1000)));
      assert.equal(rows(f,'pull_budgets').find((r)=>r.bucket===bucket).count,cap);
    });
  }
  test(`${kind}: end-of-batch failure rolls back every lifecycle write and nonce`,async(t)=>{
    const f=await fixture(t,kind),before=snapshot(f);
    f.db.inBatch=(s,n,count)=>{if(n===count-1 && s.sql.includes('strftime'))throw new Error('private failure');};
    const r=await run(f);assert.equal(r.response.status,kind==='fence'?401:503);
    assert.deepEqual(snapshot(f),before);assert.equal(JSON.stringify(r.body).includes('private'),false);
  });
  test(`${kind}: D1 clock expiry refuses even with fresh Worker clock`,async(t)=>{
    const f=await fixture(t,kind),before=snapshot(f);f.sqlTime=NOW+300001;
    const r=await run(f);assert.equal(r.response.status,kind==='fence'?401:503);
    assert.deepEqual(snapshot(f),before);
  });
}
