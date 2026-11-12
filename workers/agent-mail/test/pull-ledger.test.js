import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { PullLedger, offerFrameBytes } from '../pull-ledger.js';
import { bytes, sha256 } from '../signing.js';

const NOW=1_794_249_600_000, DAY=86_400_000, MONTH=30*DAY;
const id=(n)=>n.toString(16).padStart(32,'0');
const digest=(s)=>sha256(bytes(s));
const AUTH={ingress:'cloudflare',spf:'pass',dkim:'pass',dmarc:'pass',aligned:'spf+dkim'};
// Every migration and all SQL execute in actual local SQLite. Deterministic
// barriers model prepared competing batches, not independent Cloudflare writers.
class D1 {
  constructor() {
    this.sql=new DatabaseSync(':memory:'); this.sql.exec('PRAGMA foreign_keys=ON');
    this.sql.exec(readFileSync(new URL('../schema.sql',import.meta.url),'utf8'));
    const dir=new URL('../migrations/',import.meta.url);
    for(const name of readdirSync(dir).filter((s)=>/^\d+.*\.sql$/.test(s)).sort())this.sql.exec(readFileSync(new URL(name,dir),'utf8'));
  }
  prepare(sql) {
    const db=this.sql;
    return {sql,args:[],bind(...args){assert.ok(args.length<=100,'D1 bound parameter ceiling');this.args=args;return this;},
      async first(){return db.prepare(sql).get(...this.args)??null;},
      async all(){return {success:true,results:db.prepare(sql).all(...this.args)};},
      runSync(){
        const q=db.prepare(sql);
        if(q.columns().length)return {success:true,results:q.all(...this.args),meta:{changes:0}};
        const r=q.run(...this.args);return {success:true,meta:{changes:Number(r.changes)}};
      }};
  }
  async batch(statements) {
    if(this.beforeBatch)await this.beforeBatch(statements);
    this.lastBatch=statements;this.sql.exec('BEGIN IMMEDIATE');
    try{const result=statements.map((s,n)=>{if(this.inBatch)this.inBatch(s,n,statements.length);return s.runSync();});this.sql.exec('COMMIT');return result;}
    catch(e){this.sql.exec('ROLLBACK');throw e;}
  }
}
const rows=(f,table)=>f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY 1,2`).all().map((r)=>({...r}));
const one=(f,table)=>rows(f,table)[0];
const snapshot=(f)=>f.db.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pull_%' ORDER BY name")
  .all().map(({name})=>[name,rows(f,name)]);
function thread(f,n=1,end=f.time+90*DAY) {
  const tid=`thread-${n}`;
  f.db.sql.prepare(`INSERT INTO pull_intents(install_id,generation,intent_id,owner_generation,owner_hash,input_digest,thread_id,message_id,state,created_ms)
    VALUES(?,7,?,3,?,?,?,?,'accepted',?)`).run(id(1),`intent-${n}`,'a'.repeat(64),'b'.repeat(64),tid,`sent-${n}@example.com`,f.time);
  f.db.sql.prepare('INSERT INTO pull_threads VALUES(?,7,?,3,?,?,?,?,?,NULL)')
    .run(id(1),tid,`intent-${n}`,n.toString(16).padStart(64,'0'),`sent-${n}@example.com`,end,end+MONTH);
  return tid;
}
function fixture(t,{end,real=false}={}) {
  const db=new D1();t.after(()=>db.sql.close());const f={db,time:real?Date.now():NOW};
  if(!real)db.sql.function('strftime',(format,at)=>{
    assert.equal(at,'now');return format==='%s'?String(Math.floor(f.time/1000)):
      `${String(Math.floor(f.time/1000)%60).padStart(2,'0')}.${String(f.time%1000).padStart(3,'0')}`;
  });
  f.scope={install_id:id(1),generation:7,owner_generation:3,epoch:4};
  f.ledger=new PullLedger({db,clock:()=>f.time});
  db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,owner_address,address_local,key_id,
    public_key,consent_version,epoch,connection_id,created_ms,updated_ms) VALUES(?,7,3,'active',?,'Owner@example.com',?,'key',?,'v1',4,?,?,?)`)
    .run(id(1),'a'.repeat(64),'m'+'a'.repeat(29),'k'.repeat(43),id(3),f.time,f.time);
  db.sql.prepare("INSERT INTO pull_generations VALUES(?,7,3,?,'key',?,?,?,'current',?,NULL)")
    .run(id(1),'a'.repeat(64),'k'.repeat(43),'f'.repeat(64),id(2),f.time);
  f.thread=thread(f,1,end);return f;
}
const input=(f,n=1,extra={})=>({thread_id:f.thread,incoming_message_id:`reply-${n}@example.com`,text:'Reply héllo',auth:{...AUTH},...extra});
const enqueue=(f,n=1,extra={})=>f.ledger.enqueue(f.scope,input(f,n,extra));
const offer=(f,e,extra)=>f.ledger.offer(f.scope,e.event_id,extra);
const ackInput=(e,o,local=id(50))=>({event_id:e.event_id,event_digest:e.event_digest,body_digest:e.body_digest,lease_id:o.lease_id,local_receipt_id:local});
const ack=(f,e,o,local)=>f.ledger.ack(f.scope,ackInput(e,o,local));
const holdInput=(e,o,extra={})=>({event_id:e.event_id,event_digest:e.event_digest,lease_id:o.lease_id,reason:'conversation_busy',retry_after_ms:0,...extra});
function fence(f,mode='revoke') {
  f.db.sql.prepare('INSERT INTO pull_fences VALUES(?,?,7,?,?,?,?,?,?)').run(id(1),id(2),'f'.repeat(64),mode,id(9),id(10),'b'.repeat(64),f.time);
}
function rotate(f,owner=4) {
  f.db.sql.prepare("UPDATE pull_generations SET state='fenced' WHERE install_id=?").run(id(1));
  f.db.sql.prepare("UPDATE pull_installs SET generation=8,owner_generation=?,epoch=5,state='active' WHERE install_id=?").run(owner,id(1));
  f.db.sql.prepare("INSERT INTO pull_generations VALUES(?,8,?,?,'key',?,?,?,'current',?,NULL)")
    .run(id(1),owner,'a'.repeat(64),'k'.repeat(43),'e'.repeat(64),id(4),f.time);
}

