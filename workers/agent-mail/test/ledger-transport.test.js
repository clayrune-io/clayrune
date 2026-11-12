import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { Ledger, DAY } from '../ledger.js';
import { PullLedger } from '../pull-ledger.js';

const NOW = 1_794_249_600_000;
const id = (n) => n.toString(16).padStart(32,'0');
const hex = (n) => n.toString(16).padStart(64,'0');
const address = `m${'a'.repeat(29)}`;
// Real SQLite, every migration, D1 transactional batches, bounded parameters.
// Hooks model deterministic writer interleavings, not deployed D1 concurrency.
class D1 {
  constructor() {
    this.sql = new DatabaseSync(':memory:'); this.sql.exec('PRAGMA foreign_keys=ON');
    this.sql.exec(readFileSync(new URL('../schema.sql',import.meta.url),'utf8'));
    const dir = new URL('../migrations/',import.meta.url);
    for (const n of readdirSync(dir).filter((n)=>/^\d+.*\.sql$/.test(n)).sort()) this.sql.exec(readFileSync(new URL(n,dir),'utf8'));
  }
  prepare(sql) {
    const owner = this, db = this.sql;
    return { sql, args: [], bind(...args) { assert.ok(args.length<=100); this.args=args; return this; },
      async first() { if(owner.beforeRead)await owner.beforeRead(sql);return db.prepare(sql).get(...this.args)??null; },
      async all() { return {success:true,results:db.prepare(sql).all(...this.args)}; },
      async run() { if(owner.beforeRun)await owner.beforeRun(sql);return this.runSync(); },
      runSync() {
        const q=db.prepare(sql);
        if(q.columns().length) return {success:true,results:q.all(...this.args),meta:{changes:0}};
        return {success:true,meta:{changes:Number(q.run(...this.args).changes)}};
      } };
  }
  async batch(statements) {
    if(this.beforeBatch)await this.beforeBatch(statements);
    this.lastBatch=statements;this.sql.exec('BEGIN IMMEDIATE');
    try {
      const result=statements.map((s,n)=>{if(this.inBatch)this.inBatch(s,n,statements.length);return s.runSync();});
      this.sql.exec('COMMIT');return result;
    } catch(e) { this.sql.exec('ROLLBACK');throw e; }
  }
}
const rows = (f,t) => f.db.sql.prepare(`SELECT * FROM ${t} ORDER BY 1,2`).all().map((r)=>({...r}));
const one = (f,t) => rows(f,t)[0];
const snapshot = (f) => f.db.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
  .all().map(({name})=>[name,rows(f,name)]);
