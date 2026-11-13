import test from 'node:test';
import assert from 'node:assert/strict';
import { PullActivateHTTP } from '../pull-activate-http.js';
import { boundaryTests, fixture, run, value, rows, one, snapshot, id, NOW, SECRET, routes, seedDelivery, seedRevert } from './fixtures/pull-lifecycle.js';
boundaryTests(test,'rotate');

test('rotation atomically fences old key, increments g and epoch, requires explicit final enable',async(t)=>{
  const f=await fixture(t,'rotate'),v=await value(f),r=await run(f,v);
  assert.equal(r.response.status,200);assert.deepEqual(r.body,{v:1,code:'ok',request_id:id(10),install_id:id(1),
    generation:8,owner_generation:3,key_id:'next',state:'verified'});
  assert.equal(r.response.headers.get('x-mail-generation'),'7');assert.equal(one(f).state,'verified');assert.equal(one(f).active_since_ms,null);
  assert.equal(one(f).epoch,5);assert.equal(one(f).public_key,f.next.publicKey);assert.deepEqual(rows(f,'pull_generations').map((g)=>g.state),['fenced','current']);
  const flow=rows(f,'pull_flows').find((r)=>r.flow_id===id(4));assert.equal(flow.state,'completed');assert.equal(flow.expires_ms,NOW+30*60000);
  const replay=await run(f,v);assert.deepEqual(replay.body,r.body);assert.equal(one(f).generation,8);
  // Old key has only exact receipt access, never authority for another operation.
  const denied=await run(f,{...v,op_id:id(11)});assert.equal(denied.response.status,401);
  f.http=new PullActivateHTTP(f.config);
  const enabled=await run(f,{op_id:id(12),flow_id:id(4),flow_secret:SECRET,consent_version:'v1'},
    {path:routes.activate,key:f.next.key,fields:{key_id:'next',generation:'8'}});
  assert.equal(enabled.response.status,200);assert.equal(one(f).state,'active');
});
for(const state of ['pending','verified','disabled','frozen'])test(`rotation requires active current key: ${state}`,async(t)=>{
  const f=await fixture(t,'rotate');f.sql.prepare('UPDATE pull_installs SET state=?').run(state);
  const before=snapshot(f),r=await run(f);assert.equal(r.response.status,409);assert.deepEqual(snapshot(f),before);
});
for(const state of ['pending','cancelled','expired','completed'])test(`rotation refuses missing mailbox approval: ${state}`,async(t)=>{
  const f=await fixture(t,'rotate');f.sql.prepare('UPDATE pull_flows SET state=? WHERE flow_id=?').run(state,id(4));
  const before=snapshot(f),r=await run(f);assert.equal(r.response.status,403);assert.equal(r.body.code,'human_required');assert.deepEqual(snapshot(f),before);
});
for(const extra of [{new_key_id:'other'},{new_public_key:'a'.repeat(43)},{fence_hash:'b'.repeat(64)},{new_signature:'a'.repeat(86)}])test(`rotation exact candidate binding ${JSON.stringify(extra)}`,async(t)=>{
  const f=await fixture(t,'rotate'),before=snapshot(f),r=await run(f,await value(f,extra));
  assert.ok([400,401].includes(r.response.status));assert.deepEqual(snapshot(f),before);
});
test('rotation cannot extend expired old-mailbox approval',async(t)=>{
  const f=await fixture(t,'rotate');f.time=NOW+30*60000;const before=snapshot(f),r=await run(f);
  assert.equal(r.response.status,410);assert.equal(r.body.code,'flow_expired');assert.deepEqual(snapshot(f),before);
});
test('concurrent cancellation of approved rotation rolls back old-key retirement',async(t)=>{
  const f=await fixture(t,'rotate');let once=false;
  f.db.beforeBatch=(ss)=>{if(once || !ss.some((s)=>s.sql.includes('SET generation=generation+1')))return;once=true;
    f.sql.prepare("UPDATE pull_flows SET state='cancelled' WHERE flow_id=?").run(id(4));};
  const r=await run(f);assert.equal(r.response.status,403);assert.equal(one(f).generation,7);assert.equal(one(f).state,'active');
  assert.equal(rows(f,'pull_nonces').length,0);assert.equal(rows(f,'pull_operations').length,0);
});

test('rotation retires threads, bodies, tickets, sessions and tokens without erasing accepted send evidence',async(t)=>{
  const f=await fixture(t,'rotate');await seedDelivery(f);
  const r=await run(f);assert.equal(r.response.status,200);assert.equal(one(f).generation,8);
  assert.ok(rows(f,'pull_threads').every((r)=>r.revoked_ms===NOW));
  assert.ok(rows(f,'pull_events').every((r)=>r.state==='revoked' && r.text===null && r.body_bytes===0));
  assert.equal(one(f,'pull_tickets').state,'invalidated');assert.equal(one(f,'pull_tokens').state,'invalidated');
  assert.equal(one(f,'pull_browser_sessions').state,'invalidated');assert.equal(rows(f,'pull_notifications').length,0);
  assert.equal(rows(f,'pull_intents').find((r)=>r.intent_id==='accepted').state,'accepted');
  assert.equal(rows(f,'pull_intents').find((r)=>r.intent_id==='sending').state,'uncertain');
  assert.equal(rows(f,'pull_intents').find((r)=>r.intent_id==='reserved').state,'revoked');
});

test('rotation retains only the exact current change unexpired revert exception',async(t)=>{
  const f=await fixture(t,'rotate');seedRevert(f);
  assert.equal((await run(f)).response.status,200);
  const tokens=rows(f,'pull_tokens');
  assert.equal(tokens.find((r)=>r.token_hash==='1'.repeat(64)).state,'pending');
  assert.equal(tokens.find((r)=>r.token_hash==='2'.repeat(64)).state,'invalidated');
  assert.equal(tokens.find((r)=>r.token_hash==='3'.repeat(64)).state,'invalidated');
});