test('enqueue immutable transcript, UTF-8, daily reservations and durable notification',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);
  assert.equal(e.event_id,await digest(JSON.stringify([id(1),'7',f.thread,'reply-1@example.com'])));
  assert.equal(e.body_digest,await digest('Reply héllo'));assert.equal(e.seq,1);
  assert.equal(e.event_digest,await digest(JSON.stringify(['clayrune-mail-pull-event','1',id(1),'7','3',e.event_id,f.thread,'1',
    'reply-1@example.com',e.body_digest,String(NOW),String(NOW+MONTH),String(NOW+90*DAY),'text','',
    'cloudflare','pass','pass','pass','spf+dkim'])));
  assert.equal(one(f,'pull_events').body_bytes,12);assert.equal(one(f,'pull_installs').next_seq,2);
  assert.equal(rows(f,'pull_budgets').length,2);assert.ok(rows(f,'pull_budgets').every((r)=>r.count===1));
  assert.equal(one(f,'pull_notifications').dirty_from_seq,1);
  const immutable=one(f,'pull_dedupe');f.time+=100;
  const retry=await enqueue(f,1,{auth:{...AUTH,aligned:'spf'}});
  assert.equal(retry.duplicate,true);assert.deepEqual(one(f,'pull_dedupe'),immutable);
  assert.equal(one(f,'pull_installs').next_seq,2);assert.ok(rows(f,'pull_budgets').every((r)=>r.count===1));
  await assert.rejects(enqueue(f,1,{text:'changed'}),{message:'message_id_conflict'});
  assert.deepEqual(one(f,'pull_dedupe'),immutable);assert.equal(one(f,'pull_audit').code,'message_id_conflict');
  assert.equal(one(f,'pull_events').text,'Reply héllo');
});

test('held empty body is content-free; malformed/auth/oversize inputs never allocate',async(t)=>{
  const f=fixture(t);const e=await enqueue(f,1,{text:null});assert.equal(e.kind,'held');assert.equal(e.body_digest,await digest(''));
  const row=one(f,'pull_events');assert.equal(row.body_bytes,0);assert.equal(row.text,null);assert.equal(row.reason,'empty_body');
  for(const change of [{text:''},{text:' \n'},{text:'a\0b'},{text:'x'.repeat(16385)},{text:'\ud800'},
    {incoming_message_id:'<wrapped@example.com>'},{incoming_message_id:'x'.repeat(8193)},
    {auth:{...AUTH,dmarc:'fail'}},{auth:{...AUTH,spf:'fail'}},{auth:{...AUTH,extra:'raw'}},{extra:true}]) {
    const before=snapshot(f);await assert.rejects(enqueue(f,2,change),{message:'invalid_request'});assert.deepEqual(snapshot(f),before);
  }
});

