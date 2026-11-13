import test from 'node:test';
import assert from 'node:assert/strict';
import { boundaryTests, fixture, run, value, rows, one, id, hash, FENCE, NOW, snapshot, seedDelivery, seedRevert } from './fixtures/pull-lifecycle.js';
boundaryTests(test,'fence');

test('fence uses denial capability alone and echoes owner-generation zero in signed receipt',async(t)=>{
  const f=await fixture(t,'fence'),v=await value(f),r=await run(f,v);
  assert.equal(r.response.status,200);assert.deepEqual(Object.keys(r.body).sort(),['v','code','request_id','install_id','generation','state','fence_receipt'].sort());
  assert.equal(r.body.code,'fenced');assert.equal(r.body.state,'fenced');assert.equal(r.body.request_id,id(10));assert.equal(one(f).state,'disabled');
  assert.equal(one(f).generation,7);assert.equal(one(f).epoch,5);assert.equal(one(f,'pull_generations').state,'fenced');
  const originalEpoch=one(f).epoch,receipt=r.body.fence_receipt;
  assert.deepEqual((await run(f,v)).body,r.body); // Duplicate nonce permitted for exact committed fence.
  f.time+=1000;const fresh=await run(f,{...v,nonce:id(900),timestamp:f.time});assert.deepEqual(fresh.body,r.body);
  assert.equal(one(f).epoch,originalEpoch);assert.equal(one(f,'pull_fences').receipt_id,receipt);assert.equal(one(f,'pull_fences').created_ms,NOW);
  assert.equal(JSON.stringify(snapshot(f)).includes(FENCE),false);
});
for(const extra of [{fence_token:'a'.repeat(64)},{generation:8},{flow_id:id(99)},{install_id:id(99),mode:'revoke'},
  {timestamp:NOW-300001},{timestamp:NOW+300001},{mode:'delete'},{nonce:'x'}])test(`fence refuses wrong scope/proof ${JSON.stringify(extra)}`,async(t)=>{
  const f=await fixture(t,'fence'),before=snapshot(f),r=await run(f,await value(f,extra));
  assert.equal(r.response.status,401);assert.deepEqual(r.body,{v:1,code:'unauthorized'});assert.deepEqual(snapshot(f),before);
});
test('fence rejects ordinary signature headers even with valid capability',async(t)=>{
  const f=await fixture(t,'fence'),r=await run(f,await value(f),{headers:{'x-mail-signature':'anything'}});
  assert.equal(r.response.status,401);assert.equal(rows(f,'pull_fences').length,0);
});
test('cancel-before-allocation commits permanent tombstone without install FK or private identity',async(t)=>{
  const f=await fixture(t,'fence'),v=await value(f,{install_id:id(99),flow_id:id(98),generation:1,mode:'cancel'});
  const r=await run(f,v);assert.equal(r.response.status,200);assert.equal(r.body.state,'fenced');
  assert.equal(rows(f,'pull_installs').length,1);assert.equal(rows(f,'pull_operations').length,0);
  assert.deepEqual((await run(f,v)).body,r.body);
  const row=one(f,'pull_fences');assert.equal(row.fence_hash,await hash(FENCE));assert.equal(row.install_id,id(99));
});
test('old generation fence cannot alter new key, state, epoch or owner',async(t)=>{
  const f=await fixture(t,'fence');f.sql.prepare("UPDATE pull_generations SET state='fenced',fenced_ms=?").run(NOW-1);
  const i=one(f);f.sql.prepare('UPDATE pull_installs SET generation=8,epoch=5').run();
  f.sql.prepare("INSERT INTO pull_generations VALUES(?,8,3,?,'current',?,?,?,'current',?,NULL)")
    .run(id(1),i.owner_hash,f.candidate.publicKey,'b'.repeat(64),id(3),NOW);
  const before=one(f),r=await run(f);assert.equal(r.response.status,200);assert.equal(r.body.state,'already_superseded');assert.deepEqual(one(f),before);
  assert.deepEqual((await run(f,await value(f))).body,r.body);
});
test('fence operation digest includes mode and capability but excludes nonce/time',async(t)=>{
  const f=await fixture(t,'fence'),v=await value(f),r=await run(f,v);assert.equal(r.response.status,200);
  const before=snapshot(f),conflict=await run(f,{...v,mode:'cancel',nonce:id(999)});
  assert.equal(conflict.response.status,401);assert.deepEqual(snapshot(f),before);
  const expected=await hash(JSON.stringify(['clayrune-mail-pull-fence','1',id(1),'7',id(2),'revoke',id(10),await hash(FENCE)]));
  assert.equal(one(f,'pull_fences').input_digest,expected);
});

