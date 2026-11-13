import test from 'node:test';
import assert from 'node:assert/strict';
import { boundaryTests, fixture, run, value, rows, one, snapshot, id, hash, NOW, SECRET, FENCE } from './fixtures/pull-lifecycle.js';
boundaryTests(test,'activate');

test('activation commits reservation, active consent and stable signed receipt together',async(t)=>{
  const f=await fixture(t,'activate'),v=await value(f),r=await run(f,v);
  assert.equal(r.response.status,200);assert.deepEqual(r.body,{v:1,code:'ok',request_id:id(10),install_id:id(1),
    generation:1,owner_generation:1,state:'active',active_since_ms:NOW});
  assert.equal(one(f).state,'active');assert.equal(one(f,'pull_addresses').state,'assigned');
  assert.deepEqual(rows(f,'pull_owner_slots').map((r)=>r.role),['active']);
  assert.equal(one(f,'pull_flows').state,'completed');assert.equal(rows(f,'pull_operations').length,1);
  f.time+=1000;const replay=await run(f,v);assert.deepEqual(replay.body,r.body);assert.equal(one(f).active_since_ms,NOW);
  const changed=await run(f,{...v,consent_version:'v2'});assert.equal(changed.response.status,409);assert.equal(changed.body.code,'operation_conflict');
});
for(const state of ['pending','disabled','frozen','deleted'])test(`activation refuses ${state} without implicit consent`,async(t)=>{
  const f=await fixture(t,'activate');f.sql.prepare("UPDATE pull_installs SET state=?,owner_address=CASE WHEN ?='deleted' THEN NULL ELSE owner_address END").run(state,state);
  const before=snapshot(f),r=await run(f);assert.equal(r.response.status,409);assert.deepEqual(snapshot(f),before);
});
for(const extra of [{flow_secret:'a'.repeat(64)},{flow_id:id(99)},{consent_version:'v2'}])test(`activation binds exact human flow ${JSON.stringify(extra)}`,async(t)=>{
  const f=await fixture(t,'activate'),before=snapshot(f),r=await run(f,await value(f,extra));
  assert.ok([401,403].includes(r.response.status));assert.deepEqual(snapshot(f),before);
});
for(const offset of [0,1])test(`signup activation original deadline equality/+${offset}`,async(t)=>{
  const f=await fixture(t,'activate');f.time=one(f,'pull_flows').expires_ms+offset;
  const before=snapshot(f),r=await run(f);assert.equal(r.response.status,410);assert.equal(r.body.code,'flow_expired');assert.deepEqual(snapshot(f),before);
});
test('activation cannot cross a preexisting cancellation fence',async(t)=>{
  const f=await fixture(t,'activate');f.sql.prepare('INSERT INTO pull_fences VALUES(?,?,?,?,?,?,?,?,?)')
    .run(id(1),id(2),1,await hash(FENCE),'cancel',id(80),id(81),'b'.repeat(64),NOW);
  const before=snapshot(f),r=await run(f);assert.equal(r.body.code,'fenced');assert.equal(r.response.status,409);assert.deepEqual(snapshot(f),before);
});
test('activation rechecks distinct-owner quota transactionally',async(t)=>{
  const f=await fixture(t,'activate'),owner=one(f).owner_hash;let injected=false;
  f.db.beforeBatch=(ss)=>{if(injected || !ss.some((s)=>s.sql.includes("SET state='active'")))return;injected=true;
    for(let n=20;n<23;n++){
      f.sql.prepare(`INSERT INTO pull_installs SELECT ?,generation,owner_generation,state,owner_hash,owner_address,?,key_id,
        public_key,consent_version,active_since_ms,epoch,connection_id,next_seq,created_ms,updated_ms FROM pull_installs WHERE install_id=?`)
        .run(id(n),`m${id(n).slice(3)}`,id(1));
      f.sql.prepare("INSERT INTO pull_owner_slots VALUES(?,?,'active',?,NULL)").run(owner,id(n),id(n));
    }};
  const r=await run(f);assert.equal(r.response.status,409);assert.equal(r.body.code,'owner_limit');assert.equal(one(f).state,'verified');
  assert.equal(rows(f,'pull_nonces').length,0);assert.equal(rows(f,'pull_operations').length,0);
});
for(const purpose of ['owner_change','key_recovery','resume','rotate'])test(`activation uses completed ${purpose} result, not expected generation`,async(t)=>{
  const f=await fixture(t,'activate');
  const result={install_id:id(1),generation:2,owner_generation:purpose==='owner_change'?2:1,state:'verified',change_id:purpose==='owner_change'?id(9):null};
  f.sql.prepare("UPDATE pull_flows SET purpose=?,state='completed',result_json=?,expires_ms=?").run(purpose,JSON.stringify(result),NOW+30*60000);
  f.sql.prepare('UPDATE pull_installs SET generation=2,owner_generation=?').run(result.owner_generation);
  f.sql.prepare('UPDATE pull_generations SET generation=2,owner_generation=?').run(result.owner_generation);
  f.sql.prepare("UPDATE pull_addresses SET state='assigned',until_ms=NULL").run();
  f.sql.prepare("UPDATE pull_owner_slots SET role='active',until_ms=NULL,flow_id=?").run(id(99));
  f.scope={generation:'2',owner_generation:String(result.owner_generation)};
  const r=await run(f);assert.equal(r.response.status,200);assert.equal(r.body.generation,2);assert.equal(r.body.owner_generation,result.owner_generation);
});
test('mismatching management result cannot activate current registry',async(t)=>{
  const f=await fixture(t,'activate');f.sql.prepare("UPDATE pull_flows SET purpose='resume',state='completed',result_json=?")
    .run(JSON.stringify({install_id:id(1),generation:2,owner_generation:1,state:'verified',change_id:null}));
  const before=snapshot(f),r=await run(f);assert.equal(r.response.status,409);assert.equal(r.body.code,'stale_generation');assert.deepEqual(snapshot(f),before);
});
test('queued flow mutation invalidates activation snapshot and rolls back',async(t)=>{
  const f=await fixture(t,'activate');let once=false;
  f.db.beforeBatch=(ss)=>{if(once || !ss.some((s)=>s.sql.includes("SET state='active'")))return;once=true;
    f.sql.prepare("UPDATE pull_flows SET state='cancelled'").run();};
  const r=await run(f);assert.equal(r.response.status,409);assert.equal(rows(f,'pull_nonces').length,0);assert.equal(one(f).state,'verified');
});