test('pending pages are bounded, ordered, reset to zero and expose held/not-due heads',async(t)=>{
  const f=fixture(t);const first=await enqueue(f);const o=await offer(f,first);
  await f.ledger.hold(f.scope,holdInput(first,o,{retry_after_ms:NOW+DAY}));
  for(let n=2;n<=12;n++)await enqueue(f,n);
  const a=await f.ledger.page(f.scope,{limit:10});assert.equal(a.events.length,10);assert.equal(a.next_seq,10);assert.equal(a.scan_complete,false);
  assert.equal(a.pending_count,12);assert.equal(a.oldest_expires_ms,NOW+MONTH);
  assert.equal(a.events[0].state,'held');assert.equal(a.events[0].next_attempt_ms,NOW+DAY);
  const b=await f.ledger.page(f.scope,{after_seq:a.next_seq,limit:10});assert.equal(b.events.length,2);assert.equal(b.scan_complete,true);assert.equal(b.next_seq,0);
  const c=await f.ledger.page(f.scope,{after_seq:b.next_seq,limit:1});assert.equal(c.events[0].event.seq,1);
  await assert.rejects(offer(f,a.events[1].event)); // Same thread's earlier event is unsettled.
  const independent=await enqueue(f,20,{thread_id:thread(f,2)});await offer(f,independent);
  for(const q of [{limit:0},{limit:11},{after_seq:-1},{after_seq:-0}])await assert.rejects(f.ledger.page(f.scope,q),{message:'invalid_request'});
});

test('lease exact boundary reoffers with new history; lease expiry does not expire custody',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);assert.equal(o.lease_until_ms,NOW+60000);
  f.time=NOW+59999;await assert.rejects(offer(f,e),{message:'operation_conflict'});
  f.time=NOW+60000;const next=await offer(f,e);assert.notEqual(next.lease_id,o.lease_id);assert.equal(rows(f,'pull_offers').length,2);
  const r=await ack(f,e,o);assert.equal(r.committed_ms,NOW+60000);assert.equal(one(f,'pull_events').text,null);
  assert.equal(one(f,'pull_events').state,'local_received');assert.equal(one(f,'pull_dedupe').disposition,'local_received');
  assert.equal(rows(f,'pull_notifications').length,0);assert.equal(one(f,'pull_events').lease_id,null);
  f.db.sql.prepare('DELETE FROM pull_offers').run();f.time+=1;
  assert.deepEqual({...await ack(f,e,o)},r); // Receipt overrides compacted offer history.
  await assert.rejects(ack(f,e,o,id(51)),{message:'ack_conflict'});
  await assert.rejects(f.ledger.ack(f.scope,{...ackInput(e,o),body_digest:'0'.repeat(64)}),{message:'ack_conflict'});
});

test('ack requires real scoped offer history; no bulk/forged receipts',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const fake={lease_id:id(90)};
  await assert.rejects(ack(f,e,fake),{message:'lease_unknown'});
  const o=await offer(f,e);const a=ackInput(e,o);
  for(const v of [{...a,event_id:'x'},{...a,after_seq:1},{...a,lease_id:'A'.repeat(32)},{...a,local_receipt_id:null}])
    await assert.rejects(f.ledger.ack(f.scope,v),{message:'invalid_request'});
  f.db.sql.prepare('UPDATE pull_offers SET install_id=?').run(id(99));await assert.rejects(ack(f,e,o),{message:'lease_unknown'});
  assert.equal(rows(f,'pull_receipts').length,0);assert.ok(one(f,'pull_events').text);
});

for(const delta of [-1,0,1])test(`first ack at absolute expiry ${delta}ms`,async(t)=>{
  const f=fixture(t,{end:NOW+60000});const e=await enqueue(f);const o=await offer(f,e);f.time=NOW+60000+delta;
  if(delta<0)await ack(f,e,o);else await assert.rejects(ack(f,e,o),{message:'event_expired'});
});

test('committed ack and SMTP dedupe survive terminal detail purge through thread+30d',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);const r=await ack(f,e,o);
  f.time=NOW+MONTH-1;assert.equal((await f.ledger.purgeDetails()).count,0);
  f.time=NOW+MONTH;assert.equal((await f.ledger.purgeDetails()).count,1);assert.equal(rows(f,'pull_events').length,0);
  f.time=NOW+80*DAY;assert.deepEqual({...await ack(f,e,o)},r);
  const duplicate=await enqueue(f);assert.equal(duplicate.duplicate,true);assert.equal(duplicate.seq,e.seq);assert.equal(rows(f,'pull_events').length,0);
  await assert.rejects(enqueue(f,1,{text:'changed'}),{message:'message_id_conflict'});
  f.time=NOW+120*DAY-1;assert.equal((await f.ledger.purgeDedupe()).count,0);assert.deepEqual({...await ack(f,e,o)},r);
  f.time=NOW+120*DAY;await assert.rejects(ack(f,e,o),{message:'event_expired'});
  assert.equal((await f.ledger.purgeDedupe()).count,1);
  for(const table of ['pull_dedupe','pull_offers','pull_receipts','pull_events'])assert.equal(rows(f,table).length,0);
  await assert.rejects(enqueue(f),{message:'event_expired'}); // Dead thread cannot recreate purged event.
});