async function fixture(t,{real=false,policy={}}={}) {
  const db=new D1();t.after(()=>db.sql.close());const f={db,time:real?Date.now():NOW};
  if(!real) db.sql.function('strftime',(format,at)=>{
    assert.equal(at,'now');return format==='%s'?String(Math.floor(f.time/1000)):
      `${String(Math.floor(f.time/1000)%60).padStart(2,'0')}.${String(f.time%1000).padStart(3,'0')}`;
  });
  f.legacy=new Ledger(db,policy); f.pull=f.legacy.pull({clock:()=>f.time});
  db.sql.prepare(`INSERT INTO registrations VALUES(?,7,1,1,'legacy@example.com',?,'legacy','legacy-key',NULL,NULL,NULL,0)`)
    .run(id(1),address);
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,
    key_id,public_key,consent_version,epoch,created_ms,updated_ms) VALUES(?,7,3,'active',?,'owner@example.com',?,'key',?,'v1',4,?,?)`)
    .run(id(1),hex(1),address,'k'.repeat(43),f.time,f.time);
  db.sql.prepare("INSERT INTO pull_generations VALUES(?,7,3,?,'key',?,?,?,'current',?,NULL)")
    .run(id(1),hex(1),'k'.repeat(43),hex(2),id(2),f.time);
  db.sql.prepare("INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)").run(address,id(1),f.time);
  f.reg=await f.pull.registration(id(1));
  return f;
}
const value = (n=1,extra={}) => ({intent_id:`intent-${n}`,thread_id:`thread-${n}`,digest:hex(n),
  message_id:`out-${n}@example.com`,token_hash:hex(100+n),...extra});
const reserve = (f,n=1,extra={}) => f.pull.reserveSend(f.reg,value(n,extra),f.time);
const authorize = (f,n=1) => f.pull.authorizeSend(f.reg,`intent-${n}`,f.time);
const finish = (f,state='uncertain',provider='',n=1) => f.pull.finishSend(f.reg,`intent-${n}`,state,f.time,provider);
function fence(f,flow=id(2),mode='revoke') {
  f.db.sql.prepare('INSERT INTO pull_fences VALUES(?,?,7,?,?,?,?,?,?)').run(id(1),flow,hex(2),mode,id(3),id(4),hex(3),f.time);
}
// Required production retirement semantics from unit05, reduced to this unit's
// rows. It advances high-water generation even on a revert to the same owner.
function switchOwner(f,{restore=false}={}) {
  const i=one(f,'pull_installs'),g=i.generation+1,og=i.owner_generation+1;
  f.db.sql.prepare("UPDATE pull_generations SET state='fenced',fenced_ms=? WHERE state='current'").run(f.time);
  f.db.sql.prepare("UPDATE pull_intents SET state=CASE WHEN state='reserved' THEN 'revoked' ELSE 'uncertain' END WHERE state IN ('reserved','sending')").run();
  f.db.sql.prepare('UPDATE pull_threads SET revoked_ms=?').run(f.time);
  f.db.sql.prepare("UPDATE pull_installs SET generation=?,owner_generation=?,owner_hash=?,owner_address=?,epoch=epoch+1 WHERE install_id=?")
    .run(g,og,restore?hex(1):hex(9),restore?'owner@example.com':'next@example.com',id(1));
  f.db.sql.prepare("INSERT INTO pull_generations VALUES(?,?,?,?,?,?,?,?, 'current',?,NULL)")
    .run(id(1),g,og,restore?hex(1):hex(9),'key','k'.repeat(43),hex(2),id(g),f.time);
}

test('explicit pull facade is isolated from legacy registration and push delivery',async(t)=>{
  const f=await fixture(t);
  assert.equal((await f.legacy.registration(id(1))).owner_address,'legacy@example.com');
  assert.equal((await f.pull.registration(id(1))).owner_address,'owner@example.com');
  assert.equal((await f.pull.recipient(address)).owner_address,'owner@example.com');
  assert.equal(f.pull.queue instanceof PullLedger,true);
  for(const method of ['due','claim','settle','reconcile','reserveIngress']) assert.equal(f.pull[method],undefined);
  for(const method of ['registration','recipient','claimNonce','limit','reserveIngress','reject','thread','intent','reserveSend',
    'finishSend','enqueue','reconcile','due','claim','settle']) assert.equal(typeof f.legacy[method],'function');
  f.db.sql.prepare('DELETE FROM pull_addresses').run();f.db.sql.prepare('DELETE FROM pull_generations').run();
  f.db.sql.prepare('DELETE FROM pull_installs').run();
  assert.equal(await f.pull.registration(id(1)),null);assert.equal(await f.pull.recipient(address),null);
  assert.equal((await f.legacy.recipient(address)).owner_address,'legacy@example.com');
});
for(const change of ['pending','verified','disabled','frozen','deleted','fenced','owner_hash','key_id','public_key','owner_generation',
  'generation','address_reserved','address_quarantined','address_mismatch','address_foreign','revoke','cancel']) {
  test(`pull lookups reject invalid registry projection: ${change}`,async(t)=>{
    const f=await fixture(t);
    if(['pending','verified','disabled','frozen'].includes(change))f.db.sql.prepare('UPDATE pull_installs SET state=?').run(change);
    else if(change==='deleted')f.db.sql.prepare("UPDATE pull_installs SET state='deleted',owner_address=NULL").run();
    else if(change==='fenced')f.db.sql.prepare("UPDATE pull_generations SET state='fenced'").run();
    else if(change==='owner_hash')f.db.sql.prepare('UPDATE pull_generations SET owner_hash=?').run(hex(9));
    else if(change==='key_id')f.db.sql.prepare("UPDATE pull_generations SET key_id='other'").run();
    else if(change==='public_key')f.db.sql.prepare('UPDATE pull_generations SET public_key=?').run('j'.repeat(43));
    else if(change==='owner_generation')f.db.sql.prepare('UPDATE pull_generations SET owner_generation=4').run();
    else if(change==='generation')f.db.sql.prepare('UPDATE pull_installs SET generation=8').run();
    else if(change==='address_reserved'||change==='address_quarantined')f.db.sql.prepare('UPDATE pull_addresses SET state=?,until_ms=?')
      .run(change==='address_reserved'?'reserved':'quarantined',f.time+DAY);
    else if(change==='address_mismatch')f.db.sql.prepare('UPDATE pull_installs SET address_local=?').run(`m${'b'.repeat(29)}`);
    else if(change==='address_foreign')f.db.sql.prepare('UPDATE pull_addresses SET install_id=NULL').run();
    else fence(f,id(2),change);
    assert.equal(await f.pull.registration(id(1)),null);assert.equal(await f.pull.recipient(address),null);
    const before=snapshot(f);await assert.rejects(reserve(f),{message:'stale_generation'});assert.deepEqual(snapshot(f),before);
  });
}
test('lookup expected generations are explicit; unrelated cancel and epoch reconnect leave send authority intact',async(t)=>{
  const f=await fixture(t);assert.equal(await f.pull.registration(id(1),6),null);
  assert.equal(await f.pull.registration(id(1),7,2),null);assert.ok(await f.pull.registration(id(1),7,3));
  fence(f,id(99),'cancel');f.db.sql.prepare('UPDATE pull_installs SET epoch=epoch+1').run();
  await reserve(f);const sent=await authorize(f);assert.equal(sent.owner_address,'owner@example.com');
});
test('legacy send reservation, nonce, ingress, rejection and push behavior remain unchanged with all migrations',async(t)=>{
  const f=await fixture(t),r=await f.legacy.registration(id(1));
  await f.legacy.reserveSend(r,value(),f.time);assert.equal(one(f,'intents').state,'sending');
  assert.equal((await f.legacy.thread(value().token_hash)).owner_generation,7);
  await f.legacy.finishSend(r,'intent-1','uncertain',f.time);
  await f.legacy.finishSend(r,'intent-1','accepted',f.time);assert.equal(one(f,'intents').state,'accepted');
  assert.equal(await f.legacy.reserveIngress(id(1),f.time),true);assert.equal(rows(f,'rate_limits').length,2);
  await f.legacy.reject('test_refusal',f.time);assert.equal(one(f,'rejections').reason,'test_refusal');
  const e={...value(),incoming_id:'incoming',event_id:'event-1',payload:{text:'reply'},held:false};
  assert.equal((await f.legacy.enqueue(r,e,f.time)).status,'queued');
  assert.deepEqual((await f.legacy.due(f.time)).map((r)=>({...r})),[{event_id:'event-1'}]);
  const claimed=await f.legacy.claim('event-1',f.time);await f.legacy.settle(claimed,true,f.time);
  assert.equal(one(f,'events').state,'local_received');assert.equal(one(f,'events').payload,null);
  assert.equal(rows(f,'pull_intents').length,0);
});
test('pull reserve/authorize/finish uses immutable owner, separate budgets and stable provider receipt',async(t)=>{
  const f=await fixture(t);const r=await reserve(f);
  assert.equal(r.duplicate,false);assert.equal(r.state,'reserved');assert.equal(await f.pull.thread(value().token_hash),null);
  const th=one(f,'pull_threads');assert.equal(th.expires_ms,NOW+90*DAY);assert.equal(th.dedupe_until_ms,NOW+120*DAY);
  assert.equal(rows(f,'pull_budgets').length,0);const s=await authorize(f);
  assert.equal(s.authorized,true);assert.equal(s.owner_generation,3);assert.equal(s.owner_address,'owner@example.com');
  assert.equal(rows(f,'pull_budgets').length,2);assert.ok(rows(f,'pull_budgets').every((r)=>r.count===1));
  const t1=await f.pull.thread(value().token_hash);assert.equal(t1.revoked,false);assert.deepEqual(t1.message_ids,[value().message_id]);
  assert.equal(t1.owner_generation,3);assert.equal(t1.expires_at,NOW+90*DAY);
  const accepted=await finish(f,'accepted','provider-1');assert.equal(accepted.provider_id,'provider-1');
  f.time+=100;assert.deepEqual(await finish(f,'accepted','provider-1'),accepted);
  assert.equal((await reserve(f)).duplicate,true);assert.equal((await reserve(f)).state,'accepted');
  await assert.rejects(authorize(f),{message:'intent_conflict'});
  for(const state of ['uncertain','failed','revoked'])await assert.rejects(finish(f,state),{message:'intent_conflict'});
  assert.equal(rows(f,'intents').length,0);assert.equal(rows(f,'threads').length,0);assert.equal(rows(f,'rate_limits').length,0);
});
test('unknown provider acceptance remains uncertain across retries and requires positive evidence',async(t)=>{
  const f=await fixture(t);await reserve(f);await authorize(f);await finish(f);
  const before=snapshot(f);
  for(let n=0;n<3;n++) { assert.equal((await reserve(f)).state,'uncertain');await assert.rejects(authorize(f),{message:'intent_conflict'}); }
  await assert.rejects(finish(f,'failed'),{message:'intent_conflict'});
  await assert.rejects(finish(f,'accepted'),{message:'invalid_request'});assert.deepEqual(snapshot(f),before);
  const r=await finish(f,'accepted','positive-provider-receipt');assert.equal(r.state,'accepted');
  assert.equal(r.provider_id,'positive-provider-receipt');
  await assert.rejects(finish(f,'accepted','another-receipt'),{message:'intent_conflict'});
});
test('known nonacceptance and pre-handoff revocation are terminal; no reset to reserved',async(t)=>{
  const f=await fixture(t);await reserve(f);await finish(f,'revoked');
  assert.equal((await reserve(f)).state,'revoked');await assert.rejects(authorize(f),{message:'intent_conflict'});
  await reserve(f,2);await authorize(f,2);await finish(f,'failed','',2);
  assert.equal((await reserve(f,2)).state,'failed');await assert.rejects(authorize(f,2),{message:'intent_conflict'});
});
for(const conflict of [{digest:hex(8)},{thread_id:'different'},{message_id:'other@example.com'},{token_hash:hex(8)}]) {
  test(`same intent never overwrites immutable reservation ${Object.keys(conflict)[0]}`,async(t)=>{
    const f=await fixture(t);await reserve(f);const before=snapshot(f);
    await assert.rejects(reserve(f,1,conflict),{message:'intent_conflict'});assert.deepEqual(snapshot(f),before);
  });
}
for(const collision of [{thread_id:'thread-1'},{token_hash:hex(101)}]) {
  test(`thread/token collision rolls back entire second reservation ${Object.keys(collision)[0]}`,async(t)=>{
    const f=await fixture(t);await reserve(f);const before=snapshot(f);
    await assert.rejects(reserve(f,2,collision),{message:'storage_unavailable'});assert.deepEqual(snapshot(f),before);
  });
}
for(const phase of ['reserve','authorize','finish'])for(const change of ['switch','revert']) {
  test(`${phase} refuses stale owner scope after ${change}`,async(t)=>{
    const f=await fixture(t);if(phase!=='reserve')await reserve(f);if(phase==='finish')await authorize(f);
    switchOwner(f);if(change==='revert')switchOwner(f,{restore:true});
    const before=snapshot(f),op=phase==='reserve'?()=>reserve(f):phase==='authorize'?()=>authorize(f):()=>finish(f,'accepted','provider');
    await assert.rejects(op(),{message:'stale_generation'});assert.deepEqual(snapshot(f),before);
    assert.equal(await f.pull.thread(value().token_hash),null);
    if(phase==='finish')assert.equal(one(f,'pull_intents').state,'uncertain');
  });
}
for(const phase of ['reserve','authorize','finish'])for(const where of ['before','last']) {
  test(`${phase} registry retirement at ${where} batch boundary cannot commit`,async(t)=>{
    const f=await fixture(t);if(phase!=='reserve')await reserve(f);if(phase==='finish')await authorize(f);
    let expected=snapshot(f),fired=false;
    if(where==='before')f.db.beforeBatch=()=>{if(!fired){fired=true;switchOwner(f);expected=snapshot(f);}};
    else f.db.inBatch=(_s,n,len)=>{if(n===len-1){fired=true;switchOwner(f);}};
    await assert.rejects(phase==='reserve'?reserve(f):phase==='authorize'?authorize(f):finish(f,'accepted','provider'));
    assert.equal(fired,true);assert.deepEqual(snapshot(f),expected);
  });
}
test('send quota shares the control-mail global bucket and does not partly charge an install',async(t)=>{
  const f=await fixture(t,{policy:{sendsPerDay:1,globalSendsPerDay:2}});await reserve(f);await authorize(f);
  await reserve(f,2);let before=snapshot(f);await assert.rejects(authorize(f,2),{message:'send_limit'});assert.deepEqual(snapshot(f),before);
  const global=`pull:send:global:${Math.floor(f.time/DAY)}`;
  f.db.sql.prepare('UPDATE pull_budgets SET count=2 WHERE bucket=?').run(global);
  f.db.sql.prepare("DELETE FROM pull_budgets WHERE bucket LIKE 'pull:send:install:%'").run();before=snapshot(f);
  await assert.rejects(authorize(f,2),{message:'send_limit'});assert.deepEqual(snapshot(f),before);
  f.time+=DAY;await authorize(f,2);assert.equal(one(f,'pull_intents').state,'sending');
});
test('nonce isolation, shared request limits, bounded content-free audit',async(t)=>{
  const f=await fixture(t),m={direction:'install-to-pull',install_id:id(1),generation:7,key_id:'key',nonce:id(70),expires_at:NOW+300001};
  assert.equal(await f.pull.claimNonce(m),true);assert.equal(await f.pull.claimNonce(m),false);
  assert.equal(await f.legacy.claimNonce(m),true);assert.equal(rows(f,'nonces').length,1);assert.equal(rows(f,'pull_nonces').length,1);
  assert.equal(await f.pull.limit('pull:http:source:test',1,NOW),true);assert.equal(await f.legacy.limit('pull:http:source:test',1,NOW),false);
  await f.pull.reject('message_id_conflict',NOW,id(1));assert.equal(one(f,'pull_audit').code,'message_id_conflict');
  assert.equal(rows(f,'rejections').length,0);await assert.rejects(f.pull.reject('raw sensitive data',NOW));
  f.db.sql.exec(`WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<9999)
    INSERT INTO pull_audit(install_id,at_ms,code) SELECT '',${NOW},'test' FROM n`);
  await f.pull.reject('overflow',NOW);assert.equal(rows(f,'pull_audit').length,10000);assert.equal(one(f,'pull_audit_overflow').count,1);
});
test('enqueue facade uses unit07 queue and its single atomic ingress reservation',async(t)=>{
  const f=await fixture(t);await reserve(f);await authorize(f);await finish(f,'accepted','provider');
  const scope={install_id:id(1),generation:7,owner_generation:3,epoch:4};
  const input={thread_id:'thread-1',incoming_message_id:'reply@example.com',text:'reply',
    auth:{ingress:'cloudflare',spf:'pass',dkim:'pass',dmarc:'pass',aligned:'spf+dkim'}};
  const e=await f.pull.enqueue(scope,input);const again=await f.pull.enqueue(scope,input);
  assert.equal(again.event_id,e.event_id);assert.equal(again.duplicate,true);
  assert.equal(rows(f,'pull_events').length,1);assert.equal(rows(f,'events').length,0);
  assert.equal(one(f,'pull_notifications').dirty_from_seq,1);
  assert.ok(rows(f,'pull_budgets').filter((r)=>r.bucket.includes('ingress')).every((r)=>r.count===1));
});
for(const [name,setup,op] of [
  ['reserve',async()=>{},reserve],
  ['authorize',reserve,authorize],
  ['finish',async(f)=>{await reserve(f);await authorize(f);},(f)=>finish(f,'accepted','provider')],
  ['uncertain',async(f)=>{await reserve(f);await authorize(f);},(f)=>finish(f)],
  ['resolve',async(f)=>{await reserve(f);await authorize(f);await finish(f);},(f)=>finish(f,'accepted','positive')],
  ['reject',async()=>{},(f)=>f.pull.reject('test',f.time,id(1))],
]) {
  test(`${name}: a fault at EVERY batch statement rolls back ALL tables`,async(t)=>{
    const probe=await fixture(t);await setup(probe);await op(probe);const count=probe.db.lastBatch.length;
    for(let n=0;n<count;n++)await t.test(`statement ${n+1}/${count}`,async(st)=>{
      const f=await fixture(st);await setup(f);const before=snapshot(f);
      f.db.inBatch=(_s,index)=>{if(index===n)throw new Error('synthetic private storage failure');};
      await assert.rejects(op(f),{message:'storage_unavailable'});assert.deepEqual(snapshot(f),before);
    });
  });
}
test('SQLite wall clock works without the deterministic strftime override',async(t)=>{
  const f=await fixture(t,{real:true});await reserve(f);await authorize(f);assert.equal((await finish(f)).state,'uncertain');
});

test('two prepared reservations resolve one immutable intent without a second send authorization',async(t)=>{
  const f=await fixture(t);let release,arrive;
  const blocked=new Promise((r)=>{arrive=r;}),gate=new Promise((r)=>{release=r;});let n=0;
  f.db.beforeBatch=async()=>{if(n++===0){arrive();await gate;}};
  const pending=reserve(f);await blocked;const winner=await reserve(f);release();const loser=await pending;
  assert.equal(winner.duplicate,false);assert.equal(loser.duplicate,true);assert.equal(rows(f,'pull_intents').length,1);
  assert.equal(rows(f,'pull_threads').length,1);assert.equal(rows(f,'pull_budgets').length,0);
});
test('two prepared handoffs authorize the provider exactly once and charge only once',async(t)=>{
  const f=await fixture(t);await reserve(f);let release,arrive;
  const blocked=new Promise((r)=>{arrive=r;}),gate=new Promise((r)=>{release=r;});let n=0;
  f.db.beforeBatch=async()=>{if(n++===0){arrive();await gate;}};
  const pending=authorize(f);await blocked;const winner=await authorize(f);release();
  assert.equal(winner.authorized,true);await assert.rejects(pending,{message:'intent_conflict'});
  assert.ok(rows(f,'pull_budgets').every((r)=>r.count===1));assert.equal(one(f,'pull_intents').state,'sending');
});
test('late uncertain completion cannot overwrite a concurrently committed positive receipt',async(t)=>{
  const f=await fixture(t);await reserve(f);await authorize(f);let release,arrive;
  const blocked=new Promise((r)=>{arrive=r;}),gate=new Promise((r)=>{release=r;});let n=0;
  f.db.beforeBatch=async()=>{if(n++===0){arrive();await gate;}};
  const pending=finish(f);await blocked;await finish(f,'accepted','durable-receipt');release();
  await assert.rejects(pending,{message:'storage_unavailable'});
  assert.equal(one(f,'pull_intents').state,'accepted');assert.equal(one(f,'pull_intents').provider_id,'durable-receipt');
});
for(const level of ['global','install']) {
  test(`last ${level} budget taken before authorization rolls back intent and every charge`,async(t)=>{
    const f=await fixture(t,{policy:{sendsPerDay:1,globalSendsPerDay:1}});await reserve(f);let expected;
    f.db.beforeBatch=()=>{
      f.db.beforeBatch=null;const bucket=`pull:send:${level==='global'?'global':`install:${id(1)}`}:${Math.floor(f.time/DAY)}`;
      f.db.sql.prepare('INSERT INTO pull_budgets VALUES(?,1,?)').run(bucket,NOW+2*DAY);expected=snapshot(f);
    };
    await assert.rejects(authorize(f),{message:'send_limit'});assert.deepEqual(snapshot(f),expected);
    assert.equal(one(f,'pull_intents').state,'reserved');
  });
}
for(const where of ['first','last']) {
  test(`handoff cannot charge an old UTC day if time advances at the ${where} guard`,async(t)=>{
    const f=await fixture(t);await reserve(f);f.time=Math.floor(NOW/DAY)*DAY+DAY-1;const before=snapshot(f);
    f.db.inBatch=(_s,n,len)=>{if(n===(where==='first'?1:len-3))f.time++;};
    await assert.rejects(authorize(f),{message:'storage_unavailable'});assert.deepEqual(snapshot(f),before);
  });
}
test('thread expiry blocks a new handoff but cannot erase an already authorized provider result',async(t)=>{
  const f=await fixture(t);await reserve(f);await reserve(f,2);await authorize(f);
  f.time=one(f,'pull_threads').expires_ms;
  assert.equal(await f.pull.thread(value().token_hash),null);const before=snapshot(f);
  await assert.rejects(authorize(f,2),{message:'storage_unavailable'});assert.deepEqual(snapshot(f),before);
  assert.equal((await finish(f)).state,'uncertain');assert.equal((await reserve(f)).state,'uncertain');
});
test('status reads recheck current generation within their own SELECT, not only a prior lookup',async(t)=>{
  const f=await fixture(t);await reserve(f);let once=false;
  f.db.beforeRead=(sql)=>{if(!once&&sql.includes('AS digest')){once=true;switchOwner(f);}};
  await assert.rejects(f.pull.intent(f.reg,'intent-1'),{message:'stale_generation'});
  assert.equal(once,true);
});
for(const phase of ['reserve','authorize','finish'])for(const field of ['owner_address','owner_hash','key_id','public_key','generation','owner_generation','flow_id','address_local']) {
  test(`${phase} refuses forged ${field} in its immutable owner snapshot`,async(t)=>{
    const f=await fixture(t);if(phase!=='reserve')await reserve(f);if(phase==='finish')await authorize(f);
    f.reg={...f.reg,[field]:field==='generation'?8:field==='owner_generation'?4:field==='owner_address'?'other@example.com':
      field==='owner_hash'?hex(9):field==='key_id'?'other':field==='public_key'?'j'.repeat(43):field==='flow_id'?id(99):`m${'b'.repeat(29)}`};
    const before=snapshot(f);await assert.rejects(phase==='reserve'?reserve(f):phase==='authorize'?authorize(f):finish(f),{message:'stale_generation'});
    assert.deepEqual(snapshot(f),before);
  });
}
for(const state of ['reserved','sending','uncertain','failed','accepted','revoked']) {
  test(`reservation replay of ${state} never authorizes or changes durable rows`,async(t)=>{
    const f=await fixture(t);await reserve(f);
    if(['sending','uncertain','accepted'].includes(state))await authorize(f);
    if(state==='uncertain')await finish(f);
    if(state==='accepted')await finish(f,'accepted','receipt');
    if(['failed','revoked'].includes(state))await finish(f,state);
    const before=snapshot(f),r=await reserve(f);assert.equal(r.state,state);assert.equal(r.duplicate,true);
    assert.equal(r.authorized,undefined);assert.deepEqual(snapshot(f),before);
  });
}
for(const change of [{intent_id:''},{thread_id:'bad space'},{digest:'short'},{token_hash:'short'},
  {message_id:'<wrapped@example.com>'},{message_id:'x\r\nBcc:y@example.com'}]) {
  test(`malformed reservation refuses before any write: ${Object.keys(change)[0]} ${JSON.stringify(change).slice(0,40)}`,async(t)=>{
    const f=await fixture(t);const before=snapshot(f);await assert.rejects(reserve(f,1,change),{message:'invalid_request'});
    assert.deepEqual(snapshot(f),before);
  });
}
test('pull reads fail closed on missing pull storage even with a valid legacy registration',async(t)=>{
  const f=await fixture(t);f.db.sql.exec('DROP TABLE pull_addresses');
  await assert.rejects(f.pull.registration(id(1)),{message:'storage_unavailable'});
  assert.equal((await f.legacy.registration(id(1))).active,1);
});
test('handoff returns the exact authorized owner even if caller mutates its object while awaiting commit',async(t)=>{
  const f=await fixture(t);await reserve(f);
  f.db.beforeBatch=()=>{f.reg.owner_address='mutated@example.com';f.reg.owner_hash=hex(99);};
  const authorized=await authorize(f);
  assert.equal(authorized.owner_address,'owner@example.com');assert.equal(authorized.owner_hash,hex(1));
  assert.equal(authorized.authorized,true);assert.equal(one(f,'pull_intents').owner_hash,hex(1));
});
test('reserve captures input digest/token/thread before asynchronous reads',async(t)=>{
  const f=await fixture(t),v=value();let changed=false;
  f.db.beforeRead=()=>{if(!changed){changed=true;v.digest=hex(99);v.token_hash=hex(99);v.thread_id='mutated';}};
  const r=await f.pull.reserveSend(f.reg,v,f.time);
  assert.equal(changed,true);assert.equal(r.digest,value().digest);assert.equal(r.thread_id,value().thread_id);
  assert.equal(one(f,'pull_threads').token_hash,value().token_hash);
});