for(const mode of ['cancel','revoke'])test(`immutable ${mode} fence refuses a different op_id on the same scope`,async(t)=>{
  const f=await fixture(t,'fence'),v=await value(f,{mode});
  const original=await run(f,v);assert.equal(original.response.status,200);const before=snapshot(f);
  const refused=await run(f,{...v,op_id:id(11),nonce:id(900)});
  assert.equal(refused.response.status,401);assert.deepEqual(refused.body,{v:1,code:'unauthorized'});
  assert.equal(refused.response.headers.has('x-mail-signature'),false);assert.deepEqual(snapshot(f),before);
  assert.deepEqual((await run(f,v)).body,original.body);
});
for(const preallocation of [false,true])test(`cancel then revoke cannot replace the immutable fence, preallocation=${preallocation}`,async(t)=>{
  const f=await fixture(t,'fence'),v=await value(f,{mode:'cancel',...(preallocation?{install_id:id(99),flow_id:id(98),generation:1}:{})});
  const original=await run(f,v);assert.equal(original.response.status,200);
  for(const op_id of [v.op_id,id(11)]){
    const before=snapshot(f),r=await run(f,{...v,op_id,mode:'revoke',nonce:id(901)});
    assert.equal(r.response.status,401);assert.deepEqual(r.body,{v:1,code:'unauthorized'});assert.deepEqual(snapshot(f),before);
  }
  f.time+=1000;assert.deepEqual((await run(f,{...v,nonce:id(902),timestamp:f.time})).body,original.body);
});

for(const mode of ['cancel','revoke'])test(`current-generation ${mode} retires all executable authority and preserves provider uncertainty`,async(t)=>{
  const f=await fixture(t,'fence');await seedDelivery(f);
  const r=await run(f,await value(f,{mode}));assert.equal(r.response.status,200);
  assert.equal(one(f).state,'disabled');assert.equal(one(f).generation,7);
  assert.ok(rows(f,'pull_threads').every((r)=>r.revoked_ms===NOW));
  assert.ok(rows(f,'pull_events').every((r)=>r.state==='revoked' && r.text===null && r.body_bytes===0));
  assert.ok(rows(f,'pull_dedupe').every((r)=>r.disposition==='revoked'));
  assert.equal(rows(f,'pull_notifications').length,0);assert.equal(one(f,'pull_tickets').state,'invalidated');
  assert.equal(one(f,'pull_tokens').state,'invalidated');assert.equal(one(f,'pull_browser_sessions').state,'invalidated');
  assert.equal(rows(f,'pull_intents').find((r)=>r.intent_id==='accepted').state,'accepted');
  assert.equal(rows(f,'pull_intents').find((r)=>r.intent_id==='sending').state,'uncertain');
  assert.equal(rows(f,'pull_intents').find((r)=>r.intent_id==='reserved').state,'revoked');
});

test('concurrent preallocation cancellations serialize without a missing or second receipt',async(t)=>{
  const f=await fixture(t,'fence'),v=await value(f,{mode:'cancel',install_id:id(99),flow_id:id(98),generation:1});
  const [a,b]=await Promise.all([run(f,v),run(f,{...v,nonce:id(950)})]);
  assert.equal(a.response.status,200);assert.equal(b.response.status,200);assert.deepEqual(a.body,b.body);
  assert.equal(rows(f,'pull_fences').length,1);assert.equal(rows(f,'pull_operations').length,0);
});

test('revocation preserves only the current completed change unexpired revert token',async(t)=>{
  const f=await fixture(t,'fence');seedRevert(f);
  assert.equal((await run(f)).response.status,200);
  const tokens=rows(f,'pull_tokens');
  assert.equal(tokens.find((r)=>r.token_hash==='1'.repeat(64)).state,'pending');
  assert.equal(tokens.find((r)=>r.token_hash==='2'.repeat(64)).state,'invalidated');
  assert.equal(tokens.find((r)=>r.token_hash==='3'.repeat(64)).state,'invalidated');
});