for(const reason of ['unknown_thread','queue_full','storage_unavailable','local_disabled','vault_locked','conversation_busy'])
test(`hold ${reason} schedules, retains body/history and immutable digest`,async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);const before=one(f,'pull_dedupe');
  const r=await f.ledger.hold(f.scope,holdInput(e,o,{reason}));assert.equal(r.retry_after_ms,NOW+60000);
  assert.equal(one(f,'pull_events').text,'Reply héllo');assert.equal(one(f,'pull_events').state,'held');
  assert.deepEqual(one(f,'pull_dedupe'),before);assert.equal(rows(f,'pull_offers').length,1);
  f.time=NOW+60000;const next=await offer(f,e);const capped=await f.ledger.hold(f.scope,holdInput(e,next,{reason,retry_after_ms:NOW+100*DAY}));
  assert.equal(capped.retry_after_ms,e.expires_ms);assert.equal(one(f,'pull_events').expires_ms,e.expires_ms);
  await assert.rejects(f.ledger.hold(f.scope,holdInput(e,next,{reason:'raw arbitrary'})),{message:'invalid_request'});
});

test('offer history bounded at 64; compact expired only, refused first old ack and retained receipt replay',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const old=await offer(f,e);let latest=old;
  for(let n=1;n<=64;n++){f.time+=60000;latest=await offer(f,e);}
  assert.equal(rows(f,'pull_offers').length,64);assert.ok(!rows(f,'pull_offers').some((r)=>r.lease_id===old.lease_id));
  await assert.rejects(ack(f,e,old),{message:'lease_unknown'});const r=await ack(f,e,latest);assert.deepEqual({...await ack(f,e,old)},r);
});

test('window enforces ten events and server-computed encoded bytes; capacity frees after ack',async(t)=>{
  const f=fixture(t);const offered=[];
  for(let n=1;n<=10;n++){const e=await enqueue(f,n,{thread_id:n===1?f.thread:thread(f,n)});offered.push([e,await offer(f,e)]);}
  const next=await enqueue(f,11,{thread_id:thread(f,11)});await assert.rejects(offer(f,next),{message:'queue_full'});
  await ack(f,...offered[0]);await offer(f,next);assert.equal(one(f,'pull_notifications').dirty_from_seq,2);
  const big=fixture(t);const list=[];
  for(let n=1;n<=6;n++)list.push(await enqueue(big,n,{thread_id:n===1?big.thread:thread(big,n),text:'\u0001'.repeat(16384)}));
  let used=0,count=0;
  for(const e of list){const size=offerFrameBytes(e);if(used+size.encoded<=524288){await offer(big,e);used+=size.encoded;count++;}
    else await assert.rejects(offer(big,e),{message:'queue_full'});}
  assert.ok(count>0 && count<6);assert.ok(used<=524288);
  const fresh=fixture(t);const e=await enqueue(fresh);const size=offerFrameBytes(e);
  await assert.rejects(offer(fresh,e,{available_bytes:size.encoded-1}),{message:'queue_full'});
  await offer(fresh,e,{available_bytes:size.encoded});
});

test('queue count includes unexpired missing-MID holds; duplicate consumes no acceptance at cap',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);
  const insert=f.db.sql.prepare("INSERT INTO pull_ingress_holds VALUES(?, ?,7,3,?,?,'invalid_message_id',?,?)");
  for(let n=1;n<=99;n++)insert.run(id(n+100),id(1),f.thread,n.toString(16).padStart(64,'0'),NOW,NOW+MONTH);
  await assert.rejects(enqueue(f,2),{message:'queue_full'});assert.equal(one(f,'pull_installs').next_seq,2);
  assert.equal((await enqueue(f)).event_id,e.event_id);assert.ok(rows(f,'pull_budgets').every((r)=>r.count===1));
  f.db.sql.prepare('UPDATE pull_ingress_holds SET expires_ms=? WHERE hold_id=?').run(NOW,id(101));await enqueue(f,2);
});

for(const which of ['install','global'])test(`accepted daily ${which} budget is atomic, duplicate is free and midnight resets`,async(t)=>{
  const f=fixture(t);await enqueue(f);const day=Math.floor(NOW/DAY);const bucket=which==='install'?`pull:ingress:install:${id(1)}:${day}`:`pull:ingress:global:${day}`;
  f.db.sql.prepare('UPDATE pull_budgets SET count=? WHERE bucket=?').run(which==='install'?200:10000,bucket);
  const before=snapshot(f);await assert.rejects(enqueue(f,2),{message:'ingress_limit'});assert.deepEqual(snapshot(f),before);
  assert.equal((await enqueue(f)).duplicate,true);f.time=(day+1)*DAY;await enqueue(f,2);
  assert.equal(one(f,'pull_installs').next_seq,3);
});

