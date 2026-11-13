import test from 'node:test';
import assert from 'node:assert/strict';
import { boundaryTests, fixture, run, rows, one, id, NOW, DAY, LOCAL, snapshot } from './fixtures/pull-lifecycle.js';
boundaryTests(test,'status');

test('status exports exactly bounded registry metadata without lifecycle mutation',async(t)=>{
  const f=await fixture(t),before=one(f),r=await run(f);
  assert.equal(r.response.status,200);assert.deepEqual(r.body,{v:1,code:'ok',request_id:r.request.headers.get('x-mail-nonce'),
    install_id:id(1),generation:7,owner_generation:3,state:'active',address:`${LOCAL}@mail.example.net`,owner:'Owner+tag@example.com',key_id:'current',
    queue_count:0,queue_bytes:0,oldest_received_ms:null,oldest_expires_ms:null,invalid_message_id_holds:0,current_epoch:4,
    owner_change:null,last_notice_state:null,reason:''});
  assert.deepEqual(one(f),before);assert.equal(rows(f,'pull_operations').length,0);assert.equal(rows(f,'pull_nonces').length,1);
});
for(const state of ['pending','verified','disabled','frozen','deleted'])test(`current ${state} key can read exact own status`,async(t)=>{
  const f=await fixture(t);f.sql.prepare("UPDATE pull_installs SET state=?,owner_address=CASE WHEN ?='deleted' THEN NULL ELSE owner_address END").run(state,state);
  if(['disabled','deleted'].includes(state))f.sql.prepare("UPDATE pull_generations SET state='fenced',fenced_ms=?").run(NOW);
  const r=await run(f);assert.equal(r.response.status,200);assert.equal(r.body.state,state);
  assert.equal(r.body.owner,state==='deleted'?null:'Owner+tag@example.com');assert.equal(r.body.address,state==='deleted'?null:`${LOCAL}@mail.example.net`);
});
test('status counts valid and invalid-ID holds but never returns text or token',async(t)=>{
  const f=await fixture(t),reg=await f.ledger.registration(id(1));
  await f.ledger.reserveSend(reg,{intent_id:'i',thread_id:'t',digest:'a'.repeat(64),token_hash:'b'.repeat(64),message_id:'i@example.net'},NOW);
  await f.ledger.authorizeSend(reg,'i',NOW);await f.ledger.finishSend(reg,'i','accepted',NOW,'provider');
  const scope={install_id:id(1),generation:7,owner_generation:3,epoch:4};
  await f.ledger.enqueue(scope,{thread_id:'t',incoming_message_id:'first@example.net',text:'private body',
    auth:{ingress:'cloudflare',spf:'pass',dkim:'pass',dmarc:'pass',aligned:'spf+dkim'}});
  f.sql.prepare("INSERT INTO pull_ingress_holds VALUES(?,?,?,?,?,?,'invalid_message_id',?,?)")
    .run(id(50),id(1),7,3,'t','c'.repeat(64),NOW-1,NOW+DAY);
  // Old owner-generation metadata is not in the current status counters.
  f.sql.prepare("INSERT INTO pull_ingress_holds VALUES(?,?,?,?,?,?,'invalid_message_id',?,?)")
    .run(id(51),id(1),7,2,'t','d'.repeat(64),NOW-5,NOW+DAY);
  const r=await run(f);assert.equal(r.response.status,200);assert.equal(r.body.queue_count,2);assert.equal(r.body.queue_bytes,12);
  assert.equal(r.body.invalid_message_id_holds,1);assert.equal(r.body.oldest_received_ms,NOW-1);assert.equal(r.body.oldest_expires_ms,NOW+DAY);
  assert.equal(r.body.reason,'invalid_message_id');assert.equal(JSON.stringify(r.body).includes('private body'),false);
});
test('expired invalid-ID holds do not inflate current status',async(t)=>{
  const f=await fixture(t),reg=await f.ledger.registration(id(1));
  await f.ledger.reserveSend(reg,{intent_id:'i',thread_id:'t',digest:'a'.repeat(64),token_hash:'b'.repeat(64),message_id:'i@example.net'},NOW);
  f.sql.prepare("INSERT INTO pull_ingress_holds VALUES(?,?,?,?,?,?,'invalid_message_id',?,?)")
    .run(id(51),id(1),7,3,'t','d'.repeat(64),NOW-5,NOW);
  const r=await run(f);assert.equal(r.body.queue_count,0);assert.equal(r.body.oldest_received_ms,null);
});
test('status snapshot cannot leak a raced owner change',async(t)=>{
  const f=await fixture(t);let once=false;
  f.db.beforeBatch=(ss)=>{if(once || !ss.some((s)=>s.sql.includes('COUNT(*) AS n,COALESCE(SUM(body_bytes)')))return;once=true;
    f.sql.prepare("UPDATE pull_installs SET generation=8,owner_generation=4,owner_hash=?,owner_address='Other@example.net'").run('b'.repeat(64));};
  const r=await run(f);assert.equal(r.response.status,409);assert.equal(r.body.code,'stale_generation');
  assert.equal(rows(f,'pull_nonces').length,0);assert.equal(JSON.stringify(r.body).includes('Other'),false);
});