test('seq CAS race recomputes digest and returns unique monotonic seq; identical race dedupes',async(t)=>{
  const f=fixture(t);let release;const barrier=new Promise((r)=>{release=r;});let arrivals=0;
  f.db.beforeBatch=async()=>{if(++arrivals<=2){if(arrivals===2)release();await barrier;}};
  const result=await Promise.all([enqueue(f,1),enqueue(f,2)]);assert.deepEqual(result.map((e)=>e.seq).sort(),[1,2]);
  assert.equal(rows(f,'pull_dedupe').length,2);assert.equal(one(f,'pull_installs').next_seq,3);
  const g=fixture(t);let done;const wait=new Promise((r)=>{done=r;});let seen=0;
  g.db.beforeBatch=async()=>{if(++seen<=2){if(seen===2)done();await wait;}};
  const same=await Promise.all([enqueue(g),enqueue(g)]);assert.equal(same.filter((e)=>!e.duplicate).length,1);
  assert.equal(one(g,'pull_installs').next_seq,2);assert.ok(rows(g,'pull_budgets').every((r)=>r.count===1));
});

test('concurrent offers and conflicting acks have one winner; exact repeats preserve receipt',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);let go;const barrier=new Promise((r)=>{go=r;});let n=0;
  f.db.beforeBatch=async()=>{if(++n<=2){if(n===2)go();await barrier;}};
  const os=await Promise.allSettled([offer(f,e),offer(f,e)]);assert.equal(os.filter((r)=>r.status==='fulfilled').length,1);
  const o=os.find((r)=>r.status==='fulfilled').value;f.db.beforeBatch=null;
  let go2;const barrier2=new Promise((r)=>{go2=r;});let count=0;
  f.db.beforeBatch=async()=>{if(++count<=2){if(count===2)go2();await barrier2;}};
  const rs=await Promise.allSettled([ack(f,e,o,id(50)),ack(f,e,o,id(51))]);assert.equal(rs.filter((r)=>r.status==='fulfilled').length,1);
  assert.equal(rs.find((r)=>r.status==='rejected').reason.message,'ack_conflict');assert.equal(rows(f,'pull_receipts').length,1);
});

for(const mode of ['epoch','generation','owner-revert','fenced','revoke','cancel','inactive','frozen','key','owner-hash'])
for(const during of [false,true])test(`${mode} ${during?'between prepare and commit':'before operation'} refuses enqueue/page/offer/ack/hold`,async(t)=>{
  for(const method of ['enqueue','page','offer','ack','hold'])await t.test(method,async(t)=>{
    const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);
    if(method==='offer')f.time+=60000;
    const mutate=()=>{
      if(mode==='epoch')f.db.sql.prepare('UPDATE pull_installs SET epoch=5').run();
      else if(mode==='generation')rotate(f,3);
      else if(mode==='owner-revert')rotate(f,5);
      else if(mode==='fenced')f.db.sql.prepare("UPDATE pull_generations SET state='fenced'").run();
      else if(mode==='revoke'||mode==='cancel')fence(f,mode);
      else if(mode==='key')f.db.sql.prepare("UPDATE pull_installs SET key_id='different'").run();
      else if(mode==='owner-hash')f.db.sql.prepare('UPDATE pull_installs SET owner_hash=?').run('c'.repeat(64));
      else f.db.sql.prepare('UPDATE pull_installs SET state=?').run(mode==='inactive'?'disabled':'frozen');
    };
    if(during)f.db.beforeBatch=()=>{f.db.beforeBatch=null;mutate();};else mutate();
    const run=()=>method==='enqueue'?enqueue(f,2):method==='page'?f.ledger.page(f.scope):method==='offer'?offer(f,e):method==='ack'?ack(f,e,o):f.ledger.hold(f.scope,holdInput(e,o));
    await assert.rejects(run());assert.equal(rows(f,'pull_receipts').length,0);assert.equal(rows(f,'pull_dedupe').length,1);
    assert.equal(one(f,'pull_events').text,'Reply héllo');
  });
});

test('reconnect uses new epoch but old offer history; old-epoch committed receipt also refused',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);f.db.sql.prepare('UPDATE pull_installs SET epoch=5').run();
  await assert.rejects(ack(f,e,o),{message:'connection_superseded'});f.scope.epoch=5;await ack(f,e,o);
  await assert.rejects(f.ledger.ack({...f.scope,epoch:4},ackInput(e,o)),{message:'connection_superseded'});
});

test('bounded indexed expiry clears bodies/lease/notification and retains replay metadata',async(t)=>{
  const f=fixture(t,{end:NOW+60000});const events=[];
  for(let n=1;n<=3;n++)events.push(await enqueue(f,n));await offer(f,events[0]);
  f.time=NOW+59999;assert.equal((await f.ledger.expire()).count,0);f.time++;
  let a=await f.ledger.expire({limit:2});assert.equal(a.count,2);a=await f.ledger.expire({...a.cursor,limit:2});assert.equal(a.count,1);
  assert.equal((await f.ledger.expire({...a.cursor,limit:2})).count,0);
  assert.ok(rows(f,'pull_events').every((e)=>e.state==='expired'&&e.text===null&&e.body_bytes===0&&e.lease_id===null));
  assert.ok(rows(f,'pull_dedupe').every((d)=>d.disposition==='expired'));assert.equal(rows(f,'pull_notifications').length,0);
  assert.equal(rows(f,'pull_offers').length,1);
  const plan=f.db.sql.prepare("EXPLAIN QUERY PLAN SELECT event_id FROM pull_events WHERE state='pending' AND expires_ms<=? ORDER BY expires_ms,event_id LIMIT 100").all(f.time);
  assert.ok(plan.some((r)=>r.detail.includes('pull_event_expiry')));
  f.time=NOW+60000+MONTH-1;assert.equal((await f.ledger.purgeDedupe()).count,0);f.time++;
  const b=await f.ledger.purgeDedupe({limit:2});assert.equal(b.count,2);assert.equal((await f.ledger.purgeDedupe({...b.cursor,limit:2})).count,1);
  assert.equal(rows(f,'pull_dedupe').length,0);assert.equal(f.db.sql.prepare('PRAGMA foreign_key_check').all().length,0);
});

// Build each mutation once to discover its statement count, then inject at
// EVERY position on fresh fixtures and compare every pull table after rollback.
const operations={
  enqueue:async(f)=>()=>enqueue(f),
  duplicate:async(f)=>{await enqueue(f);return ()=>enqueue(f);},
  conflict:async(f)=>{await enqueue(f);return ()=>enqueue(f,1,{text:'changed'});},
  page:async(f)=>{await enqueue(f);return ()=>f.ledger.page(f.scope);},
  offer:async(f)=>{const e=await enqueue(f);return ()=>offer(f,e);},
  ack:async(f)=>{const e=await enqueue(f);const o=await offer(f,e);return ()=>ack(f,e,o);},
  repeat:async(f)=>{const e=await enqueue(f);const o=await offer(f,e);await ack(f,e,o);return ()=>ack(f,e,o);},
  hold:async(f)=>{const e=await enqueue(f);const o=await offer(f,e);return ()=>f.ledger.hold(f.scope,holdInput(e,o));},
  expire:async(f)=>{await enqueue(f);f.time+=MONTH;return ()=>f.ledger.expire();},
  details:async(f)=>{const e=await enqueue(f);const o=await offer(f,e);await ack(f,e,o);f.time+=MONTH;return ()=>f.ledger.purgeDetails();},
  metadata:async(f)=>{const e=await enqueue(f);const o=await offer(f,e);await ack(f,e,o);f.time+=120*DAY;return ()=>f.ledger.purgeDedupe();},
};
for(const [name,setup] of Object.entries(operations))test(`rollback at every statement: ${name}`,async(t)=>{
  const probe=fixture(t);const run=await setup(probe);try{await run();}catch(error){assert.equal(name,'conflict');assert.equal(error.message,'message_id_conflict');}
  const size=probe.db.lastBatch.length;assert.ok(size>=2);
  for(let at=0;at<size;at++)await t.test(`statement ${at+1}/${size}`,async(t)=>{
    const f=fixture(t);const execute=await setup(f);const before=snapshot(f);
    f.db.inBatch=(_s,n)=>{if(n===at)throw new Error('synthetic-secret-db-failure');};
    await assert.rejects(execute(),{message:'storage_unavailable'});assert.deepEqual(snapshot(f),before);
  });
});

test('queued batches cannot cross event expiry or budget UTC day, or commit without notification',async(t)=>{
  for(const method of ['enqueue','offer','ack','hold'])await t.test(method,async(t)=>{
    const f=fixture(t,{end:NOW+1000});const e=await enqueue(f);const o=method==='ack'||method==='hold'?await offer(f,e):null;
    const before=snapshot(f);f.db.beforeBatch=()=>{f.time=NOW+1000;};
    const run=()=>method==='enqueue'?enqueue(f,2):method==='offer'?offer(f,e):method==='ack'?ack(f,e,o):f.ledger.hold(f.scope,holdInput(e,o));
    await assert.rejects(run());assert.deepEqual(snapshot(f),before);
  });
  const f=fixture(t);f.time=(Math.floor(NOW/DAY)+1)*DAY-1;const before=snapshot(f);f.db.beforeBatch=()=>{f.time++;};
  await assert.rejects(enqueue(f));assert.deepEqual(snapshot(f),before);
  const g=fixture(t);g.db.sql.exec('DROP TABLE pull_notifications');await assert.rejects(enqueue(g));
  assert.equal(rows(g,'pull_events').length,0);assert.equal(one(g,'pull_installs').next_seq,1);assert.equal(rows(g,'pull_budgets').length,0);
});

test('actual SQLite wall clock, scope syntax, 30d receive bound, thread bound and notification revision',async(t)=>{
  const f=fixture(t,{real:true});const e=await enqueue(f);assert.equal(e.expires_ms,e.received_ms+MONTH);await offer(f,e);
  const g=fixture(t,{end:NOW+1000});const short=await enqueue(g);assert.equal(short.expires_ms,NOW+1000);
  const h=fixture(t);const a=await enqueue(h);const b=await enqueue(h,2);assert.equal(one(h,'pull_notifications').revision,2);
  const o=await offer(h,a);await ack(h,a,o);assert.equal(one(h,'pull_notifications').dirty_from_seq,b.seq);assert.equal(one(h,'pull_notifications').revision,3);
  for(const s of [{...h.scope,epoch:-1},{...h.scope,generation:0},{...h.scope,generation:1e15},{...h.scope,owner_generation:-0},
    {...h.scope,epoch:1.5},{...h.scope,install_id:'A'.repeat(32)}])await assert.rejects(h.ledger.page(s),{message:'invalid_request'});
  h.db.sql.prepare('UPDATE pull_installs SET epoch=0').run();h.scope.epoch=0;await enqueue(h,3);
  await assert.rejects(h.ledger.page(h.scope),{message:'invalid_request'});
});

test('enqueue preserves sequence gaps and refuses overflow without spending acceptance',async(t)=>{
  const f=fixture(t);f.db.sql.prepare('UPDATE pull_installs SET next_seq=42').run();const e=await enqueue(f);assert.equal(e.seq,42);
  f.db.sql.prepare('UPDATE pull_installs SET next_seq=?').run(Number.MAX_SAFE_INTEGER);const before=snapshot(f);
  await assert.rejects(enqueue(f,2),{message:'storage_unavailable'});assert.deepEqual(snapshot(f),before);
});

test('lease batches queued past their own deadline roll back; hold retry uses commit clock',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const before=snapshot(f);f.db.beforeBatch=()=>{f.time=NOW+60000;};
  await assert.rejects(offer(f,e));assert.deepEqual(snapshot(f),before);
  const g=fixture(t);const a=await enqueue(g);const o=await offer(g,a);g.db.beforeBatch=()=>{g.time+=10000;};
  const result=await g.ledger.hold(g.scope,holdInput(a,o));assert.equal(result.retry_after_ms,NOW+70000);
});

test('all maintenance pages handle 100 rows within D1 parameter limit; retirement does not prevent deletion',async(t)=>{
  const f=fixture(t,{end:NOW+60000});for(let n=1;n<=100;n++)await enqueue(f,n);
  const before=snapshot(f);await assert.rejects(enqueue(f,101),{message:'queue_full'});assert.deepEqual(snapshot(f),before);
  f.db.sql.prepare("UPDATE pull_installs SET state='disabled'").run();f.db.sql.prepare("UPDATE pull_generations SET state='fenced'").run();
  f.time=NOW+60000;assert.equal((await f.ledger.expire({limit:100})).count,100);
  f.time+=MONTH;assert.equal((await f.ledger.purgeDetails({limit:100})).count,100);assert.equal(rows(f,'pull_events').length,0);
  assert.equal((await f.ledger.purgeDedupe({limit:100})).count,100);assert.equal(rows(f,'pull_dedupe').length,0);
  for(const method of ['expire','purgeDetails','purgeDedupe'])for(const options of [{limit:101},{after_ms:-1},{after_id:'raw'}])
    await assert.rejects(f.ledger[method](options),{message:'invalid_request'});
});

test('global capacity includes missing-MID holds and counts atomically against competing enqueues',async(t)=>{
  const f=fixture(t);const otherThread=thread(f,2);const insert=f.db.sql.prepare("INSERT INTO pull_ingress_holds VALUES(?, ?,7,3,?,?,'invalid_message_id',?,?)");
  // Metadata fixture installs emulate other queues. Zero-byte holds alone fill
  // the global pilot ceiling; FK links remain actual scoped threads.
  f.db.sql.prepare(`INSERT INTO pull_installs SELECT ?,generation,owner_generation,state,owner_hash,owner_address,?,key_id,public_key,
    consent_version,active_since_ms,epoch,connection_id,next_seq,created_ms,updated_ms FROM pull_installs WHERE install_id=?`)
    .run(id(2),'m'+'b'.repeat(29),id(1));
  f.db.sql.prepare('INSERT INTO pull_generations SELECT ?,generation,owner_generation,owner_hash,key_id,public_key,fence_hash,flow_id,state,created_ms,fenced_ms FROM pull_generations WHERE install_id=?').run(id(2),id(1));
  f.db.sql.prepare('INSERT INTO pull_intents SELECT ?,generation,intent_id,owner_generation,owner_hash,input_digest,thread_id,message_id,provider_id,state,created_ms,finished_ms FROM pull_intents WHERE install_id=? AND thread_id=?').run(id(2),id(1),otherThread);
  f.db.sql.prepare('INSERT INTO pull_threads SELECT ?,generation,thread_id,owner_generation,intent_id,?,message_id,expires_ms,dedupe_until_ms,revoked_ms FROM pull_threads WHERE install_id=? AND thread_id=?').run(id(2),'9'.repeat(64),id(1),otherThread);
  f.db.sql.exec('BEGIN');for(let n=1;n<=9999;n++)insert.run(id(n+100),id(2),otherThread,n.toString(16).padStart(64,'0'),NOW,NOW+MONTH);f.db.sql.exec('COMMIT');
  let release;const barrier=new Promise((r)=>{release=r;});let n=0;
  f.db.beforeBatch=async()=>{if(++n<=2){if(n===2)release();await barrier;}};
  const result=await Promise.allSettled([enqueue(f,1),enqueue(f,2)]);
  assert.equal(result.filter((r)=>r.status==='fulfilled').length,1);assert.equal(result.find((r)=>r.status==='rejected').reason.message,'queue_full');
  assert.equal(rows(f,'pull_events').length,1);assert.equal(one(f,'pull_installs').next_seq,2);
  assert.ok(rows(f,'pull_budgets').every((r)=>r.count===1));
});

test('lost notification and interrupted ack response recover from durable page and original receipt',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);f.db.sql.prepare('DELETE FROM pull_notifications').run();
  const p=await f.ledger.page(f.scope);assert.equal(p.events[0].event.event_id,e.event_id);
  const o=await offer(f,e);const r=await ack(f,e,o);assert.equal(rows(f,'pull_events').length,1);
  const reloaded=new PullLedger({db:f.db,clock:()=>f.time});assert.deepEqual({...await reloaded.ack(f.scope,ackInput(e,o))},r);
  assert.equal((await reloaded.page(f.scope)).events.length,0);assert.equal((await enqueue(f)).duplicate,true);
});

test('missing receipt table fails closed; pruned offer during ack preparation is lease_unknown',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);
  f.db.beforeBatch=()=>{f.db.beforeBatch=null;f.db.sql.prepare('DELETE FROM pull_offers').run();};
  await assert.rejects(ack(f,e,o),{message:'lease_unknown'});assert.ok(one(f,'pull_events').text);assert.equal(rows(f,'pull_receipts').length,0);
  const g=fixture(t);const a=await enqueue(g);const b=await offer(g,a);await ack(g,a,b);g.db.sql.exec('DROP TABLE pull_receipts');
  await assert.rejects(ack(g,a,b),{message:'storage_unavailable'});assert.equal(one(g,'pull_events').state,'local_received');
});

test('custody receipt records transaction time and a local receipt cannot acknowledge two events',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);f.db.beforeBatch=()=>{f.db.beforeBatch=null;f.time+=1000;};
  const r=await ack(f,e,o);assert.equal(r.committed_ms,NOW+1000);assert.equal(one(f,'pull_events').terminal_ms,r.committed_ms);
  const next=await enqueue(f,2);const lease=await offer(f,next);await assert.rejects(ack(f,next,lease),{message:'ack_conflict'});
  assert.equal(rows(f,'pull_receipts').length,1);assert.equal(rows(f,'pull_events').find((row)=>row.event_id===next.event_id).text,'Reply héllo');
});

test('notification revision exhaustion rolls back enqueue, ack and hold',async(t)=>{
  for(const method of ['enqueue','ack','hold'])await t.test(method,async(t)=>{
    const f=fixture(t);const e=await enqueue(f);const next=await enqueue(f,2);const o=await offer(f,e);
    f.db.sql.prepare('UPDATE pull_notifications SET revision=?').run(Number.MAX_SAFE_INTEGER);
    const before=snapshot(f);await assert.rejects(method==='enqueue'?enqueue(f,3):method==='ack'?ack(f,e,o):f.ledger.hold(f.scope,holdInput(e,o)),{message:'storage_unavailable'});
    assert.deepEqual(snapshot(f),before);assert.ok(next.event_id);
  });
});

test('competing metadata cleanup reports actual changes while advancing the scanned cursor',async(t)=>{
  const f=fixture(t);const e=await enqueue(f);const o=await offer(f,e);await ack(f,e,o);f.time+=120*DAY;
  f.db.beforeBatch=async()=>{f.db.beforeBatch=null;assert.equal((await f.ledger.purgeDedupe()).count,1);};
  const result=await f.ledger.purgeDedupe();assert.equal(result.count,0);assert.equal(result.cursor.after_id,e.event_id);
  assert.equal((await f.ledger.purgeDedupe(result.cursor)).cursor,null);
});
